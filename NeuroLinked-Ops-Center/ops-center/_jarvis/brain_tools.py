"""
Neurolink Brain Tools â€” Task and memory management for Jarvis/User.

Reads and writes to the brain/ folder so Jarvis can:
- Add / list / complete tasks
- Remember notes (append to Notes.md)
- Recall (search) across all brain files
- Read the persistent Memory.md

This is what makes Jarvis a REAL assistant, not a pretend one.
"""

import os
import re
from datetime import datetime
from typing import Optional

BRAIN_PATH: Optional[str] = None


def init(brain_path: str):
    """Set the brain folder path. Called once at server startup."""
    global BRAIN_PATH
    BRAIN_PATH = brain_path
    os.makedirs(BRAIN_PATH, exist_ok=True)

    # Ensure the core files exist
    for fname, initial in [
        ("Tasks.md", "# Tasks\n\n## Open\n\n## Done\n"),
        ("Memory.md", "# Memory\n\n"),
        ("Notes.md", "# Notes\n\n"),
        ("Personality.md", "# Personality Directives\n\nStanding orders Jarvis has been given. Appended to the system prompt on every call.\n\n## Active Directives\n\n"),
    ]:
        fpath = os.path.join(BRAIN_PATH, fname)
        if not os.path.exists(fpath):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(initial)


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


def list_tasks() -> list[str]:
    """Return all OPEN tasks from Tasks.md."""
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


def list_done() -> list[str]:
    """Return completed tasks."""
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
    """Add a new task under the ## Open heading."""
    task = task.strip()
    if not task:
        return "Empty task, my friend."
    content = _read("Tasks.md") or "# Tasks\n\n## Open\n\n## Done\n"

    if "## Open" not in content:
        content = "# Tasks\n\n## Open\n\n## Done\n"

    # Insert the new task right after "## Open"
    lines = content.splitlines()
    new_lines = []
    inserted = False
    for i, line in enumerate(lines):
        new_lines.append(line)
        if not inserted and line.strip().startswith("## Open"):
            new_lines.append(f"- [ ] {task}")
            inserted = True
    if not inserted:
        new_lines.append("## Open")
        new_lines.append(f"- [ ] {task}")

    _write("Tasks.md", "\n".join(new_lines) + "\n")
    return f"Task added: {task}"


def complete_task(query: str) -> str:
    """Mark a task matching `query` as done (case-insensitive substring match)."""
    query = query.strip().lower()
    if not query:
        return "No task specified."

    content = _read("Tasks.md")
    if not content:
        return "No tasks file found."

    lines = content.splitlines()
    matched_line_idx = None
    matched_text = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- [ ]") and query in stripped.lower():
            matched_line_idx = i
            matched_text = stripped.replace("- [ ]", "").strip()
            break

    if matched_line_idx is None:
        return f"No open task matching '{query}'."

    # Remove from Open
    del lines[matched_line_idx]

    # Add to Done section
    done_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## Done"):
            done_idx = i
            break

    if done_idx is None:
        lines.append("## Done")
        lines.append(f"- [x] {matched_text}")
    else:
        lines.insert(done_idx + 1, f"- [x] {matched_text}")

    _write("Tasks.md", "\n".join(lines) + "\n")
    return f"Completed: {matched_text}"


def remember(note: str) -> str:
    """Append a timestamped note to Notes.md AND push to NeuroLinked Brain if connected."""
    note = note.strip()
    if not note:
        return "Nothing to remember, my friend."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n**{timestamp}** - {note}\n"
    existing = _read("Notes.md") or "# Notes\n\n"
    _write("Notes.md", existing.rstrip() + entry)

    # Dual-write to NeuroLinked Brain (no-op if not connected)
    try:
        import neurolink_bridge
        neurolink_bridge.remember(note, importance=0.6)
    except Exception:
        pass

    return f"Remembered: {note}"


def recall(query: str) -> str:
    """Search local .md memory + the NeuroLinked Brain for the query. Matches
    on any significant token (4+ chars) so a phrasing like 'what time do I
    prefer' still finds the stored note 'Client prefers mornings' via the
    'prefer' token. Always tries both stores; never returns
    'brain not initialized'."""
    import re as _re
    query = query.strip().lower()
    if not query:
        return "No search query, user."

    # Tokenize: keep words >= 4 chars, drop common stopwords. This matches
    # consult_brain's approach.
    _STOP = {"what","when","where","does","this","that","with","from",
             "have","there","then","like","tell","want","need","know",
             "about","just","only","some","very","much","many","into",
             "over","would","could","should"}
    tokens = [w for w in _re.findall(r"[a-zA-Z']{4,}", query) if w not in _STOP]
    # If stop-word filter killed everything, fall back to all 4+ char words.
    if not tokens:
        tokens = _re.findall(r"[a-zA-Z']{4,}", query)
    # Always also keep the literal full phrase as a fallback match.
    needles = list(set(tokens + [query]))

    results = []
    seen = set()

    # Local .md memory — only if the path exists
    if BRAIN_PATH and os.path.isdir(BRAIN_PATH):
        try:
            for fname in sorted(os.listdir(BRAIN_PATH)):
                if not fname.endswith(".md"):
                    continue
                content = _read(fname)
                for i, line in enumerate(content.splitlines(), 1):
                    low = line.lower()
                    if any(n in low for n in needles):
                        key = (fname, i)
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(f"[{fname}:{i}] {line.strip()}")
                        if len(results) >= 10:
                            break
                if len(results) >= 10:
                    break
        except Exception:
            pass

    # NeuroLinked Brain — semantic recall via the bridge
    try:
        import neurolink_bridge
        brain_hits = neurolink_bridge.recall(query, top_k=5)
        for h in brain_hits:
            results.append(f"[neurolink] {h}")
    except Exception:
        pass

    if not results:
        return f"Nothing found for '{query}' yet — the brain is fresh and growing."
    return "\n".join(results)


