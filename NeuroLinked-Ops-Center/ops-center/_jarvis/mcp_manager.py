"""
Gestionnaire de serveurs MCP (Model Context Protocol)

Permet à Jarvis de :
  - Lister les serveurs MCP installés
  - Installer de nouveaux serveurs MCP depuis GitHub ou une URL
  - Créer de nouveaux serveurs MCP à partir de modèles
  - Configurer les serveurs MCP pour Claude Desktop / Claude Code
  - Démarrer/arrêter les serveurs MCP

Les serveurs MCP sont stockés dans le dossier mcp_servers/ à côté de ce fichier.
La configuration est persistée dans mcp_registry.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Dossier de base pour les serveurs MCP
_HERE = Path(__file__).resolve().parent
MCP_SERVERS_DIR = _HERE / "mcp_servers"
MCP_REGISTRY = _HERE / "mcp_registry.json"
MCP_TEMPLATES_DIR = _HERE / "mcp_templates"

# S'assurer que les dossiers existent
MCP_SERVERS_DIR.mkdir(exist_ok=True)
MCP_TEMPLATES_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Modèles (templates) de serveurs MCP
# ---------------------------------------------------------------------------

MCP_TEMPLATES = {
    "brain_tools": {
        "name": "Outils Brain NeuroLinked",
        "description": "Expose les outils du Brain (tâches, mémoire, notes) via MCP.",
        "file": "brain_tools_server.py",
        "content": '''"""
Serveur MCP — Outils Brain NeuroLinked
Généré automatiquement par le gestionnaire MCP de Jarvis.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("[mcp] ERREUR : package 'mcp' non installé. Lancez : pip install mcp", file=sys.stderr)
    sys.exit(1)

import brain_tools

# Résoudre le chemin du Brain depuis config.json
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
_brain_path = None
try:
    with open(_config_path, "r", encoding="utf-8") as f:
        _cfg = json.load(f)
    _brain_path = _cfg.get("brain_path") or _cfg.get("obsidian_inbox_path")
except Exception:
    pass
if not _brain_path:
    _brain_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain")

brain_tools.init(_brain_path)

mcp = FastMCP("NeuroLinked Brain")

@mcp.tool()
def add_task(task: str) -> str:
    """Ajouter une nouvelle tâche à la liste de tâches de l'utilisateur."""
    return brain_tools.add_task(task)

@mcp.tool()
def list_tasks() -> str:
    """Lister toutes les tâches ouvertes."""
    tasks = brain_tools.list_tasks()
    return "\\n- ".join(["Tâches ouvertes :"] + tasks) if tasks else "Aucune tâche ouverte."

@mcp.tool()
def complete_task(query: str) -> str:
    """Marquer une tâche comme terminée par correspondance partielle."""
    return brain_tools.complete_task(query)

@mcp.tool()
def remember(note: str) -> str:
    """Sauvegarder une note horodatée dans le Brain."""
    return brain_tools.remember(note)

@mcp.tool()
def recall(query: str) -> str:
    """Rechercher dans tous les fichiers .md du Brain."""
    return brain_tools.recall(query)

@mcp.tool()
def read_memory() -> str:
    """Lire le contenu complet de Memory.md."""
    return brain_tools.read_memory()

if __name__ == "__main__":
    mcp.run()
