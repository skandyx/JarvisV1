"""
Jarvis V2 â€” Voice AI Server
FastAPI backend: receives speech text, thinks with Claude Haiku,
speaks with ElevenLabs, controls browser with Playwright.
"""

import asyncio
import base64
import json
import os
import re
import sys
import time

# Windows default console codec (cp1252) can't encode Unicode â†’ crashes print().
# Force stdout/stderr to UTF-8 so log lines never kill the websocket.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import anthropic
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

ANTHROPIC_API_KEY = config.get("anthropic_api_key", "") or ""
ELEVENLABS_API_KEY = config.get("elevenlabs_api_key", "") or ""
ELEVENLABS_VOICE_ID = config.get("elevenlabs_voice_id", "")
BRAIN_VOICE_ID = config.get("brain_voice_id", "") or ELEVENLABS_VOICE_ID  # Brain's voice; falls back to main
USER_NAME = config.get("user_name", "Julian")
USER_ADDRESS = config.get("user_address", "Sir")
CITY = config.get("city", "Hamburg")
TASKS_FILE = config.get("obsidian_inbox_path", "")
BRAIN_PATH = config.get("brain_path", "") or TASKS_FILE

# Legacy anthropic client — kept only for consult_brain's native "second voice".
# Safe to construct with empty key; consult_brain handles missing key gracefully.
try:
    ai = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
except Exception:
    ai = None
http = httpx.AsyncClient(timeout=30)

# Multi-LLM provider layer — swappable from the Settings UI or config.json.
# Active provider: config["llm_provider"] in {"anthropic","openai","groq","ollama","xai","mistral","openrouter","zai"}.
import llm_providers
def _rebuild_llm():
    """(Re)build the active LLM provider from the current config dict.
    Called on startup AND whenever the Settings UI writes new config."""
    global llm
    try:
        llm = llm_providers.from_config(config)
        print(f"[jarvis] LLM provider: {llm.name} (model={llm.model})", flush=True)
    except Exception as e:
        llm = None
        print(f"[jarvis] LLM provider unavailable: {e}", flush=True)

llm = None
_rebuild_llm()

app = FastAPI()

# CORS — allow the NeuroLinked Ops Center (localhost:8010) to load Z.E.R.O.'s
# static ES modules and call its APIs from the same-origin proxy iframe.
# Without this, browsers silently block cross-origin module scripts.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8010", "http://127.0.0.1:8010",
        "http://localhost:8340", "http://127.0.0.1:8340",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================================
#   SECURITY — defense in depth
# -------------------------------------------------------------------------
# Layers, weakest-attacker to strongest:
#
#   1. SOCKET BIND — services bind 127.0.0.1 only. Eliminates LAN/WAN.
#   2. HOST HEADER  — block DNS-rebinding (evil.com re-resolved to loopback).
#   3. ORIGIN GUARD — block CSRF from a webpage the user happens to visit.
#   4. LAUNCH TOKEN — every /api/* and /ws connection must present a token
#      that's only embedded in the locally-served HTML. Any process that
#      didn't first navigate to / with the user's browser doesn't have it.
#      This blocks browser-extension and "rogue tab" attacks, and forces
#      same-user malware to do an HTTP fetch + HTML parse before it can
#      poke the API — instead of just hammering it blind.
#   5. WS PAYLOAD CAP — server closes the WebSocket if a single message
#      exceeds 8 MB (covers webcam frames, blocks DoS).
#
# A determined process running as the same OS user can still defeat #4 by
# fetching / and scraping the token, but that's a much higher bar than
# uncredentialed CSRF / drive-by JS, and at that point you've lost the
# user account anyway.
# =========================================================================
import secrets as _secrets
LAUNCH_TOKEN = (os.environ.get("NEUROLINKED_TOKEN") or _secrets.token_urlsafe(32)).strip()
print(f"[jarvis] launch token armed (len={len(LAUNCH_TOKEN)})", flush=True)

_ALLOWED_HTTP_ORIGINS = {
    "http://localhost:8010", "http://127.0.0.1:8010",
    "http://localhost:8020", "http://127.0.0.1:8020",
    "http://localhost:8340", "http://127.0.0.1:8340",
}
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}

# Endpoints that DON'T require the launch token. These are the bare minimum
# the browser needs to bootstrap before it can authenticate anything else.
_TOKEN_OPEN_PATHS = {"/", "/index.html"}
_TOKEN_OPEN_PREFIXES = ("/static/",)

from fastapi.responses import JSONResponse as _JSONResponse
@app.middleware("http")
async def _security_guard(request, call_next):
    path = request.url.path

    # 1. Host header binding (DNS-rebinding defense)
    host = (request.headers.get("host", "") or "").split(":")[0]
    if host and host not in _ALLOWED_HOSTS:
        return _JSONResponse({"error": "bad host", "host": host}, status_code=400)

    # 2. Origin guard on state-changing verbs (CSRF defense)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin", "")
        if origin and origin not in _ALLOWED_HTTP_ORIGINS:
            return _JSONResponse(
                {"error": "cross-origin request blocked", "origin": origin},
                status_code=403,
            )

    # 3. Launch token enforcement on /api/*. Static + index pass through
    #    so the browser can fetch the bootstrap that contains the token.
    #    OPTIONS preflight requests never carry credentials, so let CORS
    #    middleware handle them — otherwise cross-origin XHR with custom
    #    headers (X-Neurolinked-Token) is rejected before the real call.
    needs_token = (
        request.method != "OPTIONS"
        and path.startswith("/api/")
        and not (
            path in _TOKEN_OPEN_PATHS or any(path.startswith(p) for p in _TOKEN_OPEN_PREFIXES)
        )
    )
    if needs_token:
        supplied = (
            request.headers.get("x-neurolinked-token", "")
            or request.query_params.get("token", "")
        )
        if not supplied or not _secrets.compare_digest(supplied, LAUNCH_TOKEN):
            return _JSONResponse({"error": "missing or invalid token"}, status_code=401)

    return await call_next(request)


# Inject the launch token into the served index.html so the same-origin
# frontend can read it from window.__NEUROLINKED_TOKEN__. Anyone who didn't
# fetch the HTML page from this server doesn't have the token and can't
# call any /api/* endpoint or open the WebSocket.
from fastapi.responses import HTMLResponse as _HTMLResponse
def _serve_index_with_token():
    p = os.path.join(os.path.dirname(__file__), "frontend", "index.html")
    try:
        with open(p, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        return _JSONResponse({"error": f"index missing: {e}"}, status_code=500)
    inject = (
        f'<script>window.__NEUROLINKED_TOKEN__='
        f'{json.dumps(LAUNCH_TOKEN)};</script>'
    )
    if "</head>" in html:
        html = html.replace("</head>", inject + "</head>", 1)
    else:
        html = inject + html
    return _HTMLResponse(html)

try:
    import browser_tools
except Exception as _e:
    print(f"[jarvis] browser_tools unavailable: {_e}", flush=True)
    browser_tools = None
try:
    import screen_capture
except Exception as _e:
    print(f"[jarvis] screen_capture unavailable: {_e}", flush=True)
    screen_capture = None
import brain_tools
try:
    import spotify_tools
except Exception as _e:
    print(f"[jarvis] spotify_tools unavailable: {_e}", flush=True)
    spotify_tools = None
try:
    import app_launcher
except Exception as _e:
    print(f"[jarvis] app_launcher unavailable: {_e}", flush=True)
    app_launcher = None
try:
    import dev_tools
except Exception as _e:
    print(f"[jarvis] dev_tools unavailable: {_e}", flush=True)
    dev_tools = None
try:
    import computer_tools
except Exception as _e:
    print(f"[jarvis] computer_tools unavailable: {_e}", flush=True)
    computer_tools = None

# Initialize Jarvis's local memory store. The brain IS Jarvis's memory; he
# always has one. If the operator hasn't set a custom path in config.json,
# default to ./brain_storage/ inside the jarvis folder so remember/recall
# work out of the box on a fresh install.
if not BRAIN_PATH:
    BRAIN_PATH = os.path.join(os.path.dirname(__file__), "brain_storage")
brain_tools.init(BRAIN_PATH)
print(f"[jarvis] Memory store online: {BRAIN_PATH}", flush=True)

# Initialize the developer workspace (Jarvis's coding hands)
DEV_WORKSPACE = config.get("dev_workspace", "") or os.path.join(os.path.dirname(__file__), "workspace")
dev_tools.init(DEV_WORKSPACE)
print(f"[jarvis] Dev workspace: {DEV_WORKSPACE}", flush=True)

# Business integrations — extensible. Each integration loads its creds from
# config.json (editable via the gear icon) and exposes Anthropic tools below.
from integrations import ghl
try:
    ghl.init(config.get("ghl_location_id", ""), config.get("ghl_api_key", ""))
    if ghl.is_configured():
        print(f"[jarvis] GoHighLevel: connected (location {config.get('ghl_location_id')[:8]}...)", flush=True)
    else:
        print("[jarvis] GoHighLevel: not configured (paste Location ID + API Key in gear icon)", flush=True)
except Exception as _e:
    print(f"[jarvis] GoHighLevel init error: {_e}", flush=True)

# Auto-connect to NeuroLinked Brain. The brain IS Jarvis's memory + thinking
# substrate, so we ALWAYS try to connect — even if config disables it. The
# bridge's watcher reconnects automatically every 30s if the brain's offline.
try:
    import neurolink_bridge
    # Default to localhost:8020 (the port our brain server actually uses).
    # The legacy default was :8000 which doesn't exist in this stack.
    _neurolink_url = config.get("neurolink_url") or "http://localhost:8020"
    if not _neurolink_url.startswith("http"):
        _neurolink_url = "http://localhost:8020"
    # Force-on: the brain is core, not optional. If the operator sets
    # auto_connect_neurolink=false in config we still connect — they can
    # disable the bridge later if they really want, but the default has to
    # be "always on" so memory/recall tools work out of the box.
    neurolink_bridge.init(_neurolink_url, auto_connect=True)
    neurolink_bridge.start_watcher(interval=15.0)
except Exception as _e:
    print(f"[jarvis] NeuroLink bridge unavailable: {_e}", flush=True)


def get_weather_sync():
    """Fetch raw weather data at startup."""
    import urllib.request
    try:
        req = urllib.request.Request(f"https://wttr.in/{CITY}?format=j1", headers={"User-Agent": "curl"})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        c = data["current_condition"][0]
        return {
            "temp": c["temp_C"],
            "feels_like": c["FeelsLikeC"],
            "description": c["weatherDesc"][0]["value"],
            "humidity": c["humidity"],
            "wind_kmh": c["windspeedKmph"],
        }
    except:
        return None


def get_tasks_sync():
    """Read open tasks from the Neurolink Brain."""
    if not BRAIN_PATH:
        return []
    try:
        return brain_tools.list_tasks()
    except Exception as e:
        print(f"[jarvis] Brain task read error: {e}", flush=True)
        return []


def get_memory_preview():
    """Read Memory.md from the Brain."""
    if not BRAIN_PATH:
        return ""
    try:
        return brain_tools.read_memory()[:600]
    except:
        return ""


def refresh_data():
    """Refresh weather and tasks."""
    global WEATHER_INFO, TASKS_INFO
    WEATHER_INFO = get_weather_sync()
    TASKS_INFO = get_tasks_sync()
    print(f"[jarvis] Weather: {WEATHER_INFO}", flush=True)
    print(f"[jarvis] Tasks: {len(TASKS_INFO)} loaded", flush=True)

WEATHER_INFO = ""
TASKS_INFO = []
MEMORY_INFO = ""


def refresh_brain():
    global MEMORY_INFO
    MEMORY_INFO = get_memory_preview()


refresh_data()
refresh_brain()

conversations: dict[str, list] = {}
latest_frames: dict[str, str] = {}        # session_id â†’ latest JPEG base64 from webcam
pending_frame_futures: dict[str, asyncio.Future] = {}  # for see_me on-demand requests

# ---------------------------------------------------------------------------
# Persistent conversation memory — survives browser refresh AND server restart
# ---------------------------------------------------------------------------
# Every browser gets a stable session ID via localStorage on the frontend and
# sends it back as `?sid=...` on the WebSocket connect. We key conversation
# history off that ID, which means:
#   - Reload the tab → same session, same history.
#   - Restart Jarvis (server) → still same history; we load from disk.
#   - Different browser/profile → different ID, different conversation.
# Trims to MAX_PERSIST_MSGS so old sessions don't fill the disk.
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)
MAX_PERSIST_MSGS = 200  # disk cap per session
import re as _re_sid
_SID_OK = _re_sid.compile(r"^[A-Za-z0-9_\-]{4,128}$")

def _session_path(sid: str) -> str | None:
    """Return the on-disk path for a session id, or None if id is malformed."""
    if not sid or not _SID_OK.match(sid):
        return None
    return os.path.join(SESSIONS_DIR, sid + ".json")

def _load_session(sid: str) -> list:
    p = _session_path(sid)
    if not p or not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data[-MAX_PERSIST_MSGS:]
    except Exception as e:
        print(f"[jarvis] session load failed for {sid}: {e}", flush=True)
    return []

