# NeuroLinked Ops Center

A complete local-first AI assistant stack — **Jarvis** (voice/text AI), the
**NeuroLinked Brain** (a persistent neuromorphic memory + 3D dashboard), and an
**Ops Center** dashboard with a built-in agent runner. All three run on your
machine. Nothing leaves your computer except the API calls you choose to make.

This is the **legacy v1** snapshot — what powered the maintainer's internal ops
before we moved to a newer system. It works. It's yours.

> Built and released for the community. Use it. Modify it. Ship it. No support
> provided — read the troubleshooting section first.

---

## What you get

| Service | Port | What it is |
|---|---|---|
| **Ops Center** | `8010` | The dashboard you open every day. Calendar, docs, inbox, and an agent runner. Open at `http://localhost:8010`. |
| **NeuroLinked Brain** | `8020` | A persistent memory system styled as a 3D brain visualization. Jarvis pushes every conversation through here. You can talk to it directly. Open at `http://localhost:8020`. |
| **Jarvis** | `8340` | The voice/text assistant. Wake word "Hey Jarvis." Has 60+ built-in tools — opening apps, web search, file management, GHL CRM, Spotify, vision, etc. Open at `http://localhost:8340`. |

Each is a standalone Python service. They talk to each other over localhost.
No Docker, no Node, no cloud account required.

---

## 60-second quick start (Windows)

1. **Install Python 3.11 or newer.**
   Get it from https://www.python.org/downloads/.
   **CRITICAL:** check the box that says "Add Python to PATH" during install.

2. **Double-click `START.bat`** in this folder.
   First run takes ~60 seconds while it installs Python packages. After that,
   future starts take ~15 seconds.

3. **Your browser opens to `http://localhost:8010`.** That's the Ops Center.

