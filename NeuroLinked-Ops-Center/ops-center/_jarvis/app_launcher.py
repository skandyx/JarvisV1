r"""
Jarvis App Launcher — open desktop apps by name.

Apps are configured in config.json under the "apps" list. Each entry has:
  - name      : canonical name
  - aliases   : alternate names the user might say
  - launcher  : the program to invoke (e.g. "explorer.exe", a path to .exe,
                "cmd.exe /c start ...", or a URL/protocol like "claude://")
  - args      : list of command-line args
  - notes     : optional human-readable note

Two main launch patterns on Windows:

1) Microsoft Store apps (e.g. Claude Desktop) — use stable AUMID via:
     explorer.exe shell:AppsFolder\<AUMID>
   Survives version updates because the AUMID is fixed.

2) Standard .exe in a known path — direct invocation.

Public functions:
  launch(name_or_alias)  -> str
  list_apps()            -> list of dicts
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Optional

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")


def _read_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_app(name_or_alias: str) -> Optional[dict]:
    """Resolve a name (or alias) to an app config entry. Case-insensitive,
    matches first hit."""
    target = (name_or_alias or "").strip().lower()
    if not target:
        return None
    for app in _read_config().get("apps", []):
        candidates = [app.get("name", "")] + list(app.get("aliases", []))
        for c in candidates:
            if c and c.lower() == target:
                return app
        # Allow substring fuzzy match as a fallback
        if target in (app.get("name", "") + " " + " ".join(app.get("aliases", []))).lower():
            return app
    return None


def list_apps() -> list[dict]:
    """Return a brief summary of every configured app."""
    out = []
    for app in _read_config().get("apps", []):
        out.append({
            "name": app.get("name"),
            "aliases": app.get("aliases", []),
            "launcher": app.get("launcher"),
            "notes": app.get("notes", ""),
        })
    return out


def launch(name_or_alias: str) -> str:
    """Launch an app by name. Returns a human-readable status string."""
    app = _find_app(name_or_alias)
    if not app:
        configured = [a.get("name") for a in _read_config().get("apps", [])]
        return (f"No app called '{name_or_alias}' is configured. "
                f"Configured: {configured or '(none)'}")
    launcher = app.get("launcher", "").strip()
    args = list(app.get("args", []))
    if not launcher:
        return f"App '{app.get('name')}' has no launcher configured."
    try:
        # Special-case: URL / protocol handlers (e.g. https://, claude://, spotify:)
        if launcher.startswith(("http://", "https://", "spotify:", "claude:", "vscode:", "obsidian:")):
            os.startfile(launcher)  # opens in default handler
            return f"Opened '{app.get('name')}' via protocol."
        # Standard process spawn — non-blocking, detached so Jarvis returns
        # immediately even if the launched app takes time to render.
        proc = subprocess.Popen(
            [launcher] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return f"Launched '{app.get('name')}' (pid {proc.pid})."
    except FileNotFoundError:
        return f"Launcher not found: {launcher}. Check config.json apps[].launcher path."
    except Exception as e:
        return f"Failed to launch '{app.get('name')}': {type(e).__name__}: {e}"


def add_app(name: str, launcher: str, args: list = None, aliases: list = None, notes: str = "") -> str:
    """Append a new app entry to config.json. Used at runtime when Jarvis
    discovers a new app or the user asks him to remember one."""
    args = args or []
    aliases = aliases or []
    cfg = _read_config()
    apps = cfg.setdefault("apps", [])
    # Replace existing if name matches
    apps[:] = [a for a in apps if a.get("name", "").lower() != name.lower()]
    apps.append({
        "name": name,
        "aliases": aliases,
        "launcher": launcher,
        "args": args,
        "notes": notes,
    })
    cfg["apps"] = apps
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, _CONFIG_PATH)
    return f"Registered app '{name}'."


# ============================================================================
# Wake routine — composes other tools to start the workday
# ============================================================================

def start_workday(period: str = "morning") -> str:
    """The 'time to get to work' routine. Plays the configured wake song
    AND opens Claude Desktop (and any other tools the user has tagged
    with alias 'work-startup' in config.json apps).

    `period` is "morning" | "evening" | other — used only for the spoken
    response; the routine itself is identical."""
    summary = []

    # 1. Spotify wake song (non-blocking, runs in spotify_tools)
    try:
        import spotify_tools
        wake_result = spotify_tools.play_wake_song()
        summary.append(f"Music: {wake_result}")
    except Exception as e:
        summary.append(f"Music: (spotify_tools unavailable — {e})")

    # 2. Open all apps tagged for work-startup, plus Claude as the canonical
    #    "your AI app". If 'Claude' is configured, always open it.
    apps = _read_config().get("apps", [])
    to_open = []
    for app in apps:
        aliases = [a.lower() for a in app.get("aliases", [])]
        tags = [t.lower() for t in app.get("tags", [])]
        if "work-startup" in tags or app.get("name", "").lower() == "claude":
            to_open.append(app["name"])
    # Dedup, preserve order
    seen = set()
    to_open = [x for x in to_open if not (x in seen or seen.add(x))]
    for name in to_open:
        summary.append(f"App '{name}': {launch(name)}")

    if not to_open:
        summary.append("(No 'Claude' or 'work-startup'-tagged apps configured. "
                       "Use add_app to register them.)")

    greeting = {
        "morning": "Good morning, sir. Workday started.",
        "evening": "Good evening, sir. Workday started.",
    }.get(period.lower(), "Workday routine started.")
    return greeting + " " + " | ".join(summary)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(list_apps(), indent=2))
    elif cmd == "launch":
        print(launch(" ".join(sys.argv[2:])))
    elif cmd == "workday":
        print(start_workday(sys.argv[2] if len(sys.argv) > 2 else "morning"))
    else:
        print(f"Unknown command: {cmd}")
        print("Available: list | launch <name> | workday [morning|evening]")
