"""
Gestionnaire de Plugins/Skills pour Jarvis

Permet d'installer et gérer des plugins et skills depuis :
  - Dépôts GitHub
  - Archives ZIP/TAR depuis des URLs
  - Dossiers locaux

Chaque plugin est un dossier contenant un fichier plugin.json (manifeste)
avec les métadonnées et la liste des outils fournis. Les outils d'un plugin
sont automatiquement chargés comme fonctions Python importables.

Structure d'un plugin :
  plugins/<plugin_id>/
    plugin.json        — manifeste du plugin
    tools.py           — fonctions d'outils exportées
    requirements.txt   — dépendances Python (optionnel)
    README.md          — documentation (optionnel)

Format du manifeste plugin.json :
{
    "id": "mon_plugin",
    "name": "Mon Super Plugin",
    "version": "1.0.0",
    "description": "Description du plugin",
    "author": "Auteur",
    "source": "github|url|local",
    "source_url": "https://github.com/...",
    "tools": [
        {
            "function": "ma_fonction",
            "name": "ma_fonction",
            "description": "Description de l'outil pour le LLM",
            "parameters": {
                "type": "object",
                "properties": {"param1": {"type": "string", "description": "Un paramètre"}},
                "required": ["param1"]
            }
        }
    ]
}
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
PLUGINS_DIR = _HERE / "plugins"
PLUGINS_REGISTRY = _HERE / "plugins_registry.json"

# S'assurer que le dossier plugins existe
PLUGINS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Registre des plugins
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Charger le registre des plugins."""
    if PLUGINS_REGISTRY.exists():
        try:
            return json.loads(PLUGINS_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"plugins": {}}


def _save_registry(registry: dict) -> None:
    """Sauvegarder le registre des plugins."""
    PLUGINS_REGISTRY.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Chargement dynamique des outils d'un plugin
# ---------------------------------------------------------------------------

_loaded_tool_functions: dict[str, Callable] = {}


def load_plugin_tools(plugin_id: str) -> dict[str, Any]:
    """Charger dynamiquement les outils d'un plugin.

    Importe le module tools.py du plugin et retourne un dictionnaire
    des fonctions disponibles.
    """
    plugin_dir = PLUGINS_DIR / plugin_id
    tools_file = plugin_dir / "tools.py"

    if not tools_file.exists():
        return {"ok": False, "error": f"Fichier tools.py non trouvé pour le plugin '{plugin_id}'."}

    try:
        # Import dynamique du module
        spec = importlib.util.spec_from_file_location(
            f"jarvis_plugin_{plugin_id}", str(tools_file)
        )
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"Impossible de charger le module du plugin '{plugin_id}'."}

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Charger le manifeste pour connaître les outils déclarés
        manifest = load_manifest(plugin_id)
        if not manifest.get("ok"):
            return {"ok": False, "error": f"Manifeste invalide pour le plugin '{plugin_id}'."}

        loaded = {}
        for tool_info in manifest.get("tools", []):
            func_name = tool_info.get("function")
            if func_name and hasattr(module, func_name):
                func = getattr(module, func_name)
                _loaded_tool_functions[f"{plugin_id}.{func_name}"] = func
                loaded[func_name] = {
                    "function": func,
                    "name": tool_info.get("name", func_name),
                    "description": tool_info.get("description", ""),
                    "parameters": tool_info.get("parameters", {}),
                }

        return {"ok": True, "tools": loaded, "count": len(loaded)}
    except Exception as e:
        return {"ok": False, "error": f"Erreur lors du chargement du plugin '{plugin_id}' : {e}"}


def get_tool_function(plugin_id: str, func_name: str) -> Optional[Callable]:
    """Récupérer une fonction d'outil chargée d'un plugin."""
    return _loaded_tool_functions.get(f"{plugin_id}.{func_name}")


# ---------------------------------------------------------------------------
# Opérations du gestionnaire de plugins
# ---------------------------------------------------------------------------

