#!/usr/bin/env python3
"""
migrate_to_chromadb.py — Script de migration V1 → V2

Utilisation :
    cd ops-center/_jarvis
    python migrate_to_chromadb.py

Ce script :
  1. Vérifie les dépendances
  2. Sauvegarde brain_tools.py (→ brain_tools_v1_backup.py)
  3. Installe ChromaDB + sentence-transformers si absent
  4. Remplace brain_tools.py par la version V2
  5. Lance une migration initiale des .md existants
  6. Affiche un rapport
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


HERE = Path(__file__).parent.resolve()
BRAIN_TOOLS = HERE / "brain_tools.py"
BRAIN_TOOLS_V2 = HERE / "brain_tools_v2.py"
BACKUP = HERE / "brain_tools_v1_backup.py"
CONFIG_JSON = HERE / "config.json"


def run(cmd: str, check: bool = True) -> int:
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True)
    if check and result.returncode != 0:
        print(f"  [ERROR] Command failed with code {result.returncode}")
        sys.exit(1)
    return result.returncode


def banner(msg: str):
    print(f"\n{'─'*50}")
    print(f"  {msg}")
    print(f"{'─'*50}")


# ─── Step 1 : check Python version ───────────────────────────────────────────
banner("Step 1 — Vérification Python")
if sys.version_info < (3, 9):
    print("[ERREUR] Python 3.9+ requis.")
    sys.exit(1)
print(f"  ✓ Python {sys.version.split()[0]}")


# ─── Step 2 : vérifier brain_tools_v2.py est bien là ─────────────────────────
banner("Step 2 — Vérification fichiers")
if not BRAIN_TOOLS_V2.exists():
    print(f"[ERREUR] brain_tools_v2.py introuvable dans {HERE}")
    print("  → Copiez brain_tools_v2.py dans le même dossier que ce script.")
    sys.exit(1)
print(f"  ✓ brain_tools_v2.py trouvé")


# ─── Step 3 : installer dépendances ──────────────────────────────────────────
banner("Step 3 — Installation dépendances")
deps = [
    ("chromadb", "chromadb>=0.4.0"),
    ("sentence_transformers", "sentence-transformers>=2.2.0"),
]
for module, pkg in deps:
    try:
        __import__(module)
        print(f"  ✓ {module} déjà installé")
    except ImportError:
        print(f"  → Installation de {pkg} ...")
        run(f"{sys.executable} -m pip install {pkg} --quiet")
        print(f"  ✓ {pkg} installé")


# ─── Step 4 : backup V1 ──────────────────────────────────────────────────────
banner("Step 4 — Backup brain_tools.py V1")
if BRAIN_TOOLS.exists():
    shutil.copy2(BRAIN_TOOLS, BACKUP)
    print(f"  ✓ Backup → {BACKUP.name}")
else:
    print("  ⚠ brain_tools.py introuvable — migration depuis zéro")


# ─── Step 5 : remplacer par V2 ───────────────────────────────────────────────
banner("Step 5 — Remplacement par V2")
shutil.copy2(BRAIN_TOOLS_V2, BRAIN_TOOLS)
print(f"  ✓ brain_tools.py → version V2 ChromaDB")


# ─── Step 6 : test d'import ──────────────────────────────────────────────────
banner("Step 6 — Test d'import")
sys.path.insert(0, str(HERE))
try:
    import importlib
    bt = importlib.import_module("brain_tools")
    print("  ✓ brain_tools V2 importé sans erreur")
except Exception as e:
    print(f"  [ERREUR] Import failed: {e}")
    print("  → Restauration du backup...")
    shutil.copy2(BACKUP, BRAIN_TOOLS)
    sys.exit(1)


# ─── Step 7 : migration des .md existants ────────────────────────────────────
banner("Step 7 — Migration des données existantes")
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

print(f"  Brain path: {brain_path}")
os.makedirs(brain_path, exist_ok=True)

bt.init(brain_path)
print("  ✓ ChromaDB initialisé")
print("  → Migration des .md en cours (tourne en fond)...")

import time
time.sleep(3)  # Laisser la migration démarrer

stats = bt.memory_stats()
print(f"  ✓ Vecteurs indexés : {stats['total_vectors']}")
print(f"  ✓ Fichiers .md     : {', '.join(stats['md_files']) or 'aucun'}")
print(f"  ✓ Modèle embedding : {stats['embed_model_name']}")
print(f"  ✓ Modèle prêt      : {stats['embed_model_ready']}")


# ─── Rapport final ───────────────────────────────────────────────────────────
banner("✅ Migration terminée")
print("""
  Ce qui a changé :
    • recall()     → recherche sémantique (cosine similarity) via ChromaDB
    • remember()   → dual-write MD + ChromaDB (rétrocompatible)
    • add_task()   → indexé dans ChromaDB en plus du MD
    • NOUVEAU      → search_memory(query, n) — résultats structurés pour agents
    • NOUVEAU      → ingest_document(title, content) — ingestion de docs longs
    • NOUVEAU      → ingest_obsidian_vault(path) — sync vault complet
    • NOUVEAU      → memory_stats() — santé mémoire pour le dashboard

  Fichiers créés :
    • brain_tools_v1_backup.py  — votre V1 intact
    • brain_storage/.chromadb/  — base vectorielle locale

  Prochaine étape :
    Ajouter dans server.py (après brain_tools.init) :
      import brain_tools
      brain_tools.init(BRAIN_PATH)
      # Optionnel : sync Obsidian
      # brain_tools.ingest_obsidian_vault("/path/to/your/vault")

  Pour rollback :
    cp brain_tools_v1_backup.py brain_tools.py
""")
