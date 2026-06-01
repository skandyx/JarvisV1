"""Buffer API wrapper for queueing/scheduling social posts.

Buffer's v1 API: https://buffer.com/developers/api
Authenticate with an OAuth access token (free tier supports 3 channels).

Public API:
    list_profiles(token) -> [{id, service, formatted_username}]
    create_post(token, profile_ids, text, *, media_url=None, scheduled_at=None) -> {id, status}
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime


_BASE = "https://api.bufferapp.com/1"


def list_profiles(token: str) -> dict:
    if not token:
        return {"ok": False, "error": "token required"}
    try:
        with urllib.request.urlopen(
            f"{_BASE}/profiles.json?access_token={urllib.parse.quote(token)}",
            timeout=15) as r:
            data = json.loads(r.read())
        return {"ok": True, "profiles": [
            {"id": p.get("id"), "service": p.get("service"),
             "username": p.get("formatted_username") or p.get("service_username")}
            for p in data
        ]}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def create_post(token: str, profile_ids: list, text: str, *,
                media_url: str | None = None, scheduled_at: str | None = None,
                shorten: bool = True, now: bool = False) -> dict:
    """Queue a post on the given profile(s).
    - profile_ids: list of Buffer profile ids (one per channel)
    - scheduled_at: ISO 8601 (UTC). If None and now=False, Buffer queues at next slot.
    - now: post immediately (overrides scheduled_at)
    """
    if not token:
        return {"ok": False, "error": "token required"}
    if not profile_ids:
        return {"ok": False, "error": "at least one profile_id required"}
    if not text:
        return {"ok": False, "error": "text required"}

    form: dict[str, str] = {"text": text[:2200], "shorten": "true" if shorten else "false"}
    for pid in profile_ids:
        form.setdefault("profile_ids[]", pid)
    if media_url:
        form["media[link]"] = media_url
        form["media[picture]"] = media_url
    if now:
        form["now"] = "true"
    elif scheduled_at:
        try:
            ts = int(datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).timestamp())
            form["scheduled_at"] = str(ts)
        except Exception:
            pass

    # Build multi-value form body manually (Buffer expects profile_ids[] repeated)
    parts = []
    for k, v in form.items():
        if k == "profile_ids[]":
            for pid in profile_ids:
                parts.append((k, pid))
        else:
            parts.append((k, v))
    body = urllib.parse.urlencode(parts).encode("utf-8")

    req = urllib.request.Request(
        f"{_BASE}/updates/create.json?access_token={urllib.parse.quote(token)}",
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return {"ok": True, "buffer_count": data.get("buffer_count"),
                "updates": data.get("updates") or [], "raw": data}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
