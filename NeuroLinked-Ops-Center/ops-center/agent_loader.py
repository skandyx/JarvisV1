"""
Agent loader for the the successor product section-based agent format.

Reads `NeuroLinked/sections/<section>/agents/<id>.md`, parses YAML frontmatter,
returns agent records compatible with the existing executor in server.py.

The legacy `custom_agents.json` registry is loaded separately by server.py;
this loader's records take precedence on id collision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "agent_loader requires PyYAML. Install it:\n"
        "    pip install pyyaml"
    ) from exc

# Repository layout (relative to this file):
#   ops-center/agent_loader.py    ← here
#   ops-center/server.py
#   sections/<section>/agents/<id>.md
HERE = Path(__file__).resolve().parent
SECTIONS_ROOT = HERE.parent / "sections"

# Step types accepted by the executor in server.py. Loader validates against
# this list so a malformed agent file fails fast. Mirrors STEP_CATALOG +
# legacy aliases.
VALID_STEP_TYPES = {
    "brain_search",
    "brain_remember",
    "ask_jarvis",
    "llm_ask",
    "send_email",
    "slack_notify",
    "api_request",
    "call_api",
    "create_task",
    "notify",
    "summarize",
    # Legacy aliases — some older agents use these names; handled by the same handlers internally
    "reason",
    "draft_email",
}


class AgentLoadError(Exception):
    """Raised when an agent file is malformed."""


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file with YAML frontmatter into (metadata, body).

    Frontmatter must start on line 1 with a literal '---' fence and end with
    another '---' fence. Returns (metadata_dict, body_string).
    """
    if not text.startswith("---"):
        raise AgentLoadError("missing opening '---' frontmatter fence")
    # Find closing fence
    lines = text.splitlines()
    if not lines:
        raise AgentLoadError("empty file")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise AgentLoadError("missing closing '---' frontmatter fence")
    fm_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    try:
        metadata = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise AgentLoadError(f"YAML parse error: {e}") from e
    if not isinstance(metadata, dict):
        raise AgentLoadError("frontmatter is not a mapping")
    return metadata, body


def validate_agent(meta: dict[str, Any], path: Path) -> None:
    required = {"id", "name", "section", "steps"}
    missing = required - set(meta)
    if missing:
        raise AgentLoadError(
            f"{path.name}: missing required keys: {sorted(missing)}"
        )
    if not isinstance(meta["steps"], list):
        raise AgentLoadError(f"{path.name}: 'steps' must be a list")
    for i, step in enumerate(meta["steps"]):
        if not isinstance(step, dict):
            raise AgentLoadError(f"{path.name}: step {i} is not a mapping")
        if "type" not in step:
            raise AgentLoadError(f"{path.name}: step {i} missing 'type'")
        if step["type"] not in VALID_STEP_TYPES:
            raise AgentLoadError(
                f"{path.name}: step {i} has unknown type '{step['type']}'. "
                f"Valid: {sorted(VALID_STEP_TYPES)}"
            )
    # id must be safe to use as a JSON key and a slug
    if not isinstance(meta["id"], str) or not meta["id"].replace("_", "").replace("-", "").isalnum():
        raise AgentLoadError(f"{path.name}: 'id' must be a kebab/snake-case string")


def load_one(path: Path) -> dict[str, Any]:
    """Load and validate a single agent file. Returns the agent record."""
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    validate_agent(meta, path)
    record = {
        "id": meta["id"],
        "name": meta["name"],
        "section": meta["section"],
        "description": meta.get("description", ""),
        "schedule": meta.get("schedule", "on-demand"),
        "enabled": bool(meta.get("enabled", True)),
        "inputs": list(meta.get("inputs", []) or []),
        "steps": list(meta["steps"]),
        "body": body,
        "source": "section_md",
        "source_path": str(path.relative_to(SECTIONS_ROOT.parent)),
        "created_at": meta.get("created_at", ""),
        # Manager / worker role; defaults to worker so existing agents are unaffected.
        "role": (meta.get("role") or "worker").strip().lower(),
        # Optional: when this worker finishes, auto-fire the section's manager_review.
        "manager_review": bool(meta.get("manager_review", False)),
        # Optional: list of agent ids this manager oversees (UI-only hint; not enforced).
        "reviews": list(meta.get("reviews", []) or []),
    }
    if record["role"] not in ("manager", "worker"):
        record["role"] = "worker"
    return record


