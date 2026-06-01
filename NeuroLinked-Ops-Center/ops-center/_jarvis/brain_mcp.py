"""
Neurolink Brain â€” MCP Server

Exposes the same brain_tools functions as MCP tools so ANY MCP-aware client
(Claude Desktop, Claude Code, Cursor, etc.) can access the user's Brain.

The Jarvis FastAPI server calls brain_tools directly in-process â€” this MCP
server is the ALTERNATE access path for other AI clients, backed by the
SAME folder of .md files. One brain, many front-ends.

---

SETUP:

1. Install the MCP Python SDK (one time):
       pip install mcp

2. Add this server to your Claude Desktop config:
   File: %APPDATA%/Claude/claude_desktop_config.json  (Windows)
         ~/Library/Application Support/Claude/claude_desktop_config.json  (macOS)

   Add under "mcpServers":
   {
     "mcpServers": {
       "neurolink-brain": {
         "command": "python",
         "args": ["C:\\\\path\\\\to\\\\jarvis\\\\brain_mcp.py"]
       }
     }
   }

3. Restart Claude Desktop. Your Brain tools will appear in the tool picker.

4. For Claude Code, add the same entry to ~/.claude/mcp_servers.json (or use
   `claude mcp add` in the Claude Code CLI).

---

Run standalone (for testing):
    python brain_mcp.py
"""

import json
import os
import sys

# Make sure we can import sibling modules regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("[brain_mcp] ERROR: `mcp` package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

import brain_tools


# --- Resolve brain path from the same config.json Jarvis uses ---
_here = os.path.dirname(os.path.abspath(__file__))
_config_path = os.path.join(_here, "config.json")
_brain_path = None
try:
    with open(_config_path, "r", encoding="utf-8") as f:
        _cfg = json.load(f)
    _brain_path = _cfg.get("brain_path") or _cfg.get("obsidian_inbox_path")
except Exception as e:
    print(f"[brain_mcp] Could not load config.json: {e}", file=sys.stderr)

# Fallback default: brain/ next to this file
if not _brain_path:
    _brain_path = os.path.join(_here, "brain")

brain_tools.init(_brain_path)
print(f"[brain_mcp] Brain path: {_brain_path}", file=sys.stderr)


# ============================================================================
#   MCP Server definition
# ============================================================================
mcp = FastMCP("Neurolink Brain")


# ---- Tasks ----
@mcp.tool()
def add_task(task: str) -> str:
    """Add a new task to the user's open task list (Tasks.md). Persists to disk."""
    return brain_tools.add_task(task)


@mcp.tool()
def list_tasks() -> str:
    """Return all currently open tasks as a newline-separated list."""
    tasks = brain_tools.list_tasks()
    if not tasks:
        return "No open tasks."
    return "Open tasks:\n- " + "\n- ".join(tasks)


@mcp.tool()
def complete_task(query: str) -> str:
    """Mark an open task as done by case-insensitive substring match on `query`.
    Moves it from the Open section to the Done section of Tasks.md."""
    return brain_tools.complete_task(query)


@mcp.tool()
def list_done() -> str:
    """Return recently completed tasks."""
    tasks = brain_tools.list_done()
    if not tasks:
        return "No completed tasks."
    return "Completed:\n- " + "\n- ".join(tasks)


# ---- Notes / memory / recall ----
@mcp.tool()
def remember(note: str) -> str:
    """Append a timestamped note to Notes.md. Use for ideas, preferences, facts worth preserving."""
    return brain_tools.remember(note)


@mcp.tool()
def recall(query: str) -> str:
    """Search across ALL Brain .md files for lines containing `query` (case-insensitive).
    Returns up to 10 matching lines with [filename:lineno] prefixes."""
    return brain_tools.recall(query)


@mcp.tool()
def read_memory() -> str:
    """Return the full contents of Memory.md â€” the user's persistent long-term memory."""
    return brain_tools.read_memory()


# ---- Self-modification (Jarvis personality directives) ----
@mcp.tool()
def view_directives() -> str:
    """Return Jarvis's active standing personality directives (from Personality.md).
    These are injected into Jarvis's system prompt on every conversation turn."""
    body = brain_tools.get_personality_addendum()
    return body.strip() if body.strip() else "No active directives."


@mcp.tool()
def add_directive(directive: str) -> str:
    """Append a standing directive to Jarvis's personality. Takes effect on Jarvis's next message.
    These are rules like 'always confirm before scheduling' or 'speak more briefly'."""
    return brain_tools.append_directive(directive)


@mcp.tool()
def remove_directive(query: str) -> str:
    """Remove an active directive by partial substring match."""
    return brain_tools.remove_directive(query)


@mcp.tool()
def reset_directives() -> str:
    """Wipe ALL standing directives. Destructive â€” Jarvis returns to default behavior."""
    return brain_tools.reset_personality()


# ---- Generic file I/O (scoped to Brain folder) ----
@mcp.tool()
def read_brain_file(filename: str) -> str:
    """Read any .md file in the Brain folder. Filename is sanitized to basename + .md."""
    return brain_tools.read_file(filename)


@mcp.tool()
def write_brain_file(filename: str, content: str) -> str:
    """Overwrite a .md file in the Brain. Tasks.md is protected â€” use task tools instead."""
    return brain_tools.write_file(filename, content)


@mcp.tool()
def append_brain_file(filename: str, content: str) -> str:
    """Append content to a .md file in the Brain (creates if missing). Tasks.md is protected."""
    return brain_tools.append_file(filename, content)


@mcp.tool()
def list_brain_files() -> str:
    """List all .md files currently in the Brain folder."""
    files = brain_tools.list_files()
    return "Brain files: " + ", ".join(files) if files else "No files."


# ---- Resource: expose Memory.md as readable context ----
@mcp.resource("brain://memory")
def memory_resource() -> str:
    """The complete Memory.md contents as a resource."""
    return brain_tools.read_memory()


@mcp.resource("brain://tasks")
def tasks_resource() -> str:
    """Current open tasks as a resource."""
    tasks = brain_tools.list_tasks()
    return "\n".join(f"- {t}" for t in tasks) if tasks else "No open tasks."


@mcp.resource("brain://personality")
def personality_resource() -> str:
    """Jarvis's active standing directives."""
    return brain_tools.get_personality_addendum()


if __name__ == "__main__":
    mcp.run()
