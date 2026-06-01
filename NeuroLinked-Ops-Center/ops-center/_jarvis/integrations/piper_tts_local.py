"""Piper TTS Local — Synthèse vocale 100% hors-ligne.

Piper est un moteur TTS rapide et léger qui tourne entièrement en local.
Pas d'API, pas de réseau, pas de clé. Téléchargez un modèle de voix
et Piper génère l'audio directement sur votre machine.

Dépendance : pip install piper-tts
Modèles : https://huggingface.co/rhasspy/piper-voices
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path

# Dossier des modèles Piper
PIPER_MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "piper_models")

DEFAULT_MODEL = "fr_FR-upmc-medium"  # Bonne voix française par défaut

def _find_model(model_name: str = "") -> str | None:
    """Trouve le fichier .onnx du modèle Piper."""
    search_dir = PIPER_MODELS_DIR
    if not os.path.isdir(search_dir):
        return None
    
    name = model_name or DEFAULT_MODEL
    # Cherche le fichier .onnx correspondant
    for f in os.listdir(search_dir):
        if f.endswith(".onnx") and name.replace("-", "_") in f.replace("-", "_").lower():
            return os.path.join(search_dir, f)
    return None

def synthesize(text: str, voice_id: str = "", target_path = None, **kwargs) -> dict:
    """Synthesize speech using Piper TTS (100% offline).
    
    Args:
        text: Text to synthesize
        voice_id: Model name (e.g. "fr_FR-upmc-medium")
        target_path: Output WAV path. If None, uses temp file.
    
    Returns:
        dict with ok, path, bytes, voice, engine, format
    """
    if not text:
        return {"ok": False, "error": "text requis"}
    
    model_path = _find_model(voice_id)
    if not model_path:
        return {
            "ok": False, 
            "error": f"Modèle Piper non trouvé. Téléchargez un modèle dans {PIPER_MODELS_DIR}/ "
                     f"ou installez-le via : pip install piper-tts && piper-download --language fr"
        }
    
    if target_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        target_path = tmp.name
        tmp.close()
    
    try:
        result = subprocess.run(
            ["piper", "--model", model_path, "--output_file", target_path],
            input=text[:5000],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"Piper error: {result.stderr[:200]}"}
        
        size = os.path.getsize(target_path)
        return {"ok": True, "path": target_path, "bytes": size, "voice": voice_id or DEFAULT_MODEL, "engine": "piper", "format": "wav"}
    except FileNotFoundError:
        return {"ok": False, "error": "piper non installé. Exécutez : pip install piper-tts"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def list_models() -> list:
    """List available Piper models (scans piper_models/ directory)."""
    if not os.path.isdir(PIPER_MODELS_DIR):
        return []
    models = []
    for f in os.listdir(PIPER_MODELS_DIR):
        if f.endswith(".onnx"):
            name = f.replace(".onnx", "")
            models.append({"id": name, "name": name, "path": os.path.join(PIPER_MODELS_DIR, f)})
    return models

def is_available() -> bool:
    """Check if Piper TTS is installed and has at least one model."""
    try:
        result = subprocess.run(["piper", "--help"], capture_output=True, timeout=5)
        return result.returncode == 0 and _find_model() is not None
    except Exception:
        return False