def load_all(sections_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Scan all sections and return {agent_id: agent_record}.

    Files that fail to parse are skipped (with a warning). One bad agent
    file shouldn't take down the whole system.
    """
    root = sections_root or SECTIONS_ROOT
    out: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return out
    for agent_path in sorted(root.glob("*/agents/*.md")):
        try:
            record = load_one(agent_path)
        except AgentLoadError as e:
            print(f"[agent_loader] skip {agent_path.name}: {e}", flush=True)
            continue
        agent_id = record["id"]
        if agent_id in out:
            print(
                f"[agent_loader] WARN duplicate id '{agent_id}' "
                f"({out[agent_id]['source_path']} vs {record['source_path']}); "
                f"keeping first",
                flush=True,
            )
            continue
        out[agent_id] = record
    return out


def list_sections(sections_root: Path | None = None) -> list[dict[str, Any]]:
    """Return per-section metadata: name, agent count, raw/wiki/output counts."""
    root = sections_root or SECTIONS_ROOT
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for sec_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if sec_dir.name.startswith("_") or sec_dir.name.startswith("."):
            continue
        out.append({
            "name": sec_dir.name,
            "title": sec_dir.name.title(),
            "section_md": str((sec_dir / "_section.md").relative_to(root.parent)) if (sec_dir / "_section.md").exists() else None,
            "agent_count": len([p for p in (sec_dir / "agents").glob("*.md")]) if (sec_dir / "agents").exists() else 0,
            "raw_count":   len([p for p in (sec_dir / "raw").glob("**/*") if p.is_file() and p.name != ".gitkeep"]) if (sec_dir / "raw").exists() else 0,
            "wiki_count":  len([p for p in (sec_dir / "wiki").glob("*.md") if p.name != "_index.md"]) if (sec_dir / "wiki").exists() else 0,
            "output_count": len([p for p in (sec_dir / "output").glob("**/*") if p.is_file() and p.name != ".gitkeep"]) if (sec_dir / "output").exists() else 0,
        })
    return out


def write_agent(record: dict[str, Any], sections_root: Path | None = None) -> Path:
    """Write a new agent record to sections/<section>/agents/<slug>.md.

    Returns the path written. Refuses to overwrite an existing file — caller
    must delete or use update_agent() for edits.
    """
    root = sections_root or SECTIONS_ROOT
    section = record.get("section", "").strip()
    if not section:
        raise AgentLoadError("write_agent: missing 'section'")
    sec_dir = root / section
    if not sec_dir.exists():
        raise AgentLoadError(f"write_agent: unknown section '{section}'")
    slug = record.get("slug") or _slugify(record.get("name", record.get("id", "")))
    if not slug:
        raise AgentLoadError("write_agent: cannot derive slug from name/id")
    target = sec_dir / "agents" / f"{slug}.md"
    if target.exists():
        raise AgentLoadError(f"write_agent: file already exists at {target}")

    fm_keys = ("id", "name", "section", "role", "description", "schedule", "enabled",
               "inputs", "steps", "manager_review", "reviews", "created_at")
    fm = {k: record[k] for k in fm_keys if k in record}
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    body = record.get("body", "").strip()
    if not body:
        body = f"# {record.get('name', record.get('id', ''))}\n\n_Created via the the successor product dashboard._"
    text = f"---\n{fm_yaml}\n---\n{body}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def update_agent(agent_id: str, patch: dict[str, Any], sections_root: Path | None = None) -> Path:
    """Update fields of an existing agent file. Returns the path.

    Patch keys overlay the frontmatter. Body is left alone unless 'body' is
    in the patch.
    """
    root = sections_root or SECTIONS_ROOT
    # find the file
    for path in (root or SECTIONS_ROOT).glob("*/agents/*.md"):
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except AgentLoadError:
            continue
        if meta.get("id") == agent_id:
            meta.update({k: v for k, v in patch.items() if k != "body"})
            if "body" in patch:
                body = patch["body"]
            fm_yaml = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
            path.write_text(f"---\n{fm_yaml}\n---\n{body.strip()}\n", encoding="utf-8")
            return path
    raise AgentLoadError(f"update_agent: no agent with id '{agent_id}'")


def _slugify(s: str) -> str:
    """Convert 'Cold Outreach Drafter' → 'cold-outreach-drafter'."""
    out = []
    for ch in s.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "_", "-"):
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


if __name__ == "__main__":
    # CLI smoke test: print the loaded registry
    import sys
    agents = load_all()
    sections = list_sections()
    print(json.dumps({
        "sections": sections,
        "agents_loaded": len(agents),
        "agent_ids": sorted(agents.keys()),
    }, indent=2, default=str))
    sys.exit(0)
