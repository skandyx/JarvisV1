"""
002_chromadb_to_qdrant.py — Migration ChromaDB → Qdrant (V3 future)

Placeholder — non applicable pour l'instant.
Sera activé quand Qdrant offrira un avantage mesurable
(>1M vecteurs, clustering multi-tenant, etc.)
"""

DESCRIPTION  = "Migration ChromaDB → Qdrant (V3)"
REQUIRES_GB  = 4.0
STATUS       = "placeholder"


def run(jarvis_root, venv_dir, log, create_backup, check_disk):
    raise NotImplementedError(
        "Cette migration est un placeholder V3. "
        "Elle sera implémentée quand la plateforme dépassera 1M de vecteurs "
        "ou nécessitera du multi-tenant."
    )


def rollback(jarvis_root, venv_dir, log):
    raise NotImplementedError("Pas encore implémenté.")