4. **Add an API key (one-time).**
   Click the gear icon (bottom-right) → paste an Anthropic, OpenAI, or Groq
   API key → Save.
   - Anthropic: https://console.anthropic.com — get an `sk-ant-...` key
   - OpenAI: https://platform.openai.com/api-keys — get an `sk-...` key
   - Groq (free tier exists): https://console.groq.com/keys — get a `gsk_...` key
   - **Or:** install Ollama (https://ollama.ai) for free local LLM, no key needed.

5. **Test it.** Hit the agent runner section → click `Run` on the
   pre-installed `Finance Watchdog` agent. It'll exercise the brain + LLM and
   you'll see it complete in a few seconds.

That's it. You now have a fully working local AI ops stack.

---

## Quick start (macOS / Linux)

```bash
# Open Terminal, cd into this folder
cd ~/Desktop/NeuroLinked-Ops-Center   # or wherever you put it

# Install Python deps (one-time)
python3 -m pip install --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq psutil cryptography pillow numpy python-multipart

# Start the three services in three terminal tabs:
(cd neurolinked-brain && python3 run.py --port 8020 --host 127.0.0.1)
(cd ops-center/_jarvis && python3 server.py)
(cd ops-center && python3 server.py)

# Open http://localhost:8010
```

(Or modify `START.bat` into a `.sh` script — same idea.)

---

## What each service does

### Ops Center (`localhost:8010`)
The daily dashboard. Single page, no login. Features:

- **Calendar** — drop events, see what's coming up
- **Docs** — quick-access doc list, paste links to your stuff
- **Inbox** — a unified surface for Slack/email-style notifications
- **Agent Runner** — runs the agents defined in `ops-center/custom_agents.json`
- **Settings (gear icon)** — API keys, Jarvis config, theme

### NeuroLinked Brain (`localhost:8020`)
A neuromorphic memory store visualized as a 3D brain. 10 brain regions,
each with hundreds of "neurons" — when you add a note or have a
conversation, neurons fire and connections strengthen. Over time the brain
"learns" what topics matter to you.

- **3D dashboard** — see the brain thinking in real-time
- **Notes** — your second-brain notes, searchable
- **Tasks** — to-do tracking that integrates with Jarvis
- **Conversations** — Jarvis pushes every chat through here so the brain
  accumulates context. Ask it weeks later "what did we decide about X?"

### Jarvis (`localhost:8340`)
A voice/text assistant inspired by Tony Stark's J.A.R.V.I.S. Listens for
"Hey Jarvis" through your microphone (you'll need to grant Chrome mic
permission the first time). Or type into the input box.

Built-in tools (no extra setup):
- **Brain** — search, remember, recall (talks to the NeuroLinked Brain above)
- **Tasks** — add, list, complete tasks
- **Web** — search via DuckDuckGo, fetch pages, summarize
- **Files** — read, write, list files in a workspace folder
- **Apps** — open desktop apps (Chrome, VS Code, Spotify, etc.) by name
- **Vision** — `look at this screen` (takes a screenshot, sends to vision-capable LLM)
- **Shell** — run shell commands (scoped to a workspace, or system-wide if enabled)
- **Spotify** — control playback if you provide Spotify credentials
- **GHL (GoHighLevel CRM)** — fetch contacts, send messages, trigger workflows if you have a GHL account

Open `localhost:8340` directly, or use the chat panel that appears in
the Ops Center.

---

## Adding your own agents

Agents are defined in `ops-center/custom_agents.json`. The file ships with
two examples (`Finance Watchdog`, `Meeting Prep Pro`). Add a new one:

```json
{
  "my_lead_qualifier": {
    "id": "my_lead_qualifier",
    "name": "Lead Qualifier",
    "description": "Reads new leads, scores them, drafts a reply.",
    "steps": [
      { "type": "brain_search", "inputs": { "query": "new leads this week" } },
      { "type": "reason",       "inputs": { "prompt": "Score each lead 1-10 on fit. Brief why." } },
      { "type": "draft_email",  "inputs": {
          "to": "leads@yourcompany.com",
          "subject": "Lead scores — this week",
          "notes": "Use the scoring output from the prior step."
      }},
      { "type": "notify", "inputs": { "channel": "slack", "message": "Lead scores ready." } }
    ],
    "created_at": "2026-01-01T00:00:00",
    "enabled": true
  }
}
```

Save → refresh the Ops Center → your new agent appears. Hit `Run`.

**Available step types:** `brain_search`, `reason`, `draft_email`, `notify`,
`create_task`, `summarize`, `call_api`.

---

## Optional integrations

You can plug in any of these. Open the gear icon in the Ops Center, paste
your key, save.

| Integration | What it unlocks | Where to get a key |
|---|---|---|
| Anthropic | Best general LLM (Claude) | https://console.anthropic.com |
| OpenAI | GPT-4o, DALL·E images, Whisper | https://platform.openai.com |
| Groq | Free tier, ultra-fast LLM | https://console.groq.com |
| ElevenLabs | Premium voice (Jarvis speaks better) | https://elevenlabs.io |
| GoHighLevel | CRM, contacts, SMS, workflows | Your GHL sub-account → Settings → Private Integration Token |
| Spotify | Music control via Jarvis | https://developer.spotify.com |
| Slack | Notifications channel | Workspace settings → Apps → Incoming Webhooks |

**None are required.** With just one LLM key (or Ollama running locally), the
whole stack works.

---

## Security & privacy

- **All three services bind to `127.0.0.1` only.** Nobody on your network or
  the internet can reach them. Don't change this unless you know exactly
  what you're doing.
- **Host-header guard** built in — blocks DNS-rebinding attacks even on
  the local machine.
- **Zero telemetry** — no analytics, no phone-home, no usage tracking.
  The only outbound network traffic is the API calls YOU configure
  (e.g., when Jarvis calls Anthropic, or pulls a GHL contact).
- **Your data stays on your machine.** Brain memory lives in
  `neurolinked-brain/brain_state/` — pure local JSON files. Jarvis chat
  history lives in `ops-center/_jarvis/sessions/` — same deal.

---

## Troubleshooting

**"Python is not installed or not in PATH"**
Re-install Python from python.org and **make sure to check the
"Add Python to PATH" box** during install. Then try `START.bat` again.

**One of the services fails to start**
Open the minimized terminal window for that service (Brain / Jarvis /
OpsCenter on your taskbar). Read the error. Most common causes:
- Port already in use → run `STOP.bat`, wait 5 seconds, then `START.bat` again
- Missing Python package → manually run:
  `python -m pip install --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq cryptography`

**Jarvis can't hear me**
Open `localhost:8340` directly in Chrome. Chrome will prompt for mic
permission — click Allow. If you previously denied it, click the
camera/mic icon in the address bar → reset permissions.

**"I added an API key but nothing happens"**
- Refresh the dashboard
- Check the terminal window for the relevant service — errors print there
- Make sure your key is valid by testing it on the provider's playground

**The brain dashboard at `localhost:8020` is empty**
That's normal on first boot. The brain learns as you use it. Run a few
agents, have a few Jarvis conversations, then check back in a day.

**I want to wipe it and start over**
Delete `neurolinked-brain/brain_state/` and `ops-center/_jarvis/sessions/`.
Next start it'll initialize fresh.

---

## What this ISN'T

- **Not a successor product.** the maintainer built a successor product with
  a visual agent builder, scheduled jobs, real-time live feed, role-based
  managers, and a polished UI. This is the predecessor — a simpler v1 that
  predates all that. It works for personal use and small ops; it's not
  battle-tested for teams.
- **Not multi-user.** One person, one machine. No login, no permissions.
- **Not supported.** Forks welcome. Pull requests welcome (if there's a
  public repo). Bug reports — figure it out, fix it, share the fix.

---

## License

MIT. Do whatever you want with it.

---

## Heritage

This is what we used at the maintainer in 2025-2026 before we built the
successor. It powered our actual daily ops — meeting prep, finance
review, lead routing, content drafting. The pre-installed agents are
real ones we ran. The brain is the actual neuromorphic memory model.
The Jarvis is the actual voice assistant.

We outgrew it because we needed multi-user, scheduled jobs, and a
visual builder. **You probably don't need any of that.** This v1 is
plenty for a solopreneur or small team running their own ops.

Take it. Run it. Make it yours. If it helps you ship — that was the
whole point of releasing it.

— the maintainer
# jar
