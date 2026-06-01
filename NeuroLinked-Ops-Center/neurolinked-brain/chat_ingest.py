"""
Claude Code Chat → Brain Ingestor

Watches every .jsonl transcript under ~/.claude/projects/ and feeds new
user/assistant turns into the NeuroLinked brain so Jarvis (and any other
brain consumer) has full context of every conversation You has had.

Design:
- Tail-style: track each file's last-read byte offset in offsets.json so
  we never re-ingest the same turn twice (and survive restarts).
- New files are auto-discovered every poll cycle.
- Each turn POSTs to /api/claude/remember with source='claude-code' and
  tags=[<project_dir_encoded>, <session_uuid>] so it's filterable.
- Runs forever. Designed to be launched alongside the brain (start.bat).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# Force UTF-8 stdout so Windows console doesn't crash on emoji in turns
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BRAIN_URL = "http://localhost:8020"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"
BRAIN_DIR = Path(__file__).parent
TOKEN_FILE = BRAIN_DIR / ".launch-token"
OFFSETS_FILE = BRAIN_DIR / "chat_ingest_offsets.json"
POLL_INTERVAL = 8  # seconds between scans
MAX_TEXT_LEN = 8000  # truncate very long turns so brain memory stays usable

# Turn types we ingest. queue-operation, attachment, last-prompt, ai-title
# are noise — only user/assistant carry actual conversation content.
INGEST_TYPES = {"user", "assistant"}


def _read_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_offsets() -> dict:
    if OFFSETS_FILE.exists():
        try:
            return json.loads(OFFSETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_offsets(off: dict):
    try:
        OFFSETS_FILE.write_text(json.dumps(off, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[ingest] WARN: could not save offsets: {e}")


def _extract_text(entry: dict) -> str:
    """Pull readable text from a JSONL turn. Handles both string content (user
    turns) and list-of-blocks content (assistant turns w/ tool_use, etc.)."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "tool_use":
                # Compress tool calls to a one-liner so they don't dominate
                # but still anchor the conversation when scanning back.
                name = block.get("name", "?")
                inp = json.dumps(block.get("input", {}))[:200]
                parts.append(f"[tool:{name} {inp}]")
            elif t == "tool_result":
                txt = block.get("content", "")
                if isinstance(txt, list):
                    txt = " ".join(b.get("text", "") for b in txt if isinstance(b, dict))
                parts.append(f"[result:{str(txt)[:300]}]")
        return "\n".join(p for p in parts if p)
    return ""


def _post_remember(text: str, project: str, session: str, role: str, ts: str) -> bool:
    """POST a single turn to the brain. Returns True on 2xx."""
    token = _read_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["x-neurolinked-token"] = token
    payload = {
        "text": text[:MAX_TEXT_LEN],
        "source": f"claude-code:{role}",
        "tags": ["claude-code", project, session, role, ts.split("T")[0]],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BRAIN_URL}/api/claude/remember",
        data=body, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        # 401 = brain token rotated mid-flight, just retry next cycle
        if e.code != 401:
            print(f"[ingest] HTTP {e.code} on remember: {e.read()[:120]}")
        return False
    except Exception as e:
        # Brain probably down; we'll catch up next cycle
        return False


def _scan_once(offsets: dict) -> int:
    """Walk every .jsonl, ingest only bytes past the saved offset.
    Returns number of turns ingested this scan."""
    if not PROJECTS_ROOT.exists():
        return 0
    ingested = 0
    for jsonl in PROJECTS_ROOT.rglob("*.jsonl"):
        key = str(jsonl)
        last_offset = offsets.get(key, 0)
        try:
            size = jsonl.stat().st_size
        except FileNotFoundError:
            continue
        if size <= last_offset:
            continue  # nothing new

        try:
            with jsonl.open("rb") as f:
                f.seek(last_offset)
                # Read ALL new bytes; split on newline.
                new_bytes = f.read()
        except Exception:
            continue

        # Strict line parsing: the last byte may be mid-line if the writer
        # is mid-flush. We commit only up to the last complete newline.
        try:
            text_blob = new_bytes.decode("utf-8", errors="replace")
        except Exception:
            text_blob = ""
        lines = text_blob.split("\n")
        # Keep last partial line for next cycle
        consumed = len(text_blob.encode("utf-8")) - len(lines[-1].encode("utf-8"))
        complete_lines = lines[:-1]

        # Project + session derived from the path
        # path: ~/.claude/projects/<project_encoded>/<session_uuid>.jsonl
        try:
            project = jsonl.parent.name
            session = jsonl.stem
        except Exception:
            project, session = "unknown", "unknown"

        for line in complete_lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") not in INGEST_TYPES:
                continue
            text = _extract_text(entry)
            if not text or len(text.strip()) < 5:
                continue
            ts = entry.get("timestamp", "")
            role = entry.get("type", "?")
            if _post_remember(text, project, session, role, ts):
                ingested += 1

        # Update offset only if we successfully consumed something
        offsets[key] = last_offset + consumed
    return ingested


def main():
    print(f"[ingest] watching {PROJECTS_ROOT}")
    print(f"[ingest] target brain: {BRAIN_URL}")
    print(f"[ingest] offsets file: {OFFSETS_FILE}")
    offsets = _load_offsets()
    print(f"[ingest] loaded offsets for {len(offsets)} known files")

    # On the very first run with no offsets, seed each existing file at its
    # current EOF so we don't try to dump 100MB of historical chats into the
    # brain on launch — we only ingest new turns from now on.
    if not offsets:
        print("[ingest] FIRST RUN: seeding offsets at EOF (history will not be re-ingested)")
        if PROJECTS_ROOT.exists():
            for jsonl in PROJECTS_ROOT.rglob("*.jsonl"):
                try:
                    offsets[str(jsonl)] = jsonl.stat().st_size
                except Exception:
                    pass
        _save_offsets(offsets)

    while True:
        try:
            n = _scan_once(offsets)
            if n > 0:
                _save_offsets(offsets)
                print(f"[ingest] +{n} turns")
        except Exception as e:
            print(f"[ingest] scan error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
