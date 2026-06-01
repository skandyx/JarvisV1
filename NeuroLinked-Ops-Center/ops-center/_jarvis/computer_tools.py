"""
Computer Tools â€” Jarvis's eyes, hands, and keyboard on the user's machine.

Full OS-level control:
  - Mouse: move, click, double-click, right-click, drag, scroll, get position
  - Keyboard: type text, press keys, hotkey combos
  - Screen: dimensions, screenshot of arbitrary region
  - Windows (OS level): list / focus / get active window
  - Processes: list running processes, kill process

Safety:
  - pyautogui failsafe is enabled â€” moving the mouse to the top-left corner
    (0, 0) will instantly abort any running automation.
  - All functions are wrapped in try/except so a single failure can't crash
    the server.
"""

import os
from typing import Optional

# Graceful imports â€” if pyautogui can't initialize on this box, computer tools
# become no-ops rather than crashing the whole server at startup.
try:
    import pyautogui
    pyautogui.FAILSAFE = True  # mouse â†’ (0,0) aborts
    pyautogui.PAUSE = 0.05     # small settle between actions
    _PYAUTOGUI_OK = True
except Exception as e:
    _PYAUTOGUI_OK = False
    _PYAUTOGUI_ERR = str(e)

try:
    import pygetwindow as gw
    _GW_OK = True
except (ImportError, NotImplementedError):
    # pygetwindow ne fonctionne pas sur Linux
    _GW_OK = False

try:
    import psutil
    _PSUTIL_OK = True
except Exception:
    _PSUTIL_OK = False


def _check_pyautogui() -> Optional[str]:
    if not _PYAUTOGUI_OK:
        return f"pyautogui not available: {_PYAUTOGUI_ERR}. Install with `pip install pyautogui`."
    return None


# ============================================================================
#   Screen / mouse
# ============================================================================

def get_screen_size() -> str:
    err = _check_pyautogui()
    if err: return err
    w, h = pyautogui.size()
    return f"Screen: {w}x{h}"


def get_mouse_position() -> str:
    err = _check_pyautogui()
    if err: return err
    x, y = pyautogui.position()
    return f"Mouse at ({x}, {y})"


def move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    err = _check_pyautogui()
    if err: return err
    try:
        pyautogui.moveTo(int(x), int(y), duration=float(duration))
        return f"Moved to ({x}, {y})"
    except Exception as e:
        return f"Move error: {e}"


def click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1) -> str:
    err = _check_pyautogui()
    if err: return err
    try:
        kwargs = {"button": button, "clicks": int(clicks)}
        if x is not None and y is not None:
            kwargs["x"] = int(x)
            kwargs["y"] = int(y)
        pyautogui.click(**kwargs)
        loc = f"({x}, {y})" if x is not None else "current mouse position"
        return f"Clicked {button} x{clicks} at {loc}"
    except Exception as e:
        return f"Click error: {e}"


def double_click(x: Optional[int] = None, y: Optional[int] = None) -> str:
    return click(x, y, button="left", clicks=2)


def right_click(x: Optional[int] = None, y: Optional[int] = None) -> str:
    return click(x, y, button="right", clicks=1)


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5, button: str = "left") -> str:
    err = _check_pyautogui()
    if err: return err
    try:
        pyautogui.moveTo(int(x1), int(y1))
        pyautogui.dragTo(int(x2), int(y2), duration=float(duration), button=button)
        return f"Dragged ({x1},{y1}) â†’ ({x2},{y2})"
    except Exception as e:
        return f"Drag error: {e}"


def scroll(amount: int = 3, direction: str = "down") -> str:
    """Scroll at current mouse position. direction: 'up' | 'down' | 'left' | 'right'."""
    err = _check_pyautogui()
    if err: return err
    try:
        amt = int(amount)
        if direction == "up":       pyautogui.scroll(amt * 120)
        elif direction == "down":   pyautogui.scroll(-amt * 120)
        elif direction == "left":   pyautogui.hscroll(-amt * 120)
        elif direction == "right":  pyautogui.hscroll(amt * 120)
        else: return f"Unknown direction: {direction}"
        return f"Scrolled {direction} x{amt}"
    except Exception as e:
        return f"Scroll error: {e}"


# ============================================================================
#   Keyboard
# ============================================================================

def type_text(text: str, interval: float = 0.02) -> str:
    err = _check_pyautogui()
    if err: return err
    try:
        pyautogui.typewrite(text, interval=float(interval))
        return f"Typed {len(text)} chars"
    except Exception as e:
        return f"Type error: {e}"


def press_key(key: str) -> str:
    """Press a single key (e.g. 'enter', 'tab', 'f5', 'escape')."""
    err = _check_pyautogui()
    if err: return err
    try:
        pyautogui.press(key)
        return f"Pressed {key}"
    except Exception as e:
        return f"Press error: {e}"


def hotkey(keys: str) -> str:
    """Press a combo (comma-separated): 'ctrl,c' or 'ctrl,shift,t' or 'win,r'."""
    err = _check_pyautogui()
    if err: return err
    try:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        pyautogui.hotkey(*parts)
        return f"Hotkey {'+'.join(parts)}"
    except Exception as e:
        return f"Hotkey error: {e}"


# ============================================================================
#   Windows (OS-level, via pygetwindow)
# ============================================================================

def list_windows() -> str:
    if not _GW_OK:
        return "pygetwindow not available. Install with `pip install pygetwindow`."
    try:
        titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
        return "\n".join(titles) if titles else "(no windows)"
    except Exception as e:
        return f"List windows error: {e}"


def focus_window(title_substring: str) -> str:
    if not _GW_OK:
        return "pygetwindow not available."
    try:
        match = [w for w in gw.getAllWindows() if title_substring.lower() in w.title.lower()]
        if not match:
            return f"No window matching '{title_substring}'"
        w = match[0]
        try:
            w.activate()
        except Exception:
            # On Windows, activate() sometimes fails if window is minimized; restore first
            try:
                w.restore()
                w.activate()
            except Exception:
                pass
        return f"Focused: {w.title}"
    except Exception as e:
        return f"Focus error: {e}"


def get_active_window() -> str:
    if not _GW_OK:
        return "pygetwindow not available."
    try:
        w = gw.getActiveWindow()
        if w is None:
            return "(no active window)"
        return f"Active: {w.title} ({w.width}x{w.height})"
    except Exception as e:
        return f"Active-window error: {e}"


# ============================================================================
#   Processes
# ============================================================================

def list_processes(filter_name: str = "") -> str:
    if not _PSUTIL_OK:
        return "psutil not available. Install with `pip install psutil`."
    try:
        rows = []
        for p in psutil.process_iter(["pid", "name"]):
            name = p.info.get("name") or ""
            if filter_name and filter_name.lower() not in name.lower():
                continue
            rows.append(f"{p.info['pid']:>6}  {name}")
            if len(rows) >= 50:
                break
        return "\n".join(rows) if rows else "(no matches)"
    except Exception as e:
        return f"Process list error: {e}"


def kill_process(pid: int) -> str:
    if not _PSUTIL_OK:
        return "psutil not available."
    try:
        p = psutil.Process(int(pid))
        name = p.name()
        p.terminate()
        return f"Terminated {pid} ({name})"
    except psutil.NoSuchProcess:
        return f"No process with pid {pid}"
    except psutil.AccessDenied:
        return f"Access denied killing {pid}"
    except Exception as e:
        return f"Kill error: {e}"