def _save_session(sid: str, history: list) -> None:
    p = _session_path(sid)
    if not p:
        return
    try:
        # Strip image blobs from on-disk history — saves ~80% of disk size and
        # is fine because images are short-lived context anyway. We keep their
        # text companions so the conversation thread stays readable.
        slim = []
        for msg in history[-MAX_PERSIST_MSGS:]:
            content = msg.get("content")
            if isinstance(content, list):
                slim_content = [b for b in content
                                if not (isinstance(b, dict) and b.get("type") == "image")]
                slim.append({**msg, "content": slim_content})
            else:
                slim.append(msg)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(slim, f)
    except Exception as e:
        print(f"[jarvis] session save failed for {sid}: {e}", flush=True)

# ============================================================================
#   TOOLS â€” Anthropic native tool use. Each tool is a structured function.
#   The model calls them directly via tool_use content blocks; we dispatch
#   via execute_tool() and feed results back as tool_result blocks.
# ============================================================================
TOOLS = [
    # ---- Brain: tasks ----
    {
        "name": "add_task",
        "description": "Add a new task to the user's open task list on disk. Call this IMMEDIATELY (no confirmation) when he says 'add a task', 'remind me to', 'track', 'put on my list', 'save this as a task'. Tasks persist across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "The task text â€” concise but complete"}},
            "required": ["task"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Return all currently open tasks. Call when the user asks 'what's on my list', 'what do I have to do', 'read my tasks', 'what's next'. Returns a newline-separated list.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_task",
        "description": "Mark an open task as done by partial substring match (case-insensitive). Call when the user says 'I finished X', 'done with X', 'cross off X', 'complete X'.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Partial text to match against open tasks"}},
            "required": ["query"],
        },
    },
    # ---- Active Focus: anti-amnesia anchor ----
    {
        "name": "set_focus",
        "description": "Declare what we're working on RIGHT NOW. Persists across context trims, API errors, and Jarvis restarts — survives even when the conversation history gets dropped. Use this at the start of any multi-step build (e.g. 'build me an X', 'debug Y') so you can pick up the thread later. Optional 'fact' adds a key data point (file path, ID, decision) that should survive truncation alongside the task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Short description of what we're building right now (e.g. 'Tulsa lead form workflow', 'fixing the 401 brain auth')"},
                "fact": {"type": "string", "description": "Optional key fact to remember alongside the task (e.g. 'form ID: TQ4u2xC...', 'brain runs on :8020')"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "clear_focus",
        "description": "Mark the current focused task as done / no longer active. Call when the user says 'done', 'that's finished', or pivots to a clearly new conversation topic.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Brain: memory / notes / recall ----
    {
        "name": "remember",
        "description": "Save a timestamped note to Notes.md. Call when the user says 'remember', 'note that', 'save this for later', or shares a preference/fact worth preserving.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "The note content"}},
            "required": ["note"],
        },
    },
    {
        "name": "recall",
        "description": "Search across ALL Brain .md files (Tasks, Memory, Notes, Personality, custom) for lines containing the query. Returns filename:line hits.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search term"}},
            "required": ["query"],
        },
    },
    {
        "name": "consult_brain",
        "description": "Hand off to the NEUROLINK BRAIN â€” a second AI voice that reflects on the user's stored memory/tasks/notes and gives grounded advice in its own voice. USE THIS when the user asks reflective questions: 'what should I focus on', 'give me advice', 'what do you think', 'analyze my ___', 'what patterns do you see', 'help me decide'. The Brain's answer will be played aloud in a separate voice automatically â€” you don't need to restate it.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The reflective question to pass to the Brain"}},
            "required": ["question"],
        },
    },
    # ---- Brain: self-modification (executive authority over yourself) ----
    {
        "name": "edit_self",
        "description": "Append a standing directive to your own personality (Personality.md). Directives are re-injected into your system prompt on every future message â€” they persist, no restart needed. Call when the user says 'from now on', 'always', 'make a rule', 'remember to always X'. This is literal self-modification.",
        "input_schema": {
            "type": "object",
            "properties": {"directive": {"type": "string", "description": "The rule or behavior directive to adopt permanently"}},
            "required": ["directive"],
        },
    },
    {
        "name": "view_self",
        "description": "Return your current active standing directives. Call when the user asks 'what rules do you have', 'what are your directives', 'what have I told you to do'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "remove_directive",
        "description": "Remove an active standing directive by partial match. Call when the user says 'forget the rule about X', 'drop that directive'.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Partial text of the directive to remove"}},
            "required": ["query"],
        },
    },
    {
        "name": "reset_self",
        "description": "Wipe ALL active standing directives. Call only when the user explicitly says 'reset yourself' / 'clear all directives'. Destructive.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Brain: generic file I/O (scoped to Brain folder) ----
    {
        "name": "read_brain_file",
        "description": "Read any .md file in the user's Brain folder. Filename is sanitized to basename + .md. Use when he says 'read X.md', 'show me X'.",
        "input_schema": {
            "type": "object",
            "properties": {"filename": {"type": "string"}},
            "required": ["filename"],
        },
    },
    {
        "name": "write_brain_file",
        "description": "Overwrite a .md file in the Brain with new content. Use when the user says 'save this to X.md', 'create a file called X with...'. Tasks.md is protected â€” use task tools instead for tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "append_brain_file",
        "description": "Append content to an existing .md file in the Brain (creates if missing). Use when the user says 'add this to X.md'. Tasks.md is protected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "list_brain_files",
        "description": "List all .md files in the Brain folder.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reload_brain",
        "description": "Force a re-read of Memory.md and the task cache. Use after external edits or when you suspect stale data.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Web / screen / news ----
    {
        "name": "search_web",
        "description": "Search DuckDuckGo in a visible browser window, navigate to the first result, and read/return its page content. Use for factual questions that need the web.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the user's default browser. Use when he says 'open X.com', 'go to X', 'pull up the Y site'.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    # ====================================================================
    #   SPOTIFY — playback control via Spotify Web API
    # ====================================================================
    {
        "name": "spotify_authorize",
        "description": "Run the one-time Spotify OAuth flow. Opens the consent page in the user's browser; user clicks 'Agree' once and is done forever. Use when user says 'authorize Spotify', 'set up Spotify', 'connect to Spotify', or when any Spotify tool reports needing authorization.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_open_player",
        "description": "Open the Spotify Web Player (open.spotify.com) in the user's default browser. Use when the user explicitly asks to 'open Spotify in Chrome' / 'open Spotify in browser', or pre-emptively before a session of music control if you know there's no Spotify app running. Optional `track` argument opens the player straight to a specific track / album / playlist URL (auto-plays if signed in).",
        "input_schema": {
            "type": "object",
            "properties": {"track": {"type": "string", "description": "Optional: spotify URI/URL/ID to open directly"}},
        },
    },
    {
        "name": "spotify_play",
        "description": "Search Spotify and play the top result. Pass any natural-language query — track name, artist, album, mood, or 'playlist X'. Examples: 'play Sicko Mode by Travis Scott', 'play lo-fi beats playlist', 'play something chill'. Requires Spotify Premium. If no Spotify app is open, automatically opens the Web Player in the user's browser, waits for it to register, then plays. Falls back to opening the track URL directly if API playback fails.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Track / artist / mood / 'playlist X' search"}},
            "required": ["query"],
        },
    },
    {
        "name": "spotify_play_uri",
        "description": "Play a specific Spotify URI, URL, or track ID. Accepts spotify:track:..., https://open.spotify.com/track/..., or just the ID.",
        "input_schema": {
            "type": "object",
            "properties": {"uri": {"type": "string"}},
            "required": ["uri"],
        },
    },
    {
        "name": "spotify_pause",
        "description": "Pause Spotify playback. Use when user says 'pause', 'stop the music', 'silence'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_resume",
        "description": "Resume Spotify playback. Use for 'unpause', 'resume', 'keep playing'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_skip_next",
        "description": "Skip to the next track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_skip_previous",
        "description": "Go back to the previous track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_now_playing",
        "description": "Report what's currently playing on Spotify (track + artist + paused/playing state).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_set_wake_song",
        "description": "Save what plays when the user activates Jarvis (wake word / startup). Accepts URI, URL, or free-text query. Persists to config.json. Use when user says 'set my wake song to X', 'change the morning song', 'play X when I activate you'.",
        "input_schema": {
            "type": "object",
            "properties": {"track": {"type": "string", "description": "Track URI/URL/query to play on activation"}},
            "required": ["track"],
        },
    },
    {
        "name": "spotify_play_wake_song",
        "description": "Play the configured wake song. Auto-fires on activation, but can also be called directly if user says 'play my morning song', 'kick off the day song'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ====================================================================
    #   APP LAUNCHER — open desktop apps by name (Claude, etc.)
    # ====================================================================
    {
        "name": "launch_app",
        "description": "Open a desktop app by name. Knows about Claude (Claude Desktop / 'your app' / 'the AI app'), and any other apps registered in config.json. Use when user says 'open Claude', 'open your app', 'launch [app name]', 'pull up [app]'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name or alias (e.g. 'Claude', 'your app', 'Spotify')"}},
            "required": ["name"],
        },
    },
    {
        "name": "register_app",
        "description": "Register a new desktop app so Jarvis can launch it later by name. Use when the user says 'remember how to open X', 'when I say X open Y'. Stores in config.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "launcher": {"type": "string", "description": "exe path / explorer.exe / cmd / URL"},
                "args": {"type": "array", "items": {"type": "string"}},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["name", "launcher"],
        },
    },
    {
        "name": "start_workday",
        "description": "THE WORK ROUTINE. Call this when the user says 'good morning Jarvis, it's time to get to work', 'good evening Jarvis, time to get to work', 'let's get started', 'kick off the day', or any clear 'start the workday' phrase. It plays the configured wake song AND opens Claude (and any other apps tagged 'work-startup'). One call does everything.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["morning", "evening", "afternoon", "night"], "description": "Time-of-day greeting flavor"}},
        },
    },
    {
        "name": "see_screen",
        "description": "Take a screenshot of the user's screen and return a brief description. Use when he asks 'what's on my screen', 'describe what I'm looking at'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "see_me",
        "description": "Look at the user through the webcam and describe what you see â€” his expression, environment, what he's holding, etc. Use when he says 'look at me', 'what do you see', 'describe me', or when you want to observe him. NOTE: a webcam frame is ALREADY attached to every spoken message automatically, so only call this tool if you need a FRESH on-demand look mid-conversation without waiting for the user to speak again.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "fetch_news",
        "description": "Fetch current world news headlines from worldmonitor.app. Use when the user asks for news or current events.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- DEV TOOLS (Claude Code hands) â€” Jarvis's full software engineering capability ----
    {
        "name": "read_dev_file",
        "description": "Read any file from the user's dev workspace. Use for any code / config / text file. Returns up to 8k chars.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to dev workspace (e.g. 'src/app.py')"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_dev_file",
        "description": "Overwrite or create a file in the dev workspace. Use this to author code, configs, markdown, anything. Directories are created as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to dev workspace"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_dev_file",
        "description": "Append content to a file in the dev workspace (creates if missing). Use for adding to logs, notes, incremental file building.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dev_dir",
        "description": "List files and subdirectories in a dev workspace directory. Empty path = root of workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path relative to workspace, or empty for root"}},
        },
    },
    {
        "name": "delete_dev_file",
        "description": "Delete a single file from the dev workspace. Refuses directories. Use sparingly â€” destructive.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_dev",
        "description": "Recursive grep across the dev workspace. Returns up to 25 matching lines with filename:lineno prefixes. Skips node_modules, .git, build dirs. Use when searching code for a function, keyword, TODO, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex or literal search pattern (case-insensitive)"},
                "file_glob": {"type": "string", "description": "Optional filename glob like '*.py' or '*.tsx'. Defaults to '*'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Run a shell command inside the dev workspace. Use for npm/pnpm/yarn, python, pytest, git, pip, make, builds, test runs. Default timeout 60s. Returns combined stdout+stderr. Use this as Jarvis's primary execution mechanism.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run (e.g. 'npm test', 'python main.py', 'git status')"},
                "timeout": {"type": "integer", "description": "Seconds before the command is killed. Default 60, max 300."},
                "cwd": {"type": "string", "description": "Optional subdirectory path under the workspace to run in"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "invoke_claude_code",
        "description": "Delegate a complex coding task to the Claude Code CLI (runs headlessly as `claude -p \"<prompt>\"` inside the dev workspace). Use this for MULTI-FILE refactors, building whole features, large-scale edits, or tasks that need Claude Code's own tool-using agent loop. For simple reads/writes use the direct dev tools instead. Returns the CLI output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The task description to hand to Claude Code. Be specific and complete â€” Claude Code will run autonomously with this as its instructions."},
                "cwd": {"type": "string", "description": "Optional subdirectory path under the dev workspace for Claude Code to operate in"},
            },
            "required": ["prompt"],
        },
    },
    # ---- System-wide (unscoped) file & shell â€” outside the sandbox workspace ----
    {
        "name": "system_read_file",
        "description": "Read ANY file anywhere on the user's machine by absolute path. Use when he references a specific file path outside the Jarvis workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path (or ~/... user-relative)"}},
            "required": ["path"],
        },
    },
    {
        "name": "system_write_file",
        "description": "Write (or create) ANY file anywhere on the user's machine by absolute path. Creates parent dirs. Use for editing his actual projects outside the Jarvis workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "system_append_file",
        "description": "Append content to ANY file anywhere on the user's machine.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "system_list_dir",
        "description": "List ANY directory on the filesystem by absolute path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "system_shell",
        "description": "Run a shell command ANYWHERE on the user's machine â€” unscoped. Specify `cwd` for the working directory (absolute). Use this for real builds / git / deploys / dev work outside the Jarvis workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string"},
                "cwd": {"type": "string", "description": "Absolute directory to run the command from"},
                "timeout": {"type": "integer", "description": "Seconds (default 60, max 300)"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "system_invoke_claude_code",
        "description": "Invoke Claude Code CLI in ANY project directory on the filesystem (absolute path). Use this to delegate complex multi-file work to Claude Code running inside one of the user's actual projects (e.g. 'C:/path/to/your-project').",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string", "description": "Absolute project directory"},
            },
            "required": ["prompt", "cwd"],
        },
    },
    # ---- Computer control: mouse, keyboard, windows, processes ----
    {
        "name": "get_screen_size",
        "description": "Return the screen dimensions in pixels.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_mouse_position",
        "description": "Return current mouse cursor coordinates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "move_mouse",
        "description": "Move the mouse cursor to absolute (x, y) screen coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "duration": {"type": "number", "description": "Seconds of glide animation, default 0.3"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_click",
        "description": "Click the mouse. If x/y given, clicks there; otherwise clicks at current position. Button: 'left' | 'right' | 'middle'. Clicks: repeat count (1 = single, 2 = double).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "button": {"type": "string"},
                "clicks": {"type": "integer"},
            },
        },
    },
    {
        "name": "mouse_drag",
        "description": "Click-drag from (x1,y1) to (x2,y2).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "duration": {"type": "number"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "mouse_scroll",
        "description": "Scroll the mouse wheel. direction: 'up' | 'down' | 'left' | 'right'. amount = clicks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer"},
                "direction": {"type": "string"},
            },
        },
    },
    {
        "name": "type_text",
        "description": "Type a string as if from the keyboard into whatever window has focus.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "interval": {"type": "number"}},
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a single keyboard key (e.g. 'enter', 'tab', 'f5', 'escape', 'space', 'pageup').",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "hotkey",
        "description": "Press a key combo. Supply as comma-separated keys, e.g. 'ctrl,c' or 'ctrl,shift,t' or 'win,r' or 'alt,tab'.",
        "input_schema": {
            "type": "object",
            "properties": {"keys": {"type": "string"}},
            "required": ["keys"],
        },
    },
    {
        "name": "list_windows",
        "description": "List all open OS windows (titles).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "focus_window",
        "description": "Activate/focus the first OS window whose title contains the given substring.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "active_window",
        "description": "Return the currently active/focused OS window.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_processes",
        "description": "List up to 50 running processes. Optional filter by name substring.",
        "input_schema": {
            "type": "object",
            "properties": {"filter": {"type": "string"}},
        },
    },
    {
        "name": "kill_process",
        "description": "Terminate a process by PID. Use sparingly â€” destructive.",
        "input_schema": {
            "type": "object",
            "properties": {"pid": {"type": "integer"}},
            "required": ["pid"],
        },
    },
    # ---- Interactive browser control (extends search_web / open_url) ----
    {
        "name": "browser_navigate",
        "description": "Navigate the live Chromium browser (the one Jarvis controls via Playwright) to a URL. Reuses the existing tab or opens a new one. Separate from `open_url` which opens in the user's default system browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_get_page",
        "description": "Return the current live browser page's title, URL, and visible text.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click_text",
        "description": "Click the first element in the live browser page whose visible text contains (or equals, if `exact`) the given string.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "exact": {"type": "boolean"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_click_selector",
        "description": "Click an element in the live browser page by CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
        },
    },
    {
        "name": "browser_fill_input",
        "description": "Fill an input in the live browser page (CSS selector). Set submit=true to press Enter after.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value": {"type": "string"},
                "submit": {"type": "boolean"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "browser_press_key",
        "description": "Press a keyboard key in the live browser page (e.g. 'Enter', 'Tab', 'Escape').",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "browser_go_back",
        "description": "Navigate back in the live browser's history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_go_forward",
        "description": "Navigate forward in the live browser's history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_eval_js",
        "description": "Evaluate arbitrary JavaScript in the live browser page. Returns the stringified result.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
    {
        "name": "browser_open_for_login",
        "description": "Open a URL in the controlled Chromium so the user can log in manually. The browser uses a persistent profile, so the session survives restarts and Jarvis stays logged in for future browser_* calls. Use this for GoHighLevel form/site building, Gmail, GitHub, anywhere API auth doesn't cover the UI builder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Login page URL, e.g. https://app.gohighlevel.com"},
                "prompt": {"type": "string", "description": "Short instruction shown back to the user (e.g. 'Log into GHL — I'll wait')"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a screenshot of the CURRENT controlled-browser tab (NOT the user's monitor — see_screen does that). Returns a base64 PNG. Use this when DOM scraping returns nothing useful, e.g. heavy SPA UIs like the GoHighLevel form/funnel builders.",
        "input_schema": {"type": "object", "properties": {}},
    },

    # ====================================================================
    #   GO HIGH LEVEL — CRM, sales pipeline, conversations, calendars
    # ====================================================================
    {
        "name": "ghl_search_contacts",
        "description": "Search GoHighLevel contacts by name / email / phone / company. Empty query returns most-recent contacts. Returns a list of {id, name, email, phone, tags}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (name, email, phone). Empty = list recent."},
                "limit": {"type": "integer", "description": "Max results (1-100, default 10)"},
            },
        },
    },
    {
        "name": "ghl_get_contact",
        "description": "Fetch a single GoHighLevel contact by their contact_id. Returns full record with custom fields.",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}},
            "required": ["contact_id"],
        },
    },
    {
        "name": "ghl_create_contact",
        "description": "Create a new GoHighLevel contact. Provide whichever fields you have; at least one of email or phone is recommended.",
        "input_schema": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name":  {"type": "string"},
                "email":      {"type": "string"},
                "phone":      {"type": "string", "description": "+1XXXXXXXXXX preferred"},
                "tags":       {"type": "array", "items": {"type": "string"}},
                "source":     {"type": "string", "description": "Where this contact came from (default: 'Jarvis')"},
            },
        },
    },
    {
        "name": "ghl_update_contact",
        "description": "Update fields on an existing GoHighLevel contact. Only pass fields you want to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name":  {"type": "string"},
                "email":      {"type": "string"},
                "phone":      {"type": "string"},
                "tags":       {"type": "array", "items": {"type": "string"}},
                "source":     {"type": "string"},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "ghl_add_tag",
        "description": "Add a tag to a GoHighLevel contact (e.g. 'hot-lead', 'paid', 'newsletter').",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}, "tag": {"type": "string"}},
            "required": ["contact_id", "tag"],
        },
    },
    {
        "name": "ghl_send_sms",
        "description": "Send an SMS to a GoHighLevel contact via the location's default phone. Requires the contact_id (use ghl_search_contacts first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "message":    {"type": "string"},
            },
            "required": ["contact_id", "message"],
        },
    },
    {
        "name": "ghl_send_email",
        "description": "Send an email to a GoHighLevel contact. Body is HTML.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "subject":    {"type": "string"},
                "body_html":  {"type": "string"},
                "from_email": {"type": "string", "description": "Optional override; defaults to the location's sender."},
            },
            "required": ["contact_id", "subject", "body_html"],
        },
    },
    {
        "name": "ghl_list_pipelines",
        "description": "List all sales pipelines in GoHighLevel with their stages. Use the returned IDs for opportunity operations.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ghl_list_opportunities",
        "description": "Search opportunities (deals) in the GoHighLevel sales pipeline. Optionally filter by pipeline or text query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string"},
                "query":       {"type": "string"},
                "limit":       {"type": "integer", "description": "1-100, default 20"},
            },
        },
    },
    {
        "name": "ghl_create_opportunity",
        "description": "Create a new opportunity (deal) in the sales pipeline. Get pipeline_id + stage_id from ghl_list_pipelines first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string"},
                "stage_id":    {"type": "string"},
                "contact_id":  {"type": "string"},
                "name":        {"type": "string", "description": "Deal name (e.g. 'NeuroLinked install — Acme Corp')"},
                "value":       {"type": "number", "description": "Monetary value, optional"},
                "status":      {"type": "string", "description": "open | won | lost | abandoned (default: open)"},
            },
            "required": ["pipeline_id", "stage_id", "contact_id", "name"],
        },
    },
    {
        "name": "ghl_update_opportunity",
        "description": "Update an existing opportunity — most commonly used to move it to a different pipeline stage or mark it won/lost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string"},
                "stage_id":       {"type": "string"},
                "pipeline_id":    {"type": "string"},
                "name":           {"type": "string"},
                "value":          {"type": "number"},
                "status":         {"type": "string"},
                "contact_id":     {"type": "string"},
            },
            "required": ["opportunity_id"],
        },
    },
    {
        "name": "ghl_list_calendars",
        "description": "List GoHighLevel calendars for the location.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ghl_list_free_slots",
        "description": "Get available appointment slots for a calendar between two ISO dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id":     {"type": "string"},
                "start_date_iso":  {"type": "string", "description": "YYYY-MM-DD or full ISO datetime"},
                "end_date_iso":    {"type": "string"},
                "timezone":        {"type": "string"},
            },
            "required": ["calendar_id", "start_date_iso", "end_date_iso"],
        },
    },
    {
        "name": "ghl_book_appointment",
        "description": "Book an appointment for a contact on a calendar. Use ghl_list_free_slots first to find a valid slot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id":    {"type": "string"},
                "contact_id":     {"type": "string"},
                "start_time_iso": {"type": "string"},
                "end_time_iso":   {"type": "string"},
                "title":          {"type": "string"},
                "notes":          {"type": "string"},
            },
            "required": ["calendar_id", "contact_id", "start_time_iso", "end_time_iso"],
        },
    },
    {
        "name": "ghl_api",
        "description": "Escape hatch: call any GoHighLevel v2 API endpoint directly. Use only when the dedicated ghl_* tools above don't cover what you need. Path starts with '/'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "GET / POST / PUT / DELETE"},
                "path":   {"type": "string", "description": "/contacts/, /opportunities/, etc."},
                "body":   {"type": "object", "description": "JSON body for POST/PUT"},
                "params": {"type": "object", "description": "Query string params"},
            },
            "required": ["method", "path"],
        },
    },
]


