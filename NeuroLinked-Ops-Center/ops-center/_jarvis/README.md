# Jarvis — Voice Assistant (premium tier Edition)

A voice-first AI assistant with full computer control, browser automation, multi-LLM support, and a live connection to the **NeuroLinked Brain** for persistent neural memory.

> Talk to it. It listens. It can read your files, run shell commands, control your mouse, drive your browser, take screenshots with vision, and remember everything. **No API keys required** to get started — it ships with a free built-in voice and supports local LLMs via Ollama.

---

## What it does

| Capability | Module |
|---|---|
| 🎤 Voice in/out — **free browser voice by default**, premium ElevenLabs optional | `server.py` + `frontend/settings.js` |
| 🧩 **Multi-LLM support** — Claude, GPT, Groq, Ollama (local/free), xAI Grok — hot-swap in Settings | `llm_providers.py` |
| ⚙️ **In-app Settings UI** (gear icon) — paste your own keys, no config file editing needed | `frontend/settings.js` |
| 🧠 Remember / recall / tasks (local `.md` + NeuroLink dual-write) | `brain_tools.py` + `neurolink_bridge.py` |
| 💻 Shell execution (system-wide, not sandboxed) | `dev_tools.py` → `run_shell` |
| 📂 File read / write / append / search (anywhere on disk) | `dev_tools.py` |
| 🖱️ Mouse + keyboard + window control | `computer_tools.py` |
| 🌐 Browser automation (Playwright Chromium) | `browser_tools.py` |
| 📸 Screen vision (screenshot + LLM vision) | `screen_capture.py` |
| 👁️ Webcam vision (auto-attached every message) | Built into server |
| 👏 Double-clap trigger (optional) | `scripts/clap-trigger.py` |
| 🔌 MCP server (Claude Desktop / Code / Cursor) | `brain_mcp.py` |

---

## Requirements

- **Python 3.10+** (Windows) — https://python.org/downloads
- **Google Chrome** (for Playwright browser automation)
- **One LLM key** (pick any) — or run Ollama locally for 100% free operation
    - Anthropic — https://console.anthropic.com
    - OpenAI — https://platform.openai.com
    - Groq (fast, free tier) — https://console.groq.com
    - xAI Grok — https://x.ai/api
    - Ollama (local, free) — https://ollama.com
- **ElevenLabs key** (optional, for premium voice) — https://elevenlabs.io
    - Without it, Jarvis uses the **free browser voice** automatically

---

## Install (Windows)

```cmd
install.bat
```

That's it. The installer will:

1. Check Python is installed
2. `pip install -r requirements.txt`
3. `playwright install chromium`
4. Prompt you for API keys + your name + city → writes `config.json`
5. Detect the NeuroLinked Brain at `http://localhost:8000` and auto-wire the connection

After install, double-click **`start.bat`** to launch.

---

## Auto-Connect to NeuroLinked Brain

Jarvis automatically connects to a running **NeuroLinked Brain** server at `http://localhost:8000` on startup.

- ✅ **Brain running** → Every `remember` / `recall` dual-writes to both local `.md` files *and* the Brain's neural memory. Recall queries merge results.
- ⚠️ **Brain not running** → Jarvis falls back to local memory only. A watcher retries every 30 seconds — as soon as the Brain comes online, Jarvis reconnects automatically. Nothing breaks.

To change the URL, edit `config.json`:

```json
{
  "neurolink_url": "http://localhost:8000",
  "auto_connect_neurolink": true
}
```

---

## Voice Setup

1. Browser asks for microphone permission → click **Allow**
2. **Hold SPACE** to talk, release to send. Or click the orb.
3. Webcam permission is requested too — Jarvis sees you. Frame is attached to every message automatically.
4. Without ElevenLabs keys, Jarvis falls back to silent text-only mode.

---

## Starting the Assistant

```cmd
start.bat
```

Then open:
```
http://localhost:8340
```

Port can be changed in `server.py` if 8340 is taken.

---

## Advanced

### MCP (Claude Desktop / Code / Cursor)

`brain_mcp.py` exposes Jarvis's brain tools as an MCP server so any MCP client can access the same memory.

Add to your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "jarvis-brain": {
      "command": "python",
      "args": ["C:\\path\\to\\jarvis\\brain_mcp.py"]
    }
  }
}
```

### Clap-to-launch

Run `python scripts/clap-trigger.py` in the background. Two claps launches your full workspace (Spotify + VS Code + browser + Jarvis). Edit the script to customize.

---

## File Structure

```
jarvis/
├── install.bat              # One-click installer (run first)
├── start.bat                # Launch Jarvis
├── config.json              # Your API keys + preferences
├── config.example.json      # Template
├── server.py                # FastAPI backend (Claude + voice)
├── brain_tools.py           # Tasks, memory, notes (.md files)
├── neurolink_bridge.py      # Auto-connects to NeuroLinked Brain
├── brain_mcp.py             # MCP server (for other AI clients)
├── dev_tools.py             # File + shell + Claude Code hooks
├── computer_tools.py        # Mouse, keyboard, windows
├── browser_tools.py         # Playwright browser control
├── screen_capture.py        # Screenshot + vision
├── requirements.txt
├── frontend/
│   ├── index.html
│   ├── main.js
│   └── style.css
└── scripts/
    ├── clap-trigger.py
    └── launch-jarvis.ps1
```

---

## Troubleshooting

**"Python not found"** → Install Python 3.10+ with "Add to PATH" checked.

**"pip install failed"** → Run `python -m pip install --upgrade pip` then retry.

**"playwright install failed"** → Network issue. Retry: `python -m playwright install chromium`.

**Microphone doesn't work** → Check browser mic permissions for `localhost:8340`.

**No voice output** → ElevenLabs key missing or invalid. Jarvis still works in text-only mode.

**NeuroLink says "not reachable"** → Start the NeuroLinked Brain (port 8000). Jarvis auto-reconnects within 30 seconds.

**Port 8340 already in use** → Edit the last line of `server.py` to change it.

---

## License

Released under MIT — fork it, modify it, ship it.
