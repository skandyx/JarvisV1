"""Scenario.gg PBR texture generation API wrapper.

Public API:
    generate_texture(prompt, api_key, *, resolution=2048, seamless=True,
                     poll_timeout=240) -> {ok, maps: {albedo, normal, roughness, ...}}
    download(url, target_path) -> bool

Output: matched PBR map set — albedo / normal / roughness / metallic / height / AO.
        All seamless, tileable, physically coherent.
Pricing: credit-based.
Docs: https://help.scenario.com/en/articles/texture-generation-basics/
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

_BASE = "https://api.cloud.scenario.com/v1"


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}


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


def _poll(job_id: str, api_key: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        r = _get(f"/generate/jobs/{job_id}", api_key)
        if "error" in r:
            return {"ok": False, "job_id": job_id, "error": r["error"]}
        status = (r.get("job") or {}).get("status") or r.get("status")
        if status in ("success", "succeeded", "complete"):
            assets = r.get("textures") or r.get("assets") or {}
            return {"ok": True, "job_id": job_id, "maps": assets, "raw": r}
        if status in ("failure", "failed", "cancelled"):
            return {"ok": False, "job_id": job_id, "status": status,
                    "error": r.get("error") or status}
    return {"ok": False, "job_id": job_id, "error": "timeout polling scenario job"}


def generate_texture(prompt: str, api_key: str, *,
                     resolution: int = 2048,
                     seamless: bool = True,
                     poll_timeout: int = 240) -> dict:
    """Generate a tileable PBR texture set from a text prompt."""
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    body = {
        "prompt": prompt[:1024],
        "resolution": int(resolution),
        "seamless": bool(seamless),
        "outputMaps": ["albedo", "normal", "roughness", "metallic", "height", "ao"],
    }
    r = _post("/generate/txt2img-texture", body, api_key)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    job_id = (r.get("job") or {}).get("jobId") or r.get("jobId") or r.get("id")
    if not job_id:
        return {"ok": False, "error": f"no jobId; got: {str(r)[:200]}"}
    return _poll(job_id, api_key, poll_timeout)


def download(url: str, target_path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False


def download_map_set(maps: dict, target_dir, base_name: str) -> dict:
    """Save every URL in `maps` to target_dir/<base_name>_<map>.png. Returns
    a dict of {map_name: relative_path} for the ones that downloaded ok."""
    saved: dict[str, str] = {}
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, url in (maps or {}).items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        ext = "png"
        out = target_dir / f"{base_name}_{name}.{ext}"
        if download(url, out):
            saved[name] = str(out)
    return saved