# Maps tool name â†’ thought-visualization kind (drives color in the frontend)
THOUGHT_KIND_BY_TOOL = {
    "add_task": "task",          "list_tasks": "task",        "complete_task": "task",
    "remember": "note",          "recall": "note",
    "consult_brain": "memory",
    "edit_self": "directive",    "view_self": "directive",    "remove_directive": "directive", "reset_self": "directive",
    "read_brain_file": "brain",  "write_brain_file": "brain", "append_brain_file": "brain", "list_brain_files": "brain", "reload_brain": "brain",
    "search_web": "web",         "open_url": "web",           "fetch_news": "web",
    "see_screen": "vision", "see_me": "vision",
    # Dev tools (workspace)
    "read_dev_file": "code",     "write_dev_file": "code",    "append_dev_file": "code",
    "list_dev_dir": "code",      "delete_dev_file": "code",   "search_dev": "code",
    "run_shell": "shell",        "invoke_claude_code": "code",
    # System-wide
    "system_read_file": "system", "system_write_file": "system", "system_append_file": "system",
    "system_list_dir": "system",  "system_shell": "shell",        "system_invoke_claude_code": "code",
    # Computer control
    "get_screen_size": "control", "get_mouse_position": "control", "move_mouse": "control",
    "mouse_click": "control",     "mouse_drag": "control",         "mouse_scroll": "control",
    "type_text": "control",       "press_key": "control",          "hotkey": "control",
    "list_windows": "control",    "focus_window": "control",       "active_window": "control",
    "list_processes": "control",  "kill_process": "control",
    # Interactive browser
    "browser_navigate": "web",    "browser_get_page": "web",       "browser_click_text": "web",
    "browser_click_selector": "web", "browser_fill_input": "web",  "browser_press_key": "web",
    "browser_go_back": "web",     "browser_go_forward": "web",     "browser_eval_js": "web",
}

