# NeuroLinked Ops Center — laptop setup

Everything you need to run this on a fresh Windows laptop. Should take about
15 minutes the first time.

---

## What's in the box

- `ops-center/` — the dashboard (port 8010) + Jarvis voice assistant (port 8340)
- `neurolinked-brain/` — the 3D brain visualization + memory graph (port 8020)
- `start.bat` / `stop.bat` — start/stop everything
- `install.ps1` — one-shot Windows installer

You will also need:

- **Python 3.12+** (free)
- **An Anthropic API key** (paid, gives Jarvis his brain) — already in `config.json` if you copied this folder
- **An ElevenLabs API key** (optional, premium voice) — already in `config.json` if you copied this folder
- **Ollama** (optional, free local LLM fallback if you ever lose internet)

---

## Step 1 — Install Python

1. Go to https://www.python.org/downloads/
2. Download the latest **Python 3.12** or **3.13** for Windows
3. **Important:** during install, check the box that says **"Add Python to PATH"**
4. Click "Install Now"

Verify:
```
python --version
```
Should print something like `Python 3.12.x`.

---

## Step 2 — Install everything else

In the project folder, right-click `install.ps1` → **Run with PowerShell**.

If Windows complains about execution policy, open a regular PowerShell window in
the project folder and run:
```
powershell -ExecutionPolicy Bypass -File install.ps1
```

The script will:

1. Verify Python is on PATH
2. Install all Python packages (fastapi, anthropic, playwright, etc.)
3. Download Playwright's Chromium (so Jarvis can browse the web)
4. Optionally pull the llama3.1:8b model if Ollama is installed
5. Sanity-check that `config.json` has your API keys

If you don't have Ollama and don't want it, skip the prompt — Jarvis works fine
with just an Anthropic key.

---

## Step 3 — Run

Double-click **`start.bat`** in the project folder.

Three minimized terminal windows will open (Brain, Jarvis, Ops Center). Your
browser will pop open at http://localhost:8010. The first launch takes about
6 seconds while uvicorn boots.

To stop everything: double-click **`stop.bat`**.

---

## Step 4 — First-run UI checklist

1. Click anywhere on the dashboard (Chrome requires a click before audio + mic
   work)
2. Open the **gear icon** in the Jarvis section, top-right corner
3. Verify (or paste in) your **Anthropic API key**
4. Optional: paste your **ElevenLabs key** + **voice ID** for premium voice.
   Without these, Jarvis uses the free browser voice (robotic but functional).
5. Save settings — Jarvis hot-reloads, no restart needed
6. Say **"Hey Jarvis"** or tap **SPACE** to talk

---

## Talking to Jarvis

Two ways to activate him:

| What you do | What happens |
|---|---|
| Say **"Hey Jarvis"** | Wake word; he listens for the next 10 seconds |
| Tap **SPACE** | Same thing — push-to-talk |
| Tap SPACE while he's speaking | Cuts him off so you can talk |

If he doesn't say anything for 10 seconds after wake, he goes quiet again.

---

## Troubleshooting

**"start.bat doesn't open browser / nothing happens"**
- Open Task Manager, end any `python.exe` processes
- Run `stop.bat` then `start.bat` again

**"Jarvis says 'I hit a snag on that one, sir'"**
- Means the LLM call failed. Usually one of:
  - Anthropic key is missing or wrong (gear → paste key → save)
  - Anthropic rate limit (wait a minute and retry)
- Logs are at `%TEMP%\jarvis-debug.log` and `%TEMP%\jarvis-debug.log.err`

**"I can't hear Jarvis"**
- Most common: ElevenLabs key has a per-key character cap that's exhausted
- Fix: go to https://elevenlabs.io/app/settings/api-keys → click your key →
  set monthly credit limit to **Unlimited**
- Or switch the voice mode to "Browser" in the gear icon (free, always works)

**"Microphone permission denied"**
- Chrome address bar → click the lock icon → site settings → allow microphone

**"Hey Jarvis" doesn't trigger**
- Make sure you clicked once on the page (Chrome requires a gesture before mic)
- The bottom-right HUD should say "Say 'Hey Jarvis' or tap SPACE"
- Recognition only works in **Google Chrome** or Edge — not Firefox or Safari

**Port already in use**
- Run `stop.bat`. If that doesn't help: `netstat -ano | findstr :8010` (or 8020,
  8340), then `taskkill /F /PID <pid>` for each one.

---

## Where things live

| File | What it is |
|---|---|
| `ops-center/_jarvis/config.json` | API keys, LLM provider, voice ID — also editable via gear icon |
| `ops-center/_jarvis/frontend/` | The Stark/Jarvis UI (HTML/CSS/JS) |
| `ops-center/state.json` | Calendar, slack inbox, knowledge stats — Jarvis can edit this himself |
| `ops-center/agents.json` | User-defined custom agents |
| `ops-center/custom_agents.json` | Agent templates |
| `neurolinked-brain/brain_state/` | The brain's persistent memory (notes, tasks, recall index) |

---

## Updating

If you get a new copy of the project from Google Drive:

1. Run `stop.bat` to kill the running services
2. Copy the new files over the old ones (overwrite)
3. Run `install.ps1` again (it'll skip anything already installed)
4. `start.bat`

Your `config.json`, `state.json`, `agents.json`, and `brain_state/` are safe to
keep — the install script doesn't touch them.

---

## Security notes

All three services bind to **127.0.0.1 only** — nobody on your local network or
the internet can reach them. The dashboard banner reads "All services bound to
127.0.0.1, LAN and external requests blocked at the OS socket level" — that's
real, verified at the socket layer.

Your API keys live in `config.json`. If you sync this folder to Google Drive,
keys go with it. If you want them out, blank them in `config.json` before
syncing and re-paste via the gear icon on each machine.
