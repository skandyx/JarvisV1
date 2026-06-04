#!/usr/bin/env python3
"""
migrate_to_chromadb.py — Script de migration V1 → V2
Compatible Kali Linux / environnements Python externally-managed

Utilisation :
    cd ops-center/_jarvis
    python migrate_to_chromadb.py
"""

import os
import sys
import shutil
import subprocess
import venv
from pathlib import Path

HERE = Path(__file__).parent.resolve()
BRAIN_TOOLS    = HERE / "brain_tools.py"
BRAIN_TOOLS_V2 = HERE / "brain_tools_v2.py"
BACKUP         = HERE / "brain_tools_v1_backup.py"
CONFIG_JSON    = HERE / "config.json"
VENV_DIR       = HERE / ".venv"


def run(cmd: str, check: bool = True, env_python: bool = False) -> int:
    python = str(VENV_DIR / "bin" / "python") if env_python else sys.executable
    full_cmd = cmd.replace("python", python, 1) if env_python else cmd
    print(f"  $ {full_cmd}")
    result = subprocess.run(full_cmd, shell=True)
    if check and result.returncode != 0:
        print(f"  [ERROR] Command failed (code {result.returncode})")
        sys.exit(1)
    return result.returncode


def pip(packages: str):
    pip_bin = str(VENV_DIR / "bin" / "pip")
    run(f"{pip_bin} install {packages} --quiet")


def banner(msg: str):
    print(f"\n{'─'*52}")
    print(f"  {msg}")
    print(f"{'─'*52}")


# ── Step 1 : Python version ───────────────────────────────────────────────────
banner("Step 1 — Vérification Python")
if sys.version_info < (3, 9):
    print("[ERREUR] Python 3.9+ requis.")
    sys.exit(1)
print(f"  ✓ Python {sys.version.split()[0]}")


# ── Step 2 : brain_tools_v2.py présent ───────────────────────────────────────
banner("Step 2 — Vérification fichiers")
if not BRAIN_TOOLS_V2.exists():
    print(f"[ERREUR] brain_tools_v2.py introuvable dans {HERE}")
    sys.exit(1)
print("  ✓ brain_tools_v2.py trouvé")


# ── Step 3 : créer le venv si besoin ─────────────────────────────────────────
banner("Step 3 — Environnement virtuel")
if not VENV_DIR.exists():
    print(f"  → Création du venv dans {VENV_DIR} ...")
    venv.create(str(VENV_DIR), with_pip=True)
    print("  ✓ venv créé")
else:
    print(f"  ✓ venv existant trouvé ({VENV_DIR})")

# S'assurer que pip est à jour dans le venv
pip("--upgrade pip")


# ── Step 4 : installer les dépendances dans le venv ──────────────────────────
banner("Step 4 — Installation dépendances (dans le venv)")
deps = [
    ("chromadb",              "chromadb>=0.4.0"),
    ("sentence_transformers", "sentence-transformers>=2.2.0"),
]
venv_python = str(VENV_DIR / "bin" / "python")
for module, pkg in deps:
    result = subprocess.run(
        f"{venv_python} -c 'import {module}'",
        shell=True, capture_output=True
    )
    if result.returncode == 0:
        print(f"  ✓ {module} déjà installé")
    else:
        print(f"  → Installation de {pkg} ...")
        pip(pkg)
        print(f"  ✓ {pkg} installé")


# ── Step 5 : backup V1 ───────────────────────────────────────────────────────
banner("Step 5 — Backup brain_tools.py V1")
if BRAIN_TOOLS.exists():
    shutil.copy2(BRAIN_TOOLS, BACKUP)
    print(f"  ✓ Backup → {BACKUP.name}")
else:
    print("  ⚠ brain_tools.py introuvable — migration depuis zéro")


# ── Step 6 : remplacer par V2 ────────────────────────────────────────────────
banner("Step 6 — Remplacement par V2")
shutil.copy2(BRAIN_TOOLS_V2, BRAIN_TOOLS)
print("  ✓ brain_tools.py → version V2 ChromaDB")


# ── Step 7 : test d'import dans le venv ──────────────────────────────────────
banner("Step 7 — Test d'import")
test_script = f"""
import sys
sys.path.insert(0, r'{HERE}')
import brain_tools
print('  ✓ brain_tools V2 importé sans erreur')
"""
test_file = HERE / "_test_import.py"
test_file.write_text(test_script)
result = subprocess.run(f"{venv_python} {test_file}", shell=True)
test_file.unlink(missing_ok=True)
if result.returncode != 0:
    print("  [ERREUR] Import échoué — restauration du backup...")
    shutil.copy2(BACKUP, BRAIN_TOOLS)
    sys.exit(1)


# ── Step 8 : migration des .md existants ─────────────────────────────────────
banner("Step 8 — Migration des données existantes")
import json

brain_path = None
if CONFIG_JSON.exists():
    try:
        with open(CONFIG_JSON) as f:
            cfg = json.load(f)
        brain_path = cfg.get("brain_path") or cfg.get("obsidian_inbox_path")
    except Exception:
        pass

if not brain_path:
    brain_path = str(HERE / "brain_storage")

print(f"  Brain path : {brain_path}")
os.makedirs(brain_path, exist_ok=True)

migrate_script = f"""
import sys, time
sys.path.insert(0, r'{HERE}')
import brain_tools
brain_tools.init(r'{brain_path}')
print('  ✓ ChromaDB initialisé')
print('  → Migration des .md en cours...')
time.sleep(4)
stats = brain_tools.memory_stats()
print(f"  ✓ Vecteurs indexés : {{stats['total_vectors']}}")
print(f"  ✓ Fichiers .md     : {{', '.join(stats['md_files']) or 'aucun'}}")
print(f"  ✓ Modèle embedding : {{stats['embed_model_name']}}")
"""
migrate_file = HERE / "_run_migrate.py"
migrate_file.write_text(migrate_script)
subprocess.run(f"{venv_python} {migrate_file}", shell=True)
migrate_file.unlink(missing_ok=True)


# ── Rapport final ─────────────────────────────────────────────────────────────
banner("✅ Migration terminée")
print(f"""
  Fichiers créés :
    • brain_tools_v1_backup.py     — ton V1 intact
    • brain_storage/.chromadb/     — base vectorielle locale
    • .venv/                       — environnement Python isolé

  Pour lancer Jarvis avec le venv :
    source .venv/bin/activate
    python server.py

  Ou mettre à jour START.bat / start.sh :
    Remplacer "python server.py" par
    ".venv/bin/python server.py"  (Linux/Mac)
    ".venv\\Scripts\\python server.py"  (Windows)

  Pour rollback :
    cp brain_tools_v1_backup.py brain_tools.py
""")
