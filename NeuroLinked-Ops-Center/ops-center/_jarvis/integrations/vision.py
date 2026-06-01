"""OpenAI gpt-4o vision wrapper — multi-image scene description.

Used by `video_analyze` to actually WATCH a video (sample keyframes + describe
what's happening) so the smart clip picker has visual context, not just audio
transcription.

Public API:
    describe_frames(api_key, frame_paths, prompt, *, model="gpt-4o-mini") → {ok, text, error?}

Cost: gpt-4o-mini handles vision at ~$0.15/M input tokens. Each image ≈ 1k
tokens, so 12 keyframes ≈ $0.002 per analysis. gpt-4o (full) ≈ 5x more.
"""
from __future__ import annotations
import base64
import json
import urllib.request
import urllib.error
from pathlib import Path


_ENDPOINT = "https://api.openai.com/v1/chat/completions"


def _encode_image(path: str | Path) -> str:
    """Return data: URI for an image file."""
    p = Path(path)
    suffix = p.suffix.lower().lstrip(".")
    if suffix in ("jpg", "jpeg"): mime = "image/jpeg"
    elif suffix == "png":         mime = "image/png"
    elif suffix == "webp":        mime = "image/webp"
    else:                          mime = "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def describe_frames(api_key: str, frame_paths: list, prompt: str, *,
                    model: str = "gpt-4o-mini",
                    max_tokens: int = 1500,
                    detail: str = "low") -> dict:
    """Send N frames + a prompt to gpt-4o vision. Returns the model's response.

    Args:
        frame_paths: ordered list of image paths (e.g. keyframes from ffmpeg).
        prompt: the question — e.g. "describe each frame in 1-2 sentences;
                identify the main subject, action, setting, and any text overlays".
        detail: "low" (fast/cheap) or "high" (more tokens, sharper recognition).
    """
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    if not frame_paths:
        return {"ok": False, "error": "no frames provided"}

    content: list = [{"type": "text", "text": prompt}]
    for fp in frame_paths:
        try:
            data_uri = _encode_image(fp)
        except Exception as e:
            return {"ok": False, "error": f"failed to encode {fp}: {e}"}
        content.append({
            "type": "image_url",
            "image_url": {"url": data_uri, "detail": detail},
        })

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": int(max_tokens),
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        choices = data.get("choices") or []
        if not choices:
            return {"ok": False, "error": "no choices returned", "raw": str(data)[:300]}
        text = (choices[0].get("message") or {}).get("content", "")
        usage = data.get("usage") or {}
        return {
            "ok": True,
            "text": text,
            "model": model,
            "frame_count": len(frame_paths),
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:400]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
