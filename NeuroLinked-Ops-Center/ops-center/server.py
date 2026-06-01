#!/usr/bin/env python3
"""
NeuroLinked Ops Center Backend — live operator state. Local state is persisted to
state.json next to this file so Jarvis can read and mutate it directly via his
file tools. Nothing here reaches out to external services automatically; wiring
real calendar/Slack/email sources is left to the operator (see state.json).

Endpoints:
  GET  /                       → dashboard HTML
  GET  /api/brain/stats        → knowledge-base counters (from state.json)
  GET  /api/brain/query?q=...  → tries NeuroLink brain :8020 first, falls back to local docs
  GET  /api/calendar/today     → today's calendar (from state.json)
  GET  /api/calendar/next      → next upcoming event
  GET  /api/slack/inbox        → Slack-style inbox (from state.json)
  GET  /api/plan-my-day        → composed day plan from calendar + open work
  GET  /api/agent/tasks        → list all agent task runs (multi-agent mgmt)
  GET  /api/agent/tasks/:id    → one task
  POST /api/agent/run          → run agent sync (returns result)
  POST /api/agent/start        → start agent async (returns task_id)
  POST /api/agent/cancel       → cancel a running task
  POST /api/calendar/create    → add event to calendar (persisted to state.json)
"""
import ipaddress, json, os, re, socket, sys, threading, time, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

# Windows default console codec (cp1252) can't encode the Unicode arrows/em-dashes
# used in log lines — force UTF-8 so startup doesn't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
HTML_PATH = BASE_DIR / "index.html"           # legacy ops-center dashboard
STATE_PATH = BASE_DIR / "state.json"
PORT = 8010

# the successor product UI — new daily workspace, served at "/" with the legacy
# dashboard moved to "/legacy". The directory holds vanilla HTML/JS/CSS;
# Three.js + GSAP are loaded from CDN at runtime.
AGENTIC_OS_DIR = BASE_DIR.parent / "agentic-os"
AGENTIC_OS_HTML = AGENTIC_OS_DIR / "index.html"
SECTIONS_DIR = BASE_DIR.parent / "sections"

# Try to import the section-md agent loader. If it fails (PyYAML missing,
# sections/ doesn't exist yet), the server falls back to the legacy
# custom_agents.json registry only — no crash.
try:
    import agent_loader as _agent_loader
    AGENT_LOADER_OK = True
except Exception as _e:
    print(f"[boot] agent_loader unavailable, falling back to custom_agents.json only: {_e}", flush=True)
    _agent_loader = None
    AGENT_LOADER_OK = False

# Scheduler (APScheduler-based). Started in main(); see scheduler.py.
try:
    from scheduler import AgentScheduler, parse_schedule as _parse_schedule
    SCHEDULER_OK = True
except Exception as _e:
    print(f"[boot] scheduler unavailable: {_e}", flush=True)
    AgentScheduler = None
    SCHEDULER_OK = False
SCHEDULER: "AgentScheduler | None" = None  # populated in main()

# --------------------------------------------------------------------------
# SECURITY LIMITS — hard caps on user-submitted data
# --------------------------------------------------------------------------
MAX_AGENT_NAME = 80
MAX_AGENT_DESC = 500
MAX_STEP_INPUT = 2000
MAX_STEPS      = 20
MAX_AGENTS     = 100
ALLOWED_STEP_TYPES = {"brain_search","reason","draft_email","call_api","create_task","notify","summarize"}

# SSRF guard — block any URL resolving to a private/loopback/link-local IP.
# Protects the call_api step from being used to pivot to internal services (brain:8020, jarvis:8340, LAN).
def _is_safe_public_host(url: str) -> tuple[bool, str]:
    try:
        u = urlparse(url)
        if u.scheme not in ("http","https"): return False, f"scheme '{u.scheme}' not allowed"
        host = u.hostname or ""
        if not host: return False, "no host"
        # Resolve and check every returned IP
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            return False, f"dns fail: {e}"
        for fam, *_ , sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except Exception:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, f"private/internal IP blocked: {ip_str}"
        return True, "ok"
    except Exception as e:
        return False, f"url parse error: {e}"


# --------------------------------------------------------------------------
# OPERATOR STATE — persisted to state.json next to this file.
# Jarvis can mutate this file directly via his system_write_file tool; we
# reload it lazily on each request so his edits take effect immediately.
# --------------------------------------------------------------------------

_DEFAULT_STATE = {
    "brain_stats": {
        "total_notes": 0,
        "folders": 0,
        "folder_breakdown": {},
    },
    "docs": [],       # [{title, folder, tags: []}]
    "calendar": [],   # [{title, start}]
    "slack_inbox": [],# [{channel, from, msg, priority, ts}]
}


def _load_state() -> dict:
    """Read state.json; seed an empty state on first run."""
    try:
        if STATE_PATH.exists():
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Backfill any missing top-level keys so the API never KeyErrors.
            for k, v in _DEFAULT_STATE.items():
                data.setdefault(k, v if not isinstance(v, (dict, list)) else (dict(v) if isinstance(v, dict) else list(v)))
            return data
    except Exception as e:
        print(f"[ops-center] state.json load failed, using defaults: {e}", flush=True)
    # First run: write the empty template so the operator sees where to edit.
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_STATE, f, indent=2)
    except Exception:
        pass
    # Deep-copy the defaults so callers don't mutate the template.
    return json.loads(json.dumps(_DEFAULT_STATE))


def _save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[ops-center] state.json save failed: {e}", flush=True)


# In-process cache; lazily (re)loaded by helpers below.
_STATE = _load_state()


def _refresh_state() -> dict:
    """Pull latest from disk (lets Jarvis edit state.json and have us see it)."""
    global _STATE
    _STATE = _load_state()
    return _STATE


def BRAIN_STATS():  # callable to always get fresh values
    return _refresh_state().get("brain_stats", {})


def DOCS():
    return _refresh_state().get("docs", [])


def CALENDAR():
    return _refresh_state().get("calendar", [])


def SLACK_INBOX():
    return _refresh_state().get("slack_inbox", [])


# --------------------------------------------------------------------------
# BRAIN QUERY — local docs index (supplement to the NeuroLink Brain at :8020)
# --------------------------------------------------------------------------

def brain_search(q: str, limit: int = 8) -> list:
    qlow = q.lower().strip()
    if not qlow:
        return []
    hits = []
    for d in DOCS():
        score = 0
        hay = (d["title"] + " " + d["folder"] + " " + " ".join(d["tags"])).lower()
        if qlow in hay:
            score += 10
        for tok in qlow.split():
            if tok in hay:
                score += 3
        if score > 0:
            hits.append((score, d))
    hits.sort(key=lambda x: -x[0])
    return [{"title": d["title"], "folder": d["folder"], "path": f"{d['folder']}/{d['title']}.md"} for _, d in hits[:limit]]


# --------------------------------------------------------------------------
# AGENT TASKS — multi-agent parallel management
# --------------------------------------------------------------------------

AGENT_TASKS = {}  # task_id -> record


# --------------------------------------------------------------------------
# CUSTOM AGENT BUILDER — user-created agents with ordered step workflows.
# Persists to ./custom_agents.json so agents survive a server restart.
# --------------------------------------------------------------------------

CUSTOM_AGENTS_FILE = BASE_DIR / "custom_agents.json"

# Step catalog — each step is a structured unit with typed inputs.
# Frontend reads this to render a drag/select palette.
STEP_CATALOG = [
    {"id":"brain_search", "label":"Search the Brain (NeuroLinked)", "desc":"Query your real vault via NeuroLinked Brain on :8020.", "inputs":[{"name":"query","label":"Query","type":"text"}]},
    {"id":"brain_remember","label":"Save to the Brain",             "desc":"Store a note/finding back into NeuroLinked — becomes searchable next run.","inputs":[{"name":"content","label":"Content (supports {{step.N.output}})","type":"textarea"},{"name":"tags","label":"Tags (comma-separated)","type":"text"}]},
    {"id":"ask_jarvis",    "label":"Ask JARVIS",                     "desc":"Route a question through JARVIS — uses its full tool suite + brain context.","inputs":[{"name":"prompt","label":"Prompt","type":"textarea"}]},
    {"id":"llm_ask",      "label":"Ask the LLM",           "desc":"Reason with an LLM. Local Ollama by default; pick a cloud credential for Claude/OpenAI/Mistral/OpenRouter.", "inputs":[{"name":"prompt","label":"Prompt","type":"textarea"},{"name":"credential","label":"LLM Credential (optional — leave blank for local Ollama)","type":"credential:llm"}]},
    {"id":"send_email",   "label":"Send Email (SMTP)",      "desc":"Send a real email via your SMTP credential.",         "inputs":[{"name":"to","label":"To","type":"text"},{"name":"subject","label":"Subject","type":"text"},{"name":"body","label":"Body (supports {{step.N.output}} tokens)","type":"textarea"},{"name":"credential","label":"SMTP Credential","type":"credential:smtp"}]},
    {"id":"slack_notify", "label":"Slack Notify (Webhook)", "desc":"Post a message to Slack via Incoming Webhook.",       "inputs":[{"name":"message","label":"Message","type":"textarea"},{"name":"credential","label":"Slack Webhook Credential","type":"credential:slack_webhook"}]},
    {"id":"api_request",  "label":"Authenticated HTTP Request", "desc":"Make a real HTTP request with optional auth.",    "inputs":[{"name":"url","label":"URL","type":"text"},{"name":"method","label":"Method","type":"text"},{"name":"body","label":"Body (JSON — optional)","type":"textarea"},{"name":"credential","label":"Auth Credential (optional)","type":"credential:api"}]},
    {"id":"call_api",     "label":"Unauthenticated HTTP (public)", "desc":"Plain GET/POST to a public URL. No creds.",     "inputs":[{"name":"url","label":"URL","type":"text"},{"name":"method","label":"Method","type":"text"}]},
    {"id":"create_task",  "label":"Create a Task",          "desc":"Add a task to the user's task list.",                 "inputs":[{"name":"title","label":"Task Title","type":"text"}]},
    {"id":"notify",       "label":"Notify (UI toast)",      "desc":"Surface a notification inside this dashboard.",       "inputs":[{"name":"message","label":"Message","type":"textarea"}]},
    {"id":"summarize",    "label":"Summarize Results",      "desc":"Roll the prior step outputs into a concise summary.", "inputs":[]},
]

# Agent templates — scaffolded workflows for common business jobs.
AGENT_TEMPLATES = [
    {"id":"blank",             "name":"Blank",                              "desc":"Start from scratch.", "steps":[]},
    {"id":"email_summary",     "name":"Daily Brain Summary → Email",         "desc":"Searches your brain, summarizes with an LLM, emails you the brief.", "steps":[
        {"type":"brain_search","inputs":{"query":"today"}},
        {"type":"llm_ask","inputs":{"prompt":"Summarize the most important items from the brain results above in under 200 words."}},
        {"type":"send_email","inputs":{"to":"you@example.com","subject":"Daily Brain Summary","body":"{{step.2.output}}"}},
    ]},
    {"id":"slack_alert",       "name":"API → LLM Analysis → Slack",          "desc":"Hits an API, asks the LLM what changed, posts to Slack.", "steps":[
        {"type":"api_request","inputs":{"url":"https://api.example.com/events","method":"GET"}},
        {"type":"llm_ask","inputs":{"prompt":"Analyze this API response. What changed? Flag anything urgent."}},
        {"type":"slack_notify","inputs":{"message":"{{step.2.output}}"}},
    ]},
    {"id":"lead_followup",     "name":"New Lead → Personalized Outreach",    "desc":"Pulls lead context, drafts outreach, emails.", "steps":[
        {"type":"brain_search","inputs":{"query":"lead"}},
        {"type":"llm_ask","inputs":{"prompt":"Write a personalized 3-sentence outreach email to this lead using the brain context above."}},
        {"type":"send_email","inputs":{"to":"lead@example.com","subject":"Quick question","body":"{{step.2.output}}"}},
    ]},
    {"id":"invoice_watch",     "name":"Invoice Overdue Watcher",             "desc":"Scans for overdue invoices, drafts reminder, emails.", "steps":[
        {"type":"brain_search","inputs":{"query":"overdue invoice"}},
        {"type":"llm_ask","inputs":{"prompt":"List each invoice with amount and days overdue. Draft a polite 2-sentence reminder email per customer."}},
        {"type":"send_email","inputs":{"to":"ap@example.com","subject":"Invoice reminder","body":"{{step.2.output}}"}},
    ]},
    {"id":"content_researcher","name":"Research → Draft Content",            "desc":"LLM-researches a topic, drafts short-form content.", "steps":[
        {"type":"llm_ask","inputs":{"prompt":"Research the topic: [REPLACE ME]. Produce 3 angles for a short-form social post."}},
        {"type":"llm_ask","inputs":{"prompt":"Pick the strongest angle above and write a Twitter post + LinkedIn post."}},
        {"type":"create_task","inputs":{"title":"Review generated post drafts"}},
    ]},
]

# Credential kinds — each kind is a "shape" with required fields.
CREDENTIAL_KINDS = [
    {"id":"smtp",          "label":"Email (SMTP)",         "fields":[{"name":"host","label":"SMTP Host","type":"text","default":"smtp.gmail.com"},{"name":"port","label":"Port","type":"text","default":"587"},{"name":"username","label":"Username","type":"text"},{"name":"password","label":"Password / App Password","type":"password"},{"name":"from_addr","label":"From address","type":"text"}]},
    {"id":"slack_webhook", "label":"Slack Incoming Webhook","fields":[{"name":"webhook_url","label":"Webhook URL","type":"password"}]},
    {"id":"api",           "label":"HTTP Bearer / API Key", "fields":[{"name":"auth_type","label":"Auth type (bearer/header/basic)","type":"text","default":"bearer"},{"name":"value","label":"Token / Key","type":"password"},{"name":"header_name","label":"Header name (if 'header')","type":"text","default":"X-API-Key"}]},
    {"id":"llm",           "label":"LLM Cloud Key",         "fields":[{"name":"provider","label":"Provider (anthropic/openai/groq/mistral/openrouter)","type":"text","default":"anthropic"},{"name":"api_key","label":"API Key","type":"password"},{"name":"model","label":"Model (anthropic: claude-sonnet-4-5 | claude-haiku-4-5 | claude-opus-4-5, openai: gpt-4o-mini, mistral: mistral-large-latest, openrouter: openai/gpt-4o-mini)","type":"text","default":"claude-sonnet-4-5"}]},
]

# Add kinds to ALLOWED_STEP_TYPES now that we renamed/added
ALLOWED_STEP_TYPES.update({
    "llm_ask","send_email","slack_notify","api_request","brain_remember","ask_jarvis",
    # Agency content production
    "dalle_image","replicate_image","replicate_video","elevenlabs_tts",
    # Premium AI video
    "heygen_avatar","kling_video",
    # Video editing pipeline
    "video_transcribe","video_smart_clips","video_extract_clip","video_clean","video_caption_burn","video_concat","video_probe",
    "video_analyze","video_clip_batch","video_qa","buffer_post_batch",
    # 3D asset generation
    "tripo_3d","rodin_3d","scenario_pbr","skybox_hdri",
    # Posting / ops
    "buffer_post","slack_post","slack_dm",
    "discord_post","discord_dm","discord_read","discord_audit","discord_reconcile","discord_verify_watch",
    "ghl_workflow","ghl_create_opp",
    # Research
    "web_scrape","rss_fetch",
    # Manager orchestration
    "agent_review","agent_dispatch","write_output",
})


# --------------------------------------------------------------------------
# CREDENTIAL VAULT — Fernet-encrypted at rest, file perms 0600.
# Secrets NEVER leave the server in plaintext; the list endpoint returns
# only (id, name, kind) — the actual fields are decrypted just-in-time
# at step execution and kept in-process memory only.
# --------------------------------------------------------------------------
from cryptography.fernet import Fernet

VAULT_DIR = Path.home() / ".neurolinked"
VAULT_DIR.mkdir(mode=0o700, exist_ok=True)
VAULT_KEY_FILE = VAULT_DIR / "vault.key"
VAULT_FILE     = VAULT_DIR / "credentials.enc"

def _load_or_create_vault_key() -> bytes:
    if VAULT_KEY_FILE.exists():
        return VAULT_KEY_FILE.read_bytes()
    k = Fernet.generate_key()
    VAULT_KEY_FILE.write_bytes(k)
    try: os.chmod(VAULT_KEY_FILE, 0o600)
    except Exception: pass
    return k

_VAULT_KEY = _load_or_create_vault_key()
_FERNET = Fernet(_VAULT_KEY)

def _vault_load() -> dict:
    if not VAULT_FILE.exists(): return {}
    try:
        blob = VAULT_FILE.read_bytes()
        return json.loads(_FERNET.decrypt(blob).decode())
    except Exception as e:
        print(f"[vault] decrypt failed: {e}"); return {}

def _vault_save(vault: dict):
    VAULT_FILE.write_bytes(_FERNET.encrypt(json.dumps(vault).encode()))
    try: os.chmod(VAULT_FILE, 0o600)
    except Exception: pass

def vault_list_public() -> list:
    v = _vault_load()
    return [{"id": cid, "name": c["name"], "kind": c["kind"], "created_at": c.get("created_at","")}
            for cid, c in sorted(v.items(), key=lambda kv: kv[1].get("created_at",""), reverse=True)]

def vault_put(name: str, kind: str, fields: dict) -> dict:
    v = _vault_load()
    cid = f"c_{uuid.uuid4().hex[:8]}"
    v[cid] = {"id": cid, "name": name[:60], "kind": kind, "fields": fields, "created_at": datetime.now().isoformat()}
    _vault_save(v)
    return {"id": cid, "name": v[cid]["name"], "kind": kind}

def vault_delete(cid: str) -> bool:
    v = _vault_load()
    if cid in v:
        del v[cid]; _vault_save(v); return True
    return False

def vault_get_secret(cid: str) -> dict:
    """Decrypt just-in-time at step execution. Returns full credential fields."""
    if not cid: return {}
    return (_vault_load().get(cid) or {}).get("fields", {})


def _load_custom_agents() -> dict:
    try:
        if CUSTOM_AGENTS_FILE.exists():
            return json.loads(CUSTOM_AGENTS_FILE.read_text())
    except Exception as e:
        print(f"[agents] load error: {e}")
    return {}


def _save_custom_agents():
    try:
        CUSTOM_AGENTS_FILE.write_text(json.dumps(CUSTOM_AGENTS, indent=2))
    except Exception as e:
        print(f"[agents] save error: {e}")


# Seed with 2 example agents so the UI shows non-empty from first load.
_SEED_AGENTS = {
    "seed_finance": {
        "id": "seed_finance",
        "name": "Finance Watchdog",
        "description": "Reviews overdue AR weekly, drafts polite nudge emails, flags anything > $5K.",
        "steps": [
            {"type": "brain_search", "inputs": {"query": "overdue invoice"}},
            {"type": "reason", "inputs": {"prompt": "For each overdue invoice found, classify as NORMAL (< $5K) or FLAG (>= $5K) and propose a 1-line nudge for each."}},
            {"type": "draft_email", "inputs": {"to": "ap@example.com", "subject": "Friendly reminder — open invoice", "notes": "Use the reasoning output from the prior step."}},
            {"type": "notify", "inputs": {"channel": "slack", "message": "Finance Watchdog: drafts ready for review."}},
        ],
        "created_at": "2026-04-23T00:00:00",
        "enabled": True,
    },
    "seed_meeting_ready": {
        "id": "seed_meeting_ready",
        "name": "Meeting Prep Pro",
        "description": "Pulls context for the next calendar event and prepares a 1-page brief.",
        "steps": [
            {"type": "brain_search", "inputs": {"query": "acme"}},
            {"type": "reason", "inputs": {"prompt": "Using the notes above, build a 3-section brief: 1) Background 2) Open questions 3) My recommended talking points."}},
            {"type": "create_task", "inputs": {"title": "Review meeting brief before call"}},
        ],
        "created_at": "2026-04-23T00:00:00",
        "enabled": True,
    },
}


CUSTOM_AGENTS = _load_custom_agents()
# Auto-seed only if the legacy registry file doesn't exist at all.
# An empty `{}` is treated as an intentional drain (e.g. after migrating to
# section-md agents) and we leave it that way.
if not CUSTOM_AGENTS_FILE.exists():
    CUSTOM_AGENTS = dict(_SEED_AGENTS)
    _save_custom_agents()


# Merge in agents defined in sections/<name>/agents/*.md. Section-md agents
# take precedence on id collision (so the migrated finance_watchdog overrides
# the legacy seed_finance entry). Recompiles whenever load_section_agents()
# is called — see the /api/agents/reload endpoint.
def reload_section_agents() -> int:
    """Re-scan sections/*/agents/*.md and merge into CUSTOM_AGENTS.
    Returns the count of section-md agents loaded."""
    if not AGENT_LOADER_OK:
        return 0
    try:
        records = _agent_loader.load_all()
    except Exception as e:
        print(f"[agents] section-md load failed: {e}", flush=True)
        return 0
    # Drop any previously-loaded section-md agents so deletions are reflected
    for aid in list(CUSTOM_AGENTS.keys()):
        if CUSTOM_AGENTS[aid].get("source") == "section_md":
            del CUSTOM_AGENTS[aid]
    for aid, rec in records.items():
        CUSTOM_AGENTS[aid] = rec
    # Reconcile scheduler jobs with the new registry
    try:
        if SCHEDULER:
            SCHEDULER.refresh()
    except Exception:
        pass
    return len(records)


_n = reload_section_agents()
if _n:
    print(f"[boot] loaded {_n} section-md agent(s)", flush=True)


# --------------------------------------------------------------------------
# AGENT CONTEXT INJECTION — makes agents act like teammates, not isolated
# step machines. Every llm_ask / agent_review automatically learns:
#   - who it is (id, name, role, description)
#   - what section it's in (purpose, KPIs from _section.md)
#   - who its siblings are (other agents in the section + their purpose)
#   - who its manager is + the manager's rubric
#   - cross-section handoffs (so it knows when to route work elsewhere)
# --------------------------------------------------------------------------

def _parse_section_md(section_name: str) -> dict:
    """Pull purpose / KPIs / Handoffs / Boundaries out of sections/<name>/_section.md.
    Tolerant — returns empty strings for any section not present."""
    out = {"purpose": "", "kpis": "", "handoffs": "", "boundaries": ""}
    p = SECTIONS_DIR / section_name / "_section.md"
    if not p.exists():
        return out
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return out
    # Strip frontmatter if any
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:].lstrip()
    # Pull the lead paragraph as the purpose if no ## Purpose section
    sections = re.split(r"\n##\s+([^\n]+)\n", text)
    if sections:
        # First chunk is everything before the first ##
        lead = sections[0].strip()
        # Remove the H1 if present (no trailing-newline requirement — re.split eats it)
        lead = re.sub(r"^#\s+[^\n]+\n*", "", lead).strip()
        out["purpose"] = lead[:600]
        # Walk paired (heading, body) entries
        for i in range(1, len(sections), 2):
            heading = sections[i].strip().lower()
            body = (sections[i + 1] if i + 1 < len(sections) else "").strip()
            if "purpose" in heading and not out["purpose"].strip():
                out["purpose"] = body[:600]
            elif "kpi" in heading or "metric" in heading:
                out["kpis"] = body[:400]
            elif "handoff" in heading or "downstream" in heading or "upstream" in heading:
                out["handoffs"] = body[:600]
            elif "boundar" in heading or "out of scope" in heading or "not for" in heading:
                out["boundaries"] = body[:400]
    return out