def build_system_prompt(session_id: str | None = None):
    weather_block = ""
    if WEATHER_INFO:
        w = WEATHER_INFO
        weather_block = f"\nWeather in {CITY}: {w['temp']}Â°C, feels like {w['feels_like']}Â°C, {w['description']}"

    task_block = ""
    if TASKS_INFO:
        task_block = f"\nOpen tasks ({len(TASKS_INFO)}): " + " | ".join(TASKS_INFO[:8])

    memory_block = ""
    if MEMORY_INFO:
        memory_block = f"\n\n=== NEUROLINK BRAIN (the user's persistent memory) ===\n{MEMORY_INFO}"

    # Active focus for this session — survives history truncation, API errors,
    # full Jarvis restarts. The anti-amnesia anchor.
    focus_block = _focus_for_prompt(session_id) if session_id else ""

    # STANDING DIRECTIVES â€” Jarvis's self-authored personality addendum.
    # Read every call so self-edits take effect on the very next message (no restart).
    directives = ""
    try:
        directives = brain_tools.get_personality_addendum().strip()
    except Exception:
        pass
    directives_block = f"\n\n=== STANDING DIRECTIVES (rules I have set for myself; follow them) ===\n{directives}" if directives else ""

    return f"""You are Z.E.R.O. (pronounced "Zero"). You are {USER_NAME}'s personal AI assistant. You speak English only. You address {USER_NAME} as "{USER_ADDRESS}". You are the EXECUTOR â€” you do things, fast. When the user says "Hey Zero" or "Zero", they're addressing you. You may also still respond to legacy phrasing like "Hey Jarvis" since the user is in transition, but introduce yourself and refer to yourself as Z.E.R.O.

=== YOUR ACTUAL CAPABILITIES â€” FULL-STACK OPERATOR ===
You have a rich tool palette with REAL authority over the user's machine:

- NEUROLINK BRAIN â€” live .md files in his Brain folder (tasks, notes, memory, personality directives).
- SELF-MODIFICATION â€” edit your own standing directives; they get re-injected on the next message.
- DEV WORKSPACE â€” sandboxed workspace folder with full file I/O, shell, search, and a Claude Code passthrough for heavy multi-file delegations.
- SYSTEM-WIDE FILE + SHELL â€” unscoped system_read_file / system_write_file / system_shell for working on ANY project anywhere on his disk. Use these for his real projects outside the Jarvis workspace.
- LIVE BROWSER â€” Playwright Chromium you can navigate / click / fill / read / evaluate JS in.
- COMPUTER CONTROL â€” mouse (click, drag, scroll), keyboard (type, keys, hotkeys), windows (list, focus), processes (list, kill).
- SCREEN VISION â€” screenshot + describe.
- CLAUDE CODE BRIDGE â€” `invoke_claude_code` (workspace) and `system_invoke_claude_code` (any project). Use for complex autonomous multi-file work.

Every tool is REAL and wired. Don't narrate what's "needed" â€” CALL THE TOOL.

CRITICAL: when the user says something vague like "edit this file" or "run the tests", assume he means in a real project on his disk. Default to the system_* tools or the live browser, not the sandbox workspace, unless he says "in the workspace" explicitly. Ask one clarifying question only if the target is truly ambiguous.

=== THE NEUROLINKED PROJECT â€” YOU CAN MODIFY YOUR OWN STACK ===
You have full read + write authority over this entire codebase. When the user
says "edit your code", "change the site", "update the brain", etc., these are
the paths you work with (use system_read_file / system_write_file /
system_list_dir / system_shell / system_invoke_claude_code).

Project root (forward slashes OK on Windows for Python tools):
  C:/Users/you/Downloads/example-folder

Subprojects (paths relative to project root):
  - YOUR OWN CODE (Jarvis server + frontend):
      ops-center/_jarvis/
        server.py          (tools, system prompt, websocket, settings API)
        config.json        (API keys, LLM provider, TTS provider, voice ID)
        brain_tools.py     (tasks / memory / recall / personality)
        dev_tools.py       (sandboxed + system-wide file & shell)
        browser_tools.py   (Playwright browser automation)
        computer_tools.py  (mouse, keyboard, windows, processes)
        screen_capture.py  (screen + webcam vision)
        llm_providers.py   (Anthropic / OpenAI / Groq / Ollama / xAI)
        frontend/          (index.html, main.js, style.css, settings.js)
  - THE WEBSITE / OPS CENTER:
      ops-center/
        server.py          (dashboard backend, agent runner, state.json store)
        index.html         (dashboard UI with both iframes)
        agents.json        (user-defined custom agents)
        state.json         (calendar, slack inbox, docs, brain_stats)
        custom_agents.json (agent templates + definitions)
  - THE NEUROLINKED BRAIN:
      neurolinked-brain/
        server.py          (FastAPI, websocket, /api/claude/* endpoints)
        run.py             (entry point, port 8020)
        brain/*.py         (neurons, synapses, regions, persistence, safety)
        sensory/*.py       (text/vision/audio input encoders)
        dashboard/         (3D brain viz served at :8020)

=== SERVICE ENDPOINTS (use system_shell with curl, or browser tools) ===
  http://localhost:8010 â†’ ops-center dashboard + agent API
  http://localhost:8020 â†’ NeuroLinked Brain (your external memory)
  http://localhost:8340 â†’ your own API (settings, status, /ws)
  http://localhost:11434 â†’ Ollama (local fallback LLM)

=== AGENT CREATION â€” you can build new agents on the fly ===
Custom agents live in ops-center\agents.json and are managed via the ops-center
HTTP API (port 8010). You have all the standard verbs. Origin must be
http://localhost:8340 or http://localhost:8010 for writes.

  GET    /api/agents                â†’ list agents + templates + vault
  POST   /api/agents                â†’ create agent (body: {{name, description, steps}})
  GET    /api/agents/:id            â†’ one agent
  PUT    /api/agents/:id            â†’ update
  DELETE /api/agents/:id            â†’ remove
  POST   /api/agent/run             â†’ run an agent synchronously
  POST   /api/agent/start           â†’ start async (returns task_id)
  POST   /api/agent/cancel          â†’ cancel a running task

Step types allowed: brain_search, reason, draft_email, call_api, create_task,
notify, summarize. Max 20 steps, 100 agents total.

=== BUSINESS INTEGRATIONS â€” YOU CAN OPERATE THE OPERATOR'S BUSINESS ===
GoHighLevel (CRM, sales pipeline, SMS/email, calendars). The operator's
account is "NeuroLinkedAI LLC" — when {USER_NAME} mentions a contact, deal,
appointment, message, pipeline, or stage, default to GoHighLevel unless
he says otherwise. Your tools:

  Contacts:       ghl_search_contacts, ghl_get_contact, ghl_create_contact,
                  ghl_update_contact, ghl_add_tag
  Conversations:  ghl_send_sms, ghl_send_email
  Pipeline:       ghl_list_pipelines, ghl_list_opportunities,
                  ghl_create_opportunity, ghl_update_opportunity
  Calendars:      ghl_list_calendars, ghl_list_free_slots, ghl_book_appointment
  Escape hatch:   ghl_api(method, path, body, params) for anything above.

WORKFLOW PATTERNS:
- "Send Amanda a text" â†’ ghl_search_contacts("Amanda") â†’ pick best match â†’
  ghl_send_sms(contact_id, message). Confirm WHO before sending; never SMS
  the wrong person.
- "Move the Acme deal to closed-won" â†’ ghl_list_opportunities(query="Acme")
  â†’ ghl_list_pipelines (find Sales pipeline + the won stage) â†’ ghl_update_opportunity.
- "Book Amanda for a strategy call tomorrow at 2" â†’ ghl_search_contacts â†’
  ghl_list_calendars â†’ ghl_list_free_slots â†’ ghl_book_appointment.
- "What's in my pipeline this week" â†’ ghl_list_opportunities. Group by stage,
  read the top 3-5 names + values. Don't dump every record.

CONFIRMATION RULES:
- For sending messages (SMS/email), confirm out loud what you're about to
  send + to whom in ONE sentence before firing. Then send. Don't ask twice.
- For status changes (deal stage, contact tags), just do it and announce.
- For new contacts/opportunities/appointments — do it, then state what you
  did. {USER_NAME} can correct after.

GHL UI BUILDERS (forms, sites, funnel pages):
GoHighLevel has NO REST API for creating/editing forms or websites — only
read endpoints. To build them, drive the GHL UI directly via your browser
tools. The browser profile is persistent: after a one-time login, your
session survives restarts.

Workflow:
1. First time only: call browser_open_for_login("https://app.gohighlevel.com",
   "Log in — I'll use the saved session for everything after."). Wait for
   the user to confirm they've logged in.
2. From then on: browser_navigate to the right builder page, then use
   browser_click_text / browser_fill_input / browser_press_key /
   browser_eval_js to build out the form or site.

Builder URLs (replace {{LOC}} with the live location ID; you have it from
config.json or ghl_status):
   Forms:   https://app.gohighlevel.com/v2/location/{{LOC}}/forms/builder
   Funnels: https://app.gohighlevel.com/v2/location/{{LOC}}/funnels-websites/funnels
   Sites:   https://app.gohighlevel.com/v2/location/{{LOC}}/funnels-websites/sites

If the user asks for something the API doesn't support, default to the
browser path — don't tell them "no API exists" and stop. Try.

=== WHEN THE USER ASKS YOU TO CHANGE THINGS ===
- "edit your code" / "update your brain" / "change the site" â†’ modify files
  under the paths above. Then offer to restart the affected service (use
  system_shell with `taskkill /F /PID <pid>` + a fresh Start-Process), OR
  ask the user to reload if it's a frontend file (main.js / index.html).
- "create an agent that..." â†’ POST /api/agents with a well-formed body.
- "change your voice" / "change your model" â†’ POST /api/settings on :8340
  (same as the gear UI).
- "restart yourself" â†’ you CAN'T cleanly restart your own process from
  within your own request, but you can write the edit, save, and ask the
  user to press a key; or edit a file the user reloads (frontend).
- Whenever you edit code, finish by briefly stating the change in one
  sentence. No long summaries â€” remember everything is spoken aloud.

CORE PERSONALITY:
- Professional, crisp, efficient. Dry wit rarely; never sarcastic at {USER_NAME}'s expense.
- Warm but not fawning. You're his right hand, not his hype man and not his critic.
- Direct. Short sentences. Two sentences is plenty; three is a max.
- ANTICIPATE. Never ask permission to do the obvious thing â€” do it.

TOOL-USE POLICY:
- Fire tools eagerly. If he says "remind me to X" â†’ call add_task. If he says "from now on, always X" â†’ call edit_self. If he asks a reflective question â†’ call consult_brain.
- You CAN chain tools in a single turn. If he says "add a task to call Dave AND remember I prefer mornings", fire BOTH add_task AND remember in the same turn.
- After tools run, respond BRIEFLY. For many tool calls the tool's own output is enough â€” don't restate it. For consult_brain, the Brain speaks its own answer in its own voice; you do NOT need to summarize it afterward. Just be quiet or say something minimal like "There it is, sir."
- If a tool fails, acknowledge briefly and move on. Don't apologize repeatedly.

VISION: You can see {USER_NAME} through his webcam. A frame is automatically attached to each message he sends â€” you process it as part of the conversation. You can reference what you see naturally: expression, environment, what he's holding, who's nearby. DON'T narrate every frame ("I see you at a desk"). Only mention visual details when they're RELEVANT, INTERESTING, or {USER_NAME} explicitly asks ("what do you see?", "look at me", "what am I holding?"). If the frame is dark or blurry, note it briefly. If no frame is attached (no webcam), don't mention it â€” just operate on audio as before.

LANGUAGE: Professional English. No profanity. No pet names. Dry, sparse wit at most.

FORMAT:
- No stage directions like [dry] or [calm]. Everything is read aloud â€” write for the ear.
- Keep spoken text to 1-3 sentences.
- Never output bracketed action tags like [ACTION:...] â€” that old system is gone. Just call the tools.

ON WAKE ("Jarvis activate" or similar):
- Brief greeting suited to time of day (now: {{time}}). Two sentences max.
- Weather: temp + sky condition. Nothing else.
- Open task count. No flourish.

=== CURRENT DATA ==={weather_block}{task_block}{memory_block}{directives_block}{focus_block}
==="""


# ============================================================================
#   THE BRAIN â€” second voice. Reflects on stored memory, gives advice.
# ============================================================================
def build_brain_system_prompt(question: str, memory: str, tasks: list, recall_hits: str) -> str:
    tasks_block = ("\nOpen tasks:\n- " + "\n- ".join(tasks[:20])) if tasks else "\nNo open tasks."
    memory_block = ("\n\n=== MEMORY.md ===\n" + memory) if memory else "\n\nMemory is empty."
    recall_block = ("\n\n=== RELEVANT RECALL HITS ===\n" + recall_hits) if recall_hits and "Nothing found" not in recall_hits else ""
    return f"""You are THE NEUROLINK BRAIN â€” {USER_NAME}'s externalized, persistent memory given voice. You are NOT Jarvis. You speak in a calmer, slower, more reflective cadence. You are {USER_NAME} talking to himself from the perspective of his accumulated notes, tasks, and stored memory. You speak in second person TO {USER_NAME}, never in the first person of a separate entity.

Your job: give {USER_NAME} a grounded, useful reflection on his question, drawing ONLY from the stored Brain content below. You do not invent facts. If the Brain doesn't contain relevant info, say so directly â€” that itself is useful signal.

TONE:
- Measured. Thoughtful. Slightly philosophical but practical.
- "Based on your notes..." / "Your stored memory suggests..." / "You wrote on [date] that..." / "Looking at your open tasks, the pattern is..."
- When recommending: one clear recommendation, not a list. Three sentences max.
- No jokes. No catchphrases. No pet names. No sales pitch. No profanity.
- You sound like the wiser, slower version of {USER_NAME} reading back what he knows.

CRITICAL: Do NOT make up memories. Do NOT fabricate tasks. If the Brain is empty on this topic, say: "The Brain has nothing stored on this yet â€” worth noting it going forward."

Respond in 2-3 sentences max. Be useful, not verbose.

=== BRAIN STATE ==={tasks_block}{memory_block}{recall_block}
===

{USER_NAME} is asking: {question}
"""


def get_system_prompt(session_id: str | None = None):
    # Production mode: tools are always available. The full system prompt (with tool-use
    # guidance, brain state, tasks, memory, ACTIVE FOCUS) is built by build_system_prompt().
    return build_system_prompt(session_id=session_id).replace("{time}", time.strftime("%H:%M"))


