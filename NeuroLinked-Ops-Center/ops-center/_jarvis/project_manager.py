"""
Gestionnaire de Projets pour Jarvis

Permet à Jarvis d'accéder à des dossiers locaux contenant des projets
et d'assigner des agents pour vérifier, analyser et aider à coder.

Fonctionnalités :
  - Enregistrer des dossiers de projets locaux
  - Assigner des agents (vérification code, review, assistance) aux projets
  - Scanner un projet pour comprendre sa structure
  - Générer des rapports de review automatiques
  - Surveiller les changements de fichiers

Les projets sont persistés dans projects_registry.json.
Les agents assignés sont des configurations de tâches que Jarvis peut exécuter.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
PROJECTS_REGISTRY = _HERE / "projects_registry.json"

# Types d'agents disponibles pour les projets
AGENT_TYPES = {
    "code_review": {
        "name": "Vérificateur de Code",
        "description": "Analyse le code du projet pour trouver des bugs, des problèmes de style, des vulnérabilités de sécurité et des optimisations possibles.",
        "prompt_template": (
            "Tu es un vérificateur de code expert. Analyse les fichiers suivants du projet '{project_name}' "
            "et identifie : 1) Bugs potentiels 2) Problèmes de sécurité 3) Optimisations possibles "
            "4) Améliorations de style/code. Sois précis avec les noms de fichiers et les numéros de ligne."
        ),
    },
    "architect": {
        "name": "Architecte Logiciel",
        "description": "Analyse l'architecture du projet, suggère des améliorations de structure, des patterns de conception et une meilleure organisation.",
        "prompt_template": (
            "Tu es un architecte logiciel senior. Analyse l'architecture du projet '{project_name}' "
            "et évalue : 1) Structure des dossiers 2) Séparation des responsabilités "
            "3) Patterns utilisés 4) Suggère des améliorations architecturales."
        ),
    },
    "test_helper": {
        "name": "Assistant Tests",
        "description": "Aide à écrire des tests unitaires et d'intégration pour le projet. Génère des cas de test et vérifie la couverture.",
        "prompt_template": (
            "Tu es un expert en tests logiciels. Pour le projet '{project_name}', "
            "analyse le code existant et : 1) Identifie les fonctions sans tests "
            "2) Propose des cas de test unitaires 3) Suggère des tests d'intégration "
            "4) Recommande des améliorations de couverture."
        ),
    },
    "doc_writer": {
        "name": "Rédacteur de Documentation",
        "description": "Génère et améliore la documentation du projet : README, docstrings, commentaires, guides.",
        "prompt_template": (
            "Tu es un rédacteur technique expert. Pour le projet '{project_name}', "
            "analyse le code et : 1) Génère des docstrings manquantes "
            "2) Améliore le README 3) Crée des guides d'utilisation "
            "4) Documente les API et interfaces publiques."
        ),
    },
    "security_auditor": {
        "name": "Auditeur de Sécurité",
        "description": "Analyse le projet pour les vulnérabilités de sécurité : injections, fuites de données, mauvaises configurations.",
        "prompt_template": (
            "Tu es un auditeur de sécurité expert. Analyse le projet '{project_name}' "
            "pour : 1) Injections SQL/ XSS / CSRF 2) Fuites de données sensibles "
            "3) Mauvaises configurations de sécurité 4) Dépendances vulnérables "
            "5) Gestion des secrets et clés API."
        ),
    },
    "refactorer": {
        "name": "Expert Refactoring",
        "description": "Identifie les parties du code qui nécessitent un refactoring et propose des réécritures.",
        "prompt_template": (
            "Tu es un expert en refactoring. Analyse le projet '{project_name}' "
            "et : 1) Identifie le code dupliqué 2) Trouve les fonctions trop longues "
            "3) Propose des refactorings spécifiques 4) Suggère des patterns de conception appropriés."
        ),
    },
}


# ---------------------------------------------------------------------------
# Registre des projets
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Charger le registre des projets."""
    if PROJECTS_REGISTRY.exists():
        try:
            return json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"projects": {}}


def _save_registry(registry: dict) -> None:
    """Sauvegarder le registre des projets."""
    PROJECTS_REGISTRY.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Opérations sur les projets