''',
    },
    "custom_tools": {
        "name": "Outils Personnalisés",
        "description": "Modèle vide pour créer vos propres outils MCP. Ajoutez vos fonctions décorées @mcp.tool().",
        "file": "custom_tools_server.py",
        "content": '''"""
Serveur MCP — Outils Personnalisés
Généré automatiquement par le gestionnaire MCP de Jarvis.

INSTRUCTIONS :
1. Ajoutez vos fonctions décorées avec @mcp.tool() ci-dessous
2. Chaque fonction doit avoir un docstring (utilisé comme description de l'outil)
3. Les paramètres de type str, int, float, bool sont automatiquement détectés
4. Relancez le serveur pour voir les changements
"""
import sys, os
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("[mcp] ERREUR : package 'mcp' non installé. Lancez : pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("Outils Personnalisés Jarvis")

@mcp.tool()
def exemple_saluer(nom: str) -> str:
    """Dire bonjour à quelqu'un par son nom. Exemple de tool personnalisé."""
    return f"Bonjour {nom} ! Je suis un outil MCP personnalisé de Jarvis."

# ============================================
# AJOUTEZ VOS OUTILS CI-DESSOUS
# ============================================

if __name__ == "__main__":
    mcp.run()
''',
    },
    "web_search": {
        "name": "Recherche Web",
        "description": "Serveur MCP pour la recherche web via DuckDuckGo.",
        "file": "web_search_server.py",
        "content": '''"""
Serveur MCP — Recherche Web
Généré automatiquement par le gestionnaire MCP de Jarvis.
"""
import sys, os, json, urllib.request, urllib.parse
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("[mcp] ERREUR : package 'mcp' non installé. Lancez : pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("Recherche Web Jarvis")

@mcp.tool()
def search_web(query: str, max_results: int = 5) -> str:
    """Rechercher sur le web via DuckDuckGo Instant Answer API."""
    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-MCP/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        results = []
        abstract = data.get("Abstract", "")
        if abstract:
            results.append(f"Résumé : {abstract}")
        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text']}")
        return "\\n".join(results) if results else f"Aucun résultat pour '{query}'."
    except Exception as e:
        return f"Erreur de recherche : {e}"

if __name__ == "__main__":
    mcp.run()
''',
    },
}


# ---------------------------------------------------------------------------
# Registre MCP — persistance JSON
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Charger le registre des serveurs MCP."""
    if MCP_REGISTRY.exists():
        try:
            return json.loads(MCP_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"servers": {}}


def _save_registry(registry: dict) -> None:
    """Sauvegarder le registre."""
    MCP_REGISTRY.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Opérations du gestionnaire MCP
# ---------------------------------------------------------------------------

def list_servers() -> list[dict]:
    """Lister tous les serveurs MCP enregistrés avec leur statut."""
    registry = _load_registry()
    result = []
    for sid, info in registry.get("servers", {}).items():
        server_dir = MCP_SERVERS_DIR / sid
        entry_file = server_dir / info.get("entry_file", "server.py")
        result.append({
            "id": sid,
            "name": info.get("name", sid),
            "description": info.get("description", ""),
            "source": info.get("source", "local"),
            "source_url": info.get("source_url", ""),
            "entry_file": info.get("entry_file", "server.py"),
            "installed_at": info.get("installed_at", ""),
            "enabled": info.get("enabled", True),
            "installed": server_dir.exists(),
            "entry_exists": entry_file.exists(),
        })
    return result


def install_from_github(github_url: str, name: Optional[str] = None) -> dict:
    """Installer un serveur MCP depuis un dépôt GitHub.

    Clone le dépôt dans mcp_servers/<id>/ et l'enregistre dans le registre.
    Cherche automatiquement un fichier server.py, main.py ou *_mcp.py comme point d'entrée.
    """
    # Nettoyer l'URL GitHub
    url = github_url.strip().rstrip("/")
    if not url.startswith("https://github.com/") and not url.startswith("git@github.com:"):
        return {"ok": False, "error": f"URL GitHub invalide : {url}"}

    # Générer un ID à partir de l'URL
    parts = url.rstrip("/").split("/")
    repo_name = parts[-1].replace(".git", "") if parts else "unknown"
    owner = parts[-2] if len(parts) >= 2 else "unknown"
    server_id = f"{owner}_{repo_name}".replace("-", "_")

    if not name:
        name = repo_name.replace("-", " ").replace("_", " ").title()

    target_dir = MCP_SERVERS_DIR / server_id
    if target_dir.exists():
        shutil.rmtree(target_dir)

    try:
        # Cloner le dépôt (shallow clone pour la vitesse)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target_dir)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"Échec du clone git : {result.stderr[:300]}"}
    except FileNotFoundError:
        return {"ok": False, "error": "git n'est pas installé sur ce système. Installez git et réessayez."}
    except subprocess.TimeoutExpired:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"ok": False, "error": "Le clone git a dépassé le délai de 120 secondes."}
    except Exception as e:
        return {"ok": False, "error": f"Erreur lors du clone : {e}"}

    # Chercher le point d'entrée
    entry_file = _find_entry_file(target_dir)

    # Enregistrer dans le registre
    registry = _load_registry()
    registry.setdefault("servers", {})[server_id] = {
        "name": name,
        "description": f"Serveur MCP installé depuis GitHub : {url}",
        "source": "github",
        "source_url": url,
        "entry_file": entry_file,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
    }
    _save_registry(registry)

    return {
        "ok": True,
        "id": server_id,
        "name": name,
        "entry_file": entry_file,
        "path": str(target_dir),
    }


def install_from_url(zip_url: str, name: Optional[str] = None) -> dict:
    """Installer un serveur MCP depuis une URL (fichier .zip ou .tar.gz).

    Télécharge l'archive, l'extrait dans mcp_servers/<id>/ et l'enregistre.
    """
    url = zip_url.strip()
    if not url:
        return {"ok": False, "error": "URL vide."}

    # Générer un ID
    from urllib.parse import urlparse
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path) or "mcp_server"
    server_id = filename.replace(".zip", "").replace(".tar.gz", "").replace(".tgz", "").replace("-", "_")
    if not name:
        name = server_id.replace("_", " ").title()

    target_dir = MCP_SERVERS_DIR / server_id
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        import tempfile
        import urllib.request

        # Télécharger l'archive
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-MCP-Installer/1.0"})
            resp = urllib.request.urlopen(req, timeout=60)
            shutil.copyfileobj(resp, tmp)
            tmp_path = tmp.name

        # Extraire
        if tmp_path.endswith(".tar.gz") or tmp_path.endswith(".tgz"):
            shutil.unpack_archive(tmp_path, target_dir, format="gztar")
        else:
            shutil.unpack_archive(tmp_path, target_dir, format="zip")

        os.unlink(tmp_path)
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"ok": False, "error": f"Erreur lors du téléchargement/extraction : {e}"}

    entry_file = _find_entry_file(target_dir)

    registry = _load_registry()
    registry.setdefault("servers", {})[server_id] = {
        "name": name,
        "description": f"Serveur MCP installé depuis URL : {url}",
        "source": "url",
        "source_url": url,
        "entry_file": entry_file,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
    }
    _save_registry(registry)

    return {
        "ok": True,
        "id": server_id,
        "name": name,
        "entry_file": entry_file,
        "path": str(target_dir),
    }


def create_from_template(template_id: str, server_name: Optional[str] = None) -> dict:
    """Créer un nouveau serveur MCP à partir d'un modèle prédéfini."""
    template = MCP_TEMPLATES.get(template_id)
    if not template:
        available = ", ".join(MCP_TEMPLATES.keys())
        return {"ok": False, "error": f"Modèle inconnu '{template_id}'. Disponibles : {available}"}

    if not server_name:
        server_name = template["name"]

    server_id = server_name.lower().replace(" ", "_").replace("-", "_")
    # Nettoyer les caractères spéciaux
    server_id = "".join(c for c in server_id if c.isalnum() or c == "_")

    target_dir = MCP_SERVERS_DIR / server_id
    if target_dir.exists():
        return {"ok": False, "error": f"Un serveur avec l'ID '{server_id}' existe déjà."}

    target_dir.mkdir(parents=True, exist_ok=True)
    entry_file = template["file"]
    entry_path = target_dir / entry_file
    entry_path.write_text(template["content"], encoding="utf-8")

    # Copier les fichiers nécessaires (brain_tools.py, etc.)
    if template_id == "brain_tools":
        brain_tools_src = _HERE / "brain_tools.py"
        if brain_tools_src.exists():
            shutil.copy2(brain_tools_src, target_dir / "brain_tools.py")
        config_src = _HERE / "config.json"
        if config_src.exists():
            shutil.copy2(config_src, target_dir / "config.json")

    registry = _load_registry()
    registry.setdefault("servers", {})[server_id] = {
        "name": server_name,
        "description": template["description"],
        "source": "template",
        "source_url": "",
        "entry_file": entry_file,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
        "template_id": template_id,
    }
    _save_registry(registry)

    return {
        "ok": True,
        "id": server_id,
        "name": server_name,
        "entry_file": entry_file,
        "path": str(target_dir),
    }


def remove_server(server_id: str) -> dict:
    """Supprimer un serveur MCP installé."""
    registry = _load_registry()
    if server_id not in registry.get("servers", {}):
        return {"ok": False, "error": f"Serveur '{server_id}' non trouvé dans le registre."}

    server_dir = MCP_SERVERS_DIR / server_id
    if server_dir.exists():
        shutil.rmtree(server_dir)

    del registry["servers"][server_id]
    _save_registry(registry)

    return {"ok": True, "removed": server_id}


def get_claude_desktop_config() -> dict:
    """Générer la configuration pour Claude Desktop (claude_desktop_config.json).

    Retourne un dict au format attendu par Claude Desktop avec tous les
    serveurs MCP activés.
    """
    registry = _load_registry()
    mcp_servers = {}

    for sid, info in registry.get("servers", {}).items():
        if not info.get("enabled", True):
            continue
        server_dir = MCP_SERVERS_DIR / sid
        entry_file = info.get("entry_file", "server.py")
        entry_path = server_dir / entry_file
        if not entry_path.exists():
            continue
        mcp_servers[sid] = {
            "command": sys.executable,
            "args": [str(entry_path)],
        }

    return {"mcpServers": mcp_servers}


def get_claude_code_config() -> dict:
    """Générer la configuration pour Claude Code (mcp_servers.json)."""
    return get_claude_desktop_config()


def list_templates() -> list[dict]:
    """Lister les modèles de serveurs MCP disponibles."""
    return [
        {
            "id": tid,
            "name": t["name"],
            "description": t["description"],
            "entry_file": t["file"],
        }
        for tid, t in MCP_TEMPLATES.items()
    ]


def _find_entry_file(server_dir: Path) -> str:
    """Chercher automatiquement le point d'entrée d'un serveur MCP.

    Cherche dans l'ordre : server.py, main.py, *_mcp.py, brain_mcp.py,
    puis n'importe quel fichier .py contenant 'FastMCP'.
    """
    candidates = ["server.py", "main.py", "brain_mcp.py"]

    for candidate in candidates:
        if (server_dir / candidate).exists():
            return candidate

    # Chercher *_mcp.py
    for f in server_dir.glob("*_mcp.py"):
        return f.name

    # Chercher dans les sous-dossiers courants (src/, etc.)
    for subdir in ["src", "server", "mcp_server"]:
        sub_path = server_dir / subdir
        if sub_path.is_dir():
            for candidate in ["server.py", "main.py"]:
                if (sub_path / candidate).exists():
                    return f"{subdir}/{candidate}"

    # Dernier recours : chercher un fichier .py avec FastMCP
    for f in server_dir.rglob("*.py"):
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            if "FastMCP" in content or "mcp" in content.lower():
                return str(f.relative_to(server_dir))
        except Exception:
            continue

    return "server.py"  # Par défaut