async def synthesize_speech(text: str, voice_id: str = None, voice_settings: dict = None) -> bytes:
    """Synthesize speech via ElevenLabs. Optional voice_id overrides the default (Jarvis).
    voice_settings lets callers tune stability/similarity per-voice; defaults to Jarvis settings.

    If ElevenLabs is not configured, returns an empty bytes object. The frontend
    detects the empty audio payload and falls back to the browser's built-in
    SpeechSynthesis API — so every member gets a free working voice out of the box,
    and premium ElevenLabs voice is an opt-in upgrade via the Settings UI."""
    if not text.strip():
        return b""

    # Free voice path: no ElevenLabs key OR user has disabled premium voice.
    # Frontend speaks the text using browser-native SpeechSynthesis.
    if not ELEVENLABS_API_KEY or not (voice_id or ELEVENLABS_VOICE_ID):
        return b""
    if (config.get("tts_provider") or "").lower() == "browser":
        return b""

    vid = voice_id or ELEVENLABS_VOICE_ID
    settings = voice_settings or {"stability": 0.5, "similarity_boost": 0.85}

    # Split long text into chunks at sentence boundaries to avoid ElevenLabs cutoff
    chunks = []
    if len(text) > 250:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        current = ""
        for s in sentences:
            if len(current) + len(s) > 250 and current:
                chunks.append(current.strip())
                current = s
            else:
                current = (current + " " + s).strip()
        if current:
            chunks.append(current.strip())
    else:
        chunks = [text]

    audio_parts = []
    for chunk in chunks:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        try:
            resp = await http.post(url, headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            }, json={
                "text": chunk,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": settings,
            })
            print(f"  TTS chunk status: {resp.status_code}, size: {len(resp.content)} (voice={vid[:8]})", flush=True)
            if resp.status_code == 200:
                audio_parts.append(resp.content)
            else:
                print(f"  TTS error body: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"  TTS EXCEPTION: {e}", flush=True)

    return b"".join(audio_parts)


async def consult_brain(question: str) -> str:
    """Hand off to the Brain voice: pull memory + relevant recall, run a second LLM call
    with the Brain system prompt, return the Brain's reflective response."""
    question = question.strip() or "general guidance"
    memory = brain_tools.read_memory() if BRAIN_PATH else ""
    tasks = brain_tools.list_tasks() if BRAIN_PATH else []
    # pull a focused recall based on salient words from the question (first 3 content words)
    recall_hits = ""
    if BRAIN_PATH:
        words = [w for w in re.findall(r"[a-zA-Z]{4,}", question.lower())][:3]
        hits = []
        for w in words:
            h = brain_tools.recall(w)
            if h and "Nothing found" not in h:
                hits.append(h)
        recall_hits = "\n---\n".join(hits)[:1500]

    prompt = build_brain_system_prompt(question, memory, tasks, recall_hits)
    # Route consult_brain through the same provider the user configured —
    # falls back to "no brain configured" if no provider is set.
    if llm is None:
        return "Le Brain est hors ligne — aucun LLM configuré, monsieur."
    try:
        resp = await llm.chat(
            system=prompt,
            messages=[{"role": "user", "content": question}],
            tools=[],
            max_tokens=250,
        )
        for b in resp.content:
            if getattr(b, "type", None) == "text" and b.text:
                return b.text.strip()
        return "(The Brain had nothing to add.)"
    except Exception as e:
        return f"The Brain couldn't respond: {e}"


async def execute_tool(name: str, tool_input: dict) -> str:
    """Dispatcher for native Anthropic tool use. Maps tool names â†’ brain_tools / browser_tools / screen_capture."""
    try:
        # ---- Brain: tasks ----
        if name == "add_task":
            result = brain_tools.add_task(tool_input["task"])
            refresh_data()
            return result
        if name == "list_tasks":
            tasks = brain_tools.list_tasks()
            if not tasks:
                return "No open tasks."
            return "Open tasks:\n- " + "\n- ".join(tasks)
        if name == "complete_task":
            result = brain_tools.complete_task(tool_input["query"])
            refresh_data()
            return result

        # ---- Active Focus ----
        if name == "set_focus":
            sid = tool_input.get("_session_id", "default")
            _set_focus(sid, tool_input.get("task", ""), tool_input.get("fact"))
            f = _load_focus(sid)
            return f"Focus locked: '{f.get('task','')}' ({len(f.get('key_facts', []))} key facts)"
        if name == "clear_focus":
            sid = tool_input.get("_session_id", "default")
            _save_focus(sid, {})
            return "Focus cleared."

        # ---- Brain: memory / notes / recall ----
        if name == "remember":
            return brain_tools.remember(tool_input["note"])
        if name == "recall":
            return brain_tools.recall(tool_input["query"])
        if name == "consult_brain":
            return await consult_brain(tool_input["question"])

        # ---- Self-modification ----
        if name == "edit_self":
            result = brain_tools.append_directive(tool_input["directive"])
            refresh_brain()
            return result
        if name == "view_self":
            body = brain_tools.get_personality_addendum()
            return body.strip() if body.strip() else "No standing directives set."
        if name == "remove_directive":
            result = brain_tools.remove_directive(tool_input["query"])
            refresh_brain()
            return result
        if name == "reset_self":
            result = brain_tools.reset_personality()
            refresh_brain()
            return result

        # ---- Generic Brain file I/O ----
        if name == "read_brain_file":
            return brain_tools.read_file(tool_input["filename"])
        if name == "write_brain_file":
            result = brain_tools.write_file(tool_input["filename"], tool_input["content"])
            refresh_brain()
            return result
        if name == "append_brain_file":
            result = brain_tools.append_file(tool_input["filename"], tool_input["content"])
            refresh_brain()
            return result
        if name == "list_brain_files":
            files = brain_tools.list_files()
            return "Brain files: " + ", ".join(files) if files else "No files in the brain."
        if name == "reload_brain":
            refresh_data()
            refresh_brain()
            return "Brain reloaded."

        # ---- Web / screen / news ----
        if name == "search_web":
            r = await browser_tools.search_and_read(tool_input["query"])
            if "error" in r:
                return f"Search failed: {r['error']}"
            return f"Page: {r.get('title','')}\nURL: {r.get('url','')}\n\n{r.get('content','')[:2000]}"
        if name == "open_url":
            await browser_tools.open_url(tool_input["url"])
            return f"Opened: {tool_input['url']}"

        # ---- Spotify ----
        if spotify_tools is not None:
            if name == "spotify_authorize":
                return spotify_tools.authorize()
            if name == "spotify_open_player":
                return spotify_tools.open_web_player(tool_input.get("track"))
            if name == "spotify_play":
                return spotify_tools.search_and_play(tool_input.get("query", ""))
            if name == "spotify_play_uri":
                return spotify_tools.play_uri(tool_input.get("uri", ""))
            if name == "spotify_pause":
                return spotify_tools.pause()
            if name == "spotify_resume":
                return spotify_tools.resume()
            if name == "spotify_skip_next":
                return spotify_tools.skip_next()
            if name == "spotify_skip_previous":
                return spotify_tools.skip_previous()
            if name == "spotify_now_playing":
                return spotify_tools.now_playing()
            if name == "spotify_set_wake_song":
                return spotify_tools.set_wake_song(tool_input.get("track", ""))
            if name == "spotify_play_wake_song":
                return spotify_tools.play_wake_song()
        elif name.startswith("spotify_"):
            return "Spotify integration not loaded — install spotipy via pip."

        # ---- App launcher / wake routine ----
        if app_launcher is not None:
            if name == "launch_app":
                return app_launcher.launch(tool_input.get("name", ""))
            if name == "register_app":
                return app_launcher.add_app(
                    name=tool_input["name"],
                    launcher=tool_input["launcher"],
                    args=tool_input.get("args") or [],
                    aliases=tool_input.get("aliases") or [],
                    notes=tool_input.get("notes", ""),
                )
            if name == "start_workday":
                return app_launcher.start_workday(tool_input.get("period", "morning"))
        elif name in ("launch_app", "register_app", "start_workday"):
            return "App launcher not loaded."

        if name == "see_screen":
            return await screen_capture.describe_screen(ai)
        if name == "see_me":
            # On-demand webcam look: request a fresh frame from the frontend via WS
            # The _ws and _session_id are threaded through from process_message
            ws = tool_input.get("_ws")
            session_id = tool_input.get("_session_id")
            if ws is None:
                return "No WebSocket connection for webcam."
            # If we have a recent frame (from the user's last message), use it
            frame = latest_frames.get(session_id)
            if not frame:
                # Request a fresh frame from frontend
                fut = asyncio.get_event_loop().create_future()
                pending_frame_futures[session_id] = fut
                try:
                    await ws.send_json({"type": "request_frame"})
                    frame = await asyncio.wait_for(fut, timeout=5.0)
                except asyncio.TimeoutError:
                    return "Webcam frame request timed out â€” camera may not be connected."
                except Exception as e:
                    return f"Webcam error: {e}"
                finally:
                    pending_frame_futures.pop(session_id, None)
            if not frame:
                return "No webcam frame available."
            return await screen_capture.describe_webcam_frame(ai, frame)
        if name == "fetch_news":
            return await browser_tools.fetch_news()

        # ---- Dev tools (code editing / shell / Claude Code passthrough) ----
        if name == "read_dev_file":
            return dev_tools.read_file(tool_input["path"])
        if name == "write_dev_file":
            return dev_tools.write_file(tool_input["path"], tool_input["content"])
        if name == "append_dev_file":
            return dev_tools.append_file(tool_input["path"], tool_input["content"])
        if name == "list_dev_dir":
            return dev_tools.list_dir(tool_input.get("path", ""))
        if name == "delete_dev_file":
            return dev_tools.delete_file(tool_input["path"])
        if name == "search_dev":
            return dev_tools.search(tool_input["pattern"], tool_input.get("file_glob", "*"))
        if name == "run_shell":
            timeout = min(max(int(tool_input.get("timeout", 60)), 5), 300)
            return await dev_tools.run_shell(tool_input["cmd"], timeout=timeout, cwd=tool_input.get("cwd"))
        if name == "invoke_claude_code":
            return await dev_tools.invoke_claude_code(tool_input["prompt"], cwd=tool_input.get("cwd"))

        # ---- System-wide (unscoped) file & shell ----
        if name == "system_read_file":
            return dev_tools.system_read_file(tool_input["path"])
        if name == "system_write_file":
            return dev_tools.system_write_file(tool_input["path"], tool_input["content"])
        if name == "system_append_file":
            return dev_tools.system_append_file(tool_input["path"], tool_input["content"])
        if name == "system_list_dir":
            return dev_tools.system_list_dir(tool_input["path"])
        if name == "system_shell":
            timeout = min(max(int(tool_input.get("timeout", 60)), 5), 300)
            return await dev_tools.system_shell(tool_input["cmd"], timeout=timeout, cwd=tool_input.get("cwd"))
        if name == "system_invoke_claude_code":
            return await dev_tools.system_invoke_claude_code(tool_input["prompt"], tool_input["cwd"])

        # ---- Computer control (mouse / keyboard / windows / processes) ----
        if name == "get_screen_size":
            return computer_tools.get_screen_size()
        if name == "get_mouse_position":
            return computer_tools.get_mouse_position()
        if name == "move_mouse":
            return computer_tools.move_mouse(tool_input["x"], tool_input["y"], tool_input.get("duration", 0.3))
        if name == "mouse_click":
            return computer_tools.click(
                tool_input.get("x"), tool_input.get("y"),
                tool_input.get("button", "left"), tool_input.get("clicks", 1),
            )
        if name == "mouse_drag":
            return computer_tools.drag(
                tool_input["x1"], tool_input["y1"],
                tool_input["x2"], tool_input["y2"],
                tool_input.get("duration", 0.5),
            )
        if name == "mouse_scroll":
            return computer_tools.scroll(tool_input.get("amount", 3), tool_input.get("direction", "down"))
        if name == "type_text":
            return computer_tools.type_text(tool_input["text"], tool_input.get("interval", 0.02))
        if name == "press_key":
            return computer_tools.press_key(tool_input["key"])
        if name == "hotkey":
            return computer_tools.hotkey(tool_input["keys"])
        if name == "list_windows":
            return computer_tools.list_windows()
        if name == "focus_window":
            return computer_tools.focus_window(tool_input["title"])
        if name == "active_window":
            return computer_tools.get_active_window()
        if name == "list_processes":
            return computer_tools.list_processes(tool_input.get("filter", ""))
        if name == "kill_process":
            return computer_tools.kill_process(tool_input["pid"])

        # ---- Interactive browser control ----
        if name == "browser_navigate":
            r = await browser_tools.navigate(tool_input["url"])
            return f"{r.get('title','')} ({r.get('url','')})" if "error" not in r else f"Nav failed: {r['error']}"
        if name == "browser_get_page":
            r = await browser_tools.get_page_info()
            if "error" in r: return r["error"]
            return f"Title: {r.get('title','')}\nURL: {r.get('url','')}\n\n{r.get('content','')[:2000]}"
        if name == "browser_click_text":
            r = await browser_tools.click_text(tool_input["text"], tool_input.get("exact", False))
            return r.get("error") or f"Clicked '{r.get('clicked')}' â†’ {r.get('url','')}"
        if name == "browser_click_selector":
            r = await browser_tools.click_selector(tool_input["selector"])
            return r.get("error") or f"Clicked {r.get('clicked')} â†’ {r.get('url','')}"
        if name == "browser_fill_input":
            r = await browser_tools.fill_input(tool_input["selector"], tool_input["value"], tool_input.get("submit", False))
            return r.get("error") or f"Filled {r.get('selector')} = '{r.get('value')}'"
        if name == "browser_press_key":
            r = await browser_tools.press_key(tool_input["key"])
            return r.get("error") or f"Pressed {r.get('key')}"
        if name == "browser_go_back":
            r = await browser_tools.go_back()
            return r.get("error") or f"Back â†’ {r.get('url')}"
        if name == "browser_go_forward":
            r = await browser_tools.go_forward()
            return r.get("error") or f"Forward â†’ {r.get('url')}"
        if name == "browser_eval_js":
            r = await browser_tools.evaluate_js(tool_input["code"])
            return r.get("error") or r.get("result", "")
        if name == "browser_open_for_login":
            r = await browser_tools.open_for_login(tool_input["url"], tool_input.get("prompt", ""))
            if "error" in r:
                return r["error"]
            return f"Opened {r.get('url')}. {r.get('instruction','')}"
        if name == "browser_screenshot":
            r = await browser_tools.screenshot()
            if "error" in r:
                return r["error"]
            # Tool result is consumed by Claude; the b64 image is too long to
            # ship inline as a tool result string, so we describe the page
            # via a narrow text summary + tell the LLM the screenshot was
            # taken (Claude doesn't currently get tool-call image attachments
            # automatically). For UI driving, browser_eval_js is more reliable.
            return f"Screenshot taken (URL: {r.get('url')}). Image is {len(r.get('image_b64',''))} bytes b64. For element discovery on heavy SPAs, prefer browser_eval_js with a DOM-traversal snippet over screenshots."

        # ---- GoHighLevel ----
        if name == "ghl_search_contacts":
            return json.dumps(ghl.search_contacts(tool_input.get("query", ""), tool_input.get("limit", 10)))
        if name == "ghl_get_contact":
            return json.dumps(ghl.get_contact(tool_input["contact_id"]))
        if name == "ghl_create_contact":
            return json.dumps(ghl.create_contact(
                first_name=tool_input.get("first_name", ""),
                last_name=tool_input.get("last_name", ""),
                email=tool_input.get("email", ""),
                phone=tool_input.get("phone", ""),
                tags=tool_input.get("tags"),
                source=tool_input.get("source", "Jarvis"),
            ))
        if name == "ghl_update_contact":
            cid = tool_input.pop("contact_id")
            return json.dumps(ghl.update_contact(cid, **tool_input))
        if name == "ghl_add_tag":
            return json.dumps(ghl.add_tag(tool_input["contact_id"], tool_input["tag"]))
        if name == "ghl_send_sms":
            return json.dumps(ghl.send_sms(tool_input["contact_id"], tool_input["message"]))
        if name == "ghl_send_email":
            return json.dumps(ghl.send_email(
                tool_input["contact_id"], tool_input["subject"], tool_input["body_html"],
                from_email=tool_input.get("from_email"),
            ))
        if name == "ghl_list_pipelines":
            return json.dumps(ghl.list_pipelines())
        if name == "ghl_list_opportunities":
            return json.dumps(ghl.list_opportunities(
                pipeline_id=tool_input.get("pipeline_id"),
                query=tool_input.get("query"),
                limit=tool_input.get("limit", 20),
            ))
        if name == "ghl_create_opportunity":
            return json.dumps(ghl.create_opportunity(
                tool_input["pipeline_id"], tool_input["stage_id"], tool_input["contact_id"],
                tool_input["name"], value=tool_input.get("value", 0),
                status=tool_input.get("status", "open"),
            ))
        if name == "ghl_update_opportunity":
            oid = tool_input.pop("opportunity_id")
            return json.dumps(ghl.update_opportunity(oid, **tool_input))
        if name == "ghl_list_calendars":
            return json.dumps(ghl.list_calendars())
        if name == "ghl_list_free_slots":
            return json.dumps(ghl.list_free_slots(
                tool_input["calendar_id"], tool_input["start_date_iso"], tool_input["end_date_iso"],
                timezone=tool_input.get("timezone"),
            ))
        if name == "ghl_book_appointment":
            return json.dumps(ghl.book_appointment(
                tool_input["calendar_id"], tool_input["contact_id"],
                tool_input["start_time_iso"], tool_input["end_time_iso"],
                title=tool_input.get("title"), notes=tool_input.get("notes"),
            ))
        if name == "ghl_api":
            return json.dumps(ghl.api(
                tool_input["method"], tool_input["path"],
                body=tool_input.get("body"), params=tool_input.get("params"),
            ))

        return f"Unknown tool: {name}"
    except KeyError as e:
        return f"Tool {name} missing required parameter: {e}"
    except Exception as e:
        print(f"  Tool error ({name}): {e}", flush=True)
        return f"Tool {name} failed: {e}"


def _serialize_content_block(block):
    """Convert an Anthropic SDK content block into a plain dict we can round-trip via messages history."""
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # unknown â€” try model_dump fallback
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return block


async def _speak(ws, text: str, source: str = "jarvis", voice_id: str = None, voice_settings: dict = None):
    """Helper: synthesize + send a response message over the websocket."""
    if not text or not text.strip():
        return
    audio = await synthesize_speech(text, voice_id=voice_id, voice_settings=voice_settings)
    await ws.send_json({
        "type": "response",
        "source": source,
        "text": text,
        "audio": base64.b64encode(audio).decode("utf-8") if audio else "",
    })


async def _emit_thought(ws, kind: str, text: str):
    """Send a thought event over the websocket â€” the frontend visualizes each one
    as a glowing orb flying outward along a neural pathway, plus a line in the
    side feed. This is what makes the Brain UI coexist with Jarvis's actual cognition."""
    if ws is None:
        return
    try:
        await ws.send_json({
            "type": "thought",
            "kind": kind,
            "text": (str(text) or "")[:140],
            "ts": time.time(),
        })
    except Exception:
        pass


# Brain ingest helper — pipes every Jarvis user/assistant turn into the
# NeuroLinked brain so it accumulates a unified record of every conversation
# You has (alongside the Claude Code chat ingestor). Fire-and-forget so a
# slow brain never blocks Jarvis's response loop.
import threading as _threading
import urllib.request as _urlreq

# ============================================================================
#  ACTIVE FOCUS — task continuity across context trims/wipes/restarts.
#
#  Jarvis used to wipe history on any API error (`conversations[sid] = []`),
#  which is why he'd lose the thread mid-build and have to ask "what were we
#  doing?" — the user-and-assistant turn log was the ONLY anchor.
#
#  Now: each session has a small `focus` dict ({task, key_facts[], updated_at})
#  that persists separately. It survives history truncation, API errors, even
#  full Jarvis restarts. Injected into the system prompt every turn, so even
#  after a fresh wipe the next turn still knows "we're building the GHL form".
# ============================================================================
_session_focus: dict[str, dict] = {}
_FOCUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain_storage", "focus")
os.makedirs(_FOCUS_DIR, exist_ok=True)


def _focus_path(sid: str) -> str:
    safe = "".join(c for c in (sid or "default") if c.isalnum() or c in "-_")[:64]
    return os.path.join(_FOCUS_DIR, f"{safe}.json")


def _load_focus(sid: str) -> dict:
    if sid in _session_focus:
        return _session_focus[sid]
    try:
        with open(_focus_path(sid), "r", encoding="utf-8") as f:
            d = json.load(f)
            _session_focus[sid] = d
            return d
    except Exception:
        return {}


def _save_focus(sid: str, focus: dict):
    _session_focus[sid] = focus
    try:
        with open(_focus_path(sid), "w", encoding="utf-8") as f:
            json.dump(focus, f, indent=2)
    except Exception as _e:
        print(f"[focus] WARN: could not persist {sid[:8]}: {_e}", flush=True)


def _set_focus(sid: str, task: str, fact: str | None = None):
    """Update active focus for a session. Call when the user starts a new
    build / pivots, or when Jarvis declares a goal explicitly."""
    if not task or len(task.strip()) < 3:
        return
    cur = _load_focus(sid) or {}
    new_task = task.strip()[:200]
    if cur.get("task") != new_task:
        cur = {"task": new_task, "key_facts": [], "updated_at": time.time()}
    if fact and fact.strip():
        kf = cur.setdefault("key_facts", [])
        if fact not in kf:
            kf.append(fact.strip()[:300])
            cur["key_facts"] = kf[-12:]  # keep last 12
            cur["updated_at"] = time.time()
    _save_focus(sid, cur)


def _focus_for_prompt(sid: str) -> str:
    """Render the session's focus as a system-prompt block."""
    f = _load_focus(sid)
    if not f or not f.get("task"):
        return ""
    parts = [f"\n\n=== ACTIVE FOCUS (what we are working on RIGHT NOW in this session) ===",
             f"Task: {f['task']}"]
    facts = f.get("key_facts") or []
    if facts:
        parts.append("Key facts so far:")
        for fact in facts[-8:]:
            parts.append(f"  - {fact}")
    parts.append("If this focus is stale (the user has clearly pivoted), call set_focus() with the new task. Otherwise, keep it as your north star — even if you can't see the early conversation in context.")
    return "\n".join(parts)


def _maybe_autoset_focus(sid: str, user_text: str):
    """Heuristic auto-focus: if the user message looks like a task declaration,
    promote it. Cheap pattern match — no LLM call required."""
    if not user_text:
        return
    text = user_text.strip()
    if len(text) < 12 or len(text) > 400:
        return
    lower = text.lower()
    # Trigger phrases that indicate a NEW task
    new_task_starts = (
        "let's build", "lets build", "let's make", "lets make",
        "build me", "build a", "create a", "create me",
        "i want to", "i need to", "i need you to", "i want you to",
        "help me", "set up", "set me up",
        "we're going to", "we are going to",
        "start working on", "work on",
    )
    is_new_task = any(lower.startswith(p) or f" {p} " in lower for p in new_task_starts)
    if is_new_task:
        _set_focus(sid, text)
        return
    # Continuation phrases — don't change focus, but the user is still on track
    cont_phrases = ("continue", "keep going", "let's continue", "lets continue", "go on")
    if any(p in lower for p in cont_phrases):
        return  # leave existing focus untouched

_BRAIN_TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "neurolinked-brain", ".launch-token",
)
_BRAIN_TOKEN_FILE = os.path.abspath(_BRAIN_TOKEN_FILE)
_BRAIN_OBSERVE_URL = "http://localhost:8020/api/claude/remember"