def read_memory() -> str:
    """Return the full Memory.md content."""
    return _read("Memory.md") or "No memory yet."


def brain_summary() -> dict:
    """Quick summary of brain state for the system prompt."""
    open_tasks = list_tasks()
    done_tasks = list_done()
    memory = read_memory()
    return {
        "open_tasks": open_tasks,
        "open_count": len(open_tasks),
        "done_count": len(done_tasks),
        "memory_preview": memory[:800],
    }


# ============================================================================
#   SELF-MODIFICATION â€” Jarvis can edit his own personality directives.
#   The server reads Personality.md on every system-prompt build, so edits
#   take effect on the NEXT message â€” no restart required.
# ============================================================================

def read_personality() -> str:
    """Read current personality directives. Empty string if none."""
    content = _read("Personality.md") or ""
    # strip the header so only the directives body goes into the prompt
    lines = content.splitlines()
    # skip leading header lines until we hit content or a directive
    return content


def get_personality_addendum() -> str:
    """Return just the active-directive text for injection into the system prompt.
    Strips the file header so we don't waste prompt tokens on the meta-comment."""
    content = _read("Personality.md") or ""
    if not content.strip():
        return ""
    # Extract everything after "## Active Directives" if present, else whole file minus first H1
    marker = "## Active Directives"
    if marker in content:
        body = content.split(marker, 1)[1].strip()
    else:
        # Drop first H1 line
        lines = content.splitlines()
        body = "\n".join(l for l in lines if not l.strip().startswith("# "))
        body = body.strip()
    return body


def append_directive(directive: str) -> str:
    """Jarvis's own self-edit action. Appends a timestamped directive to Personality.md."""
    directive = directive.strip()
    if not directive:
        return "Empty directive â€” nothing to add."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = _read("Personality.md") or "# Personality Directives\n\n## Active Directives\n"
    if "## Active Directives" not in existing:
        existing = existing.rstrip() + "\n\n## Active Directives\n"
    entry = f"- **[{timestamp}]** {directive}\n"
    # Insert at end of file
    _write("Personality.md", existing.rstrip() + "\n" + entry)
    return f"Directive added to my standing orders: {directive}"


def reset_personality() -> str:
    """Wipe all directives but keep the file structure."""
    _write("Personality.md", "# Personality Directives\n\nStanding orders Jarvis has been given. Appended to the system prompt on every call.\n\n## Active Directives\n\n")
    return "All standing directives cleared, sir."


def remove_directive(query: str) -> str:
    """Remove the first directive whose text contains `query` (case-insensitive)."""
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


# ============================================================================
#   GENERIC BRAIN FILE I/O â€” scoped strictly to the Brain folder.
#   Filenames are sanitized: only basename + .md extension.
# ============================================================================

def _sanitize_filename(name: str) -> str:
    """Strip path components, ensure .md extension, lowercase-safe."""
    name = os.path.basename((name or "").strip())
    # remove any remaining path-ish chars
    name = re.sub(r"[^A-Za-z0-9 _.\-]", "", name)
    if not name:
        return ""
    if not name.endswith(".md"):
        name = name + ".md"
    return name


def read_file(name: str) -> str:
    """Read any .md file in the Brain folder. Returns content (truncated to 3000 chars)."""
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    content = _read(fname)
    if not content:
        return f"File not found or empty: {fname}"
    return f"=== {fname} ===\n{content[:3000]}"


def write_file(name: str, content: str) -> str:
    """Write (overwrite) a .md file in the Brain folder."""
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    if fname in ("Tasks.md",):
        return f"Protected file. Use task actions instead."
    _write(fname, content.rstrip() + "\n")
    return f"Wrote {fname} ({len(content)} chars)."


def append_file(name: str, content: str) -> str:
    """Append to a .md file in the Brain folder."""
    fname = _sanitize_filename(name)
    if not fname:
        return "Invalid filename."
    if fname in ("Tasks.md",):
        return f"Protected file. Use task actions instead."
    existing = _read(fname) or ""
    _write(fname, existing.rstrip() + "\n\n" + content.rstrip() + "\n")
    return f"Appended to {fname} (+{len(content)} chars)."


def list_files() -> list[str]:
    """List all .md files in the Brain folder."""
    if not BRAIN_PATH:
        return []
    try:
        return sorted([f for f in os.listdir(BRAIN_PATH) if f.endswith(".md")])
    except Exception:
        return []