# ---------------------------------------------------------------------------

def register_project(path: str, name: Optional[str] = None) -> dict:
    """Enregistrer un dossier de projet local.

    Scanne le dossier, détecte le type de projet et l'enregistre.
    """
    project_path = Path(path).resolve()
    if not project_path.exists():
        return {"ok": False, "error": f"Chemin non trouvé : {path}"}
    if not project_path.is_dir():
        return {"ok": False, "error": f"Le chemin n'est pas un dossier : {path}"}

    project_id = project_path.name.replace("-", "_").replace(" ", "_")
    # Nettoyer les caractères spéciaux
    project_id = "".join(c for c in project_id if c.isalnum() or c == "_")

    if not name:
        name = project_path.name.replace("-", " ").replace("_", " ").title()

    # Scanner le projet pour comprendre sa structure
    scan = scan_project(str(project_path))

    registry = _load_registry()
    registry.setdefault("projects", {})[project_id] = {
        "name": name,
        "path": str(project_path),
        "project_type": scan.get("project_type", "unknown"),
        "languages": scan.get("languages", []),
        "registered_at": datetime.now().isoformat(),
        "agents": {},
        "last_scan": datetime.now().isoformat(),
        "file_count": scan.get("file_count", 0),
        "enabled": True,
    }
    _save_registry(registry)

    return {
        "ok": True,
        "id": project_id,
        "name": name,
        "path": str(project_path),
        "project_type": scan.get("project_type", "unknown"),
        "languages": scan.get("languages", []),
        "file_count": scan.get("file_count", 0),
    }


def unregister_project(project_id: str) -> dict:
    """Supprimer un projet du registre."""
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    del registry["projects"][project_id]
    _save_registry(registry)
    return {"ok": True, "removed": project_id}


def list_projects() -> list[dict]:
    """Lister tous les projets enregistrés."""
    registry = _load_registry()
    result = []
    for pid, info in registry.get("projects", {}).items():
        project_path = Path(info.get("path", ""))
        result.append({
            "id": pid,
            "name": info.get("name", pid),
            "path": info.get("path", ""),
            "project_type": info.get("project_type", "unknown"),
            "languages": info.get("languages", []),
            "registered_at": info.get("registered_at", ""),
            "last_scan": info.get("last_scan", ""),
            "file_count": info.get("file_count", 0),
            "enabled": info.get("enabled", True),
            "agent_count": len(info.get("agents", {})),
            "path_exists": project_path.exists(),
        })
    return result


def get_project(project_id: str) -> dict:
    """Récupérer les détails d'un projet."""
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    info = registry["projects"][project_id]
    info["id"] = project_id
    info["ok"] = True
    return info


def scan_project(path: str) -> dict:
    """Scanner un dossier de projet pour détecter sa structure et son type.

    Retourne des informations sur le type de projet, les langages utilisés,
    la structure des dossiers et les fichiers principaux.
    """
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return {"ok": False, "error": f"Chemin invalide : {path}"}

    # Détection du type de projet
    indicators = {
        "python": ["setup.py", "pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock"],
        "node": ["package.json", "yarn.lock", "pnpm-lock.yaml", "node_modules"],
        "rust": ["Cargo.toml", "Cargo.lock"],
        "go": ["go.mod", "go.sum"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "dotnet": ["*.csproj", "*.sln"],
        "ruby": ["Gemfile", "Rakefile"],
        "php": ["composer.json", "artisan"],
        "swift": ["Package.swift", "*.xcodeproj"],
        "flutter": ["pubspec.yaml"],
    }

    detected_types = []
    for ptype, files in indicators.items():
        for f in files:
            if list(project_path.glob(f)):
                detected_types.append(ptype)
                break

    # Détection des langages par extension de fichier
    lang_extensions = {
        "Python": {".py", ".pyw", ".pyx"},
        "JavaScript": {".js", ".jsx", ".mjs"},
        "TypeScript": {".ts", ".tsx"},
        "Rust": {".rs"},
        "Go": {".go"},
        "Java": {".java"},
        "C/C++": {".c", ".cpp", ".h", ".hpp"},
        "C#": {".cs"},
        "Ruby": {".rb"},
        "PHP": {".php"},
        "Swift": {".swift"},
        "Kotlin": {".kt"},
        "HTML/CSS": {".html", ".css", ".scss"},
        "SQL": {".sql"},
        "Shell": {".sh", ".bash", ".zsh"},
    }

    languages = set()
    file_count = 0
    skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
                 ".next", "target", ".idea", ".vscode", "vendor", "env"}

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            file_count += 1
            ext = os.path.splitext(fname)[1].lower()
            for lang, exts in lang_extensions.items():
                if ext in exts:
                    languages.add(lang)

    # Fichiers principaux du projet
    key_files = []
    important_names = {"README", "LICENSE", "Makefile", "Dockerfile", "docker-compose",
                       ".env", ".gitignore", "CHANGELOG", "CONTRIBUTING"}
    for f in project_path.iterdir():
        if f.is_file() and (f.name in important_names or
                           any(f.name.startswith(n) for n in important_names)):
            key_files.append(f.name)

    project_type = detected_types[0] if detected_types else "generic"
    if len(detected_types) > 1:
        project_type = "+".join(detected_types)

    return {
        "ok": True,
        "project_type": project_type,
        "languages": sorted(languages),
        "file_count": file_count,
        "key_files": sorted(key_files),
        "path": str(project_path),
    }


