"""ElevenLabs TTS — text-to-speech wrapper.

Public API:
    synthesize(text, api_key, voice_id, target_path) -> {ok, path?, bytes?}

Free tier: 10k chars/month. Creator tier $22/mo: 100k chars (~2 hrs voiceover).
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from pathlib import Path


_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George" — clear male VO, agency-friendly


def synthesize(text: str, api_key: str, voice_id: str, target_path, *,
               model_id: str = "eleven_multilingual_v2", stability: float = 0.5,
               similarity_boost: float = 0.75) -> dict:
    if not text or not api_key:
        return {"ok": False, "error": "text and api_key required"}
    voice_id = voice_id or DEFAULT_VOICE
    body = json.dumps({
        "text": text[:5000],
        "model_id": model_id,
        "voice_settings": {"stability": stability, "similarity_boost": similarity_boost},
    }).encode("utf-8")
    req = urllib.request.Request(f"{_BASE}/{voice_id}", data=body, method="POST",
        headers={"Content-Type": "application/json", "xi-api-key": api_key, "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    return {"ok": True, "path": str(target), "bytes": len(audio), "voice_id": voice_id, "model_id": model_id}
