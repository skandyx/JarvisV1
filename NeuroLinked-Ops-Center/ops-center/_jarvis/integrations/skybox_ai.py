"""Blockade Labs Skybox AI API wrapper — text → 360° panorama / HDRI for IBL.

Public API:
    generate(prompt, api_key, *, style_id=None, hdri=True,
             resolution=4096, poll_timeout=240) -> {ok, image_url, hdri_url, ...}
    download(url, target_path) -> bool

Output: equirectangular panorama (PNG/JPG) + HDR (when hdri=True).
Pricing: free tier (5 generations); paid tiers up to 16K with full API access.
Docs: https://api-documentation.blockadelabs.com/
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

_BASE = "https://backend.blockadelabs.com/api/v1"


def _auth_headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _post(path: str, body: dict, api_key: str) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{_BASE}{path}", data=data, method="POST",
                                  headers=_auth_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _get(path: str, api_key: str) -> dict:
    req = urllib.request.Request(f"{_BASE}{path}", headers=_auth_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _poll(skybox_id: str, api_key: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        r = _get(f"/imagine/requests/{skybox_id}", api_key)
        if "error" in r:
            return {"ok": False, "skybox_id": skybox_id, "error": r["error"]}
        req_data = r.get("request") or r
        status = req_data.get("status")
        if status == "complete":
            return {"ok": True, "skybox_id": skybox_id,
                    "image_url":     req_data.get("file_url"),
                    "thumb_url":     req_data.get("thumb_url"),
                    "hdri_url":      req_data.get("depth_map_url") or req_data.get("hdri_url"),
                    "raw":           req_data}
        if status in ("error", "abort", "failed"):
            return {"ok": False, "skybox_id": skybox_id, "status": status,
                    "error": req_data.get("error_message") or status}
    return {"ok": False, "skybox_id": skybox_id, "error": "timeout polling skybox"}


def generate(prompt: str, api_key: str, *,
             style_id: int | None = None,
             hdri: bool = True,
             resolution: int = 4096,
             poll_timeout: int = 240) -> dict:
    """Text → 360° skybox + HDRI. Returns {ok, image_url, hdri_url}."""
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    body = {"prompt": prompt[:550]}
    if style_id is not None:
        body["skybox_style_id"] = int(style_id)
    if hdri:
        body["return_depth"] = True
    r = _post("/skybox", body, api_key)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    sid = r.get("id") or (r.get("request") or {}).get("id")
    if not sid:
        return {"ok": False, "error": f"no skybox id: {str(r)[:200]}"}
    return _poll(str(sid), api_key, poll_timeout)


def download(url: str, target_path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            data = r.read()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False


# Common Skybox style IDs — see /skybox/styles for the full live list.
COMMON_STYLES = {
    "realistic":      2,
    "anime":          3,
    "digital_painting": 4,
    "fantasy_landscape": 7,
    "sci_fi":         9,
    "studio":        47,    # neutral product-shot HDRI
    "sunset":        16,
}