# ---------------------------------------------------------------------------
# Agents de projet
# ---------------------------------------------------------------------------

def assign_agent(project_id: str, agent_type: str, config: Optional[dict] = None) -> dict:
    """Assigner un agent à un projet.

    agent_type : un des types définis dans AGENT_TYPES
    config : configuration optionnelle (fichiers à ignorer, focus, etc.)
    """
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    if agent_type not in AGENT_TYPES:
        available = ", ".join(AGENT_TYPES.keys())
        return {"ok": False, "error": f"Type d'agent inconnu '{agent_type}'. Disponibles : {available}"}

    agent_info = AGENT_TYPES[agent_type]
    agent_id = f"{project_id}_{agent_type}"
    cfg = config or {}

    registry["projects"][project_id].setdefault("agents", {})[agent_id] = {
        "type": agent_type,
        "name": agent_info["name"],
        "description": agent_info["description"],
        "prompt_template": agent_info["prompt_template"],
        "assigned_at": datetime.now().isoformat(),
        "enabled": True,
        "config": cfg,
        "last_run": None,
        "last_result_summary": None,
    }
    _save_registry(registry)

    return {
        "ok": True,
        "agent_id": agent_id,
        "project_id": project_id,
        "type": agent_type,
        "name": agent_info["name"],
    }


def remove_agent(project_id: str, agent_id: str) -> dict:
    """Retirer un agent d'un projet."""
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    agents = registry["projects"][project_id].get("agents", {})
    if agent_id not in agents:
        return {"ok": False, "error": f"Agent '{agent_id}' non trouvé dans le projet '{project_id}'."}

    del agents[agent_id]
    _save_registry(registry)
    return {"ok": True, "removed": agent_id}


def list_agents(project_id: str) -> dict:
    """Lister les agents assignés à un projet."""
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    agents = registry["projects"][project_id].get("agents", {})
    return {
        "ok": True,
        "project_id": project_id,
        "agents": [
            {
                "agent_id": aid,
                "type": a.get("type", ""),
                "name": a.get("name", ""),
                "description": a.get("description", ""),
                "enabled": a.get("enabled", True),
                "last_run": a.get("last_run"),
                "config": a.get("config", {}),
            }
            for aid, a in agents.items()
        ],
        "count": len(agents),
    }


def list_available_agent_types() -> list[dict]:
    """Lister les types d'agents disponibles."""
    return [
        {
            "type": atype,
            "name": info["name"],
            "description": info["description"],
        }
        for atype, info in AGENT_TYPES.items()
    ]


