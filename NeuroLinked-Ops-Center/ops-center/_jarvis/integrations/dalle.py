"""DALL·E 3 image generation. Uses the OpenAI Images endpoint.

Public API:
    generate(prompt, api_key, size="1024x1024", quality="standard") -> {url, b64?, model, usage}

Costs (as of 2026-01):
    standard 1024x1024  → $0.040
    HD       1024x1024  → $0.080
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error


_ENDPOINT = "https://api.openai.com/v1/images/generations"


def generate(prompt: str, api_key: str, *, size: str = "1024x1024", quality: str = "standard", n: int = 1) -> dict:
    if not prompt or not api_key:
        return {"error": "prompt and api_key required", "ok": False}
    body = json.dumps({
        "model": "dall-e-3",
        "prompt": prompt[:4000],
        "n": int(n),
        "size": size,
        "quality": quality,
        "response_format": "url",
    }).encode("utf-8")
    req = urllib.request.Request(_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        items = data.get("data") or []
        if not items:
            return {"error": "no image returned", "ok": False}
        return {
            "ok": True,
            "url": items[0].get("url"),
            "revised_prompt": items[0].get("revised_prompt"),
            "model": "dall-e-3",
            "size": size,
            "quality": quality,
        }
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}", "ok": False}
    except Exception as e:
        return {"error": str(e)[:200], "ok": False}


def download(url: str, target_path) -> bool:
    """Fetch the generated image URL to disk. OpenAI URLs are short-lived (~1h)."""
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
        from pathlib import Path
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False
