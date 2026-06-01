"""HeyGen API wrapper — AI avatar / talking-head video generation.

Public API:
    generate(api_key, script, *, avatar_id, voice_id, dimension, background)
        → {ok, video_id, status, video_url?, thumbnail_url?, error?}

Auth: X-API-Key header.

Generation is async — POST /v2/video/generate returns a video_id. Poll
GET /v1/video_status.get?video_id=... until status == 'completed', then
the response carries `video_url` (an mp4 URL valid for ~7 days).

Pricing (as of mid-2025):
    Free trial      → 1 minute total
    Creator $24/mo  → 15 minutes / month + 5 instant avatars
    Team    $89/mo  → unlimited minutes
    API     metered → ~$0.30/min via API plan
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path


_BASE_V2 = "https://api.heygen.com/v2"
_BASE_V1 = "https://api.heygen.com/v1"

# Sensible defaults — Daisy is HeyGen's free public stock avatar usable on every
# tier, paired with a clean English VO. Override per agent via inputs.
DEFAULT_AVATAR_ID = "Daisy-inskirt-20220818"
DEFAULT_VOICE_ID  = "1bd001e7e50f421d891986aad5158bc8"  # Brian, neutral US English


def generate(api_key: str, script: str, *,
             avatar_id: str = DEFAULT_AVATAR_ID,
             voice_id: str = DEFAULT_VOICE_ID,
             width: int = 1080, height: int = 1920,
             background_color: str = "#000000",
             avatar_style: str = "normal",
             poll_timeout: int = 300) -> dict:
    """Submit a talking-head generation and poll until complete or timeout.
    Returns {ok, video_id, video_url?, thumbnail_url?, status, error?}.
    """
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not script:
        return {"ok": False, "error": "script required"}

    body = json.dumps({
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": avatar_id, "avatar_style": avatar_style},
            "voice":     {"type": "text",   "input_text": script[:1500], "voice_id": voice_id},
            "background": {"type": "color", "value": background_color},
        }],
        "dimension": {"width": int(width), "height": int(height)},
    }).encode("utf-8")

    req = urllib.request.Request(f"{_BASE_V2}/video/generate", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    vid = (data.get("data") or {}).get("video_id")
    if not vid:
        return {"ok": False, "error": f"no video_id; got: {str(data)[:200]}"}

    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        time.sleep(4)
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"{_BASE_V1}/video_status.get?video_id={vid}",
                    headers={"X-API-Key": api_key}), timeout=15) as r:
                p = json.loads(r.read())
        except Exception as e:
            return {"ok": False, "error": f"poll failed: {e}", "video_id": vid}
        d = p.get("data") or {}
        status = d.get("status")
        if status == "completed":
            return {
                "ok": True,
                "status": "completed",
                "video_id": vid,
                "video_url": d.get("video_url"),
                "thumbnail_url": d.get("thumbnail_url"),
                "duration": d.get("duration"),
            }
        if status in ("failed", "canceled"):
            err = (d.get("error") or {}).get("message") or status
            return {"ok": False, "status": status, "video_id": vid, "error": err}
    return {"ok": False, "status": "timeout", "video_id": vid, "error": "generation timed out"}


def list_avatars(api_key: str) -> dict:
    """Returns the user's HeyGen avatars (helpful for the UI to populate a picker)."""
    try:
        req = urllib.request.Request(f"{_BASE_V2}/avatars",
            headers={"X-API-Key": api_key, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        avatars = ((data.get("data") or {}).get("avatars")) or []
        return {"ok": True, "avatars": [
            {"id": a.get("avatar_id"), "name": a.get("avatar_name"),
             "preview_image": a.get("preview_image_url")} for a in avatars
        ]}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def download(url: str, target_path) -> bool:
    """Fetch the generated mp4 to disk."""
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False
