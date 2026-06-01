"""
Jarvis Spotify Tools — playback control via Spotipy + OAuth.

Capabilities:
  - search_and_play(query)   → search tracks/playlists, queue + play first hit
  - play_uri(uri)            → play a specific spotify URI/ID/URL
  - pause / resume / skip_next / skip_previous
  - now_playing()            → what's currently playing
  - set_wake_song(track)     → save the wake-up song into config.json
  - play_wake_song()         → play whatever wake song is configured

Auth model:
  - First run opens a browser tab to Spotify OAuth. User logs in, grants
    permission. Spotipy stores the refresh token in `.spotify-cache` next
    to this file (gitignored).
  - All subsequent runs auto-refresh with no user interaction.

Requirements:
  - Spotify Premium account (Web API playback requires it)
  - An active device (Spotify desktop / mobile / web player open). The API
    controls EXISTING devices — it doesn't create new ones. If there's no
    active device, this module tries to pick the most recently used one.
  - Redirect URI `http://127.0.0.1:8888/callback` registered in Spotify
    Dashboard → app → settings → Redirect URIs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time as _time
import webbrowser
from typing import Optional


# ============================================================================
# Browser preference — Spotify needs to open in Chrome (not Edge), because
# the user's whole workflow runs through Chrome. Default browser on this
# machine is Edge, so we explicitly resolve a Chrome path and bypass
# webbrowser.open()'s default-browser routing.
# ============================================================================
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]


def _resolve_chrome() -> Optional[str]:
    """Return the first Chrome.exe path that exists, or None."""
    # 1. Honor explicit override in config.json (config.preferred_browser_path)
    cfg = _read_config() if "_read_config" in globals() else {}
    override = (cfg.get("preferred_browser_path") or "").strip()
    if override and os.path.exists(override):
        return override
    # 2. Try the standard Chrome install paths
    for p in _CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def _open_in_chrome(url: str) -> bool:
    """Open `url` in Chrome explicitly. Falls back to the system default
    browser if Chrome isn't installed. Returns True if the command spawned
    successfully (doesn't guarantee the page loaded — that's async)."""
    chrome = _resolve_chrome()
    if chrome:
        try:
            # Detached non-blocking spawn so Jarvis returns immediately
            subprocess.Popen(
                [chrome, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
            )
            return True
        except Exception as e:
            print(f"[spotify] Chrome launch failed, falling back to default: {e}", flush=True)
    # Fallback — webbrowser.open uses default browser (Edge on this machine)
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    _SPOTIPY_OK = True
except ImportError:
    _SPOTIPY_OK = False

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")
_CACHE_PATH = os.path.join(_BASE_DIR, ".spotify-cache")  # OAuth token cache (gitignored)
_REDIRECT_URI = "http://127.0.0.1:8888/callback"
# Scopes — what Jarvis is allowed to do on the user's account
_SCOPE = " ".join([
    "user-modify-playback-state",   # play/pause/skip/queue
    "user-read-playback-state",     # read current device + state
    "user-read-currently-playing",  # what's playing
    "streaming",                    # required for some playback ops
    "playlist-read-private",        # find user's playlists
    "user-library-read",            # read saved tracks/albums
])

_client: Optional["spotipy.Spotify"] = None
_auth_error: Optional[str] = None
# When the user asks to play something but auth isn't done yet, we queue
# the request here so the OAuth callback handler can fire it the moment
# the token lands. Single-slot queue is fine — only one play at a time.
_pending_play: Optional[dict] = None  # {"kind": "search"|"uri", "value": str}


def _read_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(cfg: dict):
    """Write config.json atomically. Used by set_wake_song."""
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, _CONFIG_PATH)


def _build_auth_manager() -> Optional["SpotifyOAuth"]:
    """Build (without authenticating) a SpotifyOAuth helper. Returns None if
    creds are missing."""
    cfg = _read_config()
    cid = cfg.get("spotify_client_id", "").strip()
    secret = cfg.get("spotify_client_secret", "").strip()
    if not cid or not secret:
        return None
    return SpotifyOAuth(
        client_id=cid,
        client_secret=secret,
        redirect_uri=_REDIRECT_URI,
        scope=_SCOPE,
        cache_path=_CACHE_PATH,
        open_browser=False,  # we open it ourselves so we never block
    )


def _ensure_client() -> Optional["spotipy.Spotify"]:
    """Return an authenticated Spotipy client (cached). On failure, sets
    _auth_error and returns None — caller should surface that string.

    NEVER blocks on first-time OAuth. If there's no cached refresh token,
    immediately returns None with an instruction to run authorize() first.
    The actual authorize() flow runs in a background thread so Jarvis's
    main dispatcher is never frozen by Spotify."""
    global _client, _auth_error

    if _client is not None:
        return _client
    if not _SPOTIPY_OK:
        _auth_error = "spotipy not installed"
        return None

    auth = _build_auth_manager()
    if auth is None:
        _auth_error = ("Spotify client_id / secret missing from config.json. "
                       "Open the dashboard gear icon to add them.")
        return None

    # Crucial: only proceed if a refresh token is already cached. If not,
    # report a clean setup-needed error instead of blocking on browser auth.
    cached = auth.get_cached_token()
    if not cached:
        _auth_error = ("Spotify needs a one-time authorization. Tell me "
                       "'authorize Spotify' (I'll open the consent page in "
                       "your browser; click Agree, and you're done).")
        return None

    try:
        # Cached token will auto-refresh inside spotipy if expired.
        _client = spotipy.Spotify(auth_manager=auth)
        _client.current_user()  # sanity ping
        _auth_error = None
        return _client
    except Exception as e:
        _auth_error = f"Spotify auth failed: {type(e).__name__}: {str(e)[:200]}"
        _client = None
        return None


def authorize() -> str:
    """Begin the one-time OAuth flow.

    Architecture:
      1. Spin up a local HTTP server on 127.0.0.1:8888 (background thread).
         Listens for ONE request on /callback, pulls the ?code= param.
      2. Open the Spotify consent URL in the user's browser.
      3. User clicks Agree → Spotify redirects to 127.0.0.1:8888/callback?code=...
      4. Our server catches the code, exchanges it for a refresh token via
         spotipy, writes the cache, and shuts itself down.

    Replaces spotipy's stdin-prompt fallback (which is what was breaking —
    it prints 'Enter the URL you were redirected to:' and waits on stdin
    that no one can reach in a headless Jarvis process)."""
    if not _SPOTIPY_OK:
        return "spotipy not installed"
    auth = _build_auth_manager()
    if auth is None:
        return "Spotify creds missing in config.json — fill them first."
    if auth.get_cached_token():
        return "Spotify is already authorized. Try a play command."

    import threading as _th
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    state = {"done": False, "error": None}

    class _CallbackHandler(BaseHTTPRequestHandler):
        # Suppress default server logging spam in Jarvis console
        def log_message(self, fmt, *args):
            pass
        def do_GET(self):
            try:
                u = urlparse(self.path)
                if u.path != "/callback":
                    self.send_response(404); self.end_headers()
                    return
                qs = parse_qs(u.query)
                code = qs.get("code", [None])[0]
                err = qs.get("error", [None])[0]
                if err:
                    state["error"] = err
                    body = f"<h1>Spotify auth error</h1><p>{err}</p><p>You can close this tab.</p>"
                elif code:
                    # Exchange code → tokens (spotipy writes to cache_path automatically)
                    try:
                        auth.get_access_token(code=code, as_dict=False, check_cache=False)
                        state["done"] = True
                        body = "<h1>✅ Spotify connected!</h1><p>You can close this tab. Jarvis is ready.</p>"
                    except Exception as e:
                        state["error"] = f"token exchange failed: {e}"
                        body = f"<h1>Token exchange failed</h1><p>{e}</p>"
                else:
                    state["error"] = "callback missing code"
                    body = "<h1>Missing code</h1>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
            except Exception as e:
                state["error"] = f"handler crashed: {e}"
                try:
                    self.send_response(500); self.end_headers()
                    self.wfile.write(str(e).encode())
                except Exception:
                    pass

    def _run_server():
        try:
            srv = HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
            srv.timeout = 1
            # Serve up to ~5 minutes, polling state['done']
            for _ in range(300):
                srv.handle_request()
                if state["done"] or state["error"]:
                    break
            srv.server_close()
            if state["done"]:
                global _client, _auth_error, _pending_play
                _client = None  # force re-init so the new cache is picked up
                _auth_error = None
                print("[spotify] OAuth complete — refresh token cached", flush=True)
                # If the user kicked off auth via a play command, fire that
                # play now so they don't have to ask twice.
                pending = _pending_play
                _pending_play = None
                if pending:
                    print(f"[spotify] auto-resuming queued {pending['kind']}: {pending['value'][:60]}", flush=True)
                    try:
                        if pending["kind"] == "search":
                            result = search_and_play(pending["value"])
                        else:
                            result = play_uri(pending["value"])
                        print(f"[spotify] auto-resume: {result}", flush=True)
                    except Exception as _e:
                        print(f"[spotify] auto-resume failed: {_e}", flush=True)
            elif state["error"]:
                print(f"[spotify] OAuth failed: {state['error']}", flush=True)
                _pending_play = None  # clear; user will need to ask again
        except OSError as e:
            # Port 8888 already bound? Earlier auth attempt may have left a server stuck.
            print(f"[spotify] callback server failed to bind: {e}", flush=True)

    _th.Thread(target=_run_server, daemon=True).start()
    # Tiny delay so the server is listening before the browser hits localhost
    _time.sleep(0.3)

    auth_url = auth.get_authorize_url()
    _open_in_chrome(auth_url)

    return ("Opening Spotify consent page in Chrome. Click 'Agree' "
            "and the page will say 'Spotify connected' — then ask me to "
            "play anything.")


def open_web_player(track_uri: Optional[str] = None) -> str:
    """Open the Spotify Web Player in Chrome (NOT the system default, which
    on this machine is Edge). Once loaded, it registers as an available
    Spotify Connect device. If `track_uri` is given, opens directly to that
    track (auto-plays if user is signed in).

    Use to bootstrap playback when no Spotify device is currently active."""
    try:
        if track_uri:
            url = _spotify_uri_to_url(track_uri) or "https://open.spotify.com"
        else:
            url = "https://open.spotify.com"
        ok = _open_in_chrome(url)
        if not ok:
            return f"Could not open Chrome (and fallback failed). URL: {url}"
        return f"Opened Spotify Web Player in Chrome → {url}"
    except Exception as e:
        return f"Could not open Spotify Web Player: {e}"


def _spotify_uri_to_url(uri: str) -> Optional[str]:
    """Convert spotify:track:ID / spotify:album:ID / spotify:playlist:ID into
    the public open.spotify.com URL the web player accepts."""
    if not uri:
        return None
    if uri.startswith("https://"):
        return uri
    if uri.startswith("spotify:"):
        parts = uri.split(":")
        if len(parts) >= 3:
            return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
    return None


def _pick_preferred_device(devices: list) -> Optional[dict]:
    """Pick the best available Spotify device. Preference order:
      1. A Chrome-named device (the user explicitly wants Chrome over Edge).
      2. The currently-active device (whatever it is).
      3. The first non-Edge device.
      4. Any device.
    """
    if not devices:
        return None
    # Prefer Chrome-named device — typically "Web Player (Chrome)"
    chrome = next((d for d in devices if "chrome" in (d.get("name", "")).lower()), None)
    if chrome:
        return chrome
    # Then active
    active = next((d for d in devices if d.get("is_active")), None)
    if active:
        return active
    # Then non-Edge
    non_edge = next((d for d in devices if "edge" not in (d.get("name", "")).lower()), None)
    if non_edge:
        return non_edge
    return devices[0]


def _ensure_active_device(sp: "spotipy.Spotify", auto_open: bool = True) -> Optional[str]:
    """Returns the active device id, preferring Chrome over Edge. If no
    Chrome device exists AND auto_open is True, opens the Spotify Web
    Player in CHROME and waits up to ~12s for it to register as a Spotify
    Connect device, then transfers playback there."""
    try:
        devices = sp.devices().get("devices", [])
        if devices:
            target = _pick_preferred_device(devices)
            if target:
                # Force-transfer playback to the preferred device. force_play=True
                # ensures Edge stops playing if it was the active device.
                try:
                    sp.transfer_playback(device_id=target["id"], force_play=True)
                except Exception:
                    pass  # transient 404/502 — just continue, start_playback follows
                return target["id"]

        # No devices at all. Bootstrap by opening the web player.
        if not auto_open:
            return None
        print("[spotify] no devices — opening Web Player in Chrome to bootstrap...", flush=True)
        _open_in_chrome("https://open.spotify.com")
        # Web player needs a moment to authenticate + register with Spotify Connect.
        # Poll up to ~12 seconds for the new device to show up.
        for attempt in range(12):
            _time.sleep(1)
            try:
                devices = sp.devices().get("devices", [])
            except Exception:
                continue
            if devices:
                target = devices[0]
                try:
                    sp.transfer_playback(device_id=target["id"], force_play=False)
                except Exception:
                    pass
                print(f"[spotify] web player registered as device after {attempt+1}s", flush=True)
                return target["id"]
        # Still nothing — likely the user isn't logged into Spotify in their browser
        return None
    except Exception:
        return None


# ============================================================================
# Public tool surface — each returns a human-readable string for Jarvis to speak
# ============================================================================

def search_and_play(query: str) -> str:
    """Search for a track / album / artist / playlist and play the top hit.

    Strategy:
      1. Search via API to resolve the query → URI
      2. Try API playback (most reliable when an active device exists)
      3. If no device → auto-open Web Player in Chrome → wait ~12s → retry
      4. If still failing → fall back to opening the track URL directly so
         Chrome's Spotify Web Player just plays it via clickthrough.

    If the user has never authorized Spotify, this method automatically
    queues the request and kicks off the OAuth flow — when the user clicks
    Agree, the callback handler resumes the play."""
    if not query or not query.strip():
        return "Spotify: empty search query."
    sp = _ensure_client()
    if sp is None:
        # Auto-bootstrap: queue this play, kick off OAuth flow.
        global _pending_play
        _pending_play = {"kind": "search", "value": query}
        msg = authorize()
        return (f"Authorizing Spotify first. {msg} "
                f"Once you click Agree, '{query}' will start automatically.")

    # 1. Search → URI
    try:
        is_playlist = "playlist" in query.lower()
        if is_playlist:
            res = sp.search(q=query, type="playlist", limit=1)
            items = res.get("playlists", {}).get("items", [])
            if not items:
                return f"Spotify: no playlist matched '{query}'."
            uri = items[0]["uri"]
            display = f"playlist '{items[0]['name']}'"
        else:
            res = sp.search(q=query, type="track", limit=1)
            items = res.get("tracks", {}).get("items", [])
            if not items:
                return f"Spotify: no track matched '{query}'."
            track = items[0]
            uri = track["uri"]
            artist = track["artists"][0]["name"] if track.get("artists") else "?"
            display = f"'{track['name']}' by {artist}"
    except Exception as e:
        return f"Spotify search error: {type(e).__name__}: {str(e)[:200]}"

    # 2. + 3. Try API playback (with auto-bootstrap of web player if no device)
    device_id = _ensure_active_device(sp, auto_open=True)
    if device_id is not None:
        try:
            if is_playlist or uri.startswith("spotify:album:") or uri.startswith("spotify:artist:"):
                sp.start_playback(device_id=device_id, context_uri=uri)
            else:
                sp.start_playback(device_id=device_id, uris=[uri])
            return f"Playing {display}."
        except spotipy.exceptions.SpotifyException as e:
            # 403 = no premium / restriction; 404 = device gone; 502 = transient
            print(f"[spotify] API playback failed ({e.http_status}): {e.msg}", flush=True)
            # fall through to URL fallback

    # 4. Last-resort fallback: open the track URL directly in Chrome.
    # If user's signed into Spotify in Chrome, the web player auto-plays.
    url = _spotify_uri_to_url(uri)
    if url:
        if _open_in_chrome(url):
            return (f"Opened {display} in Chrome's Spotify Web Player — make sure "
                    f"you're signed in, and click play if it doesn't auto-start.")
        return f"Spotify: search found {display} but couldn't open Chrome."
    return f"Spotify: found {display} but no device was reachable."


def play_uri(uri: str) -> str:
    """Play a specific Spotify URI / URL / ID. Auto-detects kind (track /
    album / playlist / artist). Falls back to opening the URL directly in
    the user's browser if no device is reachable for API playback.

    Same auto-auth-on-first-call behavior as search_and_play()."""
    raw = (uri or "").strip()
    if not raw:
        return "Spotify: empty URI."
    sp = _ensure_client()
    if sp is None:
        global _pending_play
        _pending_play = {"kind": "uri", "value": raw}
        msg = authorize()
        return (f"Authorizing Spotify first. {msg} "
                f"Once you click Agree, the track will start automatically.")

    # Normalize various input forms:
    #   spotify:track:1abc...
    #   https://open.spotify.com/track/1abc...?si=...
    #   1abc...                              ← assume track if just an ID
    spotify_uri = raw
    if raw.startswith("https://"):
        try:
            path = raw.split("open.spotify.com/")[1].split("?")[0]
            kind, _id = path.split("/")
            spotify_uri = f"spotify:{kind}:{_id}"
        except Exception:
            return f"Spotify: could not parse URL '{raw[:80]}'."
    elif not raw.startswith("spotify:"):
        spotify_uri = f"spotify:track:{raw}"

    kind = spotify_uri.split(":")[1] if ":" in spotify_uri else "track"
    short_id = spotify_uri.split(":")[-1][:12]

    # Try API playback (auto-bootstraps web player if no device)
    device_id = _ensure_active_device(sp, auto_open=True)
    if device_id is not None:
        try:
            if kind in ("album", "playlist", "artist"):
                sp.start_playback(device_id=device_id, context_uri=spotify_uri)
            else:
                sp.start_playback(device_id=device_id, uris=[spotify_uri])
            return f"Playing {kind} {short_id}."
        except spotipy.exceptions.SpotifyException as e:
            print(f"[spotify] API playback failed ({e.http_status}): {e.msg}", flush=True)

    # Fallback: open the URL directly in Chrome
    url = _spotify_uri_to_url(spotify_uri)
    if url:
        if _open_in_chrome(url):
            return (f"Opened {kind} {short_id} in Chrome's Spotify Web Player. "
                    f"Sign in if prompted.")
        return f"Spotify: couldn't open Chrome for {url}"
    return f"Spotify: no device and could not build a fallback URL for {spotify_uri}."


def pause() -> str:
    sp = _ensure_client()
    if sp is None:
        return f"Spotify: {_auth_error}"
    try:
        sp.pause_playback()
        return "Paused."
    except spotipy.exceptions.SpotifyException as e:
        return f"Spotify: {e.msg or str(e)[:120]}"
    except Exception as e:
        return f"Spotify error: {e}"


def resume() -> str:
    sp = _ensure_client()
    if sp is None:
        return f"Spotify: {_auth_error}"
    try:
        sp.start_playback()
        return "Resumed."
    except spotipy.exceptions.SpotifyException as e:
        return f"Spotify: {e.msg or str(e)[:120]}"
    except Exception as e:
        return f"Spotify error: {e}"


def skip_next() -> str:
    sp = _ensure_client()
    if sp is None:
        return f"Spotify: {_auth_error}"
    try:
        sp.next_track()
        return "Skipped to next track."
    except Exception as e:
        return f"Spotify error: {e}"


def skip_previous() -> str:
    sp = _ensure_client()
    if sp is None:
        return f"Spotify: {_auth_error}"
    try:
        sp.previous_track()
        return "Back to previous track."
    except Exception as e:
        return f"Spotify error: {e}"


def now_playing() -> str:
    sp = _ensure_client()
    if sp is None:
        return f"Spotify: {_auth_error}"
    try:
        cur = sp.current_playback()
        if not cur or not cur.get("item"):
            return "Nothing currently playing."
        item = cur["item"]
        name = item.get("name", "?")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        is_playing = cur.get("is_playing", False)
        state = "Playing" if is_playing else "Paused"
        return f"{state}: '{name}' by {artists}."
    except Exception as e:
        return f"Spotify error: {e}"


# ============================================================================
# Wake song — what plays when Jarvis activates
# ============================================================================

def get_wake_song() -> str:
    """Return the configured wake-song string (URI / URL / search query)."""
    cfg = _read_config()
    return (cfg.get("spotify_track") or "").strip()


def set_wake_song(track: str) -> str:
    """Persist the wake song. Accepts URIs, URLs, or free-text search queries.
    Stored in config.json under `spotify_track`."""
    cfg = _read_config()
    cfg["spotify_track"] = (track or "").strip()
    try:
        _write_config(cfg)
        return f"Wake song set to: {track}"
    except Exception as e:
        return f"Could not save wake song: {e}"


def play_wake_song() -> str:
    """Play whatever's set as the wake song.

    Hand it straight to the Spotify desktop app via the spotify: URI scheme.
    The desktop client handles playback natively — no OAuth, no Web Playback
    SDK, no extra browser tabs piling up. Falls back to the web URL only if
    the desktop URI launch fails.
    """
    track = get_wake_song()
    if not track:
        return "No wake song configured. Set one with set_wake_song()."

    # Normalize to spotify:track:ID
    uri = track
    if track.startswith("https://open.spotify.com/"):
        # https://open.spotify.com/track/4LJufKlXXROlQODuqEXCOR(?si=...)
        try:
            tail = track.split("https://open.spotify.com/", 1)[1]
            tail = tail.split("?", 1)[0].rstrip("/")
            parts = tail.split("/")
            if len(parts) >= 2:
                kind, rid = parts[0], parts[1]
                uri = f"spotify:{kind}:{rid}"
        except Exception:
            pass
    elif not track.startswith("spotify:"):
        # Free-text song name — fall back to spotipy search if available
        return search_and_play(track) if _SPOTIPY_OK else (
            f"Wake song '{track}' isn't a Spotify URI. Set it via set_wake_song with a spotify: URI or open.spotify.com URL."
        )

    # Launch desktop client. `cmd /c start "" <uri>` lets Windows resolve the
    # spotify: protocol handler — no shell window, no new browser tab.
    import subprocess
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", uri],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return f"Playing {uri} in Spotify desktop."
    except Exception as e:
        return f"Could not launch Spotify desktop ({e})."


# ============================================================================
# Diagnostics / setup helpers
# ============================================================================

def status() -> dict:
    """Return a dict describing current Spotify integration health.
    Used by the dashboard / setup wizard."""
    cfg = _read_config()
    has_creds = bool(cfg.get("spotify_client_id") and cfg.get("spotify_client_secret"))
    cached = os.path.exists(_CACHE_PATH)
    sp = _ensure_client()
    user = None
    if sp is not None:
        try:
            u = sp.current_user()
            user = {
                "id": u.get("id"),
                "display_name": u.get("display_name"),
                "product": u.get("product"),  # 'premium' or 'free'
            }
        except Exception:
            pass
    return {
        "spotipy_installed": _SPOTIPY_OK,
        "has_credentials": has_creds,
        "oauth_cached": cached,
        "client_ok": sp is not None,
        "user": user,
        "wake_song": get_wake_song(),
        "auth_error": _auth_error,
    }


if __name__ == "__main__":
    # Quick CLI for manual testing: `python spotify_tools.py status`
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
    elif cmd == "play":
        q = " ".join(sys.argv[2:]) or "lo-fi beats"
        print(search_and_play(q))
    elif cmd == "pause":
        print(pause())
    elif cmd == "now":
        print(now_playing())
    elif cmd == "wake":
        print(play_wake_song())
    elif cmd == "set-wake":
        print(set_wake_song(" ".join(sys.argv[2:])))
    else:
        print(f"Unknown command: {cmd}")
        print("Available: status | play <query> | pause | now | wake | set-wake <song>")