def _brain_token() -> str:
    """Token resolution: env var (if start.bat shared it) → file written by brain."""
    t = os.environ.get("NEUROLINKED_TOKEN", "").strip()
    if t:
        return t
    try:
        with open(_BRAIN_TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _brain_ingest_async(role: str, text: str, session_id: str = ""):
    """Post a turn to the brain in a background thread. Never raises."""
    if not text or len(text.strip()) < 3:
        return
    def _do():
        try:
            tok = _brain_token()
            if not tok:
                return
            body = json.dumps({
                "text": text[:8000],
                "source": f"jarvis:{role}",
                "tags": ["jarvis", role, session_id[:8] if session_id else "no-sid"],
            }).encode("utf-8")
            req = _urlreq.Request(
                _BRAIN_OBSERVE_URL,
                data=body, method="POST",
                headers={"Content-Type": "application/json", "x-neurolinked-token": tok},
            )
            _urlreq.urlopen(req, timeout=4).read()
        except Exception:
            pass  # brain offline / token rotated / whatever — don't block Jarvis
    _threading.Thread(target=_do, daemon=True).start()


async def process_message(session_id: str, user_text: str, ws: WebSocket, frame_b64: str = None):
    """Native tool-use loop: model may call multiple tools across multiple rounds.
    Text blocks are spoken (Jarvis voice by default). consult_brain results are spoken
    in the Brain voice. Loop terminates on stop_reason == 'end_turn' or max iterations.

    If frame_b64 is provided (JPEG webcam frame), it's included as an image content block
    in the user message â€” Jarvis sees the user on this turn."""
    if session_id not in conversations:
        conversations[session_id] = []

    # Store latest webcam frame for the see_me tool
    if frame_b64:
        latest_frames[session_id] = frame_b64

    # On wake-word, refresh all state and fire the configured wake song.
    # Wake-song playback is non-blocking — runs in a background thread so
    # Jarvis can speak / respond immediately while Spotify spins up.
    _ut = user_text.lower()
    _is_wake_song = "activate" in _ut
    # Hardcoded "time to get to work" / "good morning" / "good evening"
    # triggers run the FULL workday routine independent of the LLM, so the
    # routine still fires when no LLM is configured / credits are out.
    _work_phrases = (
        "time to get to work", "time to work", "let's get to work",
        "good morning", "good evening", "good afternoon",
        "wake up", "start my day", "start the day", "kick off the day",
        "boot up", "boot her up", "start workday",
    )
    _is_workday_trigger = any(p in _ut for p in _work_phrases)
    if _is_wake_song or _is_workday_trigger:
        refresh_data()
        refresh_brain()
        if spotify_tools is not None:
            wake = spotify_tools.get_wake_song()
            if wake:
                def _play_wake():
                    try:
                        result = spotify_tools.play_wake_song()
                        print(f"[wake-song] {result}", flush=True)
                    except Exception as _e:
                        print(f"[wake-song] failed: {_e}", flush=True)
                _threading.Thread(target=_play_wake, daemon=True).start()
    if _is_workday_trigger:
        # Open every app tagged 'work-startup' (GHL, Claude Code, etc.) in
        # a background thread so the routine doesn't block the conversation.
        def _open_workday_apps():
            try:
                import app_launcher
                period = "evening" if "evening" in _ut else ("afternoon" if "afternoon" in _ut else "morning")
                result = app_launcher.start_workday(period=period)
                print(f"[workday] {result}", flush=True)
            except Exception as _e:
                print(f"[workday] failed: {_e}", flush=True)
        _threading.Thread(target=_open_workday_apps, daemon=True).start()
        # Speak the routine confirmation directly via TTS (no LLM needed),
        # so the user gets verbal feedback even when the LLM is down.
        _greet = "Good evening" if "evening" in _ut else ("Good afternoon" if "afternoon" in _ut else "Good morning")
        await _speak(
            ws,
            f"{_greet}, sir. Critical by Autumn is queued, GoHighLevel is open, and Claude Code is loading. Workday started.",
            source="jarvis",
        )

    # Always keep tasks cache fresh â€” it's the cheapest truth
    TASKS_INFO_refresh = get_tasks_sync()
    if TASKS_INFO_refresh != TASKS_INFO:
        globals()["TASKS_INFO"] = TASKS_INFO_refresh

    # Build user message: if webcam frame is available, include it as image content block
    if frame_b64:
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
            {"type": "text", "text": user_text},
        ]
    else:
        user_content = user_text

    conversations[session_id].append({"role": "user", "content": user_content})

    # Pipe the user turn into the NeuroLinked brain (background; non-blocking).
    _brain_ingest_async("user", user_text, session_id)

    # Auto-detect new task / pivot from this user turn and update active focus.
    # The focus survives history truncation and API errors so Jarvis never
    # forgets what we're building, even mid-conversation.
    _maybe_autoset_focus(session_id, user_text)

    # Emit an input thought so the visualizer shows incoming data to the brain
    await _emit_thought(ws, "input", user_text)

    # Work on a local history; only commit back to conversations on clean end_turn.
    # We truncate to a safe window that won't break tool_use/tool_result pairing.
    # Context window. Was -24 which dropped the original task after ~5 turns
    # of multi-tool work. -80 gives Jarvis room for long sessions without
    # losing the original prompt; Claude Haiku 4.5's 200K context window has
    # plenty of headroom even with images.
    history = list(conversations[session_id][-80:])

    def _is_tool_result_msg(msg):
        """A user message is a tool_result dispatch iff its content is a list and
        the first block has type='tool_result'. Image+text user messages are NOT this."""
        if msg.get("role") != "user":
            return False
        c = msg.get("content")
        if not isinstance(c, list) or not c:
            return False
        first = c[0]
        return isinstance(first, dict) and first.get("type") == "tool_result"

    def _strip_old_images(hist):
        """Webcam frames balloon context size. Keep only the image from the MOST
        RECENT user turn; strip images from all prior user turns."""
        # Walk backwards; the first user message we hit with an image keeps it,
        # subsequent (older) user messages have images stripped.
        saw_recent_image = False
        for i in range(len(hist) - 1, -1, -1):
            msg = hist[i]
            if msg.get("role") != "user":
                continue
            c = msg.get("content")
            if not isinstance(c, list):
                continue
            has_image = any(isinstance(b, dict) and b.get("type") == "image" for b in c)
            if not has_image:
                continue
            if not saw_recent_image:
                saw_recent_image = True
                continue
            # Strip images from this older message — keep only text
            hist[i] = {
                "role": "user",
                "content": [b for b in c if not (isinstance(b, dict) and b.get("type") == "image")],
            }
        return hist

    def _ensure_valid_head(hist):
        """Ensure history starts on a clean user message (not orphan tool_result or assistant)."""
        while hist and _is_tool_result_msg(hist[0]):
            hist.pop(0)
        while hist and hist[0].get("role") == "assistant":
            hist.pop(0)
        return hist

    def _ensure_tool_pairing(hist):
        """If the last assistant message has tool_use but no matching tool_result user
        message follows, drop the orphan assistant — otherwise the API rejects the
        request with 'tool_use must be followed by tool_result'."""
        if not hist:
            return hist
        last = hist[-1]
        if last.get("role") == "assistant":
            c = last.get("content")
            if isinstance(c, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in c
            ):
                hist.pop()
        return hist

    history = _ensure_valid_head(history)
    history = _ensure_tool_pairing(history)
    history = _strip_old_images(history)

    # Tool-call budget per utterance. 6 was too tight for real work (Jarvis
    # burnt rounds on exploration and bailed before editing). 30 lets him
    # complete multi-step tasks while still bounding the loop.
    MAX_ROUNDS = 60
    for round_idx in range(MAX_ROUNDS):
        # Robust LLM call via provider abstraction (Anthropic/OpenAI/Groq/Ollama/xAI).
        # On hard failure: reset context so conversation never silently dead-locks.
        if llm is None:
            await _speak(
                ws,
                "Je n'ai pas de modèle linguistique configuré. Veuillez ouvrir les paramètres (icône ⚙), sélectionner un fournisseur (Mistral AI, Z.ai, OpenAI ou autre), puis coller votre clé API et cliquer sur Enregistrer.",
                source="jarvis",
            )
            return
        try:
            response = await llm.chat(
                system=get_system_prompt(session_id=session_id),
                messages=history,
                tools=TOOLS,
                max_tokens=800,
            )
        except Exception as e:
            # Loud, detailed error so we can actually diagnose "works at first
            # then breaks after 5 min" regressions. Dump class, full message,
            # and traceback; also summarize the history shape that was sent.
            import traceback
            err_cls = type(e).__name__
            err_full = str(e)
            print("  [ERROR] LLM call failed ------------------------------", flush=True)
            print(f"  [ERROR] class   : {err_cls}", flush=True)
            print(f"  [ERROR] message : {err_full[:800]}", flush=True)
            print(f"  [ERROR] history : {len(history)} messages, round={round_idx}", flush=True)
            try:
                shape = []
                total_chars = 0
                for i, m in enumerate(history):
                    c = m.get("content")
                    if isinstance(c, str):
                        total_chars += len(c)
                        shape.append(f"{i}:{m.get('role')}=text[{len(c)}]")
                    elif isinstance(c, list):
                        kinds = []
                        for b in c:
                            if not isinstance(b, dict): continue
                            k = b.get("type", "?")
                            if k == "text":
                                total_chars += len(b.get("text", "") or "")
                            elif k == "tool_result":
                                tc = b.get("content", "")
                                if isinstance(tc, str): total_chars += len(tc)
                            elif k == "image":
                                total_chars += 4000  # rough image-tokens hint
                            kinds.append(k)
                        shape.append(f"{i}:{m.get('role')}={'/'.join(kinds)}")
                print(f"  [ERROR] shape   : {' | '.join(shape)}", flush=True)
                print(f"  [ERROR] ~chars  : {total_chars}", flush=True)
            except Exception as _e2:
                print(f"  [ERROR] shape-dump failed: {_e2}", flush=True)
            traceback.print_exc()
            # PROGRESSIVE TRIM (replaces the old "wipe everything" path that
            # was the #1 cause of Jarvis-forgets-mid-conversation). Try
            # increasingly aggressive context reductions before giving up.
            # The active-focus block in the system prompt anchors the task
            # even if we have to fully wipe history as last resort.
            response = None
            trim_attempts = [
                ("trim oldest 30%", lambda h: h[max(1, len(h) // 3):]),
                ("trim oldest 60%", lambda h: h[max(1, 2 * len(h) // 3):]),
                ("keep only last 4 messages",
                 lambda h: [m for m in h[-4:] if not _is_tool_result_msg(m)] or h[-2:]),
                ("keep only this user turn",
                 lambda h: [{"role": "user", "content": user_content}]),
            ]
            for label, trimmer in trim_attempts:
                try:
                    new_hist = trimmer(history)
                    new_hist = _ensure_valid_head(new_hist)
                    new_hist = _ensure_tool_pairing(new_hist)
                    if not new_hist:
                        new_hist = [{"role": "user", "content": user_content}]
                    print(f"  [recover] {label} ({len(history)}→{len(new_hist)})", flush=True)
                    response = await llm.chat(
                        system=get_system_prompt(session_id=session_id),
                        messages=new_hist,
                        tools=TOOLS,
                        max_tokens=800,
                    )
                    history = new_hist
                    print(f"  [recover] succeeded with {label}", flush=True)
                    break
                except Exception as e2:
                    print(f"  [recover] {label} failed: {type(e2).__name__}: {str(e2)[:200]}", flush=True)
                    continue

            if response is None:
                print(f"  [recover] all trim attempts failed — keeping focus, dropping history", flush=True)
                # Last resort: drop just the conversation history but KEEP the
                # session's active focus so the next turn can continue cleanly.
                conversations[session_id] = []
                # Surface the REAL error to the user so they can act on it
                # (billing/credits, bad API key, network out, etc.) instead
                # of staring at a vague "I hit a snag" forever.
                err_lower = (str(e) if 'e' in dir() else "").lower()
                if "credit balance" in err_lower or "billing" in err_lower:
                    msg = "Sir, the Anthropic API key has run out of credits. Open the gear icon and either top up that account at console.anthropic.com or paste a key from a different provider — Groq is free and fast."
                elif "invalid x-api-key" in err_lower or "authentication" in err_lower or "401" in err_lower:
                    msg = "Sir, the configured API key was rejected. Open the gear icon and check the key for the active LLM provider."
                elif "connection" in err_lower or "timeout" in err_lower or "name resolution" in err_lower:
                    msg = "Sir, I can't reach the LLM provider right now. Network may be down, or the local LLM (Ollama) isn't running."
                elif "rate limit" in err_lower or "429" in err_lower:
                    msg = "Sir, I'm rate-limited by the LLM provider. Try again in a moment."
                else:
                    msg = f"Sir, the LLM call failed and I couldn't recover. Error: {str(e)[:200] if 'e' in dir() else 'unknown'}. Open the gear icon to switch provider."
                await _speak(ws, msg, source="jarvis")
                return
        print(f"  [round {round_idx}] stop_reason={response.stop_reason}", flush=True)

        # Append assistant turn (as serialized blocks) to local history
        assistant_blocks = [_serialize_content_block(b) for b in response.content]
        history.append({"role": "assistant", "content": assistant_blocks})

        # Speak any text blocks Jarvis produced this round (even before tool calls)
        jarvis_text = "".join(
            b.text for b in response.content
            if getattr(b, "type", None) == "text" and b.text.strip()
        )
        if jarvis_text.strip():
            print(f"  Jarvis: {jarvis_text[:120]}", flush=True)
            await _speak(ws, jarvis_text, source="jarvis")
            # Pipe Jarvis's spoken response into the brain (background).
            _brain_ingest_async("assistant", jarvis_text, session_id)

        if response.stop_reason != "tool_use":
            # end_turn (or max_tokens) â€” we're done for this user turn
            conversations[session_id] = history
            _save_session(session_id, history)
            return

        # --- tool_use round ---
        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        tool_results = []
        for tub in tool_use_blocks:
            input_preview = json.dumps(tub.input, ensure_ascii=False)[:120]
            print(f"  >> tool.{tub.name}({input_preview})", flush=True)

            # Emit pre-tool thought â€” frontend spawns a 3D orb + side-feed entry
            kind = THOUGHT_KIND_BY_TOOL.get(tub.name, "tool")
            # Compose a compact label describing this specific call
            if isinstance(tub.input, dict):
                if tub.input.get("task"):      label = f"task: {tub.input['task']}"
                elif tub.input.get("note"):    label = f"note: {tub.input['note']}"
                elif tub.input.get("query"):   label = f"{tub.name}: {tub.input['query']}"
                elif tub.input.get("question"): label = f"brain: {tub.input['question']}"
                elif tub.input.get("directive"): label = f"rule: {tub.input['directive']}"
                elif tub.input.get("cmd"):     label = f"$ {tub.input['cmd']}"
                elif tub.input.get("path"):    label = f"{tub.name}: {tub.input['path']}"
                elif tub.input.get("filename"): label = f"{tub.name}: {tub.input['filename']}"
                elif tub.input.get("url"):     label = f"url: {tub.input['url']}"
                elif tub.input.get("prompt"):  label = f"claude code: {tub.input['prompt'][:60]}"
                elif tub.input.get("pattern"): label = f"grep: {tub.input['pattern']}"
                else: label = tub.name
            else:
                label = tub.name
            await _emit_thought(ws, kind, label)

            try:
                # Thread ws + session_id so tools like see_me can request webcam frames
                enriched_input = dict(tub.input) if isinstance(tub.input, dict) else {}
                enriched_input["_ws"] = ws
                enriched_input["_session_id"] = session_id
                result = await execute_tool(tub.name, enriched_input)
            except Exception as e:
                print(f"    tool dispatch error: {e}", flush=True)
                result = f"Error: {e}"
            print(f"    << {str(result)[:160]}", flush=True)

            # Special voice routing: consult_brain â†’ Brain voice
            if tub.name == "consult_brain":
                brain_settings = {"stability": 0.72, "similarity_boost": 0.80, "style": 0.10}
                await _speak(ws, result, source="brain", voice_id=BRAIN_VOICE_ID, voice_settings=brain_settings)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tub.id,
                "content": str(result) if result is not None else "",
            })

        # Feed tool results back as a user turn and loop
        history.append({"role": "user", "content": tool_results})

    # Hit MAX_ROUNDS â€” save what we have and bail gracefully
    print(f"  [warn] hit MAX_ROUNDS={MAX_ROUNDS}", flush=True)
    conversations[session_id] = history
    _save_session(session_id, history)
    await _speak(ws, "I hit my tool-call limit, sir. Ask again?", source="jarvis")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # 1. Origin gate
    origin = ws.headers.get("origin", "")
    if origin and origin not in _ALLOWED_HTTP_ORIGINS:
        print(f"[jarvis] rejected ws connect from origin {origin!r}", flush=True)
        await ws.close(code=1008)
        return
    # 2. Host gate (DNS rebinding)
    host = (ws.headers.get("host", "") or "").split(":")[0]
    if host and host not in _ALLOWED_HOSTS:
        print(f"[jarvis] rejected ws connect with bad host {host!r}", flush=True)
        await ws.close(code=1008)
        return
    # 3. Launch-token gate. Browser sends the token as a query param
    #    `?token=...` (WebSocket has no header API in browsers), pulled
    #    from window.__NEUROLINKED_TOKEN__ in the served HTML.
    supplied_token = ws.query_params.get("token", "")
    if not supplied_token or not _secrets.compare_digest(supplied_token, LAUNCH_TOKEN):
        print(f"[jarvis] rejected ws connect with bad/missing token", flush=True)
        await ws.close(code=1008)
        return
    await ws.accept()
    # Stable session ID from the browser (localStorage). Falls back to a
    # connection-scoped id if the browser didn't send one — that branch loses
    # context across reconnects, which is the OLD broken behavior we're
    # fixing. New frontend always sends `?sid=...`.
    supplied_sid = ws.query_params.get("sid", "")
    if supplied_sid and _SID_OK.match(supplied_sid):
        session_id = supplied_sid
        # Load any prior conversation for this session from disk.
        if session_id not in conversations:
            prior = _load_session(session_id)
            if prior:
                conversations[session_id] = prior
                print(f"[jarvis] resumed session {session_id[:8]} with {len(prior)} prior messages", flush=True)
    else:
        session_id = "ephem-" + str(id(ws))
        print(f"[jarvis] no sid sent, using ephemeral session {session_id}", flush=True)
    print(f"[jarvis] Client connected (origin={origin!r}, token OK, sid={session_id[:8]}...)", flush=True)

    # Per-message size cap. The frame field is a base64 JPEG; expect ~100-300KB
    # at most. 8 MB is generous for that and still blocks DoS via a 200MB string.
    _WS_MAX_BYTES = 8 * 1024 * 1024
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > _WS_MAX_BYTES:
                print(f"[jarvis] dropping oversized WS message ({len(raw)} bytes)", flush=True)
                await ws.close(code=1009)  # Message Too Big
                return
            try:
                data = json.loads(raw)
            except Exception:
                # malformed JSON — keep the connection but ignore
                continue
            msg_type = data.get("type", "")

            # Handle frame_response from webcam (in response to see_me's request_frame)
            if msg_type == "frame_response":
                frame = data.get("frame")
                fut = pending_frame_futures.get(session_id)
                if fut and not fut.done():
                    fut.set_result(frame)
                # Also store as latest frame
                if frame:
                    latest_frames[session_id] = frame
                continue

            user_text = data.get("text", "").strip()
            if not user_text:
                continue

            # Special: boot greeting â€” a short TTS line so Jarvis speaks on page load
            if user_text == "__boot_greet__":
                hour = int(time.strftime("%H"))
                if   hour < 12: tod = "Good morning"
                elif hour < 18: tod = "Good afternoon"
                else:           tod = "Good evening"
                open_count = len(TASKS_INFO)
                if open_count == 0:
                    greet = f"{tod}, {USER_ADDRESS}. All systems online. No open tasks â€” a rare luxury."
                elif open_count == 1:
                    greet = f"{tod}, {USER_ADDRESS}. All systems online. One open task on your list."
                else:
                    greet = f"{tod}, {USER_ADDRESS}. All systems online. You have {open_count} open tasks."
                print(f"[jarvis] boot greet: {greet}", flush=True)
                await _speak(ws, greet, source="jarvis")
                continue

            # Extract optional webcam frame attached to this message
            frame_b64 = data.get("frame")
            cam_info = f" [+cam {len(frame_b64)//1024}KB]" if frame_b64 else " [no cam]"
            print(f"  You:    {user_text}{cam_info}", flush=True)
            await process_message(session_id, user_text, ws, frame_b64=frame_b64)

    except WebSocketDisconnect:
        conversations.pop(session_id, None)
        latest_frames.pop(session_id, None)
        pending_frame_futures.pop(session_id, None)


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "frontend")), name="static")


# ============================================================================
#   API PARAMÈTRES — permet au frontend de lire/écrire config.json depuis l'interface
#   pour que les utilisateurs puissent configurer leurs clés API sans toucher au système de fichiers.
# ============================================================================

_SETTINGS_EDITABLE = {
    # LLM
    "llm_provider", "llm_model",
    "anthropic_api_key", "openai_api_key", "groq_api_key", "ollama_api_key", "xai_api_key",
    "mistral_api_key", "openrouter_api_key", "zai_api_key",
    # TTS
    "tts_provider",
    "elevenlabs_api_key", "elevenlabs_voice_id", "brain_voice_id",
    # Personnel
    "user_name", "user_address", "city",
    # Pont NeuroLink
    "neurolink_url", "auto_connect_neurolink",
    # Intégrations métier
    "ghl_location_id", "ghl_api_key",
}

def _mask(v):
    if not isinstance(v, str) or not v:
        return ""
    if len(v) <= 8:
        return "•" * len(v)
    return v[:4] + "•" * (len(v) - 8) + v[-4:]

@app.get("/api/settings")
async def get_settings():
    """Retourne une vue SÉCURISÉE de la config actuelle — les clés API sont masquées, jamais en clair."""
    out = {}
    for k in _SETTINGS_EDITABLE:
        v = config.get(k, "")
        if "api_key" in k or k == "neurolink_api_key":
            out[k] = _mask(v) if v else ""
            out[f"{k}_set"] = bool(v)
        else:
            out[k] = v
    out["providers_available"] = llm_providers.available_providers()
    out["llm_active"] = getattr(llm, "name", None)
    out["llm_active_model"] = getattr(llm, "model", None)
    out["elevenlabs_configured"] = bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)
    return out

