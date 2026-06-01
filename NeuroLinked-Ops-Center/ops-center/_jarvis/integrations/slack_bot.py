"""Slack Web API wrapper (bot token).

Public API:
    post_message(token, channel, text, *, blocks=None, thread_ts=None) -> {ok, ts, channel}
    open_dm(token, user_id) -> {ok, channel_id}
    dm_user(token, user_id, text) -> {ok, ts}
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error


_BASE = "https://slack.com/api"


def _post(token: str, method: str, body: dict) -> dict:
    if not token:
        return {"ok": False, "error": "token required"}
    req = urllib.request.Request(f"{_BASE}/{method}",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error", "unknown"), "raw": data}
        return data
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def post_message(token: str, channel: str, text: str, *,
                 blocks=None, thread_ts: str | None = None) -> dict:
    body: dict = {"channel": channel, "text": text[:3000]}
    if blocks:
        body["blocks"] = blocks
    if thread_ts:
        body["thread_ts"] = thread_ts
    r = _post(token, "chat.postMessage", body)
    if r.get("ok"):
        return {"ok": True, "ts": r.get("ts"), "channel": r.get("channel")}
    return r


def open_dm(token: str, user_id: str) -> dict:
    r = _post(token, "conversations.open", {"users": user_id})
    if r.get("ok"):
        return {"ok": True, "channel_id": (r.get("channel") or {}).get("id")}
    return r


def dm_user(token: str, user_id: str, text: str) -> dict:
    o = open_dm(token, user_id)
    if not o.get("ok"):
        return o
    return post_message(token, o["channel_id"], text)