def _build_agent_context(agent_id: str, *, max_chars: int = 3500) -> str:
    """Compose the system-context block injected before every reasoning prompt.
    Returns a markdown string ready to prepend to a prompt."""
    me = CUSTOM_AGENTS.get(agent_id) or {}
    if not me:
        return ""
    section = me.get("section", "")
    role = me.get("role", "worker")
    sec_meta = _parse_section_md(section) if section else {}
    siblings = [
        a for aid, a in CUSTOM_AGENTS.items()
        if aid != agent_id and a.get("section") == section
    ]
    manager = next((a for a in siblings if a.get("role") == "manager"), None) if role == "worker" else None
    workers = [a for a in siblings if a.get("role") != "manager"]

    lines: list[str] = []
    lines.append("===== AGENT CORE OS — TEAMMATE CONTEXT (auto-injected) =====")
    lines.append(f"You are: **{me.get('name','?')}** (`{agent_id}`) — {role.upper()} in the **{section or '(no section)'}** section.")
    if me.get("description"):
        lines.append(f"Your job: {me['description']}")
    if sec_meta.get("purpose"):
        lines.append(f"\n**Section purpose:** {sec_meta['purpose']}")
    if sec_meta.get("kpis"):
        lines.append(f"**Section KPIs:** {sec_meta['kpis']}")
    if sec_meta.get("boundaries"):
        lines.append(f"**Out of scope for this section:** {sec_meta['boundaries']}")

    if manager:
        lines.append(f"\n**Your manager:** {manager.get('name','?')} (`{manager.get('id','?')}`)")
        # Try to extract a rubric from the manager's body
        body = manager.get("body", "") or ""
        rub = re.search(r"##\s*Rubric\s*\n([\s\S]*?)(?=\n##|\Z)", body, re.IGNORECASE)
        if rub:
            rubric = rub.group(1).strip()[:500]
            lines.append(f"**Manager rubric (your output will be scored against this):**\n{rubric}")

    if workers and role != "manager":
        # Show siblings so this agent doesn't duplicate their work
        lines.append(f"\n**Your sibling workers in {section} ({len(workers)}):**")
        for s in workers[:12]:
            lines.append(f"- `{s.get('id')}` — {s.get('description', '')[:120]}")
    elif workers and role == "manager":
        lines.append(f"\n**You supervise {len(workers)} worker(s) in {section}:**")
        for s in workers[:15]:
            lines.append(f"- `{s.get('id')}` — {s.get('description', '')[:120]}")

    if sec_meta.get("handoffs"):
        lines.append(f"\n**Handoffs to other sections:**\n{sec_meta['handoffs']}")

    lines.append(f"\nWhen producing output: stay inside your section's scope. If the task naturally belongs to a sibling or another section, name that agent in your output rather than doing their work.")
    lines.append("===== END CONTEXT — your task follows below =====\n")

    block = "\n".join(lines)
    return block[:max_chars]


def _color_hex_to_int(c) -> int:
    """Accepts '#rrggbb', 'rrggbb', or already-int. Returns int or 0."""
    if c is None: return 0
    if isinstance(c, int): return c
    s = str(c).strip().lstrip("#")
    if not s: return 0
    try: return int(s, 16)
    except Exception: return 0


def _render(s: str, context: list, params: dict | None = None) -> str:
    """Expand {{step.N.output}} / {{step.N.body_preview}} / {{step.N.summary}} tokens.
    N is 1-indexed (matches UI numbering). Missing tokens resolve to empty string.
    Also expands:
        {{date}}      → YYYY-MM-DD (today)
        {{datetime}}  → ISO 8601
        {{time}}      → HH:MM:SS
        {{<input>}}   → from `params` dict (the agent run's input args)
    """
    if not s or "{{" not in s: return s or ""
    today = datetime.now()
    builtins = {
        "date":     today.strftime("%Y-%m-%d"),
        "datetime": today.isoformat(timespec="seconds"),
        "time":     today.strftime("%H:%M:%S"),
    }
    p = params or {}
    def repl(m):
        try:
            tok = m.group(1).strip()
            parts = tok.split(".")
            # {{step.N.key}}
            if len(parts) >= 3 and parts[0] == "step":
                idx = int(parts[1]) - 1
                if 0 <= idx < len(context):
                    key = parts[2]
                    v = context[idx].get(key, "")
                    return str(v) if v is not None else ""
            # {{date}} / {{time}} / {{datetime}}
            if tok in builtins:
                return builtins[tok]
            # {{<input>}} — single-word token matches an agent input by name
            if tok in p:
                v = p[tok]
                return str(v) if v is not None else ""
        except Exception: pass
        return m.group(0)
    return re.sub(r"\{\{([^}]+)\}\}", repl, s)