def list_plugins() -> list[dict]:
    """Lister tous les plugins installés avec leur statut."""
    registry = _load_registry()
    result = []
    for pid, info in registry.get("plugins", {}).items():
        plugin_dir = PLUGINS_DIR / pid
        manifest = load_manifest(pid) if plugin_dir.exists() else {}
        result.append({
            "id": pid,
            "name": info.get("name", pid),
            "version": info.get("version", "1.0.0"),
            "description": info.get("description", ""),
            "author": info.get("author", ""),
            "source": info.get("source", "local"),
            "source_url": info.get("source_url", ""),
            "installed_at": info.get("installed_at", ""),
            "enabled": info.get("enabled", True),
            "installed": plugin_dir.exists(),
            "tool_count": len(manifest.get("tools", [])) if manifest.get("ok") else 0,
        })
    return result


def load_manifest(plugin_id: str) -> dict:
    """Charger le manifeste plugin.json d'un plugin."""
    manifest_path = PLUGINS_DIR / plugin_id / "plugin.json"
    if not manifest_path.exists():
        return {"ok": False, "error": f"Manifeste non trouvé pour '{plugin_id}'."}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["ok"] = True
        return data
    except Exception as e:
        return {"ok": False, "error": f"Erreur de lecture du manifeste : {e}"}


