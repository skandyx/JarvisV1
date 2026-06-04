"""
brain_tools v2 — ChromaDB + Markdown hybrid memory for Jarvis V2

Architecture :
  - ChromaDB  : vector store local (semantic search / RAG)
  - .md files : toujours là (Tasks, Personality, Notes) — lisibles à la main
  - Migration : script _migrate_md_to_chroma() au premier démarrage
  - API       : 100 % rétrocompatible avec V1 (même signatures)

Installation :
    pip install chromadb sentence-transformers

Modèle d'embedding par défaut : all-MiniLM-L6-v2 (90 Mo, hors ligne après dl)
Override via env :  JARVIS_EMBED_MODEL=all-mpnet-base-v2
"""

import os
import re
import json
import hashlib
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Optional ChromaDB — graceful fallback to MD-only if not installed
# ---------------------------------------------------------------------------
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False
    print("[brain] ChromaDB not installed — pip install chromadb  (running MD-only mode)", flush=True)

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False
    print("[brain] sentence-transformers not installed — pip install sentence-transformers  (keyword search only)", flush=True)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
BRAIN_PATH: Optional[str] = None
_chroma_client = None
_collection = None          # "jarvis_memory" — notes, memory, docs
_embed_model = None
_embed_lock = threading.Lock()
_migrated = False

EMBED_MODEL_NAME = os.environ.get("JARVIS_EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_COLLECTION = "jarvis_memory"

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init(brain_path: str):
    """
    Set the brain folder path and boot ChromaDB.
    Called once at server startup — same signature as V1.
    """
    global BRAIN_PATH, _chroma_client, _collection, _embed_model, _migrated

    BRAIN_PATH = brain_path
    os.makedirs(BRAIN_PATH, exist_ok=True)

    # Ensure core MD files still exist (tasks + personality stay as MD)
    for fname, initial in [
        ("Tasks.md",       "# Tasks\n\n## Open\n\n## Done\n"),
        ("Memory.md",      "# Memory\n\n"),
        ("Notes.md",       "# Notes\n\n"),
        ("Personality.md", "# Personality Directives\n\nStanding orders Jarvis has been given.\n\n## Active Directives\n\n"),
    ]:
        fpath = os.path.join(BRAIN_PATH, fname)
        if not os.path.exists(fpath):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(initial)

    if not _CHROMA_AVAILABLE:
        print("[brain] MD-only mode active.", flush=True)
        return

    # Boot ChromaDB (persistent, local folder)
    chroma_dir = os.path.join(BRAIN_PATH, ".chromadb")
    os.makedirs(chroma_dir, exist_ok=True)
    try:
        _chroma_client = chromadb.PersistentClient(
            path=chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        _collection = _chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"[brain] ChromaDB online — {_collection.count()} vectors in '{CHROMA_COLLECTION}'", flush=True)
    except Exception as e:
        print(f"[brain] ChromaDB init failed: {e} — falling back to MD-only", flush=True)
        _chroma_client = None
        _collection = None
        return

    # Embedding model (lazy-loaded in background thread to not block startup)
    def _load_embed():
        global _embed_model
        if not _ST_AVAILABLE:
            return
        try:
            with _embed_lock:
                if _embed_model is None:
                    _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
                    print(f"[brain] Embedding model ready: {EMBED_MODEL_NAME}", flush=True)
        except Exception as e:
            print(f"[brain] Embedding model load failed: {e}", flush=True)

    threading.Thread(target=_load_embed, daemon=True).start()

    # One-time migration of existing .md content into ChromaDB
    migration_flag = os.path.join(chroma_dir, ".migrated_v1")
    if not os.path.exists(migration_flag):
        threading.Thread(target=_migrate_md_to_chroma, args=(migration_flag,), daemon=True).start()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read(fname: str) -> str:
    if not BRAIN_PATH:
        return ""
    fpath = os.path.join(BRAIN_PATH, fname)
    if not os.path.exists(fpath):
        return ""
    with open(fpath, "r", encoding="utf-8") as f:
        return f.read()


def _write(fname: str, content: str) -> None:
    if not BRAIN_PATH:
        return
    fpath = os.path.join(BRAIN_PATH, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)


def _doc_id(text: str, source: str) -> str:
    """Deterministic ID so re-ingesting the same note is idempotent."""
    return hashlib.sha256(f"{source}::{text}".encode()).hexdigest()[:16]


def _embed(text: str) -> Optional[List[float]]:
    """Return embedding vector or None if model not ready."""
    if not _ST_AVAILABLE or _embed_model is None:
        return None
    try:
        with _embed_lock:
            vec = _embed_model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        print(f"[brain] embed error: {e}", flush=True)
        return None


def _chroma_add(text: str, metadata: Dict[str, Any]) -> bool:
    """Add a document to ChromaDB. Returns True on success."""
    if _collection is None or not text.strip():
        return False
    doc_id = _doc_id(text, metadata.get("source", "unknown"))
    vec = _embed(text)
    try:
        if vec:
            _collection.upsert(
                ids=[doc_id],
                documents=[text],
                embeddings=[vec],
                metadatas=[metadata],
            )
        else:
            # Store without embedding — keyword search still works via ChromaDB's
            # built-in string matching
            _collection.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata],
            )
        return True
    except Exception as e:
        print(f"[brain] chroma_add error: {e}", flush=True)
        return False