def _run_custom_step(step: dict, context: list, agent_id: str | None = None, params: dict | None = None) -> dict:
    """Execute one step of the workflow. Real API calls, real SMTP, real LLM."""
    stype = step.get("type")
    ins = step.get("inputs", {})
    ts = datetime.now().strftime("%H:%M:%S")

    if stype == "brain_search":
        q = _render(ins.get("query", ""), context, params).strip()
        if not q:
            return {"step": stype, "ts": ts, "error": "query required", "ok": False}
        # Try the REAL NeuroLinked Brain at :8020 first — real vault data.
        # The brain server requires the NEUROLINKED_TOKEN header (same token both
        # subprocesses inherit from START-ZERO.bat). Pass it via header.
        try:
            url = f"http://127.0.0.1:8020/api/claude/search?q={urllib.parse.quote(q)}&limit=6"
            req = urllib.request.Request(url, headers={"x-neurolinked-token": LAUNCH_TOKEN})
            resp = urllib.request.urlopen(req, timeout=5)
            d = json.loads(resp.read())
            results = d.get("results", [])
            # Extract readable titles from bracket-prefixed text, e.g. [path/to/note.md]
            titles, snippets = [], []
            for r in results[:6]:
                txt = r.get("text","")
                m = re.match(r"\[([^\]]+)\]", txt)
                title = m.group(1) if m else txt[:60]
                titles.append(title)
                snippets.append(f"▸ {title}\n  {txt[m.end():].strip()[:280] if m else txt[:280]}")
            return {"step": stype, "ts": ts, "source": "neurolinked_brain",
                    "query": q, "hits": len(results), "titles": titles,
                    "output": "\n\n".join(snippets) if snippets else "(no matches)", "ok": True}
        except Exception as e:
            # Fall back to in-memory if the Brain is down
            hits = brain_search(q, limit=5)
            return {"step": stype, "ts": ts, "source": "local_fallback",
                    "query": q, "hits": len(hits), "titles": [h["title"] for h in hits],
                    "output": "\n".join(h["title"] for h in hits),
                    "warning": f"brain unreachable — used local fallback: {str(e)[:80]}",
                    "ok": True}

    if stype == "reason":
        prompt = (ins.get("prompt", "") or "").strip()[:MAX_STEP_INPUT]
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        # REAL reasoning — hit local Ollama (same model JARVIS uses). Never leaves the machine.
        try:
            # Prefer the `output` field of each prior step (full content) over raw dict serialization.
            def _fmt(c):
                out = c.get("output") or c.get("body_preview") or c.get("summary") or ""
                return f"[step {c.get('step')}]\n{out}" if out else ""
            ctx_summary = "\n\n".join([s for s in (_fmt(c) for c in context[-3:]) if s])[:6000]
            full_prompt = (f"=== CONTEXT FROM PRIOR STEPS ===\n{ctx_summary}\n\n=== YOUR TASK ===\n{prompt}" if ctx_summary else prompt)
            req_body = json.dumps({"model":"llama3.1:8b","prompt": full_prompt,"stream": False}).encode()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=req_body, headers={"Content-Type":"application/json"})
            resp = urllib.request.urlopen(req, timeout=45)
            data = json.loads(resp.read())
            out = (data.get("response") or "").strip()
            return {"step": stype, "ts": ts, "prompt": prompt[:120] + ("…" if len(prompt) > 120 else ""),
                    "output": out[:2000], "model": "llama3.1:8b", "ok": True}
        except Exception as e:
            return {"step": stype, "ts": ts, "error": f"ollama unreachable: {str(e)[:120]}",
                    "output": "(LLM unavailable — check Ollama on :11434)", "ok": False}

    if stype == "draft_email":
        return {"step": stype, "ts": ts, "to": ins.get("to",""), "subject": ins.get("subject",""),
                "body": f"[Draft email composed from context: {ins.get('notes','')[:80]}…] — Awaiting review before send.", "ok": True}

    if stype == "call_api":
        url = (ins.get("url", "") or "").strip()[:500]
        method = (ins.get("method") or "GET").upper()
        if method not in ("GET","POST","PUT","DELETE","PATCH"):
            return {"step": stype, "ts": ts, "error": f"method {method} not allowed", "ok": False}
        safe, why = _is_safe_public_host(url)
        if not safe:
            # SSRF guard — protects brain:8020 / jarvis:8340 / LAN from being hit via the agent.
            return {"step": stype, "ts": ts, "error": f"blocked: {why}", "url": url, "ok": False}
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent":"NeuroLinked-Agent/1.0"})
            resp = urllib.request.urlopen(req, timeout=20)
            body = resp.read(100_000).decode("utf-8","replace")
            return {"step": stype, "ts": ts, "method": method, "url": url,
                    "status": resp.status, "body_preview": body[:600], "ok": True}
        except Exception as e:
            return {"step": stype, "ts": ts, "method": method, "url": url,
                    "error": str(e)[:200], "ok": False}

    if stype == "create_task":
        return {"step": stype, "ts": ts, "task": ins.get("title", ""), "ok": True, "saved_to": "task list"}

    if stype == "notify":
        return {"step": stype, "ts": ts, "channel": ins.get("channel","ui"), "message": ins.get("message",""), "ok": True}

    if stype == "summarize":
        return {"step": stype, "ts": ts, "summary": f"Ran {len(context)} prior step(s). " +
                ", ".join(f"{c.get('step')}={('ok' if c.get('ok') else 'fail')}" for c in context), "ok": True}

    # ---- LLM ASK (cloud if credential provided / available, else local Ollama) ----
    if stype == "llm_ask":
        raw_prompt = _render(ins.get("prompt", ""), context, params)[:MAX_STEP_INPUT]
        if not raw_prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        # Inject prior-step output (full content, not just titles) so the LLM has real material to work with.
        def _fmt(c):
            out = c.get("output") or c.get("body_preview") or c.get("summary") or ""
            return f"[step {c.get('step')}]\n{out}" if out else ""
        ctx_summary = "\n\n".join([s for s in (_fmt(c) for c in context[-3:]) if s])[:6000]
        # AGENT TEAMMATE CONTEXT — auto-injected unless explicitly disabled
        # via inputs.skip_context = true.
        teammate_ctx = ""
        if agent_id and not str(ins.get("skip_context", "")).lower() in ("true","1","yes"):
            teammate_ctx = _build_agent_context(agent_id)
        prompt = (
            (teammate_ctx + "\n\n" if teammate_ctx else "")
            + (f"=== CONTEXT FROM PRIOR STEPS ===\n{ctx_summary}\n\n=== YOUR TASK ===\n{raw_prompt}" if ctx_summary else raw_prompt)
        )
        cid = ins.get("credential") or ""
        cred = vault_get_secret(cid) if cid else {}
        # AUTO-PICK FALLBACK — if no credential was specified but the user has
        # any LLM credential saved, use the first one. Saves agent authors from
        # having to hardcode a credential id (which would be brittle anyway).
        cred_auto_picked = None
        if (not cred or not cred.get("api_key")):
            try:
                vault = _vault_load()
                for vid, v in vault.items():
                    if v.get("kind") == "llm" and (v.get("fields") or {}).get("api_key"):
                        cred = v.get("fields") or {}
                        cred_auto_picked = vid
                        break
            except Exception:
                pass
        provider = (cred.get("provider") or "ollama").lower() if cred else "ollama"
        # Capture full prompt + context for the run drawer "thinking" view.
        # These show up in the step result so the UI can expand them.
        thinking_meta = {
            "teammate_context": teammate_ctx,
            "prior_step_context": ctx_summary,
            "user_prompt": raw_prompt,
            "full_prompt_sent": prompt,
            "provider_chosen": provider,
            "credential_used": cid or cred_auto_picked or "(none)",
            "auto_picked_credential": bool(cred_auto_picked),
        }
        try:
            if provider == "anthropic" and cred.get("api_key"):
                req_body = json.dumps({"model": cred.get("model") or "claude-sonnet-4-5",
                                        "max_tokens": 2048, "messages":[{"role":"user","content":prompt}]}).encode()
                req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=req_body,
                    headers={"Content-Type":"application/json","x-api-key":cred["api_key"],"anthropic-version":"2023-06-01"})
                resp = urllib.request.urlopen(req, timeout=90)
                d = json.loads(resp.read())
                out = "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
                usage = d.get("usage") or {}
                return {"step":stype,"ts":ts,"provider":"anthropic","model": cred.get("model") or "claude-sonnet-4-5",
                        "output": out[:8000], "ok": True,
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "thinking": thinking_meta}
            if provider == "openai" and cred.get("api_key"):
                req_body = json.dumps({"model": cred.get("model") or "gpt-4o-mini",
                                        "messages":[{"role":"user","content":prompt}]}).encode()
                req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=req_body,
                    headers={"Content-Type":"application/json","Authorization":f"Bearer {cred['api_key']}"})
                resp = urllib.request.urlopen(req, timeout=90)
                d = json.loads(resp.read())
                out = d.get("choices",[{}])[0].get("message",{}).get("content","")
                usage = d.get("usage") or {}
                return {"step":stype,"ts":ts,"provider":"openai","model": cred.get("model") or "gpt-4o-mini",
                        "output": out[:8000], "ok": True,
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "thinking": thinking_meta}
            if provider == "groq" and cred.get("api_key"):
                req_body = json.dumps({"model": cred.get("model") or "llama-3.3-70b-versatile",
                                        "messages":[{"role":"user","content":prompt}]}).encode()
                req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=req_body,
                    headers={"Content-Type":"application/json","Authorization":f"Bearer {cred['api_key']}"})
                resp = urllib.request.urlopen(req, timeout=90)
                d = json.loads(resp.read())
                out = d.get("choices",[{}])[0].get("message",{}).get("content","")
                usage = d.get("usage") or {}
                return {"step":stype,"ts":ts,"provider":"groq","model": cred.get("model") or "llama-3.3-70b-versatile",
                        "output": out[:8000], "ok": True,
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "thinking": thinking_meta}
            if provider == "mistral" and cred.get("api_key"):
                req_body = json.dumps({"model": cred.get("model") or "mistral-large-latest",
                                        "messages":[{"role":"user","content":prompt}]}).encode()
                req = urllib.request.Request("https://api.mistral.ai/v1/chat/completions", data=req_body,
                    headers={"Content-Type":"application/json","Authorization":f"Bearer {cred['api_key']}"})
                resp = urllib.request.urlopen(req, timeout=90)
                d = json.loads(resp.read())
                out = d.get("choices",[{}])[0].get("message",{}).get("content","")
                usage = d.get("usage") or {}
                return {"step":stype,"ts":ts,"provider":"mistral","model": cred.get("model") or "mistral-large-latest",
                        "output": out[:8000], "ok": True,
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "thinking": thinking_meta}
            if provider == "openrouter" and cred.get("api_key"):
                req_body = json.dumps({"model": cred.get("model") or "openai/gpt-4o-mini",
                                        "messages":[{"role":"user","content":prompt}],
                                        "max_tokens": 2048}).encode()
                req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=req_body,
                    headers={"Content-Type":"application/json","Authorization":f"Bearer {cred['api_key']}",
                             "HTTP-Referer": "https://neurolinked.ai", "X-Title": "NeuroLinked Brain"})
                resp = urllib.request.urlopen(req, timeout=90)
                d = json.loads(resp.read())
                out = d.get("choices",[{}])[0].get("message",{}).get("content","")
                usage = d.get("usage") or {}
                return {"step":stype,"ts":ts,"provider":"openrouter","model": cred.get("model") or "openai/gpt-4o-mini",
                        "output": out[:8000], "ok": True,
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "thinking": thinking_meta}
            # Fallback: local Ollama (only if NO cloud credential available)
            req_body = json.dumps({"model":"llama3.1:8b","prompt":prompt,"stream":False}).encode()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=req_body, headers={"Content-Type":"application/json"})
            resp = urllib.request.urlopen(req, timeout=45)
            d = json.loads(resp.read())
            return {"step":stype,"ts":ts,"provider":"ollama","model":"llama3.1:8b",
                    "output":(d.get("response") or "")[:8000],"ok":True,
                    "thinking": thinking_meta,
                    "warning": "No cloud LLM credential found; used local Ollama. Add an Anthropic or OpenAI key in Settings → Credentials for better quality."}
        except Exception as e:
            return {"step":stype,"ts":ts,"provider":provider,"error":str(e)[:300],"ok":False,"thinking": thinking_meta}

    # ---- SEND EMAIL via SMTP ----
    if stype == "send_email":
        cred = vault_get_secret(ins.get("credential",""))
        if not cred or not cred.get("host"):
            return {"step":stype,"ts":ts,"error":"SMTP credential required","ok":False}
        to = _render(ins.get("to",""), context, params)
        subject = _render(ins.get("subject","(no subject)"), context, params)
        body = _render(ins.get("body",""), context, params)
        try:
            import smtplib, ssl
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"] = cred.get("from_addr") or cred.get("username","")
            msg["To"] = to
            msg["Subject"] = subject[:200]
            msg.set_content(body)
            port = int(cred.get("port") or 587)
            with smtplib.SMTP(cred["host"], port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cred.get("username",""), cred.get("password",""))
                s.send_message(msg)
            return {"step":stype,"ts":ts,"to":to,"subject":subject,"sent":True,"ok":True}
        except Exception as e:
            return {"step":stype,"ts":ts,"to":to,"error":str(e)[:200],"ok":False}

    # ---- SLACK WEBHOOK ----
    if stype == "slack_notify":
        cred = vault_get_secret(ins.get("credential",""))
        url = (cred.get("webhook_url") if cred else "") or ""
        if not url.startswith("https://hooks.slack.com/"):
            return {"step":stype,"ts":ts,"error":"Slack webhook credential required","ok":False}
        msg = _render(ins.get("message",""), context, params)[:3000]
        try:
            req = urllib.request.Request(url, data=json.dumps({"text":msg}).encode(), headers={"Content-Type":"application/json"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=10)
            return {"step":stype,"ts":ts,"message":msg,"status":resp.status,"ok":True}
        except Exception as e:
            return {"step":stype,"ts":ts,"error":str(e)[:200],"ok":False}

    # ---- AUTHENTICATED HTTP REQUEST ----
    if stype == "api_request":
        url = _render(ins.get("url",""), context, params).strip()[:500]
        method = (ins.get("method") or "GET").upper()
        if method not in ("GET","POST","PUT","DELETE","PATCH"):
            return {"step":stype,"ts":ts,"error":f"method {method} not allowed","ok":False}
        safe, why = _is_safe_public_host(url)
        if not safe:
            return {"step":stype,"ts":ts,"error":f"blocked: {why}","url":url,"ok":False}
        headers = {"User-Agent":"NeuroLinked-Agent/1.0","Content-Type":"application/json"}
        cred = vault_get_secret(ins.get("credential",""))
        if cred:
            atype = (cred.get("auth_type") or "bearer").lower()
            if atype == "bearer" and cred.get("value"):
                headers["Authorization"] = f"Bearer {cred['value']}"
            elif atype == "header" and cred.get("value"):
                headers[cred.get("header_name") or "X-API-Key"] = cred["value"]
            elif atype == "basic" and cred.get("value"):
                import base64
                headers["Authorization"] = "Basic " + base64.b64encode(cred["value"].encode()).decode()
        data = None
        body_str = _render(ins.get("body",""), context, params).strip()
        if body_str and method in ("POST","PUT","PATCH"):
            data = body_str.encode()
        try:
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            resp = urllib.request.urlopen(req, timeout=20)
            body = resp.read(100_000).decode("utf-8","replace")
            return {"step":stype,"ts":ts,"method":method,"url":url,"status":resp.status,"body_preview":body[:800],"output":body[:2000],"ok":True}
        except Exception as e:
            return {"step":stype,"ts":ts,"method":method,"url":url,"error":str(e)[:200],"ok":False}

    # ---- BRAIN REMEMBER — save content back into the real NeuroLinked Brain ----
    if stype == "brain_remember":
        content = _render(ins.get("content", "") or ins.get("text",""), context, params)[:4000]
        tags = [t.strip() for t in (ins.get("tags","") or "").split(",") if t.strip()][:8]
        if not content:
            return {"step": stype, "ts": ts, "error": "content required", "ok": False}
        try:
            body = json.dumps({"text": content, "source": "agent", "tags": tags}).encode()
            req = urllib.request.Request("http://127.0.0.1:8020/api/claude/remember", data=body,
                headers={"Content-Type":"application/json", "x-neurolinked-token": LAUNCH_TOKEN}, method="POST")
            resp = urllib.request.urlopen(req, timeout=6)
            d = json.loads(resp.read())
            return {"step": stype, "ts": ts, "stored": True, "bytes": len(content), "tags": tags,
                    "brain_id": d.get("id"), "output": content[:300], "ok": True}
        except Exception as e:
            return {"step": stype, "ts": ts, "error": f"brain unreachable: {str(e)[:120]}", "ok": False}

    # ---- ASK JARVIS — route a question through JARVIS's WebSocket ----
    if stype == "ask_jarvis":
        prompt = _render(ins.get("prompt",""), context, params)[:MAX_STEP_INPUT]
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        try:
            import asyncio, websockets
            async def _ask():
                async with websockets.connect("ws://127.0.0.1:8340/ws", open_timeout=5) as ws:
                    await ws.send(json.dumps({"type":"text","text":prompt}))
                    # JARVIS replies with multiple frames (speak, brain, jarvis). Collect until 'end' or timeout.
                    collected = []
                    deadline = time.time() + 45
                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10)
                        except asyncio.TimeoutError:
                            break
                        try: frame = json.loads(raw)
                        except Exception: continue
                        if frame.get("type") in ("speak","text","message","jarvis","brain"):
                            text = frame.get("text") or frame.get("message") or ""
                            if text: collected.append(text)
                        if frame.get("type") in ("end","done","turn_end","jarvis_end"):
                            break
                    return "\n".join(collected).strip()
            out = asyncio.run(_ask())
            return {"step": stype, "ts": ts, "prompt": prompt[:120]+("…" if len(prompt)>120 else ""),
                    "output": out[:4000] or "(JARVIS did not respond — check that it's running on :8340)",
                    "ok": bool(out)}
        except Exception as e:
            return {"step": stype, "ts": ts, "error": f"jarvis bridge failed: {str(e)[:200]}", "ok": False}

    # ===== NEW STEP TYPES — agency content + posting + research + manager =====
    sections_root = BASE_DIR.parent / "sections"

    def _section_path(section_name: str, subdir: str = "output") -> Path:
        if not section_name or not re.fullmatch(r"[a-z0-9_\-]{1,40}", section_name or ""):
            raise ValueError("invalid section name")
        return sections_root / section_name / subdir

    def _slug(s: str, n: int = 60) -> str:
        s = (s or "").lower()
        out = "".join(c if c.isalnum() else "-" for c in s).strip("-")
        while "--" in out: out = out.replace("--", "-")
        return out[:n] or "untitled"

    def _date_prefix() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _resolve_cred(cred_id: str | None, kind: str | None = None) -> dict:
        """Return the fields dict for a credential.

        - If cred_id is given and matches a credential of the requested kind,
          return its fields.
        - If cred_id is empty/unset and a kind is given, auto-pick the first
          credential of that kind (most common case — single credential per kind).
        - Returns {} if nothing matches.

        Note: vault_get_secret() already returns the fields dict directly
        (not the full credential record).
        """
        public = []
        try: public = vault_list_public() or []
        except Exception: public = []

        # Explicit credential id wins.
        if cred_id:
            try:
                entry = next((c for c in public if c.get("id") == cred_id), None)
                if entry and kind and entry.get("kind") != kind:
                    return {}
                return vault_get_secret(cred_id) or {}
            except Exception:
                return {}

        # Auto-pick by kind.
        if not kind: return {}
        for entry in public:
            if entry.get("kind") == kind:
                try: return vault_get_secret(entry["id"]) or {}
                except Exception: continue
        return {}

    # ---- DALL·E 3 image generation ----
    if stype == "dalle_image":
        from _jarvis.integrations import dalle as _dalle
        section = (ins.get("section") or "creative").strip().lower()
        prompt = _render(ins.get("prompt",""), context, params)[:4000]
        size = (ins.get("size") or "1024x1024").strip()
        quality = (ins.get("quality") or "standard").strip()
        creds = _resolve_cred(ins.get("credential"), "llm")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no OpenAI key in chosen credential", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        r = _dalle.generate(prompt, api_key, size=size, quality=quality)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "error": r.get("error", "dalle failed"), "ok": False}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-dalle-{_slug(prompt[:40])}.png"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _dalle.download(r["url"], target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section, "prompt": prompt[:120],
                "media_path": rel, "media_kind": "image",
                "output": f"Image saved → {rel}" if ok else "Generated but download failed",
                "revised_prompt": r.get("revised_prompt", "")}

    # ---- Replicate image / video ----
    if stype in ("replicate_image", "replicate_video"):
        from _jarvis.integrations import replicate_api as _rep
        section = (ins.get("section") or ("creative")).strip().lower()
        prompt = _render(ins.get("prompt",""), context, params)[:4000]
        creds = _resolve_cred(ins.get("credential"), "replicate")
        token = creds.get("api_token", "")
        if not token:
            return {"step": stype, "ts": ts, "error": "no Replicate token in chosen credential", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt or image_url required", "ok": False}
        if stype == "replicate_image":
            alias = (ins.get("model") or "sdxl").strip().lower()
            cfg = _rep.MODELS.get(alias) or _rep.MODELS["sdxl"]
            input_d = dict(cfg.get("default_input", {})); input_d["prompt"] = prompt
            ext = "png"
        else:
            alias = (ins.get("model") or "svd").strip().lower()
            cfg = _rep.MODELS.get(alias) or _rep.MODELS["svd"]
            input_d = dict(cfg.get("default_input", {}))
            if alias == "svd":
                input_d["input_image"] = prompt
            else:
                input_d["prompt"] = prompt
            ext = "mp4"
        r = _rep.run(cfg["model"], cfg["version"], input_d, token, poll_timeout=300)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "error": r.get("error","replicate failed"), "ok": False, "prediction_id": r.get("prediction_id")}
        out = r.get("output")
        url = out[0] if isinstance(out, list) and out else (out if isinstance(out, str) else None)
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "no output URL", "raw": str(out)[:200]}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-{alias}-{_slug(prompt[:40])}.{ext}"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _rep.download(url, target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section, "model": cfg["model"],
                "media_path": rel, "media_kind": "video" if ext == "mp4" else "image",
                "output": f"{ext.upper()} saved → {rel}" if ok else "Generated but download failed",
                "prediction_id": r.get("prediction_id")}

    # ---- ElevenLabs voice-over ----
    if stype == "elevenlabs_tts":
        from _jarvis.integrations import elevenlabs as _el
        section = (ins.get("section") or "creative").strip().lower()
        text = _render(ins.get("text",""), context, params)[:5000]
        creds = _resolve_cred(ins.get("credential"), "elevenlabs")
        api_key = creds.get("api_key", "")
        voice_id = (ins.get("voice_id") or creds.get("voice_id") or _el.DEFAULT_VOICE).strip()
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no ElevenLabs key", "ok": False}
        if not text:
            return {"step": stype, "ts": ts, "error": "text required", "ok": False}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-vo-{_slug(text[:40])}.mp3"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        r = _el.synthesize(text, api_key, voice_id, target)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","tts failed")}
        rel = str(target.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section, "voice_id": voice_id,
                "media_path": rel, "media_kind": "audio", "bytes": r.get("bytes"),
                "output": f"VO saved → {rel}"}

    # ---- Tripo3D: text/image → GLB ----
    if stype == "tripo_3d":
        from _jarvis.integrations import tripo3d as _tripo
        section = (ins.get("section") or "creative").strip().lower()
        mode = (ins.get("mode") or "text").strip().lower()
        prompt = _render(ins.get("prompt", ""), context, params)[:1024]
        style = (ins.get("style") or "default").strip().lower()
        creds = _resolve_cred(ins.get("credential"), "tripo")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no Tripo key in chosen credential", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt or image url required", "ok": False}
        if mode == "image":
            r = _tripo.image_to_model(prompt, api_key)
        else:
            r = _tripo.text_to_model(prompt, api_key, style=_tripo.STYLES.get(style))
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False,
                    "error": r.get("error", "tripo failed"), "task_id": r.get("task_id")}
        url = r.get("model_url")
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "no model_url returned"}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-tripo-{_slug(prompt[:40])}.glb"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _tripo.download(url, target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section, "mode": mode,
                "media_path": rel, "media_kind": "model_3d",
                "output": f"GLB saved → {rel}" if ok else "Generated but download failed",
                "task_id": r.get("task_id")}

    # ---- Hyper3D Rodin: high-fidelity 3D ----
    if stype == "rodin_3d":
        from _jarvis.integrations import rodin_3d as _rodin
        section = (ins.get("section") or "creative").strip().lower()
        prompt = _render(ins.get("prompt", ""), context, params)[:1024]
        image_urls_raw = (ins.get("image_urls") or "").strip()
        image_urls = [u.strip() for u in image_urls_raw.split(",") if u.strip()] or None
        tier = _rodin.TIERS.get((ins.get("tier") or "regular").strip().lower(), "Regular")
        tapose = (ins.get("tapose") or "no").strip().lower() in ("yes", "true", "1", "y")
        creds = _resolve_cred(ins.get("credential"), "rodin")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no Rodin key in chosen credential", "ok": False}
        if not (prompt or image_urls):
            return {"step": stype, "ts": ts, "error": "prompt or image_urls required", "ok": False}
        r = _rodin.generate(prompt, api_key, image_urls=image_urls,
                            tier=tier, tapose=tapose)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False,
                    "error": r.get("error", "rodin failed"), "task_uuid": r.get("task_uuid")}
        url = r.get("model_url")
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "no model_url returned"}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-rodin-{_slug((prompt or 'asset')[:40])}.glb"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _rodin.download(url, target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section,
                "media_path": rel, "media_kind": "model_3d", "tier": tier, "tapose": tapose,
                "output": f"GLB saved → {rel}" if ok else "Generated but download failed",
                "task_uuid": r.get("task_uuid")}

    # ---- Scenario.gg PBR texture set ----
    if stype == "scenario_pbr":
        from _jarvis.integrations import scenario_pbr as _scen
        section = (ins.get("section") or "creative").strip().lower()
        prompt = _render(ins.get("prompt", ""), context, params)[:1024]
        try:
            res = int(ins.get("resolution") or 2048)
        except Exception:
            res = 2048
        creds = _resolve_cred(ins.get("credential"), "scenario")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no Scenario key", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        r = _scen.generate_texture(prompt, api_key, resolution=res)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False,
                    "error": r.get("error", "scenario failed"), "job_id": r.get("job_id")}
        try:
            base = f"{_date_prefix()}-pbr-{_slug(prompt[:40])}"
            out_dir = _section_path(section, "output") / base
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        saved = _scen.download_map_set(r.get("maps") or {}, out_dir, base)
        if not saved:
            return {"step": stype, "ts": ts, "ok": False,
                    "error": "no maps downloaded", "job_id": r.get("job_id")}
        rel_dir = str(out_dir.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "media_path": rel_dir, "media_kind": "pbr_set", "maps": list(saved.keys()),
                "output": f"PBR set ({len(saved)} maps) → {rel_dir}",
                "job_id": r.get("job_id")}

    # ---- Blockade Labs Skybox AI HDRI ----
    if stype == "skybox_hdri":
        from _jarvis.integrations import skybox_ai as _sky
        section = (ins.get("section") or "creative").strip().lower()
        prompt = _render(ins.get("prompt", ""), context, params)[:550]
        style_raw = (ins.get("style_id") or "").strip()
        style_id = None
        if style_raw:
            try:
                style_id = int(style_raw)
            except Exception:
                style_id = _sky.COMMON_STYLES.get(style_raw.lower())
        creds = _resolve_cred(ins.get("credential"), "blockade")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no Blockade Labs key", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        r = _sky.generate(prompt, api_key, style_id=style_id, hdri=True)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False,
                    "error": r.get("error", "skybox failed"), "skybox_id": r.get("skybox_id")}
        try:
            base = f"{_date_prefix()}-hdri-{_slug(prompt[:40])}"
            out_dir = _section_path(section, "output")
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        pano_target = out_dir / f"{base}.png"
        hdri_target = out_dir / f"{base}.hdr"
        pano_ok = _sky.download(r["image_url"], pano_target) if r.get("image_url") else False
        hdri_ok = _sky.download(r["hdri_url"], hdri_target) if r.get("hdri_url") else False
        rel_pano = str(pano_target.relative_to(sections_root.parent.parent)) if pano_ok else None
        rel_hdri = str(hdri_target.relative_to(sections_root.parent.parent)) if hdri_ok else None
        return {"step": stype, "ts": ts, "ok": pano_ok or hdri_ok, "section": section,
                "media_path": rel_pano or rel_hdri, "media_kind": "hdri",
                "panorama_path": rel_pano, "hdri_path": rel_hdri,
                "output": f"Panorama → {rel_pano}" + (f"; HDRI → {rel_hdri}" if rel_hdri else ""),
                "skybox_id": r.get("skybox_id")}

    # ---- HeyGen avatar video (talking-head AI) ----
    if stype == "heygen_avatar":
        from _jarvis.integrations import heygen as _hg
        section = (ins.get("section") or "creative").strip().lower()
        script = _render(ins.get("script",""), context, params)[:1500]
        creds = _resolve_cred(ins.get("credential"), "heygen")
        api_key = creds.get("api_key", "")
        avatar_id = (ins.get("avatar_id") or creds.get("default_avatar_id") or _hg.DEFAULT_AVATAR_ID).strip()
        voice_id  = (ins.get("voice_id")  or creds.get("default_voice_id")  or _hg.DEFAULT_VOICE_ID).strip()
        try: width  = int(ins.get("width")  or 1080)
        except Exception: width = 1080
        try: height = int(ins.get("height") or 1920)
        except Exception: height = 1920
        if not api_key:
            return {"step": stype, "ts": ts, "error": "no HeyGen api_key in credential", "ok": False}
        if not script:
            return {"step": stype, "ts": ts, "error": "script required", "ok": False}
        r = _hg.generate(api_key, script, avatar_id=avatar_id, voice_id=voice_id,
                         width=width, height=height)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","heygen failed"), "video_id": r.get("video_id")}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-heygen-{_slug(script[:40])}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _hg.download(r["video_url"], target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section, "avatar_id": avatar_id,
                "media_path": rel, "media_kind": "video",
                "duration": r.get("duration"), "video_id": r.get("video_id"),
                "output": f"Avatar video saved → {rel}" if ok else "Generated but download failed"}

    # ---- Kling / Hailuo cinematic video (premium tier of replicate_video) ----
    if stype == "kling_video":
        from _jarvis.integrations import replicate_api as _rep
        section = (ins.get("section") or "creative").strip().lower()
        prompt = _render(ins.get("prompt",""), context, params)[:4000]
        alias = (ins.get("model") or "kling").strip().lower()
        cfg = _rep.MODELS.get(alias) or _rep.MODELS["kling"]
        creds = _resolve_cred(ins.get("credential"), "replicate")
        token = creds.get("api_token", "")
        if not token:
            return {"step": stype, "ts": ts, "error": "no Replicate token in credential", "ok": False}
        if not prompt:
            return {"step": stype, "ts": ts, "error": "prompt required", "ok": False}
        input_d = dict(cfg.get("default_input", {}))
        input_d["prompt"] = prompt
        if ins.get("duration"):
            try: input_d["duration"] = int(ins["duration"])
            except Exception: pass
        if ins.get("aspect_ratio"):
            input_d["aspect_ratio"] = ins["aspect_ratio"].strip()
        if ins.get("start_image"):
            input_d["start_image"] = ins["start_image"].strip()
        # Cinematic models can take 60-180s on Replicate
        r = _rep.run(cfg["model"], cfg.get("version"), input_d, token, poll_timeout=420)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","kling failed"), "prediction_id": r.get("prediction_id")}
        out = r.get("output")
        url = out[0] if isinstance(out, list) and out else (out if isinstance(out, str) else None)
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "no output URL", "raw": str(out)[:200]}
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-{alias}-{_slug(prompt[:40])}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "error": str(e), "ok": False}
        ok = _rep.download(url, target)
        rel = str(target.relative_to(sections_root.parent.parent)) if ok else None
        return {"step": stype, "ts": ts, "ok": ok, "section": section, "model": cfg["model"],
                "media_path": rel, "media_kind": "video",
                "output": f"Cinematic mp4 saved → {rel}" if ok else "Generated but download failed",
                "prediction_id": r.get("prediction_id")}

    # ---- Video transcribe (Whisper) ----
    if stype == "video_transcribe":
        from _jarvis.integrations import whisper_api as _wh
        from _jarvis.integrations import ffmpeg_wrap as _ff
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        # Resolve path relative to NeuroLinked/
        neurolinked_root = sections_root.parent  # NeuroLinked/
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        creds = _resolve_cred(ins.get("credential"), "llm")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "ok": False, "error": "no OpenAI key in credential"}
        language = (ins.get("language") or "").strip() or None
        r = _wh.transcribe(src_path, api_key, language=language, response_format="verbose_json")
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error", "whisper failed")}
        # Persist SRT alongside output
        try:
            srt_target = _section_path(section, "output") / f"{_date_prefix()}-transcript-{_slug(src_path.stem)}.srt"
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}
        _ff.write_srt(r["segments"], srt_target)
        srt_rel = str(srt_target.relative_to(sections_root.parent.parent))
        # Compact transcript JSON for downstream LLM use
        compact_segments = [
            {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()}
            for s in r["segments"]
        ]
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "language": r.get("language"), "duration": r.get("duration"),
                "transcript_text": r["text"][:6000], "segments": compact_segments,
                "srt_path": srt_rel, "src": src_rel,
                "output": json.dumps({"segments": compact_segments, "srt_path": srt_rel}, ensure_ascii=False)[:8000]}

    # ---- Smart clip picker (LLM) ----
    if stype == "video_smart_clips":
        # The transcript_json input may be: a JSON string OR a {{step.N.output}} value
        # OR a {{step.N.segments}} reference. We try to parse as JSON first.
        raw = _render(ins.get("transcript_json", ""), context, params).strip()
        try:
            try: parsed = int(ins.get("target_count") or 5)
            except Exception: parsed = 5
            target_count = max(1, min(20, parsed))
            try: target_dur = int(ins.get("target_duration") or 30)
            except Exception: target_dur = 30
            angle = (ins.get("angle") or "").strip() or "the most engaging moments that work as standalone short-form content"
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"input parse: {e}"}
        # Try to extract segments
        segments_for_llm = ""
        try:
            obj = json.loads(raw) if raw.startswith(("{", "[")) else None
            segs = (obj or {}).get("segments") if isinstance(obj, dict) else (obj if isinstance(obj, list) else None)
            if segs:
                segments_for_llm = "\n".join(
                    f"[{float(s.get('start',0)):.1f}-{float(s.get('end',0)):.1f}] {(s.get('text') or '').strip()}"
                    for s in segs if (s.get("text") or "").strip()
                )[:12000]
            else:
                segments_for_llm = raw[:12000]
        except Exception:
            segments_for_llm = raw[:12000]
        if not segments_for_llm:
            return {"step": stype, "ts": ts, "ok": False, "error": "no transcript provided"}
        prompt = (
            f"You are a short-form video editor. Below is a transcript with [start-end] timestamps.\n"
            f"Pick the BEST {target_count} segments that work as standalone clips of roughly "
            f"{target_dur} seconds each. Prefer: {angle}.\n\n"
            f"Each clip must have a CLEAN START (not mid-sentence) and CLEAN END.\n"
            f"Respond with VALID JSON ONLY in this exact shape:\n"
            f'{{"clips":[{{"start":12.4,"end":42.1,"title":"Hook about X","why":"strong opener"}},...]}}'
            f"\n\nTRANSCRIPT:\n{segments_for_llm}"
        )
        sub = _run_custom_step({"type":"llm_ask","inputs":{"prompt": prompt, "credential": ins.get("credential",""), "skip_context": "true"}}, context, agent_id=agent_id)
        body = sub.get("output","") if sub.get("ok") else ""
        # Try to find a JSON block
        clips: list = []
        if body:
            m = re.search(r"\{[\s\S]*\"clips\"[\s\S]*\}", body)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    clips = parsed.get("clips", []) or []
                except Exception: pass
        if not clips:
            return {"step": stype, "ts": ts, "ok": False, "error": "LLM did not return parseable JSON",
                    "raw_llm_output": body[:1000]}
        # Sanitize
        clean_clips = []
        for c in clips[:target_count]:
            try:
                start = float(c.get("start", 0)); end = float(c.get("end", 0))
                if end > start:
                    clean_clips.append({
                        "start": round(start, 2), "end": round(end, 2),
                        "title": (c.get("title") or "").strip()[:120],
                        "why": (c.get("why") or "").strip()[:200],
                    })
            except Exception: continue
        return {"step": stype, "ts": ts, "ok": True, "count": len(clean_clips),
                "clips": clean_clips,
                "output": json.dumps({"clips": clean_clips}, ensure_ascii=False)}

    # ---- Extract a single clip ----
    if stype == "video_extract_clip":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed (looking in NeuroLinked/bin/ and PATH)"}
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        try: start = float(ins.get("start") or 0); end = float(ins.get("end") or 0)
        except Exception: return {"step": stype, "ts": ts, "ok": False, "error": "start/end must be numbers"}
        if end <= start:
            return {"step": stype, "ts": ts, "ok": False, "error": "end must be > start"}
        aspect = (ins.get("aspect") or "").strip() or None
        fade = (ins.get("fade") or "").strip().lower() in ("1","true","yes","on")
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-clip-{_slug(src_path.stem)}-{int(start)}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}
        r = _ff.extract(src_path, target, start, end, fade=fade, target_aspect=aspect)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error", "ffmpeg extract failed")}
        rel = str(target.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "media_path": rel, "media_kind": "video",
                "duration": r.get("duration"),
                "output": f"Extracted clip {start:.1f}s-{end:.1f}s -> {rel}"}

    # ---- Clean (audio loudnorm + optional color pop) ----
    if stype == "video_clean":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        color_pop = (ins.get("color_pop") or "").strip().lower() in ("1","true","yes","on")
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-clean-{_slug(src_path.stem)}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}
        r = _ff.clean(src_path, target, color_pop=color_pop)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error", "ffmpeg clean failed")}
        rel = str(target.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "media_path": rel, "media_kind": "video",
                "output": f"Cleaned -> {rel}"}

    # ---- Burn captions ----
    if stype == "video_caption_burn":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        srt_rel = (ins.get("srt") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        srt_path = (neurolinked_root / srt_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        if not srt_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"srt not found: {srt_rel}"}
        style = (ins.get("style") or "tiktok").strip().lower()
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-captioned-{_slug(src_path.stem)}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}
        r = _ff.burn_captions(src_path, srt_path, target, style=style)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error", "ffmpeg caption-burn failed")}
        rel = str(target.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "media_path": rel, "media_kind": "video", "style": style,
                "output": f"Captions burned -> {rel}"}

    # ---- Concat ----
    if stype == "video_concat":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        section = (ins.get("section") or "creative").strip().lower()
        srcs_raw = _render(ins.get("srcs", ""), context, params)
        srcs_rel = [s.strip() for s in srcs_raw.split(",") if s.strip()]
        if len(srcs_rel) < 2:
            return {"step": stype, "ts": ts, "ok": False, "error": "need at least 2 paths in srcs"}
        neurolinked_root = sections_root.parent
        srcs_abs = []
        for r in srcs_rel:
            p = (neurolinked_root / r).resolve()
            if not p.is_file():
                return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {r}"}
            srcs_abs.append(p)
        try:
            target = _section_path(section, "output") / f"{_date_prefix()}-concat-{_slug(srcs_abs[0].stem)}.mp4"
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}
        r = _ff.concat(srcs_abs, target)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error", "ffmpeg concat failed")}
        rel = str(target.relative_to(sections_root.parent.parent))
        return {"step": stype, "ts": ts, "ok": True, "section": section,
                "media_path": rel, "media_kind": "video",
                "output": f"Concatenated {len(srcs_abs)} clips -> {rel}"}

    # ---- Probe ----
    if stype == "video_probe":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffprobe not installed"}
        src_rel = (ins.get("src") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        info = _ff.probe(src_path)
        if not info.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": info.get("error", "probe failed")}
        return {"step": stype, "ts": ts, "ok": True, **info,
                "output": json.dumps({k: v for k, v in info.items() if k != "ok"}, indent=2)}

    # ---- WATCH video (vision keyframes + transcript fusion) ----
    if stype == "video_analyze":
        from _jarvis.integrations import ffmpeg_wrap as _ff, vision as _vi, whisper_api as _wh
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        creds = _resolve_cred(ins.get("credential"), "llm")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "ok": False, "error": "no OpenAI key in credential"}
        try: every = float(ins.get("every_seconds") or 5)
        except Exception: every = 5.0
        try: max_frames = int(ins.get("max_frames") or 16)
        except Exception: max_frames = 16

        # 1. Probe
        meta = _ff.probe(src_path)
        # 2. Extract keyframes
        kf_dir = neurolinked_root / ".tmp" / f"frames-{src_path.stem}-{int(time.time())}"
        kf = _ff.extract_keyframes(src_path, kf_dir, every_seconds=every, max_frames=max_frames)
        if not kf.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"keyframes: {kf.get('error')}"}
        # 3. Vision describe (low detail = cheap)
        vprompt = (
            "You are watching a video. I'm sending you keyframes sampled at fixed intervals. "
            "For EACH frame, write a 1-2 sentence description: main subject, action, setting, "
            "any text visible. Then at the end provide a short paragraph summary of the entire video's "
            "story arc. Format your response as:\n"
            "FRAME 1: <description>\nFRAME 2: <description>\n...\n\nSUMMARY: <one paragraph>"
        )
        vis = _vi.describe_frames(api_key, kf["frame_paths"], vprompt, model="gpt-4o-mini", detail="low")
        # Cleanup keyframes
        try:
            for p in kf["frame_paths"]: Path(p).unlink(missing_ok=True)
            kf_dir.rmdir()
        except Exception: pass
        if not vis.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"vision: {vis.get('error')}"}
        # 4. Whisper transcript
        wh = _wh.transcribe(src_path, api_key, response_format="verbose_json")
        if not wh.get("ok"):
            transcript_text = ""
            segments = []
            wh_err = wh.get("error", "transcription failed")
        else:
            transcript_text = wh.get("text", "")
            segments = [
                {"start": float(s.get("start",0)), "end": float(s.get("end",0)),
                 "text": (s.get("text") or "").strip()}
                for s in (wh.get("segments") or [])
            ]
            wh_err = None
            # Persist SRT
            try:
                srt_target = _section_path(section, "output") / f"{_date_prefix()}-transcript-{_slug(src_path.stem)}.srt"
                _ff.write_srt(wh.get("segments") or [], srt_target)
                srt_rel = str(srt_target.relative_to(sections_root.parent.parent))
            except Exception:
                srt_rel = None
        # 5. Compose unified analysis output
        analysis = (
            f"# Visual analysis ({kf['count']} frames @ every {every}s)\n\n"
            f"{vis['text']}\n\n"
            f"# Audio transcript ({len(segments)} segments)\n\n"
            + ("\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments[:200]))
        )
        # Save the analysis as a .md to creative/output for review
        try:
            note_path = _section_path(section, "output") / f"{_date_prefix()}-analysis-{_slug(src_path.stem)}.md"
            note_path.write_text(analysis, encoding="utf-8")
            note_rel = str(note_path.relative_to(sections_root.parent.parent))
        except Exception:
            note_rel = None
        return {
            "step": stype, "ts": ts, "ok": True, "section": section,
            "src": src_rel, "duration": meta.get("duration"),
            "frame_count": kf["count"], "vision": vis["text"][:8000],
            "transcript_text": transcript_text[:6000],
            "segments": segments,
            "srt_path": srt_rel if 'srt_rel' in locals() else None,
            "analysis_path": note_rel,
            "transcription_warning": wh_err,
            "output": analysis[:8000],
        }

    # ---- One-shot batch: extract + clean + caption N clips ----
    if stype == "video_clip_batch":
        from _jarvis.integrations import ffmpeg_wrap as _ff, whisper_api as _wh
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        section = (ins.get("section") or "creative").strip().lower()
        src_rel = (ins.get("src") or "").strip()
        neurolinked_root = sections_root.parent
        src_path = (neurolinked_root / src_rel).resolve()
        if not src_path.is_file():
            return {"step": stype, "ts": ts, "ok": False, "error": f"src not found: {src_rel}"}
        # Parse clips JSON
        raw = _render(ins.get("clips_json", ""), context, params).strip()
        clips: list = []
        try:
            obj = json.loads(raw) if raw else {}
            clips = obj.get("clips") if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
        except Exception:
            # Fallback: try to find a JSON block inside
            m = re.search(r"\{[\s\S]*\"clips\"[\s\S]*?\}", raw)
            if m:
                try: clips = json.loads(m.group(0)).get("clips") or []
                except Exception: pass
        if not clips:
            return {"step": stype, "ts": ts, "ok": False, "error": "no clips in clips_json"}
        aspect = (ins.get("aspect") or "9:16").strip() or None
        style = (ins.get("caption_style") or "tiktok").strip().lower()
        creds = _resolve_cred(ins.get("credential"), "llm")
        api_key = creds.get("api_key", "")
        if not api_key:
            return {"step": stype, "ts": ts, "ok": False, "error": "OpenAI credential required for per-clip transcription"}

        try:
            out_dir = _section_path(section, "output")
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}

        results: list = []
        for i, c in enumerate(clips, 1):
            try:
                start = float(c.get("start", 0))
                end = float(c.get("end", 0))
            except Exception:
                results.append({"clip": i, "ok": False, "error": "bad start/end"}); continue
            if end <= start:
                results.append({"clip": i, "ok": False, "error": "end <= start"}); continue
            slug_title = _slug((c.get("title") or f"clip-{i}")[:40])
            # 1. Extract
            raw_path = out_dir / f"{_date_prefix()}-clip{i:02d}-raw-{slug_title}.mp4"
            ex = _ff.extract(src_path, raw_path, start, end, fade=True, target_aspect=aspect)
            if not ex.get("ok"):
                results.append({"clip": i, "ok": False, "step": "extract", "error": ex.get("error")}); continue
            # 2. Clean
            clean_path = out_dir / f"{_date_prefix()}-clip{i:02d}-clean-{slug_title}.mp4"
            cl = _ff.clean(raw_path, clean_path, color_pop=True)
            if not cl.get("ok"):
                results.append({"clip": i, "ok": False, "step": "clean", "error": cl.get("error"), "raw_path": str(raw_path)})
                continue
            # 3. Transcribe (per-clip, accurate timestamps for captions)
            wh = _wh.transcribe(clean_path, api_key, response_format="verbose_json")
            if not wh.get("ok"):
                results.append({"clip": i, "ok": False, "step": "transcribe", "error": wh.get("error"), "clean_path": str(clean_path)})
                continue
            srt_path = out_dir / f"{_date_prefix()}-clip{i:02d}-{slug_title}.srt"
            _ff.write_srt(wh.get("segments") or [], srt_path)
            # 4. Caption-burn
            final_path = out_dir / f"{_date_prefix()}-clip{i:02d}-final-{slug_title}.mp4"
            cap = _ff.burn_captions(clean_path, srt_path, final_path, style=style)
            if not cap.get("ok"):
                results.append({"clip": i, "ok": False, "step": "caption", "error": cap.get("error")})
                continue
            # 5. Cleanup intermediate raw + clean (keep srt + final)
            try: raw_path.unlink()
            except Exception: pass
            try: clean_path.unlink()
            except Exception: pass
            results.append({
                "clip": i, "ok": True,
                "title": c.get("title", ""),
                "start": start, "end": end, "duration": end - start,
                "media_path": str(final_path.relative_to(sections_root.parent.parent)),
                "srt_path": str(srt_path.relative_to(sections_root.parent.parent)),
                "transcript": (wh.get("text") or "").strip(),
                "media_kind": "video",
            })

        ok_count = sum(1 for r in results if r.get("ok"))
        return {
            "step": stype, "ts": ts, "ok": ok_count > 0, "section": section,
            "total_clips": len(clips), "succeeded": ok_count,
            "failed": len(clips) - ok_count,
            "results": results,
            "final_paths": ",".join(r["media_path"] for r in results if r.get("ok")),
            "output": json.dumps({"results": results}, ensure_ascii=False, indent=2)[:6000],
        }

    # ---- QA pass on produced clips ----
    if stype == "video_qa":
        from _jarvis.integrations import ffmpeg_wrap as _ff
        if not _ff.have_ffmpeg():
            return {"step": stype, "ts": ts, "ok": False, "error": "ffmpeg not installed"}
        neurolinked_root = sections_root.parent
        # Parse clips plan
        raw = _render(ins.get("clips_json", ""), context, params).strip()
        plan: list = []
        try:
            obj = json.loads(raw) if raw else {}
            plan = obj.get("clips") if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
        except Exception: pass
        # Parse final paths
        finals_raw = _render(ins.get("final_paths", ""), context, params)
        final_paths = [p.strip() for p in finals_raw.split(",") if p.strip()]
        if not final_paths:
            return {"step": stype, "ts": ts, "ok": False, "error": "no final_paths to QA"}

        report: list = []
        all_pass = True
        for i, rel in enumerate(final_paths, 1):
            p = (neurolinked_root / rel).resolve()
            if not p.is_file():
                report.append({"clip": i, "path": rel, "pass": False, "issue": "file missing"}); all_pass = False; continue
            size = p.stat().st_size
            if size < 50_000:
                report.append({"clip": i, "path": rel, "pass": False, "size": size, "issue": "file too small (<50KB), likely empty"}); all_pass = False; continue
            info = _ff.probe(p)
            if not info.get("ok"):
                report.append({"clip": i, "path": rel, "pass": False, "issue": f"probe failed: {info.get('error')}"}); all_pass = False; continue
            actual_dur = float(info.get("duration") or 0)
            entry = {"clip": i, "path": rel, "pass": True, "size": size,
                     "actual_duration": round(actual_dur, 2),
                     "video_codec": info.get("video_codec"),
                     "audio_codec": info.get("audio_codec"),
                     "width": info.get("width"), "height": info.get("height")}
            # Cross-check duration vs plan
            if i - 1 < len(plan):
                planned = plan[i - 1]
                try:
                    expected = float(planned.get("end", 0)) - float(planned.get("start", 0))
                    drift = abs(actual_dur - expected)
                    entry["expected_duration"] = round(expected, 2)
                    entry["drift_seconds"] = round(drift, 2)
                    if drift > 2.0:
                        entry["pass"] = False
                        entry["issue"] = f"duration drift {drift:.1f}s vs planned {expected:.1f}s"
                        all_pass = False
                except Exception: pass
            report.append(entry)

        def _qa_line(r):
            ok_text = "PASS" if r["pass"] else "FAIL"
            detail = r.get("issue") or f"{r.get('actual_duration')}s, {r.get('width')}x{r.get('height')}"
            return f"  Clip {r['clip']}: {ok_text} — {detail}"
        summary = (
            f"QA: {sum(1 for r in report if r['pass'])}/{len(report)} passed\n\n"
            + "\n".join(_qa_line(r) for r in report)
        )
        return {"step": stype, "ts": ts, "ok": all_pass,
                "qa_pass_rate": f"{sum(1 for r in report if r['pass'])}/{len(report)}",
                "report": report, "output": summary}

    # ---- Buffer batch post (multiple captions+videos in one shot) ----
    if stype == "buffer_post_batch":
        from _jarvis.integrations import buffer as _bf
        creds = _resolve_cred(ins.get("credential"), "buffer")
        token = creds.get("access_token", "")
        if not token:
            return {"step": stype, "ts": ts, "ok": False, "error": "no Buffer token"}
        profile_ids = [p.strip() for p in (ins.get("profile_ids") or "").split(",") if p.strip()]
        if not profile_ids:
            return {"step": stype, "ts": ts, "ok": False, "error": "profile_ids required"}
        raw = _render(ins.get("posts_json", ""), context, params).strip()
        try:
            posts = json.loads(raw)
            if isinstance(posts, dict) and "posts" in posts: posts = posts["posts"]
            if not isinstance(posts, list): raise ValueError("posts_json must be a list")
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"posts_json parse: {e}"}
        results = []
        for i, post in enumerate(posts, 1):
            text = (post.get("caption") or post.get("text") or "").strip()
            media_path = (post.get("media_path") or post.get("media_url") or "").strip()
            sched = (post.get("scheduled_at") or "").strip() or None
            if not text:
                results.append({"i": i, "ok": False, "error": "missing caption"}); continue
            r = _bf.create_post(token, profile_ids, text, media_url=media_path or None, scheduled_at=sched)
            results.append({"i": i, "ok": r.get("ok", False), "queued": r.get("buffer_count"), "error": r.get("error")})
        ok_count = sum(1 for r in results if r["ok"])
        return {"step": stype, "ts": ts, "ok": ok_count > 0,
                "queued": ok_count, "total": len(posts),
                "output": f"Queued {ok_count}/{len(posts)} posts on {len(profile_ids)} profile(s)",
                "results": results}

    # ---- Buffer post (TikTok / IG / etc.) ----
    if stype == "buffer_post":
        from _jarvis.integrations import buffer as _bf
        text = _render(ins.get("text",""), context, params)[:2200]
        media_url = _render(ins.get("media_url",""), context, params).strip() or None
        scheduled_at = (ins.get("scheduled_at") or "").strip() or None
        profile_ids = [p.strip() for p in (ins.get("profile_ids") or "").split(",") if p.strip()]
        creds = _resolve_cred(ins.get("credential"), "buffer")
        token = creds.get("access_token", "")
        if not token:
            return {"step": stype, "ts": ts, "error": "no Buffer token", "ok": False}
        if not text:
            return {"step": stype, "ts": ts, "error": "text required", "ok": False}
        if not profile_ids:
            return {"step": stype, "ts": ts, "error": "profile_ids required", "ok": False}
        r = _bf.create_post(token, profile_ids, text, media_url=media_url, scheduled_at=scheduled_at)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","buffer failed")}
        return {"step": stype, "ts": ts, "ok": True, "queued": r.get("buffer_count"),
                "output": f"Queued on {len(profile_ids)} profile(s){' @ '+scheduled_at if scheduled_at else ''}"}

    # ---- Slack bot post / DM ----
    if stype in ("slack_post", "slack_dm"):
        from _jarvis.integrations import slack_bot as _sb
        creds = _resolve_cred(ins.get("credential"), "slack_bot")
        token = creds.get("bot_token", "")
        text = _render(ins.get("text",""), context, params)[:3000]
        if not token:
            return {"step": stype, "ts": ts, "error": "no Slack bot token", "ok": False}
        if not text:
            return {"step": stype, "ts": ts, "error": "text required", "ok": False}
        if stype == "slack_post":
            channel = (ins.get("channel") or "").strip()
            if not channel:
                return {"step": stype, "ts": ts, "error": "channel required", "ok": False}
            r = _sb.post_message(token, channel, text)
        else:
            user_id = (ins.get("user_id") or "").strip()
            if not user_id:
                return {"step": stype, "ts": ts, "error": "user_id required", "ok": False}
            r = _sb.dm_user(token, user_id, text)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","slack failed")}
        return {"step": stype, "ts": ts, "ok": True, "ts_slack": r.get("ts"), "output": f"Posted to {ins.get('channel') or ins.get('user_id')}"}

    # ---- Discord bot post / DM / read ----
    if stype in ("discord_post", "discord_dm", "discord_read"):
        try:
            from _jarvis.integrations import discord_bot as _db
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"discord module load: {e}"}
        creds = _resolve_cred(ins.get("credential"), "discord_bot")
        token = creds.get("bot_token", "")
        if not token:
            return {"step": stype, "ts": ts, "ok": False, "error": "no Discord bot token"}

        if stype == "discord_post":
            channel = (_render(ins.get("channel_id",""), context, params) or creds.get("default_channel_id","")).strip()
            text    = _render(ins.get("text",""), context, params)[:2000]
            if not channel:
                return {"step": stype, "ts": ts, "ok": False, "error": "channel_id required (or set default in credential)"}
            if not text:
                return {"step": stype, "ts": ts, "ok": False, "error": "text required"}
            r = _db.post_message(token, channel, text)
            if not r.get("ok"):
                return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","discord post failed")}
            return {"step": stype, "ts": ts, "ok": True, "message_id": r.get("message_id"),
                    "output": f"Posted to Discord channel {channel}"}

        if stype == "discord_dm":
            user_id = (_render(ins.get("user_id",""), context, params)).strip()
            text    = _render(ins.get("text",""), context, params)[:2000]
            if not user_id:
                return {"step": stype, "ts": ts, "ok": False, "error": "user_id required"}
            if not text:
                return {"step": stype, "ts": ts, "ok": False, "error": "text required"}
            r = _db.dm_user(token, user_id, text)
            if not r.get("ok"):
                return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","discord DM failed")}
            return {"step": stype, "ts": ts, "ok": True, "message_id": r.get("message_id"),
                    "output": f"DM'd Discord user {user_id}"}

        # discord_read
        channel = (_render(ins.get("channel_id",""), context, params) or creds.get("default_channel_id","")).strip()
        if not channel:
            return {"step": stype, "ts": ts, "ok": False, "error": "channel_id required (or set default in credential)"}
        try:    limit = int(_render(str(ins.get("limit","20")), context, params))
        except: limit = 20
        r = _db.read_recent(token, channel, limit=limit)
        if not r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": r.get("error","discord read failed")}
        msgs = r.get("messages") or []
        # Format messages as readable text for downstream LLM steps.
        lines = [f"[{m['timestamp'][:19]}] @{m['author']}: {m['content']}" for m in msgs if m.get("content")]
        return {"step": stype, "ts": ts, "ok": True, "count": len(msgs),
                "output": "\n".join(lines) or "(no messages with text content)"}

    # ---- Discord: full-state audit ----
    if stype == "discord_audit":
        try:
            from _jarvis.integrations import discord_bot as _db
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"discord module load: {e}"}
        creds = _resolve_cred(ins.get("credential"), "discord_bot")
        token = creds.get("bot_token", "")
        guild_id = (_render(ins.get("guild_id",""), context, params) or creds.get("default_guild_id","")).strip()
        if not token:    return {"step": stype, "ts": ts, "ok": False, "error": "no Discord bot token"}
        if not guild_id: return {"step": stype, "ts": ts, "ok": False, "error": "guild_id required (or set default in credential)"}

        # Pull everything we audit on.
        guild_r = _db.get_guild(token, guild_id)
        if not guild_r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"get_guild: {guild_r.get('error')}"}
        roles_r = _db.list_roles(token, guild_id)
        if not roles_r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"list_roles: {roles_r.get('error')}"}
        chans_r = _db.list_channels_full(token, guild_id)
        if not chans_r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"list_channels: {chans_r.get('error')}"}

        guild = guild_r["guild"]
        roles = roles_r["roles"]
        chans = chans_r["channels"]
        role_by_id = {r["id"]: r for r in roles}
        chan_by_id = {c["id"]: c for c in chans}

        # Bot identity + actual member entry (for accurate role hierarchy).
        me = _db.test_token(token); bot_user = (me.get("user") or {})
        bot_id = bot_user.get("id") or ""
        bot_highest_pos = -1
        bot_role_ids: list[str] = []
        if bot_id:
            bm = _db.get_bot_member(token, guild_id, bot_id)
            if bm.get("ok"):
                bot_role_ids = bm["member"].get("roles", [])
                for rid in bot_role_ids:
                    r = role_by_id.get(rid)
                    if r and r["position"] > bot_highest_pos:
                        bot_highest_pos = r["position"]

        # Optional: AutoMod rules + welcome screen. These can fail on permission
        # if the bot's missing the right scope — record but don't block the audit.
        automod_r = _db.list_automod_rules(token, guild_id)
        automod_rules = automod_r.get("rules", []) if automod_r.get("ok") else []
        automod_err = None if automod_r.get("ok") else automod_r.get("error")
        ws_r = _db.get_welcome_screen(token, guild_id)
        welcome_screen = ws_r.get("welcome_screen") if ws_r.get("ok") else None
        ws_err = None if ws_r.get("ok") else ws_r.get("error")

        # Categorize channels.
        categories = sorted([c for c in chans if c["type"] == 4], key=lambda x: x.get("position", 0))
        non_cat    = [c for c in chans if c["type"] != 4]
        cat_children: dict = {c["id"]: [] for c in categories}
        cat_children[None] = []
        for c in non_cat:
            cat_children.setdefault(c.get("parent_id"), []).append(c)

        # ---- Audit findings (the value layer) ----
        findings: list[tuple[str, str]] = []

        # Bot role hierarchy — flag roles the bot CAN'T modify because they're at or above its highest role.
        if bot_highest_pos < 0:
            findings.append(("err", "Bot has no roles assigned in this server. It cannot do anything. Invite it with a role above the roles you want it to manage."))
        else:
            unmanageable = [r["name"] for r in roles
                            if r["position"] >= bot_highest_pos
                            and r["name"] != "@everyone"
                            and r["id"] not in bot_role_ids]
            if unmanageable:
                findings.append(("warn", f"Bot cannot modify these roles (at or above its position {bot_highest_pos}): {', '.join(unmanageable)}. Move the bot's role above them to manage."))

        # Orphan channels — no category. Almost always wrong in big servers.
        orphans = cat_children.get(None, [])
        if orphans:
            findings.append(("warn", f"{len(orphans)} channel(s) have no category: " + ", ".join(f"#{c['name']}" for c in orphans[:10]) + (" …" if len(orphans) > 10 else "")))

        # Per-channel: detect overwrites that DIFFER from the parent category (desynced).
        for c in non_cat:
            cat = chan_by_id.get(c.get("parent_id"))
            if not cat: continue
            cat_keys = {(o["id"], o["allow"], o["deny"]) for o in cat["overwrites"]}
            ch_keys  = {(o["id"], o["allow"], o["deny"]) for o in c["overwrites"]}
            if c["overwrites"] and ch_keys != cat_keys:
                extra = ch_keys - cat_keys
                if extra:
                    findings.append(("info", f"#{c['name']} has overwrites that differ from its category — likely manually edited and now drifting."))

        # Critical channel-pointer settings on the server (rules, system, public updates).
        for label, key in (("rules_channel", "rules_channel_id"),
                           ("system_channel", "system_channel_id"),
                           ("public_updates_channel", "public_updates_channel_id"),
                           ("afk_channel", "afk_channel_id")):
            cid = guild.get(key)
            if cid and cid not in chan_by_id:
                findings.append(("warn", f"Server points {label} at channel {cid}, which doesn't exist."))

        # Verification level too low (0 = none, 4 = highest). For 2k+ communities, ≥2 is the norm.
        vl = guild.get("verification_level")
        if vl is not None and vl < 2:
            findings.append(("warn", f"Verification level is {vl} (very low). For a server this size, set ≥2 (Medium) to require verified email + 5-min wait."))

        # Explicit content filter — should be ≥1 for public-ish servers.
        ecf = guild.get("explicit_content_filter")
        if ecf is not None and ecf < 1:
            findings.append(("info", f"Explicit content filter is disabled. Consider enabling for members without roles."))

        # AutoMod — flag if zero rules. A server this size should have at least keyword + spam.
        if automod_err:
            findings.append(("info", f"Could not read AutoMod rules: {automod_err}. The bot may be missing the 'Manage Guild' or 'Auto Moderation' scope."))
        elif not automod_rules:
            findings.append(("warn", "No AutoMod rules configured. A community this size should have at least: profanity preset, mention spam (max 5), and harmful-link block."))

        # Welcome screen — if community feature is enabled but welcome screen is empty.
        if "COMMUNITY" in (guild.get("features") or []):
            if ws_err:
                findings.append(("info", f"Could not read welcome screen: {ws_err}."))
            elif welcome_screen and not welcome_screen.get("welcome_channels"):
                findings.append(("info", "Community server has no welcome screen channels configured. Add 3-5 channel highlights so new joiners see where to start."))

        # ---- Build the markdown report ----
        out = []
        out.append(f"# Discord Audit — {guild['name']}")
        out.append("")
        out.append(f"_{datetime.now().isoformat(timespec='seconds')}_")
        out.append("")
        out.append("## Server")
        out.append("")
        out.append(f"- **Server ID:** `{guild_id}`")
        out.append(f"- **Members (approx):** {guild.get('approximate_member_count','?')} · **online:** {guild.get('approximate_presence_count','?')}")
        out.append(f"- **Owner:** `{guild.get('owner_id','?')}`")
        out.append(f"- **Verification level:** {guild.get('verification_level','?')}  (0=none, 1=low, 2=medium, 3=high, 4=highest)")
        out.append(f"- **Explicit content filter:** {guild.get('explicit_content_filter','?')}  (0=off, 1=members w/o roles, 2=all)")
        out.append(f"- **Default notifications:** {guild.get('default_message_notifications','?')}  (0=all messages, 1=only @mentions)")
        out.append(f"- **Boost tier:** {guild.get('premium_tier','?')} · **boosts:** {guild.get('premium_subscription_count','?')}")
        out.append(f"- **Features:** {', '.join(guild.get('features') or []) or '—'}")
        out.append(f"- **Rules channel:** {chan_by_id.get(guild.get('rules_channel_id') or '', {}).get('name','—')}")
        out.append(f"- **System channel:** {chan_by_id.get(guild.get('system_channel_id') or '', {}).get('name','—')}")
        out.append(f"- **Public updates channel:** {chan_by_id.get(guild.get('public_updates_channel_id') or '', {}).get('name','—')}")
        out.append(f"- **AFK channel:** {chan_by_id.get(guild.get('afk_channel_id') or '', {}).get('name','—')} · timeout: {guild.get('afk_timeout','?')}s")
        out.append("")
        if bot_user:
            out.append(f"- **Bot identity:** {bot_user.get('username','?')} (id `{bot_id}`) · **highest role position:** {bot_highest_pos}")
        out.append("")

        out.append(f"## Roles ({len(roles)} total — top → bottom)")
        out.append("")
        out.append("| Pos | Name | Color | Hoist | Admin | Manage Roles | Managed |")
        out.append("|-----|------|-------|-------|-------|--------------|---------|")
        for r in roles:
            pf = r["permission_flags"]
            color_hex = f"#{int(r.get('color',0)):06x}" if r.get('color') else "—"
            out.append(f"| {r['position']} | {r['name']} | `{color_hex}` | {'✅' if r['hoist'] else ''} | {'✅' if pf.get('administrator') else ''} | {'✅' if pf.get('manage_roles') else ''} | {'✅' if r['managed'] else ''} |")
        out.append("")

        out.append(f"## Channels ({len(non_cat)} channels in {len(categories)} categories)")
        out.append("")
        for cat in categories:
            kids = sorted(cat_children.get(cat["id"], []), key=lambda x: x.get("position", 0))
            out.append(f"### 📁 {cat['name']}  *(category · {len(kids)} channels)*")
            if cat["overwrites"]:
                out.append("")
                out.append("**Category overwrites:**")
                for o in cat["overwrites"]:
                    target = "@everyone" if o["id"] == guild_id else (role_by_id.get(o["id"], {}).get("name") or (f"member:{o['id']}" if o['type']=='member' else o['id']))
                    allows = ", ".join(k for k in o["allow_flags"]) or "—"
                    denies = ", ".join(k for k in o["deny_flags"]) or "—"
                    out.append(f"- **{target}** — allow: `{allows}` · deny: `{denies}`")
            else:
                out.append("")
                out.append("_(no category-level overwrites)_")
            out.append("")
            if kids:
                out.append("**Channels:**")
                for c in kids:
                    detail = []
                    if c.get("topic"): detail.append(f"topic: \"{c['topic'][:60]}…\"" if len(c['topic'])>60 else f"topic: \"{c['topic']}\"")
                    if c.get("rate_limit_per_user"): detail.append(f"slowmode {c['rate_limit_per_user']}s")
                    if c.get("nsfw"): detail.append("NSFW")
                    if c.get("bitrate"): detail.append(f"bitrate {c['bitrate']}")
                    if c.get("user_limit"): detail.append(f"limit {c['user_limit']}")
                    detail.append(f"{len(c['overwrites'])} overwrites")
                    out.append(f"- `#{c['name']}` ({c['type_name']}) — " + " · ".join(detail))
                out.append("")

        if orphans:
            out.append("### 🟡 Uncategorized channels")
            out.append("")
            for c in sorted(orphans, key=lambda x: x.get("position", 0)):
                out.append(f"- `#{c['name']}` ({c['type_name']}) — {len(c['overwrites'])} overwrites")
            out.append("")

        out.append(f"## AutoMod ({len(automod_rules)} rules)")
        out.append("")
        if automod_err:
            out.append(f"_Could not read AutoMod rules — {automod_err}_")
        elif not automod_rules:
            out.append("_No AutoMod rules configured._")
        else:
            for rl in automod_rules:
                tt = rl.get("trigger_type")
                tname = {1:"keyword", 3:"spam", 4:"keyword-preset", 5:"mention-spam"}.get(tt, f"type {tt}")
                acts = [{"1":"block","2":"alert","3":"timeout"}.get(str(a.get("type")), "?") for a in rl.get("actions",[])]
                out.append(f"- `{rl.get('name','(unnamed)')}` ({tname}) — actions: {', '.join(acts) or '—'} · {'enabled' if rl.get('enabled') else '⚠ DISABLED'}")
        out.append("")

        out.append("## Welcome Screen")
        out.append("")
        if not welcome_screen:
            out.append("_(community feature off or welcome screen not configured)_")
        else:
            out.append(f"**Description:** {welcome_screen.get('description') or '—'}")
            wcs = welcome_screen.get("welcome_channels") or []
            if wcs:
                out.append("**Highlighted channels:**")
                for w in wcs:
                    cname = chan_by_id.get(w.get("channel_id"), {}).get("name", w.get("channel_id"))
                    out.append(f"- #{cname} — {w.get('description','')}")
            else:
                out.append("_No highlighted channels._")
        out.append("")

        if findings:
            out.append("## Findings")
            out.append("")
            for level, msg in findings:
                icon = {"err":"🔴","warn":"🟡","info":"⚪"}.get(level, "•")
                out.append(f"- {icon} {msg}")
            out.append("")
        else:
            out.append("## Findings")
            out.append("")
            out.append("✅ No issues detected.")
            out.append("")

        snapshot = {
            "guild": guild, "roles": roles, "channels": chans,
            "automod_rules": automod_rules, "welcome_screen": welcome_screen,
            "bot": {"id": bot_id, "username": bot_user.get("username"),
                    "highest_pos": bot_highest_pos, "role_ids": bot_role_ids},
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }
        return {"step": stype, "ts": ts, "ok": True,
                "guild_name": guild["name"],
                "member_count": guild.get("approximate_member_count"),
                "role_count": len(roles),
                "channel_count": len(non_cat),
                "category_count": len(categories),
                "automod_count": len(automod_rules),
                "findings_count": len(findings),
                "snapshot": snapshot,
                "output": "\n".join(out)}

    # ---- Discord: reconcile live state against desired YAML manifest ----
    if stype == "discord_reconcile":
        try:
            from _jarvis.integrations import discord_bot as _db
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"discord module load: {e}"}
        try:
            import yaml as _yaml  # PyYAML, installed by START-ZERO.bat
        except Exception:
            return {"step": stype, "ts": ts, "ok": False, "error": "PyYAML not installed"}

        creds = _resolve_cred(ins.get("credential"), "discord_bot")
        token = creds.get("bot_token", "")
        guild_id = (_render(ins.get("guild_id",""), context, params) or creds.get("default_guild_id","")).strip()
        mode = (_render(ins.get("mode","plan"), context, params) or "plan").strip().lower()
        if mode not in ("plan", "apply"):
            return {"step": stype, "ts": ts, "ok": False, "error": "mode must be 'plan' or 'apply'"}
        rel = (_render(ins.get("desired_state_path","community/wiki/discord-desired-state.yaml"), context, params) or "").strip()
        sections_root = (Path(__file__).resolve().parent.parent / "sections").resolve()
        manifest_path = (sections_root / rel).resolve()
        if not str(manifest_path).startswith(str(sections_root)):
            return {"step": stype, "ts": ts, "ok": False, "error": "desired_state_path must be under sections/"}
        if not manifest_path.exists():
            return {"step": stype, "ts": ts, "ok": False, "error": f"desired-state file not found: {manifest_path}"}
        if not token or not guild_id:
            return {"step": stype, "ts": ts, "ok": False, "error": "bot_token and guild_id required"}

        try:
            desired = _yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"yaml parse: {e}"}

        # ---- Pull EVERYTHING live ----
        guild_r   = _db.get_guild(token, guild_id)
        roles_r   = _db.list_roles(token, guild_id)
        chans_r   = _db.list_channels_full(token, guild_id)
        automod_r = _db.list_automod_rules(token, guild_id)
        ws_r      = _db.get_welcome_screen(token, guild_id)
        for label, r in [("guild", guild_r), ("roles", roles_r), ("channels", chans_r)]:
            if not r.get("ok"):
                return {"step": stype, "ts": ts, "ok": False, "error": f"{label}: {r.get('error')}"}

        guild_live = guild_r["guild"]
        roles_live = roles_r["roles"]
        chans_live = chans_r["channels"]
        automod_live = automod_r.get("rules", []) if automod_r.get("ok") else []
        ws_live = ws_r.get("welcome_screen") if ws_r.get("ok") else None

        role_by_name = {r["name"]: r for r in roles_live}
        chan_by_id   = {c["id"]: c for c in chans_live}
        cats_live    = {c["name"]: c for c in chans_live if c["type"] == 4}
        chans_by_parent: dict = {}
        for c in chans_live:
            if c["type"] != 4:
                chans_by_parent.setdefault(c.get("parent_id"), []).append(c)

        # Bot hierarchy (for pre-flight skip on roles above the bot).
        me = _db.test_token(token); bot_user = (me.get("user") or {})
        bot_id = bot_user.get("id") or ""
        bot_highest_pos = -1
        if bot_id:
            bm = _db.get_bot_member(token, guild_id, bot_id)
            if bm.get("ok"):
                for rid in bm["member"].get("roles", []):
                    r = next((x for x in roles_live if x["id"] == rid), None)
                    if r and r["position"] > bot_highest_pos:
                        bot_highest_pos = r["position"]

        def _parse_overwrite_perms(perms: dict) -> tuple[dict, dict]:
            """YAML overwrite value → (allow_flags, deny_flags). Accepts
            'allow' / true / 1   → allow
            'deny'  / false / 0  → deny
            anything else (null) → neutral.
            """
            allow, deny = {}, {}
            for k, v in (perms or {}).items():
                if v in ("allow", True, 1):  allow[k] = True
                elif v in ("deny", False, 0): deny[k] = True
            return allow, deny

        actions: list[dict] = []

        # ---- 1) Server-level settings diff ----
        srv = desired.get("server") or {}
        # Map YAML keys → guild keys
        srv_map = {
            "name": "name",
            "description": "description",
            "verification_level": "verification_level",
            "default_message_notifications": "default_message_notifications",
            "explicit_content_filter": "explicit_content_filter",
            "afk_timeout": "afk_timeout",
            "preferred_locale": "preferred_locale",
        }
        srv_changes = {}
        for ykey, gkey in srv_map.items():
            if ykey in srv and srv[ykey] != guild_live.get(gkey):
                srv_changes[gkey] = srv[ykey]
        # Channel-pointer settings (resolve by channel name)
        for ykey, gkey in [
            ("rules_channel", "rules_channel_id"),
            ("system_channel", "system_channel_id"),
            ("public_updates_channel", "public_updates_channel_id"),
            ("afk_channel", "afk_channel_id"),
        ]:
            want = srv.get(ykey)
            if want is not None:
                target = next((c for c in chans_live if c["name"].lower() == str(want).lower()), None)
                if target and target["id"] != guild_live.get(gkey):
                    srv_changes[gkey] = target["id"]
        if srv_changes:
            actions.append({"action": "modify_guild",
                            "desc": f"Update server settings: {', '.join(srv_changes.keys())}",
                            "payload": srv_changes})

        # ---- 2) Roles diff (create / modify) ----
        for dr in (desired.get("roles") or []):
            name = dr.get("name")
            if not name: continue
            live = role_by_name.get(name)
            if live is None:
                actions.append({"action": "create_role",
                                "desc": f"Create role '{name}'",
                                "payload": dr})
                continue
            # If role exists, only consider modifying if bot can manage it.
            if bot_highest_pos >= 0 and live["position"] >= bot_highest_pos:
                actions.append({"action": "skip",
                                "desc": f"⚠ Skip role '{name}' — its position ({live['position']}) is at/above the bot's highest role ({bot_highest_pos}). Move the bot's role above it.",
                                "payload": {}})
                continue
            want_color = _color_hex_to_int(dr.get("color"))
            want_perms = _db.perms_to_int(dr.get("permissions") or {})
            if want_color and live.get("color") != want_color:
                actions.append({"action": "modify_role", "desc": f"Recolor role '{name}'",
                                "payload": {"role_id": live["id"], "color": want_color}})
            if dr.get("hoist") is not None and bool(dr["hoist"]) != bool(live.get("hoist")):
                actions.append({"action": "modify_role", "desc": f"Toggle hoist on '{name}' to {dr['hoist']}",
                                "payload": {"role_id": live["id"], "hoist": bool(dr["hoist"])}})
            if dr.get("mentionable") is not None and bool(dr["mentionable"]) != bool(live.get("mentionable")):
                actions.append({"action": "modify_role", "desc": f"Toggle mentionable on '{name}' to {dr['mentionable']}",
                                "payload": {"role_id": live["id"], "mentionable": bool(dr["mentionable"])}})
            if want_perms and int(live.get("permissions", 0)) != want_perms:
                actions.append({"action": "modify_role", "desc": f"Update permissions on '{name}'",
                                "payload": {"role_id": live["id"], "permissions": want_perms}})

        # ---- 3) Categories + channels + overwrites + per-channel details ----
        VOICE_TYPES = {2, 13}  # voice, stage
        for dcat in (desired.get("categories") or []):
            cat_name = dcat.get("name")
            if not cat_name: continue
            live_cat = cats_live.get(cat_name)
            if live_cat is None:
                actions.append({"action": "create_category",
                                "desc": f"Create category '{cat_name}' (run again afterward to populate its channels)",
                                "payload": {"name": cat_name}})
                continue

            # Category-level overwrites
            want_overs = dcat.get("overwrites") or {}
            live_over_by_target: dict = {}
            for o in live_cat["overwrites"]:
                tname = "@everyone" if o["id"] == guild_id else (role_by_name.get(o["id"]) and o["id"] in {r["id"] for r in roles_live} and next((r["name"] for r in roles_live if r["id"]==o["id"]), o["id"]))
                # Simpler: look up role name by id
                if o["id"] == guild_id:
                    live_over_by_target["@everyone"] = o
                else:
                    role_name = next((r["name"] for r in roles_live if r["id"] == o["id"]), None)
                    if role_name:
                        live_over_by_target[role_name] = o

            for target_name, perms in want_overs.items():
                if target_name == "@everyone":
                    target_id = guild_id
                else:
                    rid = (role_by_name.get(target_name) or {}).get("id")
                    if rid is None:
                        actions.append({"action": "skip",
                                        "desc": f"⚠ Cannot set overwrite on '{cat_name}' for unknown role '{target_name}' (create the role first).",
                                        "payload": {}})
                        continue
                    target_id = rid
                want_allow, want_deny = _parse_overwrite_perms(perms)
                live = live_over_by_target.get(target_name)
                live_allow = (live or {}).get("allow_flags") or {}
                live_deny  = (live or {}).get("deny_flags") or {}
                if live is None or set(want_allow) != set(live_allow) or set(want_deny) != set(live_deny):
                    actions.append({
                        "action": "set_overwrite",
                        "desc": f"Set overwrite on category '{cat_name}' for {target_name}",
                        "payload": {
                            "channel_id": live_cat["id"],
                            "target_id": target_id,
                            "target_type": "role",
                            "allow_flags": want_allow,
                            "deny_flags": want_deny,
                        },
                    })

            # Channels under this category
            live_kids = chans_by_parent.get(live_cat["id"], [])
            live_kids_by_name = {c["name"].lower(): c for c in live_kids}
            for dch in (dcat.get("channels") or []):
                ch_name = (dch.get("name") or "").lower()
                if not ch_name: continue
                ch_type = {"text":0, "voice":2, "announcement":5, "forum":15, "stage":13}.get(dch.get("type","text"), 0)
                live_ch = live_kids_by_name.get(ch_name)
                if live_ch is None:
                    actions.append({"action": "create_channel",
                                    "desc": f"Create channel #{ch_name} in '{cat_name}'",
                                    "payload": {"name": ch_name, "type": ch_type, "parent_id": live_cat["id"]}})
                    continue

                # Channel detail drift: topic, slowmode, NSFW, voice bitrate/user_limit.
                detail_changes = {}
                if "topic" in dch and (dch.get("topic") or "") != (live_ch.get("topic") or ""):
                    detail_changes["topic"] = dch.get("topic") or ""
                if "slowmode" in dch and int(dch.get("slowmode") or 0) != int(live_ch.get("rate_limit_per_user") or 0):
                    detail_changes["rate_limit_per_user"] = int(dch.get("slowmode") or 0)
                if "nsfw" in dch and bool(dch.get("nsfw")) != bool(live_ch.get("nsfw")):
                    detail_changes["nsfw"] = bool(dch.get("nsfw"))
                if live_ch.get("type") in VOICE_TYPES:
                    if "bitrate" in dch and int(dch.get("bitrate") or 0) and int(dch.get("bitrate")) != int(live_ch.get("bitrate") or 0):
                        detail_changes["bitrate"] = int(dch.get("bitrate"))
                    if "user_limit" in dch and int(dch.get("user_limit") or 0) != int(live_ch.get("user_limit") or 0):
                        detail_changes["user_limit"] = int(dch.get("user_limit") or 0)
                if detail_changes:
                    actions.append({"action": "modify_channel",
                                    "desc": f"Update #{ch_name}: {', '.join(detail_changes.keys())}",
                                    "payload": {"channel_id": live_ch["id"], **detail_changes}})

        # ---- 4) AutoMod rules (create missing only — don't auto-delete) ----
        desired_automod = desired.get("automod") or []
        live_automod_by_name = {a.get("name"): a for a in automod_live}
        for da in desired_automod:
            aname = da.get("name")
            if not aname: continue
            if aname not in live_automod_by_name:
                actions.append({"action": "create_automod",
                                "desc": f"Create AutoMod rule '{aname}' ({da.get('trigger','keyword')})",
                                "payload": da})

        # ---- 5) Welcome screen ----
        dws = desired.get("welcome_screen")
        if dws and "COMMUNITY" in (guild_live.get("features") or []):
            want_desc = dws.get("description")
            want_channels = dws.get("channels") or []
            # Resolve channel name → id
            resolved = []
            for wc in want_channels:
                cname = (wc.get("channel") or "").lower()
                target = next((c for c in chans_live if c["name"].lower() == cname), None)
                if target:
                    resolved.append({
                        "channel_id": target["id"],
                        "description": (wc.get("description") or "")[:50],
                        "emoji_name": wc.get("emoji"),
                    })
            live_desc = (ws_live or {}).get("description")
            live_channels = (ws_live or {}).get("welcome_channels") or []
            live_ch_keys = [(w.get("channel_id"), w.get("description")) for w in live_channels]
            want_ch_keys = [(w["channel_id"], w["description"]) for w in resolved]
            if (want_desc is not None and want_desc != live_desc) or live_ch_keys != want_ch_keys:
                actions.append({"action": "modify_welcome_screen",
                                "desc": f"Update welcome screen ({len(resolved)} highlighted channels)",
                                "payload": {"description": want_desc, "welcome_channels": resolved, "enabled": True}})

        # ---- Render plan / apply ----
        out = []
        out.append(f"# Discord Reconcile Plan — mode: **{mode.upper()}**")
        out.append("")
        out.append(f"**Guild:** {guild_live.get('name')} (`{guild_id}`)")
        out.append(f"**Manifest:** `{manifest_path.relative_to(sections_root)}`")
        out.append(f"**Generated:** {datetime.now().isoformat(timespec='seconds')}")
        if bot_highest_pos >= 0:
            out.append(f"**Bot's highest role position:** {bot_highest_pos} _(can manage roles below this)_")
        out.append("")

        if not actions:
            out.append("✅ **No drift detected** — live server matches desired state.")
            applied: list = []
        else:
            # Group actions by type for readability
            by_type: dict = {}
            for a in actions:
                by_type.setdefault(a["action"], []).append(a)
            out.append(f"## {len(actions)} action{'s' if len(actions)!=1 else ''} planned")
            out.append("")
            for act_type, items in by_type.items():
                out.append(f"### `{act_type}` × {len(items)}")
                for a in items:
                    out.append(f"- {a['desc']}")
                out.append("")

            applied = []
            if mode == "apply":
                out.append("## Applying changes…")
                out.append("")
                for a in actions:
                    act = a["action"]; p = a["payload"]
                    try:
                        if act == "modify_guild":
                            r = _db.modify_guild(token, guild_id, **p)
                        elif act == "create_role":
                            r = _db.create_role(token, guild_id,
                                                name=p.get("name",""),
                                                color=_color_hex_to_int(p.get("color")),
                                                permissions=_db.perms_to_int(p.get("permissions") or {}),
                                                hoist=bool(p.get("hoist")),
                                                mentionable=bool(p.get("mentionable")))
                        elif act == "modify_role":
                            r = _db.modify_role(token, guild_id, p["role_id"],
                                                **{k: v for k, v in p.items() if k != "role_id"})
                        elif act == "create_category":
                            r = _db.create_channel(token, guild_id, p["name"], channel_type=4)
                        elif act == "create_channel":
                            r = _db.create_channel(token, guild_id, p["name"],
                                                   channel_type=p.get("type",0),
                                                   parent_id=p.get("parent_id"))
                        elif act == "modify_channel":
                            r = _db.modify_channel(token, p["channel_id"],
                                                   **{k: v for k, v in p.items() if k != "channel_id"})
                        elif act == "set_overwrite":
                            r = _db.set_channel_overwrite(token, p["channel_id"], p["target_id"],
                                                          target_type=p.get("target_type","role"),
                                                          allow_flags=p.get("allow_flags") or {},
                                                          deny_flags=p.get("deny_flags") or {})
                        elif act == "create_automod":
                            tt_map = {"keyword": 1, "spam": 3, "keyword_preset": 4, "mention_spam": 5}
                            tt = tt_map.get(p.get("trigger","keyword"), 1)
                            trigger_meta = {}
                            if tt == 1 and p.get("keywords"): trigger_meta["keyword_filter"] = p["keywords"]
                            if tt == 4 and p.get("presets"):
                                preset_map = {"profanity": 1, "sexual": 2, "slurs": 3}
                                trigger_meta["presets"] = [preset_map[x] for x in p["presets"] if x in preset_map]
                            if tt == 5 and p.get("mention_total_limit"):
                                trigger_meta["mention_total_limit"] = int(p["mention_total_limit"])
                            actions_list = []
                            for act_spec in (p.get("actions") or [{"type": "block_message"}]):
                                t = {"block_message": 1, "send_alert": 2, "timeout": 3}.get(act_spec.get("type"), 1)
                                a_entry = {"type": t}
                                if t == 2 and act_spec.get("channel"):
                                    target = next((c for c in chans_live if c["name"].lower() == act_spec["channel"].lower()), None)
                                    if target: a_entry["metadata"] = {"channel_id": target["id"]}
                                if t == 3 and act_spec.get("duration_seconds"):
                                    a_entry["metadata"] = {"duration_seconds": int(act_spec["duration_seconds"])}
                                actions_list.append(a_entry)
                            r = _db.create_automod_rule(token, guild_id,
                                                         name=p.get("name","")[:100],
                                                         trigger_type=tt,
                                                         trigger_metadata=trigger_meta,
                                                         actions=actions_list,
                                                         enabled=bool(p.get("enabled", True)))
                        elif act == "modify_welcome_screen":
                            r = _db.modify_welcome_screen(token, guild_id, **p)
                        elif act == "skip":
                            r = {"ok": True, "skipped": True}
                        else:
                            r = {"ok": False, "error": f"unknown action: {act}"}
                        applied.append({"action": act, "desc": a["desc"], "ok": r.get("ok"), "error": r.get("error")})
                        out.append(f"- {'✅' if r.get('ok') else '❌'} {a['desc']}{' — ' + str(r.get('error','')) if not r.get('ok') else ''}")
                    except Exception as e:
                        applied.append({"action": act, "desc": a["desc"], "ok": False, "error": str(e)})
                        out.append(f"- ❌ {a['desc']} — exception: {e}")
            else:
                out.append("ℹ️ **Plan only.** Re-run with `mode=apply` to execute these changes.")
        out.append("")

        return {"step": stype, "ts": ts, "ok": True,
                "mode": mode,
                "action_count": len(actions),
                "applied": applied,
                "output": "\n".join(out)}

    # ---- Discord: verify reaction watcher (polls #verify, grants role) ----
    if stype == "discord_verify_watch":
        try:
            from _jarvis.integrations import discord_bot as _db
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"discord module load: {e}"}
        creds = _resolve_cred(ins.get("credential"), "discord_bot")
        token = creds.get("bot_token", "")
        guild_id = (_render(ins.get("guild_id",""), context, params) or creds.get("default_guild_id","")).strip()
        channel_name = (_render(ins.get("verify_channel","verify"), context, params) or "verify").strip().lstrip("#").lower()
        role_name = _render(ins.get("role_name","✅ Verified"), context, params).strip()
        emoji = _render(ins.get("emoji","✅"), context, params).strip() or "✅"
        welcome_msg = _render(ins.get("welcome_message",""), context, params).strip() or "**Welcome** — react with ✅ to verify."

        if not token:    return {"step": stype, "ts": ts, "ok": False, "error": "no Discord bot token"}
        if not guild_id: return {"step": stype, "ts": ts, "ok": False, "error": "guild_id required"}

        # Resolve channel + role by name (refresh on every run — channel ids can change).
        roles_r = _db.list_roles(token, guild_id)
        chans_r = _db.list_channels_full(token, guild_id)
        if not roles_r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"list_roles: {roles_r.get('error')}"}
        if not chans_r.get("ok"):
            return {"step": stype, "ts": ts, "ok": False, "error": f"list_channels: {chans_r.get('error')}"}

        role = next((r for r in roles_r["roles"] if r["name"].lower() == role_name.lower()), None)
        if not role:
            return {"step": stype, "ts": ts, "ok": False, "error": f"role '{role_name}' not found in this server"}
        channel = next((c for c in chans_r["channels"] if c["name"].lower() == channel_name and c["type"] in (0, 5)), None)
        if not channel:
            return {"step": stype, "ts": ts, "ok": False, "error": f"channel '#{channel_name}' not found"}

        # Persistent state per (guild, channel, role): the welcome message id we posted.
        state_key = f"discord_verify::{guild_id}::{channel['id']}::{role['id']}"
        st = _load_state()
        watch_state = (st.get("discord_verify_watchers") or {}).get(state_key) or {}
        msg_id = watch_state.get("message_id")

        granted_now = []
        posted_msg = False

        # First run: post the welcome message, react with the emoji ourselves, save id.
        if not msg_id:
            post_r = _db.post_message(token, channel["id"], welcome_msg)
            if not post_r.get("ok"):
                return {"step": stype, "ts": ts, "ok": False, "error": f"post welcome: {post_r.get('error')}"}
            msg_id = post_r.get("message_id")
            # React ourselves so users have something to click on
            _db.add_reaction(token, channel["id"], msg_id, emoji)
            posted_msg = True
            watch_state = {"message_id": msg_id, "created_at": datetime.now().isoformat(timespec="seconds")}

        # Read reactions on the message
        reacts = _db.get_reactions(token, channel["id"], msg_id, emoji, limit=100)
        if not reacts.get("ok"):
            # If the message was deleted, reset our state so the next run re-posts.
            err = str(reacts.get("error",""))
            if "404" in err or "Unknown Message" in err:
                st.setdefault("discord_verify_watchers", {}).pop(state_key, None)
                _save_state(st)
                return {"step": stype, "ts": ts, "ok": False, "error": "welcome message was deleted — will repost on next run"}
            return {"step": stype, "ts": ts, "ok": False, "error": f"get_reactions: {err}"}

        users = reacts.get("users", [])
        # Skip ourselves (the bot's own seed reaction) + already-verified users we track.
        seen_users = set(watch_state.get("verified_users") or [])

        # Find the bot's own user id to skip it
        bot_me = _db.test_token(token)
        bot_uid = ((bot_me.get("user") or {}).get("id")) if bot_me.get("ok") else None

        for u in users:
            uid = u.get("id")
            if not uid or uid == bot_uid: continue
            if uid in seen_users: continue
            r = _db.add_member_role(token, guild_id, uid, role["id"])
            if r.get("ok"):
                granted_now.append({"user_id": uid, "username": u.get("username","")})
                seen_users.add(uid)
                # Remove their reaction so the user-counter on the message stays clean
                _db.remove_user_reaction(token, channel["id"], msg_id, emoji, uid)

        # Persist updated state
        watch_state["verified_users"] = sorted(seen_users)[-500:]  # cap at last 500 to bound state size
        watch_state["last_polled"] = datetime.now().isoformat(timespec="seconds")
        st.setdefault("discord_verify_watchers", {})[state_key] = watch_state
        _save_state(st)

        out_lines = []
        if posted_msg:
            out_lines.append(f"Posted welcome message to #{channel['name']} (id `{msg_id}`).")
        out_lines.append(f"Polled reactions on welcome message — {len(users)} total ✅, {len(granted_now)} newly granted '{role_name}'.")
        if granted_now:
            for g in granted_now:
                out_lines.append(f"  → granted to @{g['username']}")
        return {"step": stype, "ts": ts, "ok": True,
                "granted_count": len(granted_now),
                "total_reactors": len(users),
                "message_id": msg_id,
                "posted_message": posted_msg,
                "output": "\n".join(out_lines)}

    # ---- GHL workflow trigger ----
    if stype == "ghl_workflow":
        try:
            from _jarvis.integrations import ghl as _ghl
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"ghl module load: {e}"}
        creds = _resolve_cred(ins.get("credential"), "ghl")
        api_key = creds.get("api_key", ""); loc = creds.get("location_id", "")
        if not (api_key and loc):
            return {"step": stype, "ts": ts, "ok": False, "error": "GHL credential incomplete"}
        try: _ghl.init(loc, api_key)
        except Exception: pass
        wid = (ins.get("workflow_id") or "").strip()
        cid = (ins.get("contact_id") or "").strip() or None
        if not wid:
            return {"step": stype, "ts": ts, "ok": False, "error": "workflow_id required"}
        body = {"contactId": cid} if cid else {}
        try:
            r = _ghl.api("POST", f"/workflows/{wid}/subscribe", body=body)
            return {"step": stype, "ts": ts, "ok": "error" not in r, "output": f"Triggered workflow {wid}", "raw": r}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)[:200]}

    # ---- GHL create opportunity ----
    if stype == "ghl_create_opp":
        try:
            from _jarvis.integrations import ghl as _ghl
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": f"ghl module load: {e}"}
        creds = _resolve_cred(ins.get("credential"), "ghl")
        api_key = creds.get("api_key", ""); loc = creds.get("location_id", "")
        if not (api_key and loc):
            return {"step": stype, "ts": ts, "ok": False, "error": "GHL credential incomplete"}
        try: _ghl.init(loc, api_key)
        except Exception: pass
        body = {
            "pipelineId": (ins.get("pipeline_id") or "").strip(),
            "pipelineStageId": (ins.get("stage_id") or "").strip(),
            "name": _render(ins.get("name",""), context, params)[:200],
            "status": "open",
        }
        if ins.get("contact_id"): body["contactId"] = ins["contact_id"].strip()
        if ins.get("monetary_value"):
            try: body["monetaryValue"] = float(ins["monetary_value"])
            except Exception: pass
        try:
            r = _ghl.api("POST", "/opportunities", body=body)
            return {"step": stype, "ts": ts, "ok": "error" not in r, "output": f"Created opp {body['name']}", "raw": r}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)[:200]}

    # ---- Web scrape via Playwright (lightweight) ----
    if stype == "web_scrape":
        url = (ins.get("url") or "").strip()
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "url required"}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NeuroLinkedOS"})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", "replace")
            # Strip tags + scripts
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()[:6000]
            return {"step": stype, "ts": ts, "ok": True, "url": url, "output": text, "chars": len(text)}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)[:200]}

    # ---- RSS fetch ----
    if stype == "rss_fetch":
        url = (ins.get("url") or "").strip()
        try: limit = int(ins.get("limit") or 10)
        except Exception: limit = 10
        if not url:
            return {"step": stype, "ts": ts, "ok": False, "error": "url required"}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NeuroLinkedOS"})
            with urllib.request.urlopen(req, timeout=15) as r:
                xml = r.read().decode("utf-8", "replace")
            items = []
            for m in re.finditer(r"<(?:item|entry)\b[^>]*>([\s\S]*?)</(?:item|entry)>", xml, re.I):
                blob = m.group(1)
                title = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blob, re.I|re.S)
                link = re.search(r"<link[^>]*?>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", blob, re.I|re.S) \
                       or re.search(r'<link[^>]*href="([^"]+)"', blob, re.I)
                desc = re.search(r"<(?:description|summary)[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</(?:description|summary)>", blob, re.I)
                items.append({
                    "title": (title.group(1) if title else "").strip()[:300],
                    "link": (link.group(1) if link else "").strip(),
                    "summary": re.sub(r"<[^>]+>", "", desc.group(1) if desc else "").strip()[:400],
                })
                if len(items) >= limit: break
            output = "\n\n".join(f"• {i['title']}\n  {i['link']}\n  {i['summary'][:200]}" for i in items)
            return {"step": stype, "ts": ts, "ok": True, "url": url, "items": items, "count": len(items),
                    "output": output[:6000]}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)[:200]}

    # ---- Manager: review last task in section ----
    if stype == "agent_review":
        section = (ins.get("section") or "").strip().lower()
        rubric = _render(ins.get("rubric",""), context, params) or "Score this output as PASS or FAIL with one-sentence rationale."
        # Find most recent task whose agent.section == section (other than the manager itself)
        recent = sorted(AGENT_TASKS.values(), key=lambda t: t.get("started_at",""), reverse=True)
        target_task = None
        for t in recent:
            ag = CUSTOM_AGENTS.get(t.get("agent_id") or "")
            if ag and ag.get("section") == section and ag.get("role", "worker") != "manager":
                target_task = t
                break
        if not target_task:
            return {"step": stype, "ts": ts, "ok": True, "output": f"No recent worker task in '{section}' to review.", "verdict": "skip"}
        steps_dump = json.dumps(target_task.get("steps_done") or [], indent=2)[:8000]
        # Reuse the llm_ask path for the actual reasoning
        prompt = (
            f"You are a section manager. Review the following worker output against this rubric and respond "
            f"with a single line starting `VERDICT: PASS|FAIL|HOLD` followed by a 1-paragraph rationale.\n\n"
            f"RUBRIC:\n{rubric}\n\nWORKER OUTPUT (JSON):\n{steps_dump}"
        )
        sub = _run_custom_step({"type":"llm_ask","inputs":{"prompt": prompt, "credential": ins.get("credential","")}}, context, agent_id=agent_id)
        verdict = "HOLD"
        body = sub.get("output","") if sub.get("ok") else ""
        m = re.match(r"\s*VERDICT:\s*(PASS|FAIL|HOLD)", body, re.I)
        if m: verdict = m.group(1).upper()
        return {"step": stype, "ts": ts, "ok": sub.get("ok", False), "section": section,
                "reviewed_task_id": target_task.get("id"),
                "reviewed_agent": target_task.get("agent_id"),
                "verdict": verdict, "output": body[:4000]}

    # ---- Manager: dispatch another agent ----
    if stype == "agent_dispatch":
        target_id = (ins.get("agent_id") or "").strip()
        if not target_id or target_id not in CUSTOM_AGENTS:
            return {"step": stype, "ts": ts, "ok": False, "error": f"unknown agent_id '{target_id}'"}
        # Loop guard: cap chain depth at 3 via state.json
        try:
            state = _load_state()
            depth = int(state.get("dispatch_depth", 0))
            if depth >= 3:
                return {"step": stype, "ts": ts, "ok": False, "error": "dispatch chain limit (3) reached"}
            state["dispatch_depth"] = depth + 1
            _save_state(state)
        except Exception: pass
        try:
            tid = start_custom_agent_task(target_id)
            return {"step": stype, "ts": ts, "ok": True, "dispatched_task_id": tid, "output": f"Dispatched {target_id} → {tid}"}
        except Exception as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)[:200]}

    # ---- write_output: save text to a section's output/ folder ----
    if stype == "write_output":
        section = (ins.get("section") or "").strip().lower()
        # Render template tokens ({{date}}, {{datetime}}, {{<input>}}) in the
        # filename BEFORE sanitizing — otherwise {{ }} get replaced with underscores.
        filename = _render(ins.get("filename") or "", context, params).strip()
        if not filename:
            filename = f"{_date_prefix()}-output.md"
        # Sanitize: keep colons out of filenames on Windows; isoformat datetimes have them.
        filename = re.sub(r"[^A-Za-z0-9._\- ]", "_", filename) or f"{_date_prefix()}-output.md"
        content = _render(ins.get("content",""), context, params)
        if not section or not content:
            return {"step": stype, "ts": ts, "ok": False, "error": "section and content required"}
        try:
            target = _section_path(section, "output") / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            rel = str(target.relative_to(sections_root.parent.parent))
            return {"step": stype, "ts": ts, "ok": True, "path": rel, "bytes": len(content),
                    "output": f"Wrote → {rel}"}
        except ValueError as e:
            return {"step": stype, "ts": ts, "ok": False, "error": str(e)}

    return {"step": stype, "ts": ts, "error": "unknown step type", "ok": False}