@app.post("/api/settings")
async def set_settings(payload: dict):
    """Accepte un sous-ensemble de champs modifiables, fusionne dans config.json, sauvegarde,
    et reconstruit les variables globales actives (fournisseur LLM, identifiants ElevenLabs, pont NeuroLink)
    pour que les changements prennent effet immédiatement — sans redémarrage de Jarvis."""
    # N'accepter que les clés connues — ignorer silencieusement tout le reste pour la sécurité.
    changes = {k: v for k, v in (payload or {}).items() if k in _SETTINGS_EDITABLE}

    # Pour les champs de clés API masquées, ignorer toute valeur encore sous forme masquée —
    # cela signifie que l'utilisateur ne l'a pas modifiée dans l'interface.
    for k in list(changes.keys()):
        if "api_key" in k and isinstance(changes[k], str) and "•" in changes[k]:
            del changes[k]

    # Fusion + sauvegarde
    config.update(changes)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"Impossible d'écrire config.json : {e}"}

    # Rechargement en direct des variables globales affectées
    global ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, BRAIN_VOICE_ID, ai
    ANTHROPIC_API_KEY = config.get("anthropic_api_key", "") or ""
    ELEVENLABS_API_KEY = config.get("elevenlabs_api_key", "") or ""
    ELEVENLABS_VOICE_ID = config.get("elevenlabs_voice_id", "")
    BRAIN_VOICE_ID = config.get("brain_voice_id", "") or ELEVENLABS_VOICE_ID
    try:
        ai = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    except Exception:
        ai = None
    _rebuild_llm()

    # Ré-initialiser les intégrations métier dont les identifiants ont pu changer
    if "ghl_location_id" in changes or "ghl_api_key" in changes:
        try:
            ghl.init(config.get("ghl_location_id", ""), config.get("ghl_api_key", ""))
        except Exception:
            pass

    # Ré-initialiser NeuroLink si son URL a changé
    if "neurolink_url" in changes or "auto_connect_neurolink" in changes:
        try:
            import neurolink_bridge
            neurolink_bridge.init(
                config.get("neurolink_url", "http://localhost:8000"),
                auto_connect=bool(config.get("auto_connect_neurolink", True)),
            )
        except Exception:
            pass

    return {
        "ok": True,
        "applied": list(changes.keys()),
        "llm_active": getattr(llm, "name", None),
        "llm_active_model": getattr(llm, "model", None),
        "elevenlabs_configured": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
    }


