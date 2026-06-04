"""
migration_manager.py — Jarvis Migration & Upgrade Framework

Gère toutes les évolutions futures de la plateforme :
  - Mémoire (JSON → ChromaDB → Qdrant → Milvus)
  - Agents, MCP, plugins, modèles IA
  - Auto-évolution contrôlée (Phase 3)

Utilisation :
    python migration_manager.py list              # lister les migrations
    python migration_manager.py run 001           # appliquer une migration
    python migration_manager.py run all           # appliquer toutes les pending
    python migration_manager.py rollback 001      # rollback
    python migration_manager.py status            # état complet
"""

import os
import sys
import json
import shutil
import logging
import subprocess
import importlib.util
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

# ─── Paths ───────────────────────────────────────────────────────────────────
HERE         = Path(__file__).parent.resolve()
JARVIS_ROOT  = HERE.parent
HISTORY_FILE = HERE / "migration_history.json"
LOG_FILE     = HERE / "migration.log"
VENV_DIR     = JARVIS_ROOT / ".venv"

# ─── Logging (terminal + fichier) ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("migration_manager")


# ─── History ─────────────────────────────────────────────────────────────────

def _load_history() -> Dict[str, Any]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"applied": [], "rollbacks": []}


def _save_history(h: Dict[str, Any]):
    HISTORY_FILE.write_text(json.dumps(h, indent=2, ensure_ascii=False), encoding="utf-8")


def _mark_applied(migration_id: str, meta: Dict):
    h = _load_history()
    h["applied"].append({
        "id": migration_id,
        "ts": datetime.now().isoformat(),
        **meta,
    })
    _save_history(h)


def _mark_rollback(migration_id: str):
    h = _load_history()
    h["applied"] = [m for m in h["applied"] if m["id"] != migration_id]
    h["rollbacks"].append({"id": migration_id, "ts": datetime.now().isoformat()})
    _save_history(h)


def _is_applied(migration_id: str) -> bool:
    h = _load_history()
    return any(m["id"] == migration_id for m in h["applied"])


# ─── Disk check ──────────────────────────────────────────────────────────────

def check_disk_space(required_gb: float = 2.0) -> bool:
    total, used, free = shutil.disk_usage(JARVIS_ROOT)
    free_gb = free / (1024 ** 3)
    log.info(f"Espace disque libre : {free_gb:.1f} Go (requis : {required_gb} Go)")
    if free_gb < required_gb:
        log.error(f"Espace insuffisant — {free_gb:.1f} Go < {required_gb} Go requis")
        return False
    return True


# ─── GPU check ───────────────────────────────────────────────────────────────