def _chroma_search(query: str, n: int = 8, where: Optional[Dict] = None) -> List[Dict]:
    """
    Semantic search (with embedding) or keyword fallback.
    Returns list of {text, metadata, distance}.
    """
    if _collection is None:
        return []
    try:
        vec = _embed(query)
        kwargs: Dict[str, Any] = {
            "n_results": min(n, max(1, _collection.count())),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        if vec:
            kwargs["query_embeddings"] = [vec]
            results = _collection.query(**kwargs)
        else:
            kwargs["query_texts"] = [query]
            results = _collection.query(**kwargs)
        out = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            out.append({"text": doc, "metadata": meta, "distance": dist})
        return out
    except Exception as e:
        print(f"[brain] chroma_search error: {e}", flush=True)
        return []


def _migrate_md_to_chroma(flag_path: str):
    """
    One-time migration: chunk all existing .md files and push to ChromaDB.
    Runs in a daemon thread at startup — never blocks Jarvis.
    """
    if not BRAIN_PATH or _collection is None:
        return
    print("[brain] Starting V1 → ChromaDB migration...", flush=True)
    count = 0
    for fname in sorted(os.listdir(BRAIN_PATH)):
        if not fname.endswith(".md"):
            continue
        content = _read(fname)
        if not content.strip():
            continue
        # Chunk by paragraph (double newline), min 30 chars
        chunks = [c.strip() for c in re.split(r"\n{2,}", content) if len(c.strip()) >= 30]
        for chunk in chunks:
            ok = _chroma_add(chunk, {
                "source": fname,
                "type": "migration",
                "ts": datetime.now().isoformat(),
            })
            if ok:
                count += 1
    # Write migration flag
    try:
        with open(flag_path, "w") as f:
            f.write(f"migrated {count} chunks at {datetime.now().isoformat()}\n")
    except Exception:
        pass
    print(f"[brain] Migration done — {count} chunks indexed in ChromaDB", flush=True)


# ---------------------------------------------------------------------------
# Tasks (MD-based — stays fast & human-readable)
# ---------------------------------------------------------------------------

def list_tasks() -> List[str]:
    """Return all OPEN tasks from Tasks.md. (V1 compatible)"""
    content = _read("Tasks.md")
    tasks = []
    in_open = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open"):
            in_open = True
            continue
        if stripped.startswith("## Done"):
            in_open = False
            continue
        if in_open and stripped.startswith("- [ ]"):
            tasks.append(stripped.replace("- [ ]", "").strip())
    return tasks


def list_done() -> List[str]:
    """Return completed tasks. (V1 compatible)"""
    content = _read("Tasks.md")
    tasks = []
    in_done = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Done"):
            in_done = True
            continue
        if stripped.startswith("## ") and in_done:
            in_done = False
            continue
        if in_done and stripped.startswith("- [x]"):
            tasks.append(stripped.replace("- [x]", "").strip())
    return tasks


def add_task(task: str) -> str:
    """Add a new task under ## Open. Also indexes in ChromaDB. (V1 compatible)"""
    task = task.strip()
    if not task:
        return "Empty task."
    content = _read("Tasks.md") or "# Tasks\n\n## Open\n\n## Done\n"
    if "## Open" not in content:
        content = "# Tasks\n\n## Open\n\n## Done\n"
    lines = content.splitlines()
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and line.strip().startswith("## Open"):
            new_lines.append(f"- [ ] {task}")
            inserted = True
    if not inserted:
        new_lines.extend(["## Open", f"- [ ] {task}"])
    _write("Tasks.md", "\n".join(new_lines) + "\n")

    # Index in ChromaDB for semantic task retrieval
    _chroma_add(task, {
        "source": "Tasks.md",
        "type": "task",
        "status": "open",
        "ts": datetime.now().isoformat(),
    })
    return f"Task added: {task}"


def complete_task(query: str) -> str:
    """Mark a task as done. (V1 compatible)"""
    query = query.strip().lower()
    if not query:
        return "No task specified."
    content = _read("Tasks.md")
    if not content:
        return "No tasks file found."
    lines = content.splitlines()
    matched_idx, matched_text = None, None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- [ ]") and query in stripped.lower():
            matched_idx = i
            matched_text = stripped.replace("- [ ]", "").strip()
            break
    if matched_idx is None:
        return f"No open task matching '{query}'."
    del lines[matched_idx]
    done_idx = next((i for i, l in enumerate(lines) if l.strip().startswith("## Done")), None)
    if done_idx is None:
        lines += ["## Done", f"- [x] {matched_text}"]
    else:
        lines.insert(done_idx + 1, f"- [x] {matched_text}")
    _write("Tasks.md", "\n".join(lines) + "\n")

    # Update ChromaDB metadata
    if _collection is not None and matched_text:
        doc_id = _doc_id(matched_text, "Tasks.md")
        try:
            _collection.update(ids=[doc_id], metadatas=[{
                "source": "Tasks.md",
                "type": "task",
                "status": "done",
                "ts": datetime.now().isoformat(),
            }])
        except Exception:
            pass
    return f"Completed: {matched_text}"


# ---------------------------------------------------------------------------
# Memory & Notes — dual-write MD + ChromaDB
# ---------------------------------------------------------------------------

def remember(note: str) -> str:
    """
    Append note to Notes.md AND index in ChromaDB for semantic recall.
    (V1 compatible)
    """
    note = note.strip()
    if not note:
        return "Nothing to remember."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n**{timestamp}** - {note}\n"
    existing = _read("Notes.md") or "# Notes\n\n"
    _write("Notes.md", existing.rstrip() + entry)

    # Index in ChromaDB
    _chroma_add(note, {
        "source": "Notes.md",
        "type": "note",
        "ts": timestamp,
    })

    # NeuroLink bridge (unchanged from V1)
    try:
        import neurolink_bridge
        neurolink_bridge.remember(note, importance=0.6)
    except Exception:
        pass

    return f"Remembered: {note}"


def remember_with_context(note: str, context: str = "", tags: Optional[List[str]] = None) -> str:
    """
    NEW in V2 — rich ingestion with optional context and tags.
    Combines note + context for better semantic retrieval.
    """
    note = note.strip()
    if not note:
        return "Nothing to remember."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    full_text = f"{note}\n{context}".strip() if context else note

    # MD
    entry = f"\n**{timestamp}** - {note}\n"
    if context:
        entry += f"  *Context: {context}*\n"
    existing = _read("Notes.md") or "# Notes\n\n"
    _write("Notes.md", existing.rstrip() + entry)

    # ChromaDB
    meta: Dict[str, Any] = {
        "source": "Notes.md",
        "type": "note",
        "ts": timestamp,
    }
    if tags:
        meta["tags"] = json.dumps(tags)
    _chroma_add(full_text, meta)
    return f"Remembered with context: {note}"


def ingest_document(title: str, content: str, source: str = "document",
                    chunk_size: int = 400, chunk_overlap: int = 80) -> str:
    """
    NEW in V2 — ingest any long-form text into ChromaDB with chunking.
    Use for: Obsidian vault files, web pages, PDF extracts, etc.
    Returns number of chunks indexed.
    """
    if not content.strip():
        return "Empty document."
    if _collection is None:
        return "ChromaDB not available — document not indexed."

    # Simple sliding-window chunker (word boundary)
    words = content.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        if len(chunk.strip()) >= 30:
            chunks.append(chunk)
        i += chunk_size - chunk_overlap

    ts = datetime.now().isoformat()
    for idx, chunk in enumerate(chunks):
        _chroma_add(chunk, {
            "source": source,
            "title": title,
            "type": "document",
            "chunk_idx": idx,
            "ts": ts,
        })
    return f"Indexed '{title}' — {len(chunks)} chunks in ChromaDB."


def ingest_obsidian_vault(vault_path: str) -> str:
    """
    NEW in V2 — walk an Obsidian vault and index all .md files.
    Call once (or on a schedule) to keep ChromaDB in sync.
    """
    if not os.path.isdir(vault_path):
        return f"Vault path not found: {vault_path}"
    count = 0
    for root, _, files in os.walk(vault_path):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                title = fname.replace(".md", "")
                rel = os.path.relpath(fpath, vault_path)
                result = ingest_document(title, content, source=f"obsidian:{rel}")
                # parse chunk count from result string
                m = re.search(r"(\d+) chunks", result)
                count += int(m.group(1)) if m else 0
            except Exception as e:
                print(f"[brain] obsidian ingest skip {fname}: {e}", flush=True)
    return f"Obsidian vault indexed — {count} total chunks from {vault_path}"


# ---------------------------------------------------------------------------
# Recall — semantic first, keyword fallback
# ---------------------------------------------------------------------------

def recall(query: str, top_k: int = 8) -> str:
    """
    V2 semantic recall: ChromaDB cosine search → keyword fallback → NeuroLink.
    Fully backward compatible with V1 (same signature).
    """
    query = query.strip()
    if not query:
        return "No search query."

    results = []

    # 1. ChromaDB semantic search
    if _collection is not None and _collection.count() > 0:
        hits = _chroma_search(query, n=top_k)
        for h in hits:
            meta = h["metadata"]
            src = meta.get("source", "?")
            score = round(1 - h["distance"], 2)  # cosine similarity
            snippet = h["text"][:120].replace("\n", " ")
            results.append(f"[{src} | score={score}] {snippet}")

    # 2. Keyword fallback on .md files (always runs — covers Tasks, Personality)
    if BRAIN_PATH and os.path.isdir(BRAIN_PATH):
        _STOP = {
            "what","when","where","does","this","that","with","from",
            "have","there","then","like","tell","want","need","know",
            "about","just","only","some","very","much","many","into",
            "over","would","could","should",
        }
        tokens = [w for w in re.findall(r"[a-zA-Z']{4,}", query.lower()) if w not in _STOP]
        needles = list(set(tokens + [query.lower()])) or [query.lower()]
        seen = set()
        for fname in sorted(os.listdir(BRAIN_PATH)):
            if not fname.endswith(".md"):
                continue
            content = _read(fname)
            for i, line in enumerate(content.splitlines(), 1):
                low = line.lower()
                if any(n in low for n in needles):
                    key = (fname, i)
                    if key not in seen:
                        seen.add(key)
                        results.append(f"[{fname}:{i}] {line.strip()}")
                        if len(results) >= top_k + 5:
                            break

    # 3. NeuroLink bridge (unchanged)
    try:
        import neurolink_bridge
        for h in neurolink_bridge.recall(query, top_k=5):
            results.append(f"[neurolink] {h}")
    except Exception:
        pass

    if not results:
        return f"Nothing found for '{query}' yet — the brain is fresh and growing."
    return "\n".join(results[:top_k + 5])


def search_memory(query: str, n: int = 6, source_filter: Optional[str] = None) -> List[Dict]:
    """
    NEW in V2 — returns raw structured results for agent pipelines.
    Each item: {text, source, type, score, ts}
    """
    where = {"source": source_filter} if source_filter else None
    hits = _chroma_search(query, n=n, where=where)
    return [
        {
            "text": h["text"],
            "source": h["metadata"].get("source", "?"),
            "type": h["metadata"].get("type", "?"),
            "score": round(1 - h["distance"], 3),
            "ts": h["metadata"].get("ts", ""),
        }
        for h in hits
    ]


# ---------------------------------------------------------------------------
# Memory.md (unchanged from V1 — used for system prompt injection)
# ---------------------------------------------------------------------------

def read_memory() -> str:
    """Return Memory.md content. (V1 compatible)"""
    return _read("Memory.md") or "No memory yet."


def write_memory(content: str) -> str:
    """Overwrite Memory.md and re-index in ChromaDB."""
    _write("Memory.md", content)
    # Re-index chunks
    if _collection is not None:
        chunks = [c.strip() for c in re.split(r"\n{2,}", content) if len(c.strip()) >= 30]
        for chunk in chunks:
            _chroma_add(chunk, {
                "source": "Memory.md",
                "type": "memory",
                "ts": datetime.now().isoformat(),
            })
    return "Memory.md updated and re-indexed."


# ---------------------------------------------------------------------------
# ChromaDB stats (new)
# ---------------------------------------------------------------------------

def memory_stats() -> Dict[str, Any]:
    """
    NEW in V2 — returns memory health info for the dashboard.
    """
    stats: Dict[str, Any] = {
        "chroma_available": _CHROMA_AVAILABLE,
        "embed_model_ready": _embed_model is not None,
        "embed_model_name": EMBED_MODEL_NAME,
        "total_vectors": 0,
        "md_files": [],
    }
    if _collection is not None:
        try:
            stats["total_vectors"] = _collection.count()
        except Exception:
            pass
    if BRAIN_PATH:
        stats["md_files"] = sorted([f for f in os.listdir(BRAIN_PATH) if f.endswith(".md")])
    return stats


def brain_summary() -> Dict[str, Any]:
    """Quick summary for the system prompt. (V1 compatible)"""
    open_tasks = list_tasks()
    done_tasks = list_done()
    memory = read_memory()
    return {
        "open_tasks": open_tasks,
        "open_count": len(open_tasks),
        "done_count": len(done_tasks),
        "memory_preview": memory[:800],
        "vector_count": _collection.count() if _collection else 0,
    }


# ---------------------------------------------------------------------------
# Personality / directives (unchanged from V1)
# ---------------------------------------------------------------------------

def get_personality_addendum() -> str:
    content = _read("Personality.md") or ""
    if not content.strip():
        return ""
    marker = "## Active Directives"
    if marker in content:
        body = content.split(marker, 1)[1].strip()
    else:
        lines = content.splitlines()
        body = "\n".join(l for l in lines if not l.strip().startswith("# ")).strip()
    return body


def append_directive(directive: str) -> str:
    directive = directive.strip()
    if not directive:
        return "Empty directive."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = _read("Personality.md") or "# Personality Directives\n\n## Active Directives\n"
    if "## Active Directives" not in existing:
        existing = existing.rstrip() + "\n\n## Active Directives\n"
    entry = f"- **[{timestamp}]** {directive}\n"
    _write("Personality.md", existing.rstrip() + "\n" + entry)
    return f"Directive added: {directive}"


def reset_personality() -> str:
    _write("Personality.md", "# Personality Directives\n\nStanding orders Jarvis has been given.\n\n## Active Directives\n\n")
    return "All standing directives cleared."


def remove_directive(query: str) -> str:
    query = query.strip().lower()
    if not query:
        return "No query specified."
    content = _read("Personality.md") or ""
    lines = content.splitlines()
    removed = None
    new_lines = []
    for line in lines:
        if removed is None and line.strip().startswith("- **[") and query in line.lower():
            removed = line.strip()
            continue
        new_lines.append(line)
    if removed is None:
        return f"No directive matching '{query}'."
    _write("Personality.md", "\n".join(new_lines) + "\n")
    return f"Removed directive: {removed}"


def read_personality() -> str:
    return _read("Personality.md") or ""


def view_self() -> str:
    body = get_personality_addendum()
    return body.strip() if body.strip() else "No standing directives set."


# ---------------------------------------------------------------------------
# Generic Brain file I/O (unchanged from V1, + optional ChromaDB indexing)
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    name = os.path.basename((name or "").strip())
    name = re.sub(r"[^A-Za-z0-9 _.\-]", "", name)
    if not name:
        return ""
    if not name.endswith(".md"):
        name = name + ".md"
    return name


def read_file(name: str) -> str:
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    content = _read(fname)
    if not content:
        return f"File not found or empty: {fname}"
    return f"=== {fname} ===\n{content[:3000]}"


def write_file(name: str, content: str) -> str:
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    if fname in ("Tasks.md",):
        return "Protected file. Use task actions instead."
    _write(fname, content.rstrip() + "\n")
    # Re-index in ChromaDB
    if _collection is not None:
        chunks = [c.strip() for c in re.split(r"\n{2,}", content) if len(c.strip()) >= 30]
        for chunk in chunks:
            _chroma_add(chunk, {
                "source": fname,
                "type": "brain_file",
                "ts": datetime.now().isoformat(),
            })
    return f"Wrote {fname} ({len(content)} chars)."


def append_file(name: str, content: str) -> str:
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    if fname in ("Tasks.md",):
        return "Protected file. Use task actions instead."
    existing = _read(fname) or ""
    new_content = existing.rstrip() + "\n\n" + content.rstrip() + "\n"
    _write(fname, new_content)
    # Index new chunk
    if _collection is not None:
        _chroma_add(content.strip(), {
            "source": fname,
            "type": "brain_file",
            "ts": datetime.now().isoformat(),
        })
    return f"Appended to {fname} (+{len(content)} chars)."


def list_files() -> List[str]:
    if not BRAIN_PATH:
        return []
    try:
        return sorted([f for f in os.listdir(BRAIN_PATH) if f.endswith(".md")])
    except Exception:
        return []