def run_agent(project_id: str, agent_id: str, focus_files: Optional[list[str]] = None) -> dict:
    """Préparer l'exécution d'un agent sur un projet.

    Construit le prompt complet avec le contexte du projet et les fichiers pertinents.
    L'exécution réelle se fait par Jarvis via le LLM.
    """
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    project = registry["projects"][project_id]
    agents = project.get("agents", {})
    if agent_id not in agents:
        return {"ok": False, "error": f"Agent '{agent_id}' non trouvé."}

    agent = agents[agent_id]
    project_path = project.get("path", "")

    if not Path(project_path).exists():
        return {"ok": False, "error": f"Le chemin du projet n'existe plus : {project_path}"}

    # Collecter les fichiers pertinents
    files_content = _collect_project_files(project_path, focus_files)

    # Construire le prompt
    prompt = agent["prompt_template"].format(project_name=project.get("name", project_id))
    if files_content:
        prompt += f"\n\n=== FICHIERS DU PROJET ===\n{files_content}"

    # Mettre à jour la date de dernière exécution
    registry["projects"][project_id]["agents"][agent_id]["last_run"] = datetime.now().isoformat()
    _save_registry(registry)

    return {
        "ok": True,
        "agent_id": agent_id,
        "project_id": project_id,
        "agent_name": agent.get("name", ""),
        "prompt": prompt,
        "files_analyzed": len(files_content.split("=== FICHIER :")) - 1 if files_content else 0,
    }


def _collect_project_files(project_path: str, focus_files: Optional[list[str]] = None,
                           max_files: int = 20, max_chars_per_file: int = 3000) -> str:
    """Collecter le contenu des fichiers pertinents d'un projet.

    Si focus_files est fourni, ne collecte que ces fichiers.
    Sinon, collecte les fichiers les plus importants.
    """
    root = Path(project_path)
    skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
                 ".next", "target", ".idea", ".vscode", "vendor", "env", ".tox", "coverage"}
    skip_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav",
                ".zip", ".exe", ".dll", ".pyc", ".so", ".dylib", ".lock", ".min.js", ".min.css"}

    # Extensions de code prioritaires
    priority_ext = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
                    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift"}

    files = []
    if focus_files:
        for f in focus_files:
            fp = root / f
            if fp.exists() and fp.is_file():
                files.append(fp)
    else:
        # Scanner automatiquement
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in skip_ext or ext in {".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".md"}:
                    continue
                if ext in priority_ext:
                    files.append(Path(dirpath) / fname)

        # Trier par priorité : fichiers à la racine d'abord
        files.sort(key=lambda f: (len(f.relative_to(root).parts), str(f)))
        files = files[:max_files]

    if not files:
        return ""

    parts = []
    total_chars = 0
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
            rel_path = str(fp.relative_to(root))
            if len(content) > max_chars_per_file:
                content = content[:max_chars_per_file] + f"\n... [tronqué, {len(content)} caractères au total]"
            parts.append(f"=== FICHIER : {rel_path} ===\n{content}")
            total_chars += len(content)
            if total_chars > 50000:
                break
        except Exception:
            continue

    return "\n\n".join(parts)


def get_project_structure(project_id: str, max_depth: int = 3) -> dict:
    """Récupérer la structure arborescente d'un projet."""
    registry = _load_registry()
    if project_id not in registry.get("projects", {}):
        return {"ok": False, "error": f"Projet '{project_id}' non trouvé."}

    project_path = registry["projects"][project_id].get("path", "")
    if not Path(project_path).exists():
        return {"ok": False, "error": f"Chemin du projet introuvable : {project_path}"}

    skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
                 ".next", "target", ".idea", ".vscode", "vendor", "env", ".tox"}

    def _walk(path: Path, depth: int) -> dict:
        if depth > max_depth:
            return {"name": path.name, "type": "directory", "truncated": True}
        result = {"name": path.name, "type": "directory", "children": []}
        try:
            for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                if item.name.startswith(".") and item.name not in {".env", ".gitignore"}:
                    continue
                if item.is_dir():
                    if item.name in skip_dirs:
                        continue
                    result["children"].append(_walk(item, depth + 1))
                else:
                    ext = item.suffix.lower()
                    size = item.stat().st_size
                    result["children"].append({
                        "name": item.name,
                        "type": "file",
                        "ext": ext,
                        "size": size,
                    })
        except PermissionError:
            result["error"] = "Permission refusée"
        return result

    tree = _walk(Path(project_path), 0)
    tree["ok"] = True
    return tree