def _run_custom_agent_task(task_id: str, agent_id: str):
    """Thread entry: walk the agent's steps, append each result to the task record."""
    agent = CUSTOM_AGENTS.get(agent_id)
    if not agent:
        AGENT_TASKS[task_id].update({"status": "error", "error": "agent not found",
                                      "finished_at": datetime.now().isoformat()})
        return

    AGENT_TASKS[task_id]["status"] = "running"
    AGENT_TASKS[task_id]["agent_name"] = agent["name"]
    AGENT_TASKS[task_id]["steps_total"] = len(agent["steps"])
    AGENT_TASKS[task_id]["steps_done"] = []

    ctx = []
    # Pull the params from the task record so per-run inputs (theme, niche, etc.)
    # are available to {{<input>}} token substitution inside step inputs.
    run_params = AGENT_TASKS.get(task_id, {}).get("params") or {}
    for i, step in enumerate(agent["steps"]):
        if AGENT_TASKS.get(task_id, {}).get("status") == "cancelled":
            return
        time.sleep(0.9)  # pacing so UI animates
        result = _run_custom_step(step, ctx, agent_id=agent_id, params=run_params)
        ctx.append(result)
        AGENT_TASKS[task_id]["steps_done"] = list(ctx)
        AGENT_TASKS[task_id]["progress"] = f"{i+1}/{len(agent['steps'])}"

    AGENT_TASKS[task_id].update({
        "status": "done",
        "finished_at": datetime.now().isoformat(),
        "result": {
            "agent": agent["name"],
            "ran_at": datetime.now().strftime("%H:%M:%S"),
            "actions": [f"Ran {len(agent['steps'])} step workflow", *(f"  ✓ {s.get('step')}" for s in ctx)],
            "data": {"steps": ctx},
            "next_step": f"Review step outputs · or enable auto-run schedule",
        },
    })

    # ----- MANAGER AUTO-FIRE -----
    # If this just-finished worker has `manager_review: true` in its frontmatter,
    # find the section's manager and fire it. The manager reviews this run via
    # its `agent_review` step (which pulls the most recent worker task in the
    # section), then posts its rollup (often to Slack).
    #
    # Loop guard: uses the same `dispatch_depth` counter as `agent_dispatch`.
    # Each direct-invoke run resets depth → managers never chain into infinite
    # loops because managers themselves don't have manager_review: true.
    try:
        if (
            agent.get("manager_review") is True
            and agent.get("role", "worker") != "manager"
        ):
            section = agent.get("section")
            if section:
                manager = next(
                    (a for aid, a in CUSTOM_AGENTS.items()
                     if a.get("section") == section and a.get("role") == "manager"),
                    None,
                )
                if manager:
                    # Check loop guard
                    try:
                        st = _load_state()
                        depth = int(st.get("dispatch_depth", 0))
                    except Exception:
                        depth = 0
                    if depth >= 3:
                        print(f"[manager_review] {agent_id} skipped — dispatch depth {depth} exceeded", flush=True)
                    else:
                        try:
                            st = _load_state(); st["dispatch_depth"] = depth + 1; _save_state(st)
                        except Exception: pass
                        print(f"[manager_review] {agent_id} done → auto-firing manager {manager['id']} in section {section}", flush=True)
                        # Fire manager in its own thread (start_custom_agent_task already detaches)
                        threading.Thread(
                            target=start_custom_agent_task,
                            args=(manager["id"],),
                            kwargs={"params": {}},
                            daemon=True,
                        ).start()
                        # Tag the worker's task record so the UI can show the chain
                        AGENT_TASKS[task_id]["manager_auto_fired"] = manager["id"]
                else:
                    print(f"[manager_review] {agent_id} done but no manager found in section {section}", flush=True)
    except Exception as e:
        print(f"[manager_review] hook failed for {agent_id}: {e}", flush=True)


