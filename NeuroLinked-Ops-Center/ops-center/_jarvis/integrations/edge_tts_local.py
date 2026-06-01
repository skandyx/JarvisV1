"""Edge TTS Local — Synthèse vocale gratuite via Microsoft Edge TTS.

Pas de clé API nécessaire. Utilise les serveurs Microsoft Edge mais sans
authentification. Des dizaines de voix disponibles en français, anglais,
espagnol, etc.

Dépendance : pip install edge-tts
"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import urllib.request
from pathlib import Path

# Voix françaises disponibles
FRENCH_VOICES = {
    "denise": "fr-FR-DeniseNeural",
    "henri": "fr-FR-HenriNeural", 
    "vivienne": "fr-FR-VivienneMultilingualNeural",
    "alois": "fr-FR-AloisNeural",
    "eloi": "fr-FR-EloiNeural",
    "jean": "fr-FR-JeanNeural",
    "celine": "fr-FR-CelineNeural",
    "josephine": "fr-FR-JosephineNeural",
}

# Voix anglaises disponibles
ENGLISH_VOICES = {
    "ava": "en-US-AvaNeural",
    "andrew": "en-US-AndrewNeural",
    "emma": "en-US-EmmaNeural",
    "brian": "en-US-BrianNeural",
    "jenny": "en-US-JennyNeural",
    "guy": "en-US-GuyNeural",
    "aria": "en-US-AriaNeural",
    "davis": "en-US-DavisNeural",
}

ALL_VOICES = {**FRENCH_VOICES, **ENGLISH_VOICES}
DEFAULT_VOICE = "en-US-GuyNeural"

async def _synthesize_edge(text: str, voice: str, output_path: str, rate: str = "+0%", pitch: str = "+0Hz") -> dict:
    """Synthesize speech using edge-tts library."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text[:5000], voice, rate=rate, pitch=pitch)
        await communicate.save(output_path)
        size = os.path.getsize(output_path)
        return {"ok": True, "path": output_path, "bytes": size, "voice": voice, "engine": "edge_tts", "format": "mp3"}
    except ImportError:
        return {"ok": False, "error": "edge-tts non installé. Exécutez : pip install edge-tts"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def synthesize(text: str, voice_id: str = "", target_path = None, **kwargs) -> dict:
    """Synchronous wrapper for edge-tts synthesis.
    
    Args:
        text: Text to synthesize (max 5000 chars)
        voice_id: Voice name (e.g. "fr-FR-DeniseNeural") or shorthand (e.g. "denise")
        target_path: Output path for audio file. If None, uses temp file.
    
    Returns:
        dict with ok, path, bytes, voice, engine, format
    """
    if not text:
        return {"ok": False, "error": "text requis"}
    
    # Resolve voice shorthand
    voice = voice_id or DEFAULT_VOICE
    if voice.lower() in ALL_VOICES:
        voice = ALL_VOICES[voice.lower()]
    
    if target_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        target_path = tmp.name
        tmp.close()
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If called from async context, create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    asyncio.run,
                    _synthesize_edge(text, voice, target_path, 
                                     rate=kwargs.get("rate", "+0%"),
                                     pitch=kwargs.get("pitch", "+0Hz"))
                ).result()
            return result
        else:
            return asyncio.run(_synthesize_edge(text, voice, target_path,
                                                 rate=kwargs.get("rate", "+0%"),
                                                 pitch=kwargs.get("pitch", "+0Hz")))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def list_voices(lang: str = "") -> list:
    """List available voices (local static list, no API call)."""
    voices = []
    for k, v in FRENCH_VOICES.items():
        voices.append({"id": v, "name": k, "lang": "fr"})
    for k, v in ENGLISH_VOICES.items():
        voices.append({"id": v, "name": k, "lang": "en"})
    if lang == "fr":
        return [v for v in voices if v["lang"] == "fr"]
    if lang == "en":
        return [v for v in voices if v["lang"] == "en"]
    return voices

def is_available() -> bool:
    """Check if edge-tts is installed."""
    try:
        import edge_tts
        return True
    except ImportError:
        return False
