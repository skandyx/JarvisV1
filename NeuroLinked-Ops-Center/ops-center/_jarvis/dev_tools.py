"""
Dev Tools â€” Jarvis's code-editing hands.

Scoped to a configurable `dev_workspace` folder. Jarvis can:
  - read / write / append / list files
  - run shell commands (npm, python, git, pytest, build scripts, etc.)
  - recursive grep
  - INVOKE CLAUDE CODE headlessly (`claude -p "prompt"`) for complex coding delegations

Path safety: any relative path is resolved under WORKSPACE, and absolute
paths that escape WORKSPACE are rejected.
"""

import asyncio
import fnmatch
import os
import re
from typing import Optional

WORKSPACE: str = ""


def init(workspace: str):
    """Set and ensure the dev workspace directory exists."""
    global WORKSPACE
    WORKSPACE = os.path.abspath(workspace)
    os.makedirs(WORKSPACE, exist_ok=True)


def _safe_path(path: str) -> str:
    """Resolve path within WORKSPACE. Rejects anything that escapes."""
    if not WORKSPACE:
        raise RuntimeError("dev_tools not initialized â€” call dev_tools.init(path) first.")
    if os.path.isabs(path):
        p = os.path.abspath(path)
    else:
        p = os.path.abspath(os.path.join(WORKSPACE, path))
    if not (p == WORKSPACE or p.startswith(WORKSPACE + os.sep)):
        raise ValueError(f"Path escapes workspace: {path}")
    return p


# ============================================================================
#   File I/O
# ============================================================================

def read_file(path: str, max_chars: int = 8000) -> str:
    """Read a file from the workspace. Truncated to max_chars."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return str(e)
    if not os.path.exists(p):
        return f"File not found: {path}"
    if os.path.isdir(p):
        return f"Is a directory (use list_dir): {path}"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > max_chars:
            return content[:max_chars] + f"\n\n... [truncated, {len(content)} total chars]"
        return content
    except Exception as e:
        return f"Read error: {e}"


def write_file(path: str, content: str) -> str:
    """Overwrite (or create) a file in the workspace."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return str(e)
    try:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {path} ({len(content)} chars)"
    except Exception as e:
        return f"Write error: {e}"


def append_file(path: str, content: str) -> str:
    """Append content to a file (creates if missing)."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return str(e)
    try:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended to {path} (+{len(content)} chars)"
    except Exception as e:
        return f"Append error: {e}"


def list_dir(path: str = "") -> str:
    """List entries in a workspace directory."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return str(e)
    if not os.path.exists(p):
        return f"Not found: {path}"
    if not os.path.isdir(p):
        return f"Not a directory: {path}"
    entries = []
    for item in sorted(os.listdir(p)):
        if item.startswith(".") and item not in (".env", ".gitignore"):
            continue
        full = os.path.join(p, item)
        marker = "/" if os.path.isdir(full) else ""
        entries.append(f"{item}{marker}")
    return "\n".join(entries) if entries else "(empty)"


def delete_file(path: str) -> str:
    """Delete a file (not a directory). Scoped to workspace."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return str(e)
    if not os.path.exists(p):
        return f"File not found: {path}"
    if os.path.isdir(p):
        return f"Is a directory; refusing to delete: {path}"
    try:
        os.remove(p)
        return f"Deleted {path}"
    except Exception as e:
        return f"Delete error: {e}"


def search(pattern: str, file_glob: str = "*") -> str:
    """Recursive grep across the workspace. Returns up to 25 matching lines."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)

    skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build", ".next", "venv", ".venv", "target"}
    skip_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav", ".zip", ".exe", ".dll", ".pyc"}

    matches = []
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if not fnmatch.fnmatch(fname, file_glob):
                continue
            if os.path.splitext(fname)[1].lower() in skip_ext:
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, WORKSPACE).replace(os.sep, "/")
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            matches.append(f"{rel}:{i}: {line.rstrip()[:150]}")
                            if len(matches) >= 25:
                                break
            except Exception:
                continue
            if len(matches) >= 25:
                break
        if len(matches) >= 25:
            break

    return "\n".join(matches) if matches else f"No matches for '{pattern}'"


# ============================================================================
#   Shell execution (scoped to workspace)
# ============================================================================

async def run_shell(cmd: str, timeout: int = 60, cwd: Optional[str] = None) -> str:
    """Run a shell command inside the workspace. Returns stdout+stderr (truncated).
    `cwd` is optional subdirectory path under workspace."""
    try:
        workdir = _safe_path(cwd) if cwd else WORKSPACE
    except ValueError as e:
        return str(e)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return f"Command timed out after {timeout}s: {cmd}"
        output = stdout.decode("utf-8", errors="replace") or "(no output)"
        rc = proc.returncode
        suffix = "" if rc == 0 else f"\n[exit {rc}]"
        return (output[:4000] + suffix)
    except Exception as e:
        return f"Shell error: {e}"


# ============================================================================
#   Claude Code passthrough â€” Jarvis delegates hard coding work to Claude Code
# ============================================================================

