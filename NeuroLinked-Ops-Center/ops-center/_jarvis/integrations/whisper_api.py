"""OpenAI Whisper transcription wrapper.

Public API:
    transcribe(audio_or_video_path, api_key, *, language=None, response_format="verbose_json")
        → {ok, text, segments, language, duration, error?}

Pricing: $0.006/minute. A 60-second video costs $0.006. Cheapest reliable
transcription on the market.

Whisper accepts mp3, mp4, mpeg, mpga, m4a, wav, webm — and reads audio
from video files automatically.
"""
from __future__ import annotations

import json
import mimetypes
import urllib.request
import urllib.error
import uuid
from pathlib import Path


_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
_MAX_BYTES = 25 * 1024 * 1024  # OpenAI hard limit per upload


def _multipart_body(file_path: Path, *, model: str, response_format: str,
                    language: str | None) -> tuple[bytes, str]:
    boundary = f"----NeuroLinkedOS{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    parts: list[bytes] = []

    def add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")

    add_field("model", model)
    add_field("response_format", response_format)
    if language:
        add_field("language", language)

    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(path: str | Path, api_key: str, *,
               model: str = "whisper-1",
               language: str | None = None,
               response_format: str = "verbose_json") -> dict:
    if not api_key:
        return {"ok": False, "error": "api_key required"}
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"file not found: {p}"}
    size = p.stat().st_size
    if size > _MAX_BYTES:
        return {"ok": False, "error": f"file too large for Whisper API ({size} bytes; max 25MB). Pre-compress audio with ffmpeg before retrying."}

    body, content_type = _multipart_body(p, model=model,
                                         response_format=response_format,
                                         language=language)
    req = urllib.request.Request(_ENDPOINT, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
        if response_format == "verbose_json":
            data = json.loads(raw)
            return {
                "ok": True,
                "text": data.get("text", ""),
                "language": data.get("language"),
                "duration": data.get("duration"),
                "segments": data.get("segments") or [],   # [{start,end,text,...}, ...]
            }
        elif response_format == "json":
            data = json.loads(raw)
            return {"ok": True, "text": data.get("text", "")}
        elif response_format in ("srt", "vtt", "text"):
            return {"ok": True, "text": raw.decode("utf-8", "replace")}
        return {"ok": True, "raw": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