def start_custom_agent_task(agent_id: str, params: dict | None = None) -> str:
    # Reset dispatch chain depth at the top of every fresh direct invocation.
    # (agent_dispatch increments it; this prevents one stuck count from blocking forever.)
    try:
        st = _load_state(); st["dispatch_depth"] = 0; _save_state(st)
    except Exception: pass
    tid = uuid.uuid4().hex[:8]
    agent = CUSTOM_AGENTS.get(agent_id) or {}
    AGENT_TASKS[tid] = {
        "id": tid,
        # Set BOTH keys for compatibility — legacy code uses "agent",
        # the live feed and run drawer read "agent_id".
        "agent": agent_id,
        "agent_id": agent_id,
        "agent_name": agent.get("name", agent_id),
        "params": dict(params or {}),
        "status": "queued",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "result": None,
        "custom": True,
    }
    threading.Thread(target=_run_custom_agent_task, args=(tid, agent_id), daemon=True).start()
    return tid


def _agent_do(agent_id: str, params: dict) -> dict:
    """Synthesize a realistic result for each agent type. In-memory only."""
    now = datetime.now().strftime("%H:%M:%S")

    if agent_id == "meeting_prep":
        cal = CALENDAR()
        nxt = cal[0] if cal else None
        key = (nxt["title"].split("·")[0].strip().split(" ")[0].lower() if nxt else "")
        related = brain_search(key, limit=4) if key else []
        return {
            "agent": "Meeting Brief",
            "ran_at": now,
            "actions": [
                f"Next meeting: {nxt['title'] if nxt else '—'}",
                f"Pulled {len(related)} related brain docs",
                "Drafted 3-paragraph brief with talking points + objection prep",
            ],
            "data": {"meeting": nxt, "related_notes": related},
            "next_step": "Attach brief to the calendar event · CREATE/REVIEW/EXECUTE",
        }

    if agent_id == "follow_up_drafter":
        target = params.get("target", "Acme Corp")
        return {
            "agent": "Follow-Up Drafter",
            "ran_at": now,
            "actions": [
                f"Target: {target}",
                "Read last 3 emails + CRM notes from brain",
                "Drafted 4-sentence personalized follow-up",
            ],
            "data": {
                "to": target,
                "subject": f"Following up on our proposal — {target}",
                "body": (
                    f"Hi — circling back on the proposal we sent last week.\n\n"
                    f"I've had time to think through the questions around rollout timing "
                    f"and have a revised plan that compresses weeks 2-3 without dropping scope. "
                    f"Would a 20-minute window Thursday or Friday work to walk through it? "
                    f"Happy to move around your calendar.\n\n"
                    f"— Your Name"
                ),
            },
            "next_step": "Review draft · then send via Gmail · CREATE/REVIEW/EXECUTE",
        }

    if agent_id == "inbox_triage":
        return {
            "agent": "Inbox Sentinel",
            "ran_at": now,
            "actions": [
                "Scanned 47 emails (last 24h)",
                "Classified: 3 HIGH · 12 MED · 32 LOW",
                "Auto-drafted 3 HIGH replies for your review",
            ],
            "data": {"high": 3, "med": 12, "low": 32},
            "next_step": "Review drafted HIGH replies · CREATE/REVIEW/EXECUTE",
        }

    if agent_id == "invoice_triage":
        return {
            "agent": "Invoice Triage",
            "ran_at": now,
            "actions": [
                "Scanned 12 recent invoice PDFs",
                "Extracted vendor + amount via OCR",
                "Flagged 2 invoices over $5K threshold",
            ],
            "data": {"scanned": 12, "flagged_high": 2, "total_ar": "$26,300"},
            "next_step": "Route flagged → QuickBooks AP · CREATE/REVIEW/EXECUTE",
        }

    if agent_id == "brain_search":
        q = params.get("q", "contract")
        results = brain_search(q, limit=6)
        return {
            "agent": "Brain Query",
            "ran_at": now,
            "actions": [f"Query: '{q}'", f"Returned {len(results)} hits from the brain"],
            "data": {"results": results, "q": q},
            "next_step": "Compose summary note · CREATE/REVIEW/EXECUTE",
        }

    if agent_id == "manager_report":
        return {
            "agent": "Manager Weekly Report",
            "ran_at": now,
            "actions": [
                "Pulled activity across 4 managers",
                "Summarized wins, blockers, goals for the week",
                "Report saved to reports/weekly/2026-W17.md",
            ],
            "data": {"managers": 4, "actions_reviewed": 182},
            "next_step": "Share with leadership Slack · CREATE/REVIEW/EXECUTE",
        }

    return {"agent": agent_id, "ran_at": now, "actions": ["Unknown agent"], "data": {}, "next_step": None}