def check_gpu() -> bool:
    venv_python = str(VENV_DIR / "bin" / "python")
    result = subprocess.run(
        f'{venv_python} -c "import torch; print(torch.cuda.is_available())"',
        shell=True, capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip() == "True":
        log.info("✓ CUDA détecté — embeddings sur GPU")
        return True
    log.info("⚠ CPU uniquement — embeddings plus lents mais fonctionnels")
    return False


# ─── Full backup ─────────────────────────────────────────────────────────────

def create_full_backup(brain_path: Optional[str] = None) -> Path:
    """
    Sauvegarde complète : brain_storage + config.json + obsidian (si présent).
    Retourne le chemin du dossier backup créé.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = JARVIS_ROOT / f"backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up = []

    # brain_storage
    bp = Path(brain_path) if brain_path else JARVIS_ROOT / "brain_storage"
    if bp.exists():
        shutil.copytree(bp, backup_dir / "brain_storage")
        backed_up.append("brain_storage/")

    # config.json
    cfg = JARVIS_ROOT / "config.json"
    if cfg.exists():
        shutil.copy2(cfg, backup_dir / "config.json")
        backed_up.append("config.json")

    # Obsidian vault (si configuré)
    try:
        import json as _json
        with open(cfg) as f:
            config = _json.load(f)
        vault = config.get("obsidian_vault_path") or config.get("obsidian_inbox_path")
        if vault and Path(vault).exists():
            shutil.copytree(vault, backup_dir / "obsidian")
            backed_up.append("obsidian/")
    except Exception:
        pass

    log.info(f"Backup créé : {backup_dir} ({', '.join(backed_up)})")
    return backup_dir


# ─── Benchmark memory ────────────────────────────────────────────────────────

def benchmark_memory(label: str = "") -> Dict[str, Any]:
    """
    Capture un snapshot des stats mémoire + temps de recherche.
    Retourne un dict pour comparaison avant/après.
    """
    import time
    venv_python = str(VENV_DIR / "bin" / "python")

    script = f"""
import sys, time
sys.path.insert(0, r'{JARVIS_ROOT}')
try:
    import brain_tools
    cfg_path = r'{JARVIS_ROOT / "config.json"}'
    import json
    brain_path = None
    try:
        with open(cfg_path) as f:
            c = json.load(f)
        brain_path = c.get('brain_path') or c.get('obsidian_inbox_path')
    except: pass
    brain_path = brain_path or r'{JARVIS_ROOT / "brain_storage"}'
    brain_tools.init(brain_path)
    stats = brain_tools.memory_stats()

    # Benchmark recherche
    t0 = time.time()
    brain_tools.recall("jarvis memory test benchmark")
    elapsed_ms = round((time.time() - t0) * 1000, 1)

    import json as _j
    print(_j.dumps({{
        "vectors": stats.get("total_vectors", 0),
        "md_files": len(stats.get("md_files", [])),
        "embed_ready": stats.get("embed_model_ready", False),
        "search_ms": elapsed_ms,
    }}))
except Exception as e:
    import json as _j
    print(_j.dumps({{"error": str(e)}}))
"""
    tmp = HERE / "_bench_tmp.py"
    tmp.write_text(script)
    result = subprocess.run(f"{venv_python} {tmp}", shell=True, capture_output=True, text=True)
    tmp.unlink(missing_ok=True)

    try:
        data = json.loads(result.stdout.strip().split("\n")[-1])
    except Exception:
        data = {"error": result.stdout or result.stderr}

    data["label"] = label
    data["ts"] = datetime.now().isoformat()
    log.info(f"Benchmark [{label}]: {data}")
    return data


# ─── Migration loader ─────────────────────────────────────────────────────────

def _list_migration_files() -> List[Path]:
    return sorted(HERE.glob("[0-9][0-9][0-9]_*.py"))


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_migrations():
    """Affiche toutes les migrations avec leur statut."""
    files = _list_migration_files()
    if not files:
        print("  Aucune migration trouvée dans", HERE)
        return
    print(f"\n  {'ID':<8} {'Statut':<12} {'Fichier'}")
    print(f"  {'─'*8} {'─'*12} {'─'*40}")
    for f in files:
        mid = f.stem.split("_")[0]
        status = "✅ appliquée" if _is_applied(mid) else "⏳ pending"
        print(f"  {mid:<8} {status:<12} {f.name}")
    print()


def run_migration(migration_id: str, dry_run: bool = False):
    """Applique une migration par ID (ex: '001') ou 'all'."""
    files = _list_migration_files()

    targets = files if migration_id == "all" else [
        f for f in files if f.stem.startswith(migration_id)
    ]

    if not targets:
        log.error(f"Migration '{migration_id}' introuvable.")
        return

    for mfile in targets:
        mid = mfile.stem.split("_")[0]
        if _is_applied(mid):
            log.info(f"[{mid}] Déjà appliquée — skip")
            continue

        log.info(f"[{mid}] Lancement : {mfile.name}")
        if dry_run:
            log.info(f"[{mid}] DRY RUN — aucune modification")
            continue

        # Benchmark avant
        bench_before = benchmark_memory("avant")

        mod = _load_migration(mfile)
        try:
            result = mod.run(
                jarvis_root=JARVIS_ROOT,
                venv_dir=VENV_DIR,
                log=log,
                create_backup=create_full_backup,
                check_disk=check_disk_space,
            )
            bench_after = benchmark_memory("après")

            diff = {
                "vectors_delta": bench_after.get("vectors", 0) - bench_before.get("vectors", 0),
                "search_ms_before": bench_before.get("search_ms"),
                "search_ms_after": bench_after.get("search_ms"),
            }
            log.info(f"[{mid}] ✅ Succès — +{diff['vectors_delta']} vecteurs, "
                     f"search {diff['search_ms_before']}ms → {diff['search_ms_after']}ms")

            _mark_applied(mid, {"file": mfile.name, "result": result, "bench": diff})

        except Exception as e:
            log.error(f"[{mid}] ❌ Échec : {e}")
            import traceback; traceback.print_exc()


def rollback_migration(migration_id: str):
    """Rollback une migration par ID."""
    files = [f for f in _list_migration_files() if f.stem.startswith(migration_id)]
    if not files:
        log.error(f"Migration '{migration_id}' introuvable.")
        return
    mod = _load_migration(files[0])
    if not hasattr(mod, "rollback"):
        log.error(f"Pas de fonction rollback() dans {files[0].name}")
        return
    try:
        mod.rollback(jarvis_root=JARVIS_ROOT, venv_dir=VENV_DIR, log=log)
        _mark_rollback(migration_id)
        log.info(f"[{migration_id}] Rollback effectué.")
    except Exception as e:
        log.error(f"[{migration_id}] Rollback échoué : {e}")


def show_status():
    """Rapport complet : migrations + mémoire + disque."""
    print("\n" + "═"*52)
    print("  JARVIS MIGRATION STATUS")
    print("═"*52)

    total, used, free = shutil.disk_usage(JARVIS_ROOT)
    print(f"\n  Disque   : {free/(1024**3):.1f} Go libres / {total/(1024**3):.1f} Go total")

    h = _load_history()
    print(f"  Migrations appliquées : {len(h['applied'])}")
    print(f"  Rollbacks effectués   : {len(h['rollbacks'])}")

    bench = benchmark_memory("status")
    if "error" not in bench:
        print(f"\n  Mémoire vectorielle   : {bench.get('vectors', 0)} vecteurs")
        print(f"  Fichiers .md          : {bench.get('md_files', 0)}")
        print(f"  Embedding model prêt  : {bench.get('embed_ready', False)}")
        print(f"  Temps de recherche    : {bench.get('search_ms', '?')} ms")

    list_migrations()


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        list_migrations()
    elif args[0] == "status":
        show_status()
    elif args[0] == "run":
        mid = args[1] if len(args) > 1 else "all"
        dry = "--dry-run" in args
        run_migration(mid, dry_run=dry)
    elif args[0] == "rollback":
        mid = args[1] if len(args) > 1 else ""
        if mid:
            rollback_migration(mid)
        else:
            print("Usage: python migration_manager.py rollback <id>")
    else:
        print(__doc__)
