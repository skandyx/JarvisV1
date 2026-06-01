"""Replicate.com prediction API wrapper.

Public API:
    run(model, version, input_dict, api_token, *, poll_timeout=180) -> {output, status, prediction_id}

Used for:
    - Image: stability-ai/sdxl
    - Video: stability-ai/stable-video-diffusion, lucataco/animate-diff
    - TTS:   suno-ai/bark (free voice clone fallback)

Pricing is per-second of GPU time, billed by Replicate. SVD ~$0.0023/sec, SDXL ~$0.002/sec.
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error


_BASE = "https://api.replicate.com/v1"


def run(model: str, version: str | None, input_dict: dict, api_token: str, *, poll_timeout: int = 180) -> dict:
    """Run a Replicate prediction.

    If `version` is None, calls the per-model endpoint /v1/models/<owner>/<name>/predictions
    (used for "official" models like kling, hailuo where Replicate doesn't pin a version
    hash). Otherwise calls /v1/predictions with the version hash.
    """
    if not api_token:
        return {"error": "api_token required", "ok": False}

    body = json.dumps({"version": version, "input": input_dict or {}} if version
                      else {"input": input_dict or {}}).encode("utf-8")
    url = f"{_BASE}/predictions" if version else f"{_BASE}/models/{model}/predictions"
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Token {api_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pred = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}", "ok": False}
    except Exception as e:
        return {"error": str(e)[:200], "ok": False}

    pid = pred.get("id")
    if not pid:
        return {"error": f"no prediction id; got: {pred}", "ok": False}

    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"{_BASE}/predictions/{pid}",
                    headers={"Authorization": f"Token {api_token}"}), timeout=15) as r:
                p = json.loads(r.read())
        except Exception as e:
            return {"error": f"poll failed: {e}", "ok": False, "prediction_id": pid}
        status = p.get("status")
        if status == "succeeded":
            return {
                "ok": True,
                "status": "succeeded",
                "prediction_id": pid,
                "output": p.get("output"),
                "model": model,
                "metrics": p.get("metrics") or {},
            }
        if status in ("failed", "canceled"):
            return {
                "ok": False,
                "status": status,
                "prediction_id": pid,
                "error": p.get("error") or status,
            }
    return {"ok": False, "error": "timeout polling prediction", "prediction_id": pid}


def download(url: str, target_path) -> bool:
    """Fetch a Replicate output URL (image or video) to disk."""
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        from pathlib import Path
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Curated model defaults so the agent .md files don't have to memorize hashes.
# Update versions here when Replicate publishes new releases.
# ---------------------------------------------------------------------------
MODELS = {
    # Image — SDXL is the workhorse for ad creative
    "sdxl": {
        "model": "stability-ai/sdxl",
        "version": "39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
        "default_input": {"width": 1024, "height": 1024, "num_inference_steps": 30, "guidance_scale": 7.5},
    },
    # Video — Stable Video Diffusion: takes an image, animates 4 seconds
    "svd": {
        "model": "stability-ai/stable-video-diffusion",
        "version": "3f0457e4619daac51203dedb472816fd4af51f3149fa7a9e0b5ffcf1b8172438",
        "default_input": {"video_length": "14_frames_with_svd", "sizing_strategy": "maintain_aspect_ratio", "frames_per_second": 6},
    },
    # Video — AnimateDiff: text-to-video, more cinematic for ad intros
    "animatediff": {
        "model": "lucataco/animate-diff",
        "version": "beecf59c4aee8d81bf04f0381033dfa10dc16e845b4ae00d281e2fa377e48a9f",
        "default_input": {"steps": 25, "guidance_scale": 7.5, "frames": 16},
    },
    # Video — Kling 2.0 Master: top-tier realism (rivals Veo / Sora) for cinematic
    # ad creative. Pricing ~$0.10/sec generated. 5-10 second clips, text-to-video
    # OR image-to-video via the `start_image` input.
    "kling": {
        "model": "kwaivgi/kling-v2.0",
        "version": None,  # Replicate accepts the model slug without a version pin for "official" models
        "default_input": {"duration": 5, "aspect_ratio": "9:16", "negative_prompt": "blurry, low quality, distorted"},
    },
    # Video — Kling 1.6 Pro: cheaper fallback (~$0.05/sec) when 2.0 budget is tight
    "kling-pro": {
        "model": "kwaivgi/kling-v1.6-pro",
        "version": None,
        "default_input": {"duration": 5, "aspect_ratio": "9:16"},
    },
    # Video — Hailuo / MiniMax video-01: another tier-1 realism option, 6 sec only
    "hailuo": {
        "model": "minimax/video-01",
        "version": None,
        "default_input": {"prompt_optimizer": True},
    },
}
