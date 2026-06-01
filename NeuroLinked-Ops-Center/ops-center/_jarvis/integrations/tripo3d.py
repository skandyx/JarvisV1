"""Tripo3D API wrapper — text-to-3D and image-to-3D generation.

Public API:
    text_to_model(prompt, api_key, *, style=None, poll_timeout=300) -> {ok, model_url, ...}
    image_to_model(image_url, api_key, *, poll_timeout=300) -> {ok, model_url, ...}
    download(url, target_path) -> bool

Output: GLB by default (also supports FBX/OBJ via convert tasks).
Pricing: free tier = 300 credits/month. Image-to-3D ~20 credits, text-to-3D ~30 credits.
Docs: https://platform.tripo3d.ai/docs/generation
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

_BASE = "https://api.tripo3d.ai/v2/openapi"


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


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


def _poll(task_id: str, api_key: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        r = _get(f"/task/{task_id}", api_key)
        if "error" in r:
            return {"ok": False, "task_id": task_id, "error": r["error"]}
        d = r.get("data") or {}
        status = d.get("status")
        if status == "success":
            output = d.get("output") or {}
            model_url = output.get("pbr_model") or output.get("model") or output.get("base_model")
            return {"ok": True, "task_id": task_id, "model_url": model_url, "raw": d}
        if status in ("failed", "cancelled", "banned", "expired"):
            return {"ok": False, "task_id": task_id, "status": status,
                    "error": d.get("error_msg") or status}
    return {"ok": False, "task_id": task_id, "error": "timeout polling tripo task"}


def text_to_model(prompt: str, api_key: str, *, style: str | None = None,
                  texture: bool = True, pbr: bool = True, poll_timeout: int = 300) -> dict:
    """Text → 3D model. Returns {ok, model_url, task_id}."""
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    body = {"type": "text_to_model", "prompt": prompt[:1024],
            "texture": texture, "pbr": pbr}
    if style:
        body["style"] = style
    r = _post("/task", body, api_key)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    task_id = (r.get("data") or {}).get("task_id")
    if not task_id:
        return {"ok": False, "error": f"no task_id; got: {str(r)[:200]}"}
    return _poll(task_id, api_key, poll_timeout)


def image_to_model(image_url: str, api_key: str, *,
                   texture: bool = True, pbr: bool = True, poll_timeout: int = 300) -> dict:
    """Image URL → 3D model. Tripo 3.1: ~2 sec mesh, clean topology."""
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not image_url:
        return {"ok": False, "error": "image_url required"}
    body = {"type": "image_to_model",
            "file": {"type": "url", "url": image_url},
            "texture": texture, "pbr": pbr}
    r = _post("/task", body, api_key)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    task_id = (r.get("data") or {}).get("task_id")
    if not task_id:
        return {"ok": False, "error": f"no task_id; got: {str(r)[:200]}"}
    return _poll(task_id, api_key, poll_timeout)


def download(url: str, target_path) -> bool:
    """Fetch a Tripo output URL (GLB) to disk."""
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            data = r.read()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False


# Curated style aliases — Tripo accepts a fixed list of style strings.
STYLES = {
    "realistic": "person:person2cartoon",  # placeholder; set None for default
    "stylized": "object:clay",
    "voxel":    "object:steampunk",
    "lego":     "gold",
    "default":  None,
}