def run_agent_task(agent_id: str, params: dict) -> dict:
    """Synchronous single-shot run used by /api/agent/run."""
    # Simulate a few hundred ms of work so the UI feels alive
    time.sleep(0.4)
    return _agent_do(agent_id, params)


def _run_async(task_id: str, agent_id: str, params: dict):
    # Small delay so "running" state is visible in the UI while work is queued.
    AGENT_TASKS[task_id]["status"] = "running"
    time.sleep(2.2)
    if AGENT_TASKS.get(task_id, {}).get("status") == "cancelled":
        return
    try:
        result = _agent_do(agent_id, params)
        AGENT_TASKS[task_id].update({
            "status": "done",
            "finished_at": datetime.now().isoformat(),
            "result": result,
        })
    except Exception as e:
        AGENT_TASKS[task_id].update({
            "status": "error",
            "finished_at": datetime.now().isoformat(),
            "error": str(e)[:200],
        })


def start_agent_task(agent_id: str, params: dict) -> str:
    tid = uuid.uuid4().hex[:8]
    AGENT_TASKS[tid] = {
        "id": tid,
        "agent": agent_id,
        "params": params,
        "status": "queued",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "result": None,
    }
    threading.Thread(target=_run_async, args=(tid, agent_id, params), daemon=True).start()
    return tid


# --------------------------------------------------------------------------
# CALENDAR — persisted to state.json. Wire a real calendar source by replacing
# this function with your Google/Outlook/CalDAV client, keeping the same shape.
# --------------------------------------------------------------------------

