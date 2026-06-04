"""
001_v1_to_chromadb.py — Migration V1 (JSON/MD) → ChromaDB

Appelée par migration_manager.py — ne pas lancer directement.
"""

import os
import sys
import shutil
import subprocess
import venv as _venv
from pathlib import Path
from datetime import datetime


DESCRIPTION = "Migration mémoire JSON/MD → ChromaDB (V2)"
REQUIRES_GB = 2.0


def run(jarvis_root: Path, venv_dir: Path, log, create_backup, check_disk) -> dict:
    """Point d'entrée appelé par migration_manager.run_migration()."""

    # 1. Vérification espace disque
    if not check_disk(REQUIRES_GB):
        raise RuntimeError(f"Espace disque insuffisant (requis : {REQUIRES_GB} Go)")

    # 2. Backup complet avant toute modification
    log.info("Backup complet en cours...")
    backup_dir = create_backup()
    log.info(f"Backup → {backup_dir}")

    # 3. Créer/vérifier le venv
    _ensure_venv(venv_dir, log)

    # 4. Installer dépendances dans le venv
    _install_deps(venv_dir, log)

    # 5. Vérification GPU
    _check_gpu(venv_dir, log)

    # 6. Copier brain_tools_v2.py → brain_tools.py
    v2_src = jarvis_root / "brain_tools_v2.py"
    brain_tools = jarvis_root / "brain_tools.py"
    backup_bt = jarvis_root / "brain_tools_v1_backup.py"

    if v2_src.exists():
        if brain_tools.exists():
            shutil.copy2(brain_tools, backup_bt)
            log.info(f"brain_tools.py sauvegardé → {backup_bt.name}")
        shutil.copy2(v2_src, brain_tools)
        log.info("brain_tools.py → version V2 ChromaDB installée")
    else:
        raise FileNotFoundError(f"brain_tools_v2.py introuvable dans {jarvis_root}")

    # 7. Migration des données .md → ChromaDB
    result = _run_migration(venv_dir, jarvis_root, log)

    return {
        "backup": str(backup_dir),
        "vectors_migrated": result.get("vectors", 0),
        "status": "success",
    }


def rollback(jarvis_root: Path, venv_dir: Path, log):
    """Restore brain_tools V1 depuis le backup."""
    backup = jarvis_root / "brain_tools_v1_backup.py"
    target = jarvis_root / "brain_tools.py"
    if backup.exists():
        shutil.copy2(backup, target)
        log.info("Rollback effectué — brain_tools V1 restauré")
    else:
        log.error("Backup brain_tools_v1_backup.py introuvable — rollback impossible")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ensure_venv(venv_dir: Path, log):
    if not venv_dir.exists():
        log.info(f"Création du venv dans {venv_dir} ...")
        _venv.create(str(venv_dir), with_pip=True)
        log.info("venv créé")
    else:
        log.info(f"venv existant : {venv_dir}")
    # Mettre à jour pip silencieusement
    pip = str(venv_dir / "bin" / "pip")
    subprocess.run(f"{pip} install --upgrade pip --quiet", shell=True)


def _install_deps(venv_dir: Path, log):
    pip = str(venv_dir / "bin" / "pip")
    venv_python = str(venv_dir / "bin" / "python")
    deps = [
        ("chromadb",              "chromadb>=0.4.0"),
        ("sentence_transformers", "sentence-transformers>=2.2.0"),
    ]
    for module, pkg in deps:
        check = subprocess.run(
            f'{venv_python} -c "import {module}"',
            shell=True, capture_output=True,
        )
        if check.returncode == 0:
            log.info(f"{module} déjà installé")
        else:
            log.info(f"Installation de {pkg} ...")
            subprocess.run(f"{pip} install {pkg} --quiet", shell=True, check=True)
            log.info(f"{pkg} installé")


def _check_gpu(venv_dir: Path, log):
    venv_python = str(venv_dir / "bin" / "python")
    result = subprocess.run(
        f'{venv_python} -c "import torch; print(torch.cuda.is_available())"',
        shell=True, capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip() == "True":
        log.info("✓ CUDA détecté — embeddings sur GPU")
    else:
        log.info("⚠ CPU uniquement — embeddings fonctionnels mais plus lents")


def _run_migration(venv_dir: Path, jarvis_root: Path, log) -> dict:
    """Lance la migration des .md dans ChromaDB via le venv."""
    import json as _json

    config_path = jarvis_root / "config.json"
    brain_path = str(jarvis_root / "brain_storage")
    try:
        with open(config_path) as f:
            cfg = _json.load(f)
        brain_path = cfg.get("brain_path") or cfg.get("obsidian_inbox_path") or brain_path
    except Exception:
        pass

    venv_python = str(venv_dir / "bin" / "python")
    script = f"""
import sys, time, json
sys.path.insert(0, r'{jarvis_root}')
import brain_tools
brain_tools.init(r'{brain_path}')
time.sleep(5)
stats = brain_tools.memory_stats()
print(json.dumps({{"vectors": stats.get("total_vectors", 0), "files": stats.get("md_files", [])}}))
"""
    tmp = jarvis_root / "_mig001_tmp.py"
    tmp.write_text(script)
    result = subprocess.run(f"{venv_python} {tmp}", shell=True, capture_output=True, text=True)
    tmp.unlink(missing_ok=True)

    try:
        data = _json.loads(result.stdout.strip().split("\n")[-1])
        log.info(f"Migration terminée — {data.get('vectors', 0)} vecteurs indexés")
        return data
    except Exception:
        log.warning(f"Impossible de parser le résultat : {result.stdout}")
        return {}