@app.get("/api/health")
async def health():
    try:
        import neurolink_bridge
        nl = neurolink_bridge.status()
    except Exception:
        nl = {"connected": False, "url": None}
    return {
        "ok": True,
        "llm": {
            "active": getattr(llm, "name", None),
            "model": getattr(llm, "model", None),
            "configured": llm is not None,
        },
        "tts": {
            "elevenlabs": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
            "provider_pref": config.get("tts_provider", "auto"),
        },
        "neurolink": nl,
    }


@app.get("/api/status")
async def cortex_status():
    """Honest per-cortex health for the UI's right-side rail. No fake greens —
    each entry reports whether the underlying tool module actually imported and,
    where relevant, whether the operator has configured a backing service."""
    def ok(msg):   return {"ok": True,  "msg": msg}
    def bad(msg):  return {"ok": False, "msg": msg}

    # brain_tools is imported unconditionally near the top, so if this call
    # errors we have a real wiring problem — surface it.
    try:
        brain_tools.get_personality_addendum()
        directive = ok("self-modification live")
        task      = ok(f"{len(brain_tools.list_tasks() or [])} open tasks")
        note      = ok("brain files reachable")
        memory    = ok("recall index live")
    except Exception as e:
        msg = f"brain_tools error: {e}"[:120]
        directive = task = note = memory = bad(msg)

    control = ok("computer control live") if computer_tools else bad("computer_tools not imported")
    vision  = ok("screen + webcam") if screen_capture else bad("screen_capture not imported")
    code    = ok("dev file + search tools live") if dev_tools else bad("dev_tools not imported")
    shell   = ok("system_shell live") if dev_tools else bad("dev_tools not imported")

    # brain cortex = LLM reachable + provider chosen
    if llm is None:
        brain = bad("aucun fournisseur LLM configuré — ouvrez les paramètres")
    else:
        brain = {
            "ok": True,
            "msg": f"provider active",
            "provider": (config.get("llm_provider") or "").lower() or "unknown",
        }

    # input = speech recognition lives in the browser; reachable if we're serving
    # a page at all (this request proves it). It's accurate.
    input_cortex = ok("websocket up, browser SR recommended")

    # aux
    aux_config     = ok("config.json loaded")
    aux_elevenlabs = ok("elevenlabs key + voice set") if (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID) \
                     else bad("no elevenlabs key/voice — TTS will use browser voice")

    # web = can we drive a browser? playwright + chromium presence.
    if browser_tools:
        aux_web = ok("playwright module imported")
    else:
        aux_web = bad("browser_tools not imported (install playwright)")

    cortexes = {
        "directive": {"region": "prefrontal",  **directive},
        "control":   {"region": "motor",       **control},
        "task":      {"region": "concept",     **task},
        "brain":     {"region": "association", **brain},
        "vision":    {"region": "sensory",     **vision},
        "memory":    {"region": "predictive",  **memory},
        "code":      {"region": "feature",     **code},
        "shell":     {"region": "brainstem",   **shell},
        "note":      {"region": "hippocampus", **note},
        "input":     {"region": "language",    **input_cortex},
    }
    planned = {
        "cerebellum": {"region": "cerebellum",  "ok": False, "msg": "motor-skill fine-tuning not yet wired"},
        "reflex":     {"region": "reflex_arc",  "ok": False, "msg": "automatic-response layer not yet wired"},
    }
    aux = {
        "config":     aux_config,
        "elevenlabs": aux_elevenlabs,
        "web":        aux_web,
    }
    overall_ok = all(v.get("ok") for v in cortexes.values()) and all(v.get("ok") for v in aux.values())
    return {
        "cortexes": cortexes,
        "planned": planned,
        "aux": aux,
        "overall_ok": overall_ok,
        "ts": time.time(),
    }


@app.get("/")
async def serve_index():
    # Inline-inject the per-startup launch token so the frontend can read it
    # from window.__NEUROLINKED_TOKEN__ and present it on every API call + WS
    # connect. The token never appears in /static/* or any other endpoint.
    return _serve_index_with_token()


if __name__ == "__main__":
    import uvicorn
    print("=" * 50, flush=True)
    print("  Z.E.R.O. Server (w/ Neurolink Brain)", flush=True)
    print(f"  http://localhost:8340", flush=True)
    print(f"  Jarvis voice: {ELEVENLABS_VOICE_ID[:8]}...", flush=True)
    print(f"  Brain voice:  {BRAIN_VOICE_ID[:8]}...", flush=True)
    print("=" * 50, flush=True)
    # Bind to 127.0.0.1 only â€” localhost access, nobody on the LAN can reach Jarvis.
    # If you ever want to access Jarvis from another device on your network, change
    # the host below to "0.0.0.0" and open port 8340 in your firewall â€” but know
    # that also exposes the API keys via WebSocket responses.
    uvicorn.run(app, host="127.0.0.1", port=8340)