def install_from_github(github_url: str, name: Optional[str] = None) -> dict:
    """Installer un plugin depuis un dépôt GitHub.

    Clone le dépôt, cherche un fichier plugin.json et installe les dépendances.
    """
    url = github_url.strip().rstrip("/")
    if not url.startswith("https://github.com/") and not url.startswith("git@github.com:"):
        return {"ok": False, "error": f"URL GitHub invalide : {url}"}

    parts = url.rstrip("/").split("/")
    repo_name = parts[-1].replace(".git", "") if parts else "unknown"
    owner = parts[-2] if len(parts) >= 2 else "unknown"
    plugin_id = f"{owner}_{repo_name}".replace("-", "_")

    target_dir = PLUGINS_DIR / plugin_id
    if target_dir.exists():
        shutil.rmtree(target_dir)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target_dir)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"Échec du clone git : {result.stderr[:300]}"}
    except FileNotFoundError:
        return {"ok": False, "error": "git n'est pas installé. Installez git et réessayez."}
    except subprocess.TimeoutExpired:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"ok": False, "error": "Le clone git a dépassé le délai de 120 secondes."}
    except Exception as e:
        return {"ok": False, "error": f"Erreur lors du clone : {e}"}

    # Créer un manifeste par défaut si absent
    manifest_path = target_dir / "plugin.json"
    if not manifest_path.exists():
        plugin_name = name or repo_name.replace("-", " ").replace("_", " ").title()
        default_manifest = {
            "id": plugin_id,
            "name": plugin_name,
            "version": "1.0.0",
            "description": f"Plugin installé depuis GitHub : {url}",
            "author": owner,
            "source": "github",
            "source_url": url,
            "tools": _auto_discover_tools(target_dir),
        }
        manifest_path.write_text(json.dumps(default_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # Installer les dépendances
    _install_dependencies(target_dir)

    # Mettre à jour le registre
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = _load_registry()
    registry.setdefault("plugins", {})[plugin_id] = {
        "name": manifest.get("name", plugin_id),
        "version": manifest.get("version", "1.0.0"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "source": "github",
        "source_url": url,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
    }
    _save_registry(registry)

    # Charger les outils
    load_plugin_tools(plugin_id)

    return {
        "ok": True,
        "id": plugin_id,
        "name": manifest.get("name", plugin_id),
        "tool_count": len(manifest.get("tools", [])),
        "path": str(target_dir),
    }


def install_from_url(zip_url: str, name: Optional[str] = None) -> dict:
    """Installer un plugin depuis une URL (fichier .zip ou .tar.gz)."""
    url = zip_url.strip()
    if not url:
        return {"ok": False, "error": "URL vide."}

    from urllib.parse import urlparse
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path) or "plugin"
    plugin_id = filename.replace(".zip", "").replace(".tar.gz", "").replace(".tgz", "").replace("-", "_")

    target_dir = PLUGINS_DIR / plugin_id
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        import tempfile
        import urllib.request

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Plugin-Installer/1.0"})
            resp = urllib.request.urlopen(req, timeout=60)
            shutil.copyfileobj(resp, tmp)
            tmp_path = tmp.name

        if tmp_path.endswith(".tar.gz") or tmp_path.endswith(".tgz"):
            shutil.unpack_archive(tmp_path, target_dir, format="gztar")
        else:
            shutil.unpack_archive(tmp_path, target_dir, format="zip")

        os.unlink(tmp_path)
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"ok": False, "error": f"Erreur lors du téléchargement : {e}"}

    # Gérer le cas où l'archive extrait un sous-dossier
    contents = list(target_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        # Déplacer le contenu du sous-dossier vers le dossier du plugin
        sub_dir = contents[0]
        for item in sub_dir.iterdir():
            shutil.move(str(item), str(target_dir / item.name))
        sub_dir.rmdir()

    # Créer un manifeste par défaut si absent
    manifest_path = target_dir / "plugin.json"
    if not manifest_path.exists():
        plugin_name = name or plugin_id.replace("_", " ").title()
        default_manifest = {
            "id": plugin_id,
            "name": plugin_name,
            "version": "1.0.0",
            "description": f"Plugin installé depuis URL : {url}",
            "author": "Inconnu",
            "source": "url",
            "source_url": url,
            "tools": _auto_discover_tools(target_dir),
        }
        manifest_path.write_text(json.dumps(default_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    _install_dependencies(target_dir)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = _load_registry()
    registry.setdefault("plugins", {})[plugin_id] = {
        "name": manifest.get("name", plugin_id),
        "version": manifest.get("version", "1.0.0"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "source": "url",
        "source_url": url,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
    }
    _save_registry(registry)

    load_plugin_tools(plugin_id)

    return {
        "ok": True,
        "id": plugin_id,
        "name": manifest.get("name", plugin_id),
        "tool_count": len(manifest.get("tools", [])),
        "path": str(target_dir),
    }


def install_from_local(local_path: str, name: Optional[str] = None) -> dict:
    """Installer un plugin depuis un dossier local.

    Copie le dossier dans le répertoire plugins/ et l'enregistre.
    """
    src = Path(local_path).resolve()
    if not src.exists():
        return {"ok": False, "error": f"Chemin local non trouvé : {local_path}"}
    if not src.is_dir():
        return {"ok": False, "error": f"Le chemin n'est pas un dossier : {local_path}"}

    plugin_id = src.name.replace("-", "_")
    target_dir = PLUGINS_DIR / plugin_id

    if target_dir.exists():
        shutil.rmtree(target_dir)

    shutil.copytree(src, target_dir)

    # Créer un manifeste par défaut si absent
    manifest_path = target_dir / "plugin.json"
    if not manifest_path.exists():
        plugin_name = name or plugin_id.replace("_", " ").title()
        default_manifest = {
            "id": plugin_id,
            "name": plugin_name,
            "version": "1.0.0",
            "description": f"Plugin installé depuis le dossier local : {local_path}",
            "author": "Local",
            "source": "local",
            "source_url": local_path,
            "tools": _auto_discover_tools(target_dir),
        }
        manifest_path.write_text(json.dumps(default_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    _install_dependencies(target_dir)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = _load_registry()
    registry.setdefault("plugins", {})[plugin_id] = {
        "name": manifest.get("name", plugin_id),
        "version": manifest.get("version", "1.0.0"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "source": "local",
        "source_url": local_path,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
    }
    _save_registry(registry)

    load_plugin_tools(plugin_id)

    return {
        "ok": True,
        "id": plugin_id,
        "name": manifest.get("name", plugin_id),
        "tool_count": len(manifest.get("tools", [])),
        "path": str(target_dir),
    }


def remove_plugin(plugin_id: str) -> dict:
    """Supprimer un plugin installé."""
    registry = _load_registry()
    if plugin_id not in registry.get("plugins", {}):
        return {"ok": False, "error": f"Plugin '{plugin_id}' non trouvé dans le registre."}

    plugin_dir = PLUGINS_DIR / plugin_id
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)

    # Nettoyer les fonctions chargées
    keys_to_remove = [k for k in _loaded_tool_functions if k.startswith(f"{plugin_id}.")]
    for k in keys_to_remove:
        del _loaded_tool_functions[k]

    del registry["plugins"][plugin_id]
    _save_registry(registry)

    return {"ok": True, "removed": plugin_id}


def toggle_plugin(plugin_id: str, enabled: bool) -> dict:
    """Activer ou désactiver un plugin sans le supprimer."""
    registry = _load_registry()
    if plugin_id not in registry.get("plugins", {}):
        return {"ok": False, "error": f"Plugin '{plugin_id}' non trouvé."}

    registry["plugins"][plugin_id]["enabled"] = enabled
    _save_registry(registry)

    if enabled:
        load_plugin_tools(plugin_id)
    else:
        keys_to_remove = [k for k in _loaded_tool_functions if k.startswith(f"{plugin_id}.")]
        for k in keys_to_remove:
            del _loaded_tool_functions[k]

    return {"ok": True, "plugin_id": plugin_id, "enabled": enabled}


def get_all_plugin_tool_schemas() -> list[dict]:
    """Récupérer les schémas d'outils de tous les plugins activés.

    Retourne une liste de schémas au format Anthropic (compatible Jarvis tools).
    """
    registry = _load_registry()
    schemas = []

    for pid, info in registry.get("plugins", {}).items():
        if not info.get("enabled", True):
            continue
        manifest = load_manifest(pid)
        if not manifest.get("ok"):
            continue
        for tool_info in manifest.get("tools", []):
            schemas.append({
                "name": tool_info.get("name", tool_info.get("function", "")),
                "description": tool_info.get("description", ""),
                "input_schema": tool_info.get("parameters", {
                    "type": "object",
                    "properties": {},
                }),
                "_plugin_id": pid,
                "_plugin_function": tool_info.get("function", ""),
            })

    return schemas


def execute_plugin_tool(plugin_id: str, func_name: str, **kwargs) -> str:
    """Exécuter une fonction d'outil d'un plugin."""
    func = get_tool_function(plugin_id, func_name)
    if func is None:
        # Essayer de charger le plugin à la volée
        load_plugin_tools(plugin_id)
        func = get_tool_function(plugin_id, func_name)
    if func is None:
        return f"Outil '{func_name}' du plugin '{plugin_id}' non trouvé."
    try:
        result = func(**kwargs)
        return str(result) if result is not None else ""
    except Exception as e:
        return f"Erreur d'exécution du plugin '{plugin_id}.{func_name}' : {e}"


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _auto_discover_tools(plugin_dir: Path) -> list[dict]:
    """Découvrir automatiquement les outils dans un dossier de plugin.

    Cherche les fonctions documentées dans tools.py ou le fichier Python principal.
    """
    tools_file = plugin_dir / "tools.py"
    if not tools_file.exists():
        # Chercher n'importe quel fichier .py
        py_files = list(plugin_dir.glob("*.py"))
        if py_files:
            tools_file = py_files[0]
        else:
            return []

    try:
        content = tools_file.read_text(encoding="utf-8", errors="ignore")
        tools = []
        # Chercher les fonctions avec docstring
        import re
        func_pattern = re.compile(r'def\s+(\w+)\s*\([^)]*\)\s*->\s*\w+\s*:\s*\n\s*"""([^"]*)"""', re.MULTILINE)
        for match in func_pattern.finditer(content):
            func_name = match.group(1)
            if func_name.startswith("_"):
                continue
            doc = match.group(2).strip()
            tools.append({
                "function": func_name,
                "name": func_name,
                "description": doc or f"Outil {func_name}",
                "parameters": {"type": "object", "properties": {}},
            })
        return tools
    except Exception:
        return []


def _install_dependencies(plugin_dir: Path) -> None:
    """Installer les dépendances Python d'un plugin depuis requirements.txt."""
    req_file = plugin_dir / "requirements.txt"
    if not req_file.exists():
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
            capture_output=True, timeout=120
        )
    except Exception as e:
        print(f"[plugins] Échec de l'installation des dépendances : {e}", flush=True)