# ============================================================================
#   SYSTEM-WIDE (unscoped) file & shell access.
#
#   These operate anywhere on the user's machine â€” no workspace sandbox.
#   Jarvis uses them for real dev work (editing project files, running
#   arbitrary commands, etc.). The user is the authorized operator of his
#   own computer; no artificial wall here.
# ============================================================================

def system_read_file(abs_path: str, max_chars: int = 8000) -> str:
    """Read any file on the filesystem by absolute (or user-relative) path."""
    try:
        p = os.path.abspath(os.path.expanduser(abs_path))
        if not os.path.exists(p):
            return f"File not found: {p}"
        if os.path.isdir(p):
            return f"Is a directory (use system_list_dir): {p}"
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > max_chars:
            return content[:max_chars] + f"\n\n... [truncated, {len(content)} total chars]"
        return content
    except Exception as e:
        return f"System read error: {e}"


def system_write_file(abs_path: str, content: str) -> str:
    """Write any file on the filesystem. Creates parent dirs."""
    try:
        p = os.path.abspath(os.path.expanduser(abs_path))
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {p} ({len(content)} chars)"
    except Exception as e:
        return f"System write error: {e}"


def system_append_file(abs_path: str, content: str) -> str:
    """Append to any file on the filesystem. Creates parent dirs."""
    try:
        p = os.path.abspath(os.path.expanduser(abs_path))
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended to {p} (+{len(content)} chars)"
    except Exception as e:
        return f"System append error: {e}"


def system_list_dir(abs_path: str) -> str:
    """List any directory on the filesystem."""
    try:
        p = os.path.abspath(os.path.expanduser(abs_path))
        if not os.path.isdir(p):
            return f"Not a directory: {p}"
        entries = []
        for item in sorted(os.listdir(p)):
            full = os.path.join(p, item)
            marker = "/" if os.path.isdir(full) else ""
            entries.append(f"{item}{marker}")
        return "\n".join(entries) if entries else "(empty)"
    except Exception as e:
        return f"System list error: {e}"


async def system_shell(cmd: str, timeout: int = 60, cwd: Optional[str] = None) -> str:
    """Run a shell command anywhere on the filesystem (unscoped).
    Use this for builds / git / npm outside the workspace."""
    workdir = None
    if cwd:
        try:
            workdir = os.path.abspath(os.path.expanduser(cwd))
            if not os.path.isdir(workdir):
                return f"cwd not a directory: {workdir}"
        except Exception as e:
            return f"Bad cwd: {e}"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try: proc.kill()
            except Exception: pass
            return f"System shell timed out after {timeout}s: {cmd}"
        output = stdout.decode("utf-8", errors="replace") or "(no output)"
        rc = proc.returncode
        suffix = "" if rc == 0 else f"\n[exit {rc}]"
        return output[:4000] + suffix
    except Exception as e:
        return f"System shell error: {e}"


async def invoke_claude_code(prompt: str, timeout: int = 240, cwd: Optional[str] = None) -> str:
    """Invoke the `claude` CLI in headless print mode (-p) with the given prompt.
    This lets Jarvis hand off complex multi-file coding tasks to Claude Code itself,
    which will run in the specified workspace directory.

    Falls back to a helpful message if `claude` is not on PATH.
    """
    try:
        workdir = _safe_path(cwd) if cwd else WORKSPACE
    except ValueError as e:
        return str(e)
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return (
            "Claude Code CLI not found on PATH. "
            "Install it (requires Node.js): `npm i -g @anthropic-ai/claude-code`, "
            "then `claude` will be available. "
            "Meanwhile Jarvis can still edit code directly via read_dev_file / write_dev_file / run_shell."
        )
    except Exception as e:
        return f"Claude Code launch error: {e}"

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Claude Code timed out after {timeout}s."

    output = stdout.decode("utf-8", errors="replace") or "(no output)"
    rc = proc.returncode
    suffix = "" if rc == 0 else f"\n[claude exit {rc}]"
    return output[:6000] + suffix


async def system_invoke_claude_code(prompt: str, abs_cwd: str, timeout: int = 240) -> str:
    """Invoke Claude Code CLI in ANY directory on the filesystem (unscoped).
    Use this to delegate work to Claude Code inside an existing project â€”
    e.g. 'C:/path/to/your-project' â€” not limited to the Jarvis workspace."""
    try:
        workdir = os.path.abspath(os.path.expanduser(abs_cwd))
        if not os.path.isdir(workdir):
            return f"cwd not a directory: {workdir}"
    except Exception as e:
        return f"Bad cwd: {e}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return (
            "Claude Code CLI not found on PATH. "
            "Install: `npm i -g @anthropic-ai/claude-code` (requires Node.js)."
        )
    except Exception as e:
        return f"Claude Code launch error: {e}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try: proc.kill()
        except Exception: pass
        return f"Claude Code timed out after {timeout}s."
    output = stdout.decode("utf-8", errors="replace") or "(no output)"
    rc = proc.returncode
    suffix = "" if rc == 0 else f"\n[claude exit {rc}]"
    return output[:6000] + suffix
