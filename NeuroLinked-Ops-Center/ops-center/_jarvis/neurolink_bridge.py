"""
NeuroLinked Brain Bridge

Automatically connects Jarvis to a running NeuroLinked Brain server (default
http://localhost:8000) so every "remember" and "recall" call in Jarvis also
flows through the NeuroLinked neural memory.

- If the Brain is reachable: Jarvis dual-writes to both the local .md files
  AND the Brain's /api/claude/remember endpoint. Recall queries also consult
  the Brain and merge results with local notes.
- If the Brain is NOT reachable: Jarvis falls back to the local .md memory
  only — nothing breaks. A single warning is printed at startup.

No configuration required. Just start the NeuroLinked Brain on port 8000
(or set `neurolink_url` in config.json) and Jarvis picks it up automatically.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional
from urllib import request as _urlreq
from urllib.error import URLError, HTTPError

_URL: Optional[str] = None
_CONNECTED: bool = False
_LOCK = threading.Lock()

# Shared launch token. The brain server requires X-Neurolinked-Token on every
# /api/* call. start.bat / start.sh inject NEUROLINKED_TOKEN into the env of all
# three services so they share one token. If the env var is missing the
# bridge falls back to no-auth (legacy/dev mode) — the brain will respond
# with 401 and we'll just log the failure.
def _auth_headers() -> dict:
    tok = os.environ.get("NEUROLINKED_TOKEN", "")
    return {"X-Neurolinked-Token": tok} if tok else {}


def init(neurolink_url: str = "http://localhost:8000", auto_connect: bool = True) -> bool:
    """Try to reach the NeuroLinked Brain. Returns True if connected.

    Safe to call anytime. Failure is non-fatal — Jarvis keeps running on
    local memory only.
    """
    global _URL, _CONNECTED
    with _LOCK:
        _URL = neurolink_url.rstrip("/")
        if not auto_connect:
            _CONNECTED = False
            print("[NEUROLINK] Auto-connect disabled in config.")
            return False
        _CONNECTED = _ping(_URL)
        if _CONNECTED:
            print(f"[NEUROLINK] Connected to Brain at {_URL}")
        else:
            print(f"[NEUROLINK] Brain not reachable at {_URL} — running on local memory only.")
            print("[NEUROLINK] (Start the NeuroLinked Brain server and Jarvis will auto-detect it next call.)")
        return _CONNECTED


def is_connected() -> bool:
    return _CONNECTED


def _ping(url: str, timeout: float = 2.0) -> bool:
    try:
        req = _urlreq.Request(url + "/", method="GET")
        with _urlreq.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _post_json(path: str, body: dict, timeout: float = 5.0) -> Optional[dict]:
    if not _URL or not _CONNECTED:
        return None
    try:
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            # Same-origin marker so the brain's CSRF guard accepts our POST.
            "Origin": "http://localhost:8340",
            **_auth_headers(),
        }
        req = _urlreq.Request(_URL + path, data=data, headers=headers, method="POST")
        with _urlreq.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (URLError, HTTPError, TimeoutError):
        # Brain went away — mark disconnected but don't crash
        _mark_disconnected()
        return None
    except Exception:
        return None


def _get(path: str, timeout: float = 5.0) -> Optional[dict]:
    if not _URL or not _CONNECTED:
        return None
    try:
        req = _urlreq.Request(_URL + path, headers=_auth_headers(), method="GET")
        with _urlreq.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (URLError, HTTPError, TimeoutError):
        _mark_disconnected()
        return None
    except Exception:
        return None


def _mark_disconnected():
    global _CONNECTED
    with _LOCK:
        if _CONNECTED:
            print("[NEUROLINK] Lost connection to Brain — falling back to local memory.")
        _CONNECTED = False


def retry_connect() -> bool:
    """Attempt to re-establish connection in the background."""
    global _CONNECTED
    if _URL and not _CONNECTED:
        if _ping(_URL):
            with _LOCK:
                _CONNECTED = True
            print(f"[NEUROLINK] Re-connected to Brain at {_URL}")
            return True
    return _CONNECTED


# ---------------------------------------------------------------------------
# Public API — mirrors brain_tools.remember / recall signatures
# ---------------------------------------------------------------------------

def remember(text: str, importance: float = 0.5) -> bool:
    """Push a memory into the NeuroLinked Brain. Silent no-op if disconnected."""
    if not _CONNECTED:
        return False
    resp = _post_json("/api/claude/remember", {"text": text, "importance": importance})
    return resp is not None and not resp.get("error")


def recall(query: str, top_k: int = 5) -> list[str]:
    """Query the NeuroLinked Brain for matching memories. Empty list if disconnected."""
    if not _CONNECTED:
        return []
    from urllib.parse import quote_plus
    resp = _get(f"/api/claude/recall?q={quote_plus(query)}&k={top_k}")
    if not resp:
        return []
    hits = resp.get("results") or resp.get("memories") or []
    return [h.get("text", str(h)) if isinstance(h, dict) else str(h) for h in hits][:top_k]


def status() -> dict:
    """Return current bridge status — useful for /health endpoints."""
    return {
        "url": _URL,
        "connected": _CONNECTED,
    }


# ---------------------------------------------------------------------------
# Background reconnect watcher — keeps Jarvis resilient across Brain restarts
# ---------------------------------------------------------------------------

def start_watcher(interval: float = 30.0):
    """Periodically retry connection in a daemon thread."""
    def _loop():
        while True:
            time.sleep(interval)
            if not _CONNECTED:
                retry_connect()
    t = threading.Thread(target=_loop, daemon=True, name="neurolink-watcher")
    t.start()