def create_event(title: str, start_iso: str, duration_min: int = 30) -> dict:
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", ""))
    except Exception:
        dt = datetime.now() + timedelta(hours=1)
    pretty = dt.strftime("%A %b %d · %I:%M %p")
    evt = {"title": title, "start": pretty}
    state = _refresh_state()
    cal = state.setdefault("calendar", [])
    cal.insert(0, evt)
    # Keep calendar size reasonable
    del cal[30:]
    _save_state(state)
    return {"ok": True, "title": title, "start": pretty, "end": (dt + timedelta(minutes=duration_min)).strftime("%I:%M %p"), "calendar": "Local"}


# --------------------------------------------------------------------------
# HTTP HANDLER
# --------------------------------------------------------------------------

_ALLOWED_ORIGINS = {
    "http://localhost:8010","http://127.0.0.1:8010",
    "http://localhost:8340","http://127.0.0.1:8340",
    "http://localhost:8020","http://127.0.0.1:8020",
}
# DNS-rebinding defense: only accept requests whose Host header is a
# local-loopback alias.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}

# Per-startup launch token. Anything hitting /api/* must present it. The
# dashboard HTML embeds it for the same-origin frontend on first load.
import secrets as _secrets
LAUNCH_TOKEN = (os.environ.get("NEUROLINKED_TOKEN") or _secrets.token_urlsafe(32)).strip()
print(f"[ops-center] launch token armed (len={len(LAUNCH_TOKEN)})", flush=True)

