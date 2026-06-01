"""Hyper3D Rodin Gen-2 API wrapper — high-fidelity text/image-to-3D.

Public API:
    generate(prompt, api_key, *, image_urls=None, tier="Regular",
             tapose=False, geometry="quad", poll_timeout=420) -> {ok, model_url, ...}
    download(url, target_path) -> bool

Output: GLB with 4K PBR textures, quad-mesh topology.
Pricing: per-generation (free trial credits available). Gen-2: 30-60 sec.
Docs: https://developer.hyper3d.ai/
Cloud alt: https://fal.ai/models/fal-ai/hyper3d/rodin (different API surface).

This wraps the direct Hyper3D endpoint. If you'd rather route through fal.ai,
use the api_request step or add a separate fal_rodin module.
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

_BASE = "https://hyperhuman.deemos.com/api/v2"


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _post(path: str, body: dict, api_key: str) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{_BASE}{path}", data=data, method="POST",
                                  headers=_auth_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _get(path: str, api_key: str) -> dict:
    req = urllib.request.Request(f"{_BASE}{path}", headers=_auth_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _poll(task_uuid: str, subscription_key: str, api_key: str, timeout: int) -> dict:
    """Rodin polls via /status until task completes, then /download for asset URLs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        r = _post("/status", {"subscription_key": subscription_key}, api_key)
        if "error" in r:
            return {"ok": False, "task_uuid": task_uuid, "error": r["error"]}
        jobs = r.get("jobs") or []
        if not jobs:
            continue
        statuses = {j.get("status") for j in jobs}
        if statuses <= {"Done"}:
            d = _post("/download", {"task_uuid": task_uuid}, api_key)
            if "error" in d:
                return {"ok": False, "task_uuid": task_uuid, "error": d["error"]}
            items = d.get("list") or []
            glb = next((i.get("url") for i in items
                        if str(i.get("name", "")).lower().endswith(".glb")), None)
            if not glb and items:
                glb = items[0].get("url")
            return {"ok": True, "task_uuid": task_uuid, "model_url": glb,
                    "all_assets": items}
        if "Failed" in statuses or "Cancelled" in statuses:
            return {"ok": False, "task_uuid": task_uuid,
                    "error": f"job failed: {statuses}"}
    return {"ok": False, "task_uuid": task_uuid, "error": "timeout polling rodin"}


def generate(prompt: str, api_key: str, *,
             image_urls: list[str] | None = None,
             tier: str = "Regular",
             tapose: bool = False,
             geometry: str = "quad",
             material: str = "PBR",
             poll_timeout: int = 420) -> dict:
    """Generate a 3D model. Either prompt-only or prompt + reference images.

    tier: "Regular" (Gen-2 standard) | "Sketch" (faster preview).
    tapose: True for character T-Pose/A-Pose output (riggable).
    geometry: "quad" (production) | "raw" (faster).
    material: "PBR" | "Shaded".
    """
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not prompt and not image_urls:
        return {"ok": False, "error": "prompt or image_urls required"}
    body = {
        "prompt": (prompt or "")[:1024],
        "tier": tier,
        "TAPose": bool(tapose),
        "geometry": geometry,
        "material": material,
    }
    if image_urls:
        body["images"] = list(image_urls)
    r = _post("/rodin", body, api_key)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    task_uuid = r.get("uuid") or r.get("task_uuid")
    sub_key = r.get("jobs", {}).get("subscription_key") if isinstance(r.get("jobs"), dict) \
              else r.get("subscription_key")
    if not (task_uuid and sub_key):
        return {"ok": False, "error": f"missing uuid/subscription_key: {str(r)[:200]}"}
    return _poll(task_uuid, sub_key, api_key, poll_timeout)


def download(url: str, target_path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            data = r.read()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False


# Tier preset — keep for agents that just want "best characters".
TIERS = {
    "regular": "Regular",
    "sketch":  "Sketch",
    "detail":  "Detail",   # Gen-2 high-detail mode (more credits)
}
