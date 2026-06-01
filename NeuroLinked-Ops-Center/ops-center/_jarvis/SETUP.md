# NeuroLinked Ops Center — setup guide

Runs on **Windows 10/11**, **macOS** (Intel + Apple Silicon), and most **Linux**
distros. About 15 minutes the first time, ~10 seconds every launch after.

---

## TL;DR

1. **Install Python 3.11+** (https://www.python.org/downloads/)
   - On Windows, check "Add Python to PATH" during install.
   - On macOS, you can also `brew install python`.
2. **Run the installer**:
   - Windows: right-click `install.ps1` → **Run with PowerShell**
   - macOS / Linux: open a terminal in the project folder and run `bash install.sh`
3. **Start it**:
   - Windows: double-click `start.bat`
   - macOS / Linux: `./start.sh` in a terminal
4. **Open** http://localhost:8010 (the launcher does this for you)
5. The settings panel pops open automatically. Paste an LLM API key
   (Anthropic, OpenAI, Groq, or xAI) and hit Save. You're live.

---

## What you need

| Required | Optional |
|---|---|
| Python 3.11+ | Ollama (free local LLM) |
| Internet (for Anthropic/OpenAI/Groq/xAI cloud LLMs) | ElevenLabs API key (premium voice; the browser voice works for free) |
| Chrome or Edge browser (Web Speech API + WebSocket support) | A microphone (for "Hey Jarvis" / push-to-talk) |
|  | A webcam (Jarvis can see you with `see_me`) |

The installer will prompt you about Ollama. Skip it if you only plan to use
cloud LLMs.

---

## LLM compatibility

Jarvis works with any of these — pick one in the gear icon:

| Provider | Model field defaults to | Where to get a key |
|---|---|---|
| **Anthropic** (Claude) | `claude-haiku-4-5-20251001` | https://console.anthropic.com/settings/keys |
| **OpenAI** (GPT) | `gpt-4o-mini` | https://platform.openai.com/api-keys |
| **Groq** (free tier, fast) | `llama-3.3-70b-versatile` | https://console.groq.com/keys |
| **Ollama** (local, free) | `llama3.1:8b` | install Ollama; no key needed |
| **xAI** (Grok) | `grok-2-latest` | https://console.x.ai/ |

You can switch any time in the gear icon — Jarvis hot-reloads the provider
without a restart.

---

## Voice

Two TTS modes, switchable in the gear icon:

- **Browser** (default) — free, built into Chrome/Edge, works offline.
  Robotic but functional.
- **ElevenLabs** — premium voice (set a voice ID like `JBFqnCBsd6RMkjVDRZzb`
  for George, the British storyteller). Falls back to the browser voice
  automatically if ElevenLabs returns an error (quota, invalid key, etc.).

For wake-word + push-to-talk:

- Say **"Hey Jarvis"** — he listens for the next 10 seconds.
- Or tap **SPACE** — same thing, push-to-talk.
- Tap SPACE while he's speaking — interrupts him so you don't talk over.

Speech recognition uses the browser's built-in `webkitSpeechRecognition`,
which **only works in Chrome and Edge**.

---

## Where things live

| File | What it is | Safe to copy across machines? |
|---|---|---|
| `ops-center/_jarvis/config.json` | Your API keys + LLM choice + voice ID | ⚠️ contains keys |
| `ops-center/_jarvis/frontend/` | Jarvis UI (HTML/CSS/JS) | yes |
| `ops-center/state.json` | Calendar, inbox, knowledge counters | yes — Jarvis can edit this himself |
| `ops-center/agents.json` | Your custom agents | yes |
| `neurolinked-brain/brain_state/` | Brain memory (notes/tasks/recall index) — created fresh on first run | yes if you want the same memory |
| `neurolinked-brain/dashboard/` | Brain UI | yes |

---

## Security

Out of the box:

- All three services bind to **127.0.0.1 only** — nobody on your LAN or the
  internet can reach them, verified at the OS socket level.
- **Per-startup launch token** — every API call and WebSocket connect needs a
  token that's only embedded in the locally-served HTML. Browser extensions,
  rogue tabs, and DNS-rebinding attacks all fail without it.
- **Origin + Host header guards** on top, layered defense in depth.
- WebSocket payload cap at 8 MB to block memory-DoS attempts.
- Token rotates on every `start` — leaked tokens are invalidated by
  stop + start.

The only realistic threat that remains is a malicious process running as your
OS user (which would also have access to your browser cookies, your config
files, etc. — that's an account-level compromise, not something this app can
defend against alone).

---

## Troubleshooting

**"start.bat / start.sh doesn't open the browser"**
- Run `stop.bat` / `./stop.sh` then `start` again.
- If a port is stuck: Windows `netstat -ano | findstr :8010` then `taskkill
  /F /PID <pid>`. macOS/Linux: `lsof -i :8010` then `kill <pid>`.
- Check the logs at `%TEMP%\neurolinked-*.log` (Windows) or `/tmp/neurolinked-*.log`
  (macOS/Linux).

**"Jarvis says 'I hit a snag on that one, sir'"**
- Means the LLM call failed. Common causes:
  - No LLM provider configured (gear icon → paste a key)
  - Wrong key for the provider (verify on the provider's dashboard)
  - Rate limit (wait a minute)
- Jarvis automatically retries once with a fresh context before showing this
  message, so persistent failures = real API/key problem.

**"I can't hear Jarvis"**
- If using ElevenLabs: probably the key's per-key character cap is exhausted.
  Go to https://elevenlabs.io/app/settings/api-keys → click your key → set
  Monthly Credit Limit to **Unlimited**.
- Or switch the voice mode to **Browser** in the gear icon — free, always
  works.
- Make sure you clicked once on the page first (Chrome's autoplay rule).

**"Microphone permission denied"**
- Chrome: click the lock icon in the address bar → Site Settings → allow
  microphone for `localhost`. Reload.

**"Hey Jarvis" doesn't trigger**
- Click anywhere on the page once first — Chrome blocks mic until a user
  gesture.
- The bottom-right HUD should say "Say 'Hey Jarvis' or tap SPACE" when armed.
- Wake word only works in Chrome / Edge.

**Port already in use**
- Windows: `stop.bat`. If still stuck, find PID with `netstat -ano | findstr
  :8010` and `taskkill /F /PID <pid>`.
- macOS/Linux: `./stop.sh`. If still stuck, `lsof -i :8010` then `kill <pid>`.

**Playwright errors on first browser tool call**
- Re-run `install.ps1` / `install.sh` to refresh Playwright's Chromium download.

---

## Updating

If you get a new copy of the project:

1. `stop.bat` / `./stop.sh`
2. Replace the project folder (overwrite, or extract a new copy)
3. Run `install.ps1` / `install.sh` again — idempotent, skips what's already done.
4. `start.bat` / `./start.sh`

Your `config.json`, `state.json`, `agents.json`, and `brain_state/` are
preserved as long as you don't delete them.

---

## Architecture (one paragraph)

Three local services, all bound to 127.0.0.1:

- `:8010` — **Ops Center dashboard** (the page you load). Embeds the Jarvis
  and Brain UIs as iframes. Custom-agent CRUD, calendar/inbox/agent state.
- `:8020` — **NeuroLinked Brain**. Persistent memory + 3D neural viz. Runs a
  small "neuromorphic" simulation that visualizes what cortex regions Jarvis
  fires while he works.
- `:8340` — **Jarvis**. The voice/text assistant. Talks to your LLM provider,
  has tools for: shell, file I/O, browser control (Playwright), screen +
  webcam vision, computer control (mouse/keyboard), Claude Code CLI.

When Jarvis fires a region during a tool call, the dashboard relays a
`postMessage` to the Brain iframe so both viewports light up the same color
in sync.