# Routes that don't need a token: the dashboard HTML and any static asset
# fetched during the page load.
_TOKEN_OPEN_PATHS = {"/", "/index.html"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host", "") or "").split(":")[0]
        return (not host) or host in _ALLOWED_HOSTS

    def _token_ok(self, path):
        """API token enforcement. Open paths skip it (dashboard HTML +
        static assets); /api/* requires X-Neurolinked-Token or ?token=."""
        if not path.startswith("/api/"):
            return True
        if path in _TOKEN_OPEN_PATHS:
            return True
        supplied = self.headers.get("X-Neurolinked-Token", "")
        if not supplied:
            try:
                qs = parse_qs(urlparse(self.path).query)
                supplied = (qs.get("token", [""])[0] or "")
            except Exception:
                supplied = ""
        if not supplied:
            return False
        return _secrets.compare_digest(supplied, LAUNCH_TOKEN)

    def _csrf_ok(self):
        """CSRF guard for POST/DELETE: reject any request whose Origin or Referer points
        outside our localhost services. Browsers ALWAYS send Origin on cross-origin
        state-changing requests, so a missing Origin + missing Referer = same-origin
        navigation from our own HTML, which we accept."""
        origin = self.headers.get("Origin", "")
        if origin:
            return origin in _ALLOWED_ORIGINS
        # No Origin header — check Referer as a fallback signal
        ref = self.headers.get("Referer", "")
        if ref:
            return any(ref.startswith(o + "/") or ref == o for o in _ALLOWED_ORIGINS)
        # Neither header present: same-origin POST from our HTML (some browsers
        # omit Origin on same-origin POST). Accept.
        return True

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # CORS: only allow localhost origins (defense-in-depth on top of 127.0.0.1 bind).
        origin = self.headers.get("Origin", "")
        if origin in ("http://localhost:8010","http://127.0.0.1:8010","http://localhost:8340","http://127.0.0.1:8340","http://localhost:8020","http://127.0.0.1:8020"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def _send_event_stream(self):
        """Server-Sent Events for the the successor product Live Feed.

        Emits one event per second pulling from AGENT_TASKS state changes
        and the latest brain pulse. The connection is held open until the
        client disconnects. Per spec: blank line between events, `data:`
        lines, `id:` for replay support."""
        try:
            self.send_response(200)
            origin = self.headers.get("Origin", "")
            if origin in _ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return

        last_seen: dict[str, str] = {}
        event_id = 0
        try:
            while True:
                # Snapshot agent task state changes
                emitted_any = False
                for tid, t in list(AGENT_TASKS.items()):
                    sig = f"{t.get('status','')}:{t.get('finished_at','')}:{len(t.get('steps',[]))}"
                    if last_seen.get(tid) != sig:
                        last_seen[tid] = sig
                        event_id += 1
                        payload = json.dumps({
                            "type": "agent_task",
                            "task_id": tid,
                            "agent_id": t.get("agent_id"),
                            "status": t.get("status"),
                            "started_at": t.get("started_at"),
                            "finished_at": t.get("finished_at"),
                            "step_count": len(t.get("steps", [])),
                        })
                        try:
                            self.wfile.write(f"id: {event_id}\nevent: agent_task\ndata: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            emitted_any = True
                        except (ConnectionResetError, BrokenPipeError):
                            return
                # Heartbeat every cycle so reverse proxies don't time out
                if not emitted_any:
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    except (ConnectionResetError, BrokenPipeError):
                        return
                time.sleep(1.0)
        except Exception:
            return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Neurolinked-Token")
        self.end_headers()

    def do_POST(self):
        # Host guard — block DNS rebinding (evil.com rebinding to 127.0.0.1).
        if not self._host_ok():
            self._send(400, {"error": "bad host", "host": self.headers.get("Host","")})
            return
        # CSRF GUARD — reject cross-origin POSTs.
        if not self._csrf_ok():
            self._send(403, {"error": "cross-origin request blocked", "origin": self.headers.get("Origin","")})
            return
        # TOKEN GUARD — every /api/* POST must present the launch token.
        p_pre = urlparse(self.path).path
        if not self._token_ok(p_pre):
            self._send(401, {"error": "missing or invalid token"})
            return
        p = urlparse(self.path).path
        body = self._read_json()

        if p == "/api/agent/run":
            aid = body.get("agent") or body.get("id", "")
            self._send(200, run_agent_task(aid, body.get("params", {}))); return

        if p == "/api/agent/start":
            aid = body.get("agent") or body.get("id", "")
            tid = start_agent_task(aid, body.get("params", {}))
            self._send(200, {"task_id": tid, "status": "queued"}); return

        if p == "/api/agent/cancel":
            tid = body.get("task_id")
            t = AGENT_TASKS.get(tid)
            if t and t.get("status") in ("queued", "running"):
                t["status"] = "cancelled"
                t["finished_at"] = datetime.now().isoformat()
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found or already finished"})
            return

        if p == "/api/calendar/create":
            r = create_event(
                body.get("title", "New meeting"),
                body.get("start", (datetime.now() + timedelta(hours=1)).isoformat()),
                int(body.get("duration", 30)),
            )
            self._send(200, r); return

        # ----- CUSTOM AGENT BUILDER -----
        if p == "/api/agents":
            # Create a new custom agent — with hardened input validation
            if len(CUSTOM_AGENTS) >= MAX_AGENTS:
                self._send(429, {"error": f"agent limit reached ({MAX_AGENTS})"}); return
            name = (body.get("name") or "").strip()
            if not name:
                self._send(400, {"error": "name is required"}); return
            if len(name) > MAX_AGENT_NAME:
                self._send(400, {"error": f"name max {MAX_AGENT_NAME} chars"}); return
            desc = (body.get("description") or "").strip()
            if len(desc) > MAX_AGENT_DESC:
                self._send(400, {"error": f"description max {MAX_AGENT_DESC} chars"}); return
            raw_steps = body.get("steps", [])
            if not isinstance(raw_steps, list) or not raw_steps:
                self._send(400, {"error": "at least one step required"}); return
            if len(raw_steps) > MAX_STEPS:
                self._send(400, {"error": f"max {MAX_STEPS} steps"}); return
            # Validate each step: allowed type + input length
            clean_steps = []
            for s in raw_steps:
                if not isinstance(s, dict):
                    self._send(400, {"error": "each step must be an object"}); return
                stype = (s.get("type") or "").strip()
                if stype not in ALLOWED_STEP_TYPES:
                    self._send(400, {"error": f"unknown step type: {stype}"}); return
                inputs = s.get("inputs", {}) or {}
                if not isinstance(inputs, dict):
                    self._send(400, {"error": "step inputs must be an object"}); return
                clean_inputs = {}
                for k, v in inputs.items():
                    clean_inputs[str(k)[:80]] = (str(v) if v is not None else "")[:MAX_STEP_INPUT]
                clean_steps.append({"type": stype, "inputs": clean_inputs})
            aid = f"a_{uuid.uuid4().hex[:8]}"   # server-generated ID (prevents path-traversal / ID spoofing)
            CUSTOM_AGENTS[aid] = {
                "id": aid,
                "name": name,
                "description": desc,
                "steps": clean_steps,
                "created_at": datetime.now().isoformat(),
                "enabled": True,
            }
            _save_custom_agents()
            self._send(200, {"ok": True, "agent": CUSTOM_AGENTS[aid]}); return

        if p.startswith("/api/agents/") and p.endswith("/run"):
            aid = p.split("/")[3]
            # ID must be alphanumeric + underscore — blocks path traversal, param injection
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", aid or ""):
                self._send(400, {"error": "invalid agent id"}); return
            if aid not in CUSTOM_AGENTS:
                self._send(404, {"error": "agent not found"}); return
            # Pull per-run inputs from the POST body (everything except known
            # control keys gets treated as a param).
            run_params = {}
            if isinstance(body, dict):
                # If the client sent a top-level "params" object, prefer it.
                if isinstance(body.get("params"), dict):
                    run_params = {str(k)[:80]: (str(v)[:1000] if v is not None else "")
                                  for k, v in body["params"].items()}
                else:
                    # Otherwise treat any string-valued top-level fields as inputs.
                    for k, v in body.items():
                        if k in ("agent", "id", "task_id"): continue
                        run_params[str(k)[:80]] = (str(v)[:1000] if v is not None else "")
            tid = start_custom_agent_task(aid, params=run_params)
            self._send(200, {"task_id": tid, "status": "queued", "agent_id": aid}); return

        if p == "/api/jarvis/builder":
            # Conversational agent builder: takes a free-text description + conversation so far,
            # returns a suggested agent config + the next question JARVIS should ask.
            # This is the state machine behind "tell JARVIS to build me a finance agent".
            desc = body.get("description", "").lower()
            turn = body.get("turn", 0)
            if turn == 0:
                # Guess a name + seed steps from the description
                if "finance" in desc or "invoice" in desc or "ar" in desc or "accounts" in desc:
                    suggested = {
                        "name": "Finance Watchdog",
                        "description": "Weekly AR review + polite nudge drafts for overdue invoices.",
                        "steps": [
                            {"type": "brain_search", "inputs": {"query": "overdue invoice"}},
                            {"type": "reason", "inputs": {"prompt": "Classify each as NORMAL (<$5K) or FLAG (>=$5K) and propose a 1-line nudge per account."}},
                            {"type": "draft_email", "inputs": {"to": "ap@example.com", "subject": "Friendly reminder — open invoice", "notes": "Use the reasoning output."}},
                        ],
                    }
                    question = "I drafted a Finance Watchdog with 3 steps. Do you want it to also notify Slack when drafts are ready? (yes / no)"
                elif "meeting" in desc or "prep" in desc or "brief" in desc:
                    suggested = {
                        "name": "Meeting Prep Pro",
                        "description": "Pulls context for the next calendar event and builds a 1-page brief.",
                        "steps": [
                            {"type": "brain_search", "inputs": {"query": "client"}},
                            {"type": "reason", "inputs": {"prompt": "Build a 3-section brief: Background, Open questions, Talking points."}},
                            {"type": "create_task", "inputs": {"title": "Review meeting brief"}},
                        ],
                    }
                    question = "Built a Meeting Prep agent. Should it auto-run 1 hour before each meeting? (yes / no)"
                elif "inbox" in desc or "email" in desc:
                    suggested = {
                        "name": "Inbox Sentinel",
                        "description": "Scores inbox, drafts replies for HIGH-priority messages.",
                        "steps": [
                            {"type": "brain_search", "inputs": {"query": "unread urgent"}},
                            {"type": "reason", "inputs": {"prompt": "Classify top 10 as HIGH/MED/LOW and draft replies for HIGH."}},
                            {"type": "notify", "inputs": {"channel": "ui", "message": "Drafts ready for review."}},
                        ],
                    }
                    question = "Inbox Sentinel built. Run every 30 min? (yes / no)"
                else:
                    suggested = {
                        "name": "Custom Agent",
                        "description": body.get("description", "Describe me further"),
                        "steps": [
                            {"type": "brain_search", "inputs": {"query": "context"}},
                            {"type": "reason", "inputs": {"prompt": "Analyze and propose next actions."}},
                            {"type": "notify", "inputs": {"channel": "ui", "message": "Done."}},
                        ],
                    }
                    question = "Here's a scaffold. What should this agent do more specifically?"
                self._send(200, {"suggested": suggested, "question": question, "turn": 1, "done": False}); return
            else:
                # Any subsequent turn — finalize and save
                cfg = body.get("suggested", {})
                cfg["id"] = f"a_{uuid.uuid4().hex[:8]}"
                cfg["created_at"] = datetime.now().isoformat()
                cfg["enabled"] = True
                CUSTOM_AGENTS[cfg["id"]] = cfg
                _save_custom_agents()
                self._send(200, {"saved": cfg, "done": True, "question": f"Saved. Ready to run '{cfg['name']}'?"}); return

        # ----- CREDENTIAL VAULT -----
        if p == "/api/credentials":
            # Create a new credential (encrypted at rest).
            # Accept BOTH shapes:
            #   1. nested: {name, kind, fields: {api_key: ..., provider: ...}}
            #   2. flat:   {name, kind, api_key: ..., provider: ...}   ← what the UI form sends
            name = (body.get("name") or "").strip()[:60]
            kind = (body.get("kind") or "").strip()
            fields = body.get("fields")
            if fields is None or not isinstance(fields, dict) or not fields:
                # Flat shape — pull every key that isn't name/kind/fields into fields
                fields = {k: v for k, v in body.items() if k not in ("name", "kind", "fields")}
            if not name or not kind:
                self._send(400, {"error": "name and kind are required"}); return
            if kind not in {k["id"] for k in CREDENTIAL_KINDS}:
                self._send(400, {"error": f"unknown kind: {kind}"}); return
            if not isinstance(fields, dict):
                self._send(400, {"error": "fields must be an object"}); return
            # Bound every field value
            clean = {str(k)[:40]: (str(v)[:2000] if v is not None else "") for k,v in fields.items()}
            record = vault_put(name, kind, clean)
            self._send(200, {"ok": True, "credential": record}); return

        if p == "/api/credentials/delete":
            cid = body.get("id","")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", cid or ""):
                self._send(400, {"error": "invalid credential id"}); return
            ok = vault_delete(cid)
            self._send(200 if ok else 404, {"ok": ok}); return

        if p == "/api/credentials/test":
            # Test a credential by kind (e.g., send a Slack ping)
            cid = body.get("id","")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", cid or ""):
                self._send(400, {"error": "invalid credential id"}); return
            cred = vault_get_secret(cid)
            if not cred:
                self._send(404, {"error": "credential not found"}); return
            kind = next((c["kind"] for c in vault_list_public() if c["id"]==cid), "")
            try:
                if kind == "slack_webhook":
                    url = cred.get("webhook_url","")
                    if not url.startswith("https://hooks.slack.com/"):
                        self._send(400, {"error":"invalid webhook url"}); return
                    req = urllib.request.Request(url, data=json.dumps({"text":"✅ NeuroLinked credential test — looking good."}).encode(), headers={"Content-Type":"application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=8)
                    self._send(200, {"ok": True, "message": "Slack ping sent"}); return
                if kind == "llm":
                    prov = cred.get("provider","").lower()
                    if prov == "anthropic" and cred.get("api_key"):
                        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                            data=json.dumps({"model":cred.get("model") or "claude-haiku-4-5","max_tokens":20,"messages":[{"role":"user","content":"Reply with: OK"}]}).encode(),
                            headers={"Content-Type":"application/json","x-api-key":cred["api_key"],"anthropic-version":"2023-06-01"})
                        urllib.request.urlopen(req, timeout=15)
                        self._send(200, {"ok": True, "message": "Anthropic key valid"}); return
                    if prov == "openai" and cred.get("api_key"):
                        req = urllib.request.Request("https://api.openai.com/v1/models",
                            headers={"Authorization": f"Bearer {cred['api_key']}"})
                        urllib.request.urlopen(req, timeout=15)
                        self._send(200, {"ok": True, "message": "OpenAI key valid"}); return
                if kind == "smtp":
                    import smtplib, ssl
                    port = int(cred.get("port") or 587)
                    with smtplib.SMTP(cred.get("host",""), port, timeout=10) as s:
                        s.starttls(context=ssl.create_default_context())
                        s.login(cred.get("username",""), cred.get("password",""))
                    self._send(200, {"ok": True, "message": "SMTP login successful"}); return
                if kind == "slack_bot":
                    # Slack auth.test — pings Slack with the bot token, returns the bot's identity.
                    bot_token = cred.get("bot_token","")
                    if not bot_token.startswith("xoxb-"):
                        self._send(400, {"error":"invalid bot token (should start with xoxb-)"}); return
                    req = urllib.request.Request("https://slack.com/api/auth.test",
                        headers={"Authorization": f"Bearer {bot_token}", "Content-Type":"application/json; charset=utf-8"},
                        method="POST")
                    resp = urllib.request.urlopen(req, timeout=10)
                    d = json.loads(resp.read())
                    if d.get("ok"):
                        self._send(200, {"ok": True, "message": f"Slack bot authenticated as {d.get('user','?')} in workspace {d.get('team','?')}"}); return
                    else:
                        self._send(200, {"ok": False, "error": f"Slack rejected: {d.get('error','unknown')}"}); return
                if kind == "discord_bot":
                    # Discord — GET /users/@me with the bot token, plus verify the configured guild is reachable.
                    from _jarvis.integrations import discord_bot as _db
                    bot_token = cred.get("bot_token","")
                    if not bot_token:
                        self._send(400, {"error":"bot_token required"}); return
                    me = _db.test_token(bot_token)
                    if not me.get("ok"):
                        self._send(200, {"ok": False, "error": f"Discord rejected token: {me.get('error','unknown')}"}); return
                    user = me.get("user") or {}
                    msg = f"Discord bot authenticated as {user.get('username','?')}#{user.get('discriminator','0')}"
                    # If a guild is configured, confirm the bot is actually in it.
                    guild_id = cred.get("default_guild_id","").strip()
                    if guild_id:
                        guilds = _db.list_guilds(bot_token)
                        if guilds.get("ok"):
                            match = next((g for g in guilds.get("guilds",[]) if g["id"] == guild_id), None)
                            if match:
                                msg += f" · joined server '{match['name']}'"
                            else:
                                msg += f" · ⚠ bot is NOT in server {guild_id} — invite it first"
                    self._send(200, {"ok": True, "message": msg}); return
                if kind == "ghl":
                    # GHL — try a lightweight call to the locations endpoint.
                    api_key = cred.get("api_key","")
                    loc = cred.get("location_id","")
                    if not api_key or not loc:
                        self._send(400, {"error":"need both api_key and location_id"}); return
                    req = urllib.request.Request(f"https://services.leadconnectorhq.com/locations/{loc}",
                        headers={"Authorization": f"Bearer {api_key}", "Version": "2021-07-28", "Accept":"application/json"})
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        self._send(200, {"ok": True, "message": f"GHL location {loc} reachable"}); return
                    except urllib.error.HTTPError as e:
                        self._send(200, {"ok": False, "error": f"GHL HTTP {e.code}: {e.read().decode('utf-8','replace')[:120]}"}); return
                if kind == "replicate":
                    api_token = cred.get("api_token","")
                    if not api_token.startswith("r8_"):
                        self._send(400, {"error":"invalid Replicate token (should start with r8_)"}); return
                    req = urllib.request.Request("https://api.replicate.com/v1/account",
                        headers={"Authorization": f"Token {api_token}"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    d = json.loads(resp.read())
                    self._send(200, {"ok": True, "message": f"Replicate account: {d.get('username') or d.get('name') or 'authenticated'}"}); return
                if kind == "elevenlabs":
                    api_key = cred.get("api_key","")
                    if not api_key:
                        self._send(400, {"error":"api_key required"}); return
                    req = urllib.request.Request("https://api.elevenlabs.io/v1/user",
                        headers={"xi-api-key": api_key})
                    resp = urllib.request.urlopen(req, timeout=10)
                    d = json.loads(resp.read())
                    sub = (d.get("subscription") or {}).get("tier","unknown")
                    self._send(200, {"ok": True, "message": f"ElevenLabs authenticated (tier: {sub})"}); return
                if kind == "heygen":
                    api_key = cred.get("api_key","")
                    if not api_key:
                        self._send(400, {"error":"api_key required"}); return
                    req = urllib.request.Request("https://api.heygen.com/v2/avatars",
                        headers={"X-API-Key": api_key, "Accept":"application/json"})
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        self._send(200, {"ok": True, "message": "HeyGen API key valid"}); return
                    except urllib.error.HTTPError as e:
                        self._send(200, {"ok": False, "error": f"HeyGen HTTP {e.code}: {e.read().decode('utf-8','replace')[:120]}"}); return
                if kind == "buffer":
                    token = cred.get("access_token","")
                    if not token:
                        self._send(400, {"error":"access_token required"}); return
                    req = urllib.request.Request(f"https://api.bufferapp.com/1/profiles.json?access_token={urllib.parse.quote(token)}")
                    resp = urllib.request.urlopen(req, timeout=10)
                    d = json.loads(resp.read())
                    self._send(200, {"ok": True, "message": f"Buffer authenticated, {len(d)} profile(s) connected"}); return
                self._send(200, {"ok": True, "message": f"No test defined for kind '{kind}' — credential saved but not validated."}); return
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)[:200]}); return

        # ----- AGENT CORE OS — create per-section .md agent -----
        if p == "/api/agents/create":
            if not AGENT_LOADER_OK:
                self._send(500, {"error": "agent_loader unavailable (PyYAML not installed)"}); return
            section = (body.get("section") or "").strip()
            name = (body.get("name") or "").strip()
            description = (body.get("description") or "").strip()
            schedule = (body.get("schedule") or "on-demand").strip()
            inputs = body.get("inputs", []) or []
            steps = body.get("steps", []) or []
            if not section:
                self._send(400, {"error": "section is required"}); return
            if not re.fullmatch(r"[a-z0-9_\-]{1,40}", section or ""):
                self._send(400, {"error": "invalid section name"}); return
            if not (SECTIONS_DIR / section).is_dir():
                self._send(400, {"error": f"unknown section '{section}'"}); return
            if not name:
                self._send(400, {"error": "name is required"}); return
            if len(name) > MAX_AGENT_NAME:
                self._send(400, {"error": f"name max {MAX_AGENT_NAME} chars"}); return
            if len(description) > MAX_AGENT_DESC:
                self._send(400, {"error": f"description max {MAX_AGENT_DESC} chars"}); return
            if not isinstance(steps, list) or not steps:
                self._send(400, {"error": "at least one step required"}); return
            if len(steps) > MAX_STEPS:
                self._send(400, {"error": f"max {MAX_STEPS} steps"}); return
            # Validate steps (same rules as legacy creator)
            clean_steps = []
            for s in steps:
                if not isinstance(s, dict):
                    self._send(400, {"error": "each step must be an object"}); return
                stype = (s.get("type") or "").strip()
                if stype not in ALLOWED_STEP_TYPES:
                    self._send(400, {"error": f"unknown step type: {stype}"}); return
                ins = s.get("inputs", {}) or {}
                if not isinstance(ins, dict):
                    self._send(400, {"error": "step inputs must be an object"}); return
                clean_steps.append({"type": stype, "inputs": {
                    str(k)[:80]: (str(v) if v is not None else "")[:MAX_STEP_INPUT]
                    for k, v in ins.items()
                }})
            slug = _agent_loader._slugify(name)
            agent_id = body.get("id") or slug.replace("-", "_")
            if not re.fullmatch(r"[a-z0-9_]{1,40}", agent_id or ""):
                self._send(400, {"error": "invalid agent id"}); return
            role = (body.get("role") or "worker").strip().lower()
            if role not in ("worker", "manager"):
                role = "worker"
            record = {
                "id": agent_id,
                "name": name,
                "section": section,
                "role": role,
                "description": description,
                "schedule": schedule,
                "enabled": True,
                "inputs": [str(x)[:80] for x in inputs if isinstance(x, str)][:8],
                "steps": clean_steps,
                "created_at": datetime.now().isoformat(),
                "body": (body.get("body") or "").strip(),
                "slug": slug,
            }
            if body.get("manager_review"):
                record["manager_review"] = True
            try:
                path_written = _agent_loader.write_agent(record)
            except _agent_loader.AgentLoadError as e:
                self._send(400, {"error": str(e)}); return
            reload_section_agents()
            self._send(200, {
                "ok": True,
                "agent": CUSTOM_AGENTS.get(agent_id, record),
                "path": str(path_written.relative_to(BASE_DIR.parent)),
            })
            return

        # ----- AGENT CORE OS — write/update a section file -----
        if p.startswith("/api/sections/") and "/files/" in p:
            # /api/sections/<name>/files/<rel>
            rest = p[len("/api/sections/"):]
            sname, _, rel = rest.partition("/files/")
            if not re.fullmatch(r"[a-z0-9_\-]{1,40}", sname or ""):
                self._send(400, {"error": "invalid section name"}); return
            sec_dir = SECTIONS_DIR / sname
            if not sec_dir.is_dir():
                self._send(404, {"error": "section not found"}); return
            if not rel:
                self._send(400, {"error": "missing file path"}); return
            # Only allow raw/, wiki/, output/ writes via this endpoint —
            # agents/ goes through /api/agents/create.
            allowed_top = ("raw/", "wiki/", "output/")
            if not any(rel.startswith(t) for t in allowed_top):
                self._send(400, {"error": "writes only allowed under raw/, wiki/, or output/"}); return
            target = (sec_dir / rel).resolve()
            if not str(target).startswith(str(sec_dir.resolve())):
                self._send(403, {"error": "path traversal blocked"}); return
            content = body.get("content", "")
            if not isinstance(content, str):
                self._send(400, {"error": "content must be a string"}); return
            if len(content) > 1024 * 1024:
                self._send(413, {"error": "content too large (max 1 MB)"}); return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._send(200, {"ok": True, "path": rel, "size": len(content)})
            return

        # ----- AGENT CORE OS — natural-language command parser -----
        # Local intent matching first (compile / audit / deploy / query),
        # falls through to an "ask Z.E.R.O." routed action otherwise.
        if p == "/api/command":
            text = (body.get("text") or "").strip()
            if not text:
                self._send(400, {"error": "text is required"}); return
            tl = text.lower()
            tokens = tl.split()
            if not tokens:
                self._send(400, {"error": "empty"}); return

            # Verb: compile / audit
            if tokens[0] in ("compile", "audit") and len(tokens) >= 2:
                section = tokens[1].strip("'\"`,.")
                if (SECTIONS_DIR / section).is_dir():
                    self._send(200, {
                        "type": tokens[0],
                        "section": section,
                        "message": f"Routed: {tokens[0]} {section}",
                    })
                    return
                self._send(200, {"type": "unknown", "message": f"unknown section '{section}'"}); return

            # Verb: deploy <agent_id>
            if tokens[0] == "deploy" and len(tokens) >= 2:
                aid = tokens[1].strip("'\"`,.").replace("-", "_")
                # Allow optional key=value pairs after
                params: dict[str, str] = {}
                for tok in tokens[2:]:
                    if "=" in tok:
                        k, _, v = tok.partition("=")
                        params[k.strip()] = v.strip()
                if aid in CUSTOM_AGENTS:
                    self._send(200, {"type": "agent_run", "agent_id": aid, "params": params,
                                     "message": f"Routed: deploy {aid}"})
                    return
                self._send(200, {"type": "unknown", "message": f"unknown agent '{aid}'"}); return

            # Verb: query <section> "<question>"
            if tokens[0] == "query" and len(tokens) >= 3:
                section = tokens[1].strip("'\"`,.")
                question = text[text.lower().find(section) + len(section):].strip().strip("'\"`")
                if (SECTIONS_DIR / section).is_dir() and question:
                    self._send(200, {"type": "query", "section": section, "question": question,
                                     "message": f"Routed: query {section}"})
                    return

            # Fallthrough: send to Z.E.R.O. as natural language
            self._send(200, {
                "type": "zero_chat",
                "text": text,
                "message": "No local verb matched — routing to Z.E.R.O.",
            })
            return

        # ----- AGENT CORE OS — manual reload of section-md agents -----
        if p == "/api/agents/reload":
            n = reload_section_agents()
            self._send(200, {"ok": True, "loaded": n}); return

        # ----- AGENT CORE OS — scheduler controls -----
        if p.startswith("/api/schedules/"):
            # /api/schedules/<aid>/pause | resume | fire
            parts = p[len("/api/schedules/"):].split("/")
            if len(parts) != 2:
                self._send(404, {"error": "bad path"}); return
            aid, action = parts
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", aid or ""):
                self._send(400, {"error": "invalid agent id"}); return
            if not SCHEDULER:
                self._send(503, {"error": "scheduler not running"}); return
            if action == "pause":
                self._send(200, {"ok": SCHEDULER.pause(aid)}); return
            if action == "resume":
                self._send(200, {"ok": SCHEDULER.resume(aid)}); return
            if action == "fire":
                tid = SCHEDULER.fire_now(aid)
                self._send(200, {"ok": bool(tid), "task_id": tid}); return
            self._send(400, {"error": "unknown action"}); return

        self._send(404, {"error": "not found", "path": p})

    def do_DELETE(self):
        # Same gates as POST.
        if not self._host_ok():
            self._send(400, {"error": "bad host"}); return
        if not self._csrf_ok():
            self._send(403, {"error": "cross-origin request blocked"}); return
        p = urlparse(self.path).path
        if not self._token_ok(p):
            self._send(401, {"error": "missing or invalid token"}); return

        # DELETE /api/agents/<id> — only deletes from in-memory + custom_agents.json.
        # Section-md agents are file-backed; deleting requires explicit file removal.
        if p.startswith("/api/agents/"):
            aid = p.split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", aid or ""):
                self._send(400, {"error": "invalid agent id"}); return
            if aid not in CUSTOM_AGENTS:
                self._send(404, {"error": "agent not found"}); return
            agent = CUSTOM_AGENTS[aid]
            # Section-md agents — refuse, advise filesystem removal.
            if agent.get("source") == "section_md":
                self._send(409, {"error": "section-md agents must be deleted from disk",
                                 "path": agent.get("source_path")}); return
            # Legacy agent — drop from registry + persist.
            del CUSTOM_AGENTS[aid]
            _save_custom_agents()
            self._send(200, {"ok": True, "deleted": aid}); return

        self._send(404, {"error": "not found", "path": p})

    def do_PATCH(self):
        # Same gates as POST.
        if not self._host_ok():
            self._send(400, {"error": "bad host"}); return
        if not self._csrf_ok():
            self._send(403, {"error": "cross-origin request blocked"}); return
        p = urlparse(self.path).path
        if not self._token_ok(p):
            self._send(401, {"error": "missing or invalid token"}); return
        body = self._read_json()

        # PATCH /api/agents/<id> — update a section-md agent's frontmatter.
        if p.startswith("/api/agents/"):
            aid = p.split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", aid or ""):
                self._send(400, {"error": "invalid agent id"}); return
            if not AGENT_LOADER_OK:
                self._send(500, {"error": "agent_loader unavailable"}); return
            patch = {}
            for k in ("name", "description", "schedule", "enabled", "inputs", "steps", "body"):
                if k in body:
                    patch[k] = body[k]
            try:
                path_written = _agent_loader.update_agent(aid, patch)
            except _agent_loader.AgentLoadError as e:
                self._send(404, {"error": str(e)}); return
            reload_section_agents()
            self._send(200, {
                "ok": True,
                "agent": CUSTOM_AGENTS.get(aid),
                "path": str(path_written.relative_to(BASE_DIR.parent)),
            })
            return

        self._send(404, {"error": "not found", "path": p})

    def do_GET(self):
        # Host guard — block DNS rebinding on read paths too.
        if not self._host_ok():
            self._send(400, {"error": "bad host", "host": self.headers.get("Host","")})
            return
        u = urlparse(self.path)
        p = u.path
        qs = parse_qs(u.query)

        # Launch-token gate on /api/*. The dashboard HTML and static assets
        # are open so the page can bootstrap.
        if not self._token_ok(p):
            self._send(401, {"error": "missing or invalid token"})
            return

        # --- Root: serve a successor product UI. Falls back to the legacy
        # dashboard if agentic-os/index.html doesn't exist yet (e.g. fresh
        # checkout before the UI has been added).
        if p in ("/", "/index.html"):
            try:
                if AGENTIC_OS_HTML.exists():
                    html = AGENTIC_OS_HTML.read_text(encoding="utf-8")
                else:
                    html = HTML_PATH.read_text(encoding="utf-8")
                inject = f'<script>window.__NEUROLINKED_TOKEN__={json.dumps(LAUNCH_TOKEN)};</script>'
                if "</head>" in html:
                    html = html.replace("</head>", inject + "</head>", 1)
                else:
                    html = inject + html
                self._send(200, html.encode("utf-8"), ctype="text/html; charset=utf-8")
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # --- /legacy → original ops-center dashboard for diagnostics.
        if p in ("/legacy", "/legacy/", "/legacy/index.html"):
            try:
                html = HTML_PATH.read_text(encoding="utf-8")
                inject = f'<script>window.__NEUROLINKED_TOKEN__={json.dumps(LAUNCH_TOKEN)};</script>'
                if "</head>" in html:
                    html = html.replace("</head>", inject + "</head>", 1)
                else:
                    html = inject + html
                self._send(200, html.encode("utf-8"), ctype="text/html; charset=utf-8")
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # --- Raw section file preview (for inline image / video / audio in run drawer)
        # /agentic-os/preview?path=sections/<section>/output/<file>
        if p == "/agentic-os/preview":
            try:
                rel = (qs.get("path", [""])[0] or "").lstrip("/").replace("\\", "/")
                if not rel.startswith("sections/"):
                    self._send(400, {"error": "path must be under sections/"}); return
                target = (BASE_DIR.parent / rel).resolve()
                if not str(target).startswith(str((BASE_DIR.parent / "sections").resolve())):
                    self._send(403, {"error": "path traversal blocked"}); return
                if not target.is_file():
                    self._send(404, {"error": "not found"}); return
                ext = target.suffix.lower()
                ctype = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
                    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                    ".pdf": "application/pdf",
                }.get(ext, "application/octet-stream")
                # Allow large files (videos can be tens of MB)
                self._send(200, target.read_bytes(), ctype=ctype)
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # --- Static assets for the new UI: /agentic-os/<path>
        # Path-traversal hardened: every served file must resolve to a
        # descendant of AGENTIC_OS_DIR.
        if p.startswith("/agentic-os/"):
            rel = p[len("/agentic-os/"):]
            try:
                target = (AGENTIC_OS_DIR / rel).resolve()
                if not str(target).startswith(str(AGENTIC_OS_DIR.resolve())):
                    self._send(403, {"error": "path traversal blocked"}); return
                if not target.is_file():
                    self._send(404, {"error": "not found", "path": p}); return
                ext = target.suffix.lower()
                ctype = {
                    ".html": "text/html; charset=utf-8",
                    ".js":   "application/javascript; charset=utf-8",
                    ".mjs":  "application/javascript; charset=utf-8",
                    ".css":  "text/css; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".svg":  "image/svg+xml",
                    ".png":  "image/png",
                    ".jpg":  "image/jpeg",
                    ".woff2":"font/woff2",
                    ".glsl": "text/plain; charset=utf-8",
                }.get(ext, "application/octet-stream")
                self._send(200, target.read_bytes(), ctype=ctype)
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # ---- Same-origin proxy for Z.E.R.O. frontend ----
        # Web Speech Recognition (webkitSpeechRecognition) is blocked in
        # cross-origin iframes by Chrome. Serving the Z.E.R.O. UI through
        # /zero/* on this same origin makes the iframe document same-origin
        # with the parent dashboard, unblocking the wake-word listener.
        # Only the HTML document needs to be same-origin; CSS/JS/WebSocket
        # still load from :8340 via an injected <base href>.
        if p == "/zero" or p.startswith("/zero/"):
            try:
                import urllib.request as _ur
                upstream_path = "/" if p in ("/zero", "/zero/") else p[len("/zero"):]
                upstream_url = f"http://127.0.0.1:8340{upstream_path}"
                if u.query:
                    upstream_url += "?" + u.query
                req = _ur.Request(upstream_url, method="GET")
                req.add_header("X-Forwarded-Host", "localhost:8010")
                with _ur.urlopen(req, timeout=10) as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "text/html; charset=utf-8")
                if "text/html" in ctype.lower():
                    text = body.decode("utf-8", errors="replace")
                    base_tag = '<base href="http://localhost:8340/">'
                    # Frontend JS uses `ws://${location.host}/ws` which now
                    # resolves to ws://localhost:8010/ws (wrong — we don't
                    # serve /ws here). Monkey-patch WebSocket to redirect
                    # 8010 → 8340 so the existing main.js works unchanged.
                    ws_redirect = (
                        '<script>'
                        '(function(){'
                        'var _W=window.WebSocket;'
                        'window.WebSocket=function(u,p){'
                        'try{'
                        'if(typeof u==="string"){'
                        'u=u.replace(/^ws:\\/\\/localhost:8010\\//,"ws://localhost:8340/");'
                        'u=u.replace(/^ws:\\/\\/127\\.0\\.0\\.1:8010\\//,"ws://127.0.0.1:8340/");'
                        '}'
                        '}catch(e){}'
                        'return p?new _W(u,p):new _W(u);'
                        '};'
                        'window.WebSocket.prototype=_W.prototype;'
                        '})();'
                        '</script>'
                    )
                    inject = base_tag + ws_redirect
                    if "<head>" in text:
                        text = text.replace("<head>", "<head>" + inject, 1)
                    else:
                        text = inject + text
                    # Cache-bust static asset URLs so a stale pre-CORS copy
                    # in Chrome's disk cache can't poison module loads.
                    import time as _t
                    _cb = str(int(_t.time() * 1000))
                    text = text.replace('?v=jarvis17', f'?v=jarvis17&_cb={_cb}')
                    text = text.replace('?v=brain3',   f'?v=brain3&_cb={_cb}')
                    body = text.encode("utf-8")
                self._send(200, body, ctype=ctype)
            except Exception as e:
                self._send(502, {"error": f"zero proxy: {e}"})
            return

        if p == "/api/brain/stats":
            self._send(200, BRAIN_STATS()); return

        if p == "/api/brain/query":
            q = (qs.get("q", [""])[0] or "").strip()
            if not q:
                self._send(400, {"error": "missing q"}); return
            results = brain_search(q, limit=8)
            self._send(200, {"q": q, "results": results, "count": len(results)}); return

        if p == "/api/calendar/today":
            self._send(200, {"events": CALENDAR()[:12], "source": "state.json"}); return

        if p == "/api/calendar/next":
            cal = CALENDAR()
            self._send(200, {"next": cal[0] if cal else None}); return

        if p == "/api/slack/inbox":
            self._send(200, {"messages": SLACK_INBOX()}); return

        if p == "/api/plan-my-day":
            # Slot every calendar event; flag HIGH-priority inbox items for action.
            cal = CALENDAR()
            plan = []
            for evt in cal[:8]:
                plan.append({
                    "slot": evt.get("start", "").split("·")[-1].strip() or "—",
                    "action": evt.get("title", "—"),
                    "why": "On calendar",
                })
            high_inbox = [m for m in SLACK_INBOX() if m.get("priority", "").upper() == "HIGH"]
            if high_inbox:
                plan.append({
                    "slot": "inbox",
                    "action": f"Reply to {len(high_inbox)} HIGH-priority message{'s' if len(high_inbox) != 1 else ''}",
                    "why": "Flagged by the inbox sentinel",
                })
            self._send(200, {"plan": plan, "generated_at": datetime.now().strftime("%H:%M:%S")}); return

        if p == "/api/agent/tasks":
            tasks = sorted(AGENT_TASKS.values(), key=lambda t: t.get("started_at",""), reverse=True)
            self._send(200, {"tasks": tasks[:40]}); return

        if p.startswith("/api/agent/tasks/"):
            tid = p.split("/")[-1]
            t = AGENT_TASKS.get(tid)
            if t: self._send(200, t)
            else: self._send(404, {"error":"not found"})
            return

        # ----- CUSTOM AGENTS -----
        if p == "/api/agents":
            self._send(200, {"agents": list(CUSTOM_AGENTS.values()),
                             "step_catalog": STEP_CATALOG,
                             "templates": AGENT_TEMPLATES,
                             "credential_kinds": CREDENTIAL_KINDS,
                             "credentials": vault_list_public()}); return

        if p == "/api/agents/catalog":
            self._send(200, {"step_catalog": STEP_CATALOG}); return

        if p == "/api/templates":
            self._send(200, {"templates": AGENT_TEMPLATES}); return

        # ----- CREDENTIAL VAULT -----
        if p == "/api/credentials":
            self._send(200, {"credentials": vault_list_public(), "kinds": CREDENTIAL_KINDS}); return

        if p == "/api/credentials/kinds":
            self._send(200, {"kinds": CREDENTIAL_KINDS}); return

        if p.startswith("/api/agents/") and not p.endswith("/run"):
            aid = p.split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", aid or ""):
                self._send(400, {"error": "invalid agent id"}); return
            a = CUSTOM_AGENTS.get(aid)
            if a: self._send(200, a)
            else: self._send(404, {"error": "agent not found"})
            return

        if p == "/api/security/status":
            self._send(200, {
                "bind_address": "127.0.0.1",
                "lan_blocked": True,
                "reachable_from": "this machine only",
                "vault_encryption": "Fernet (AES-128-CBC + HMAC-SHA256)",
                "vault_location": str(VAULT_FILE),
                "ports": {"ops": 8010, "brain": 8020, "jarvis": 8340},
            })
            return

        # ----- AGENT CORE OS — sections + section files -----
        if p == "/api/sections":
            sections = _agent_loader.list_sections() if AGENT_LOADER_OK else []
            self._send(200, {"sections": sections}); return

        if p.startswith("/api/sections/"):
            # /api/sections/<name>             → section info
            # /api/sections/<name>/files       → list files (optional ?folder=raw|wiki|output|agents)
            # /api/sections/<name>/files/<rel> → read file content
            parts = p[len("/api/sections/"):].split("/", 2)
            sname = parts[0] if parts else ""
            if not re.fullmatch(r"[a-z0-9_\-]{1,40}", sname or ""):
                self._send(400, {"error": "invalid section name"}); return
            sec_dir = SECTIONS_DIR / sname
            if not sec_dir.is_dir():
                self._send(404, {"error": "section not found"}); return

            # /api/sections/<name>
            if len(parts) == 1:
                section_md = (sec_dir / "_section.md")
                self._send(200, {
                    "name": sname,
                    "title": sname.title(),
                    "section_md": section_md.read_text(encoding="utf-8") if section_md.exists() else "",
                    "agents": [a for a in CUSTOM_AGENTS.values() if a.get("section") == sname],
                })
                return

            # /api/sections/<name>/files
            if len(parts) >= 2 and parts[1] == "files" and len(parts) == 2:
                folder = (qs.get("folder", [""])[0] or "").strip().lower()
                if folder not in ("raw", "wiki", "output", "agents"):
                    self._send(400, {"error": "folder must be raw|wiki|output|agents"}); return
                fdir = sec_dir / folder
                items = []
                if fdir.exists():
                    for fp in sorted(fdir.rglob("*")):
                        if fp.is_file() and fp.name != ".gitkeep":
                            items.append({
                                "path": str(fp.relative_to(fdir).as_posix()),
                                "name": fp.name,
                                "size": fp.stat().st_size,
                                "mtime": int(fp.stat().st_mtime),
                            })
                self._send(200, {"section": sname, "folder": folder, "files": items})
                return

            # /api/sections/<name>/files/<rel>  (rel may include subdirs but no traversal)
            if len(parts) == 3 and parts[1] == "files":
                rel = parts[2]
                # Path traversal defense
                target = (sec_dir / rel).resolve()
                if not str(target).startswith(str(sec_dir.resolve())):
                    self._send(403, {"error": "path traversal blocked"}); return
                if not target.is_file():
                    self._send(404, {"error": "file not found"}); return
                # Cap at 2 MB to keep responses sane
                size = target.stat().st_size
                if size > 2 * 1024 * 1024:
                    self._send(413, {"error": "file too large", "size": size}); return
                try:
                    content = target.read_text(encoding="utf-8")
                    self._send(200, {"path": rel, "content": content, "size": size})
                except UnicodeDecodeError:
                    self._send(415, {"error": "file is not text"})
                return

            self._send(404, {"error": "section endpoint not found", "path": p}); return

        # ----- AGENT CORE OS — events stream (SSE) -----
        if p == "/api/events/stream":
            self._send_event_stream(); return

        # ----- AGENT CORE OS — scheduler -----
        if p == "/api/schedules":
            jobs = SCHEDULER.list_jobs() if SCHEDULER else []
            self._send(200, {"scheduler_running": SCHEDULER is not None, "jobs": jobs}); return

        # ----- AGENT CORE OS — master index passthrough -----
        if p == "/api/master-index":
            mi = SECTIONS_DIR / "_master-index.md"
            self._send(200, {
                "exists": mi.exists(),
                "content": mi.read_text(encoding="utf-8") if mi.exists() else "",
            })
            return

        self._send(404, {"error": "not found", "path": p})


def main():
    # SECURITY: 127.0.0.1 ONLY — do not bind 0.0.0.0 or the LAN can reach the brain.
    # This is the fix for the breach where a phone on the WiFi hit :8010.
    # ThreadingHTTPServer so the SSE event stream (long-lived /api/events/stream)
    # doesn't block other requests. The handler is otherwise stateless.
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True

    # Boot the agent scheduler. Reads each agent's `schedule:` field and
    # registers a job that calls start_custom_agent_task at the cron time.
    global SCHEDULER
    if SCHEDULER_OK:
        try:
            SCHEDULER = AgentScheduler(
                custom_agents=CUSTOM_AGENTS,
                start_fn=start_custom_agent_task,
                state_path=STATE_PATH,
            )
            SCHEDULER.start()
        except Exception as _se:
            print(f"[scheduler] failed to start: {_se}", flush=True)

    print(f"NeuroLinked Ops Center → http://localhost:{PORT}/ (127.0.0.1 only — LAN blocked)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if SCHEDULER:
            SCHEDULER.shutdown()


if __name__ == "__main__":
    main()
