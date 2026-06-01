# ops-center-v1

A local-first ops dashboard with a built-in agent runner and (optional) Jarvis
voice/text assistant. Ships as the v1 of the NeuroLinked Ops Center —
a snapshot from active production use, scrubbed for distribution.

> **Status:** community release. Provided as-is, no support.
> The original maintainer has moved to a successor product (the successor product).
> Use this freely; modify it; ship it; build on it.

## What it does

- Single-page dashboard at `http://localhost:8010` — calendar, docs, an
  inbox, and a custom-agent runner.
- Agent system: defined in `custom_agents.json`. Each agent is a list of
  steps (`brain_search`, `reason`, `draft_email`, `notify`, `create_task`,
  `summarize`, `call_api`) executed in order via `/api/agent/run`.
- Optional Jarvis sub-app under `_jarvis/` — voice/text assistant on its
  own port (default 8340) with multi-LLM support, browser tools, and a
  GoHighLevel CRM integration.
- All services bind **127.0.0.1 only**. No LAN exposure. Host-header
  rebinding defense is built in.

## Requirements

- Python 3.11+
- That's it. No Node.js, no Docker, no build step.

## Install

```bash
# 1. Drop the folder anywhere on your machine.

# 2. (Optional) Create a venv.
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install Jarvis deps if you want voice / multi-LLM / browser tools.
#    The core dashboard (server.py at the root) uses only stdlib.
pip install -r _jarvis/requirements.txt

# 4. (Optional) Configure your keys.
cp .env.example .env
# Edit .env and fill in any keys you have.
# OR for Jarvis: cp _jarvis/config.example.json _jarvis/config.json
# and edit that.

# 5. Run.
python server.py
# Open http://localhost:8010
```

## Adding agents

Edit `custom_agents.json`. Each top-level key is an agent ID. Schema:

```json
{
  "my_agent_id": {
    "id": "my_agent_id",
    "name": "Human-readable name",
    "description": "What it does.",
    "steps": [
      { "type": "brain_search", "inputs": { "query": "..." } },
      { "type": "reason",       "inputs": { "prompt": "..." } },
      { "type": "draft_email",  "inputs": { "to": "...", "subject": "...", "notes": "..." } },
      { "type": "notify",       "inputs": { "channel": "slack", "message": "..." } }
    ],
    "created_at": "2026-01-01T00:00:00",
    "enabled": true
  }
}
```

Save the file, refresh the dashboard, hit Run. (Restart the server if
you don't see your new agent.)

## Running Jarvis (optional)

Jarvis is the voice/text assistant in `_jarvis/`. To start it standalone:

```bash
cd _jarvis
python server.py
# Listens on http://127.0.0.1:8340
```

The ops-center dashboard will detect it automatically and surface a
chat panel.

## Security

- **127.0.0.1 only.** All servers refuse non-loopback hosts. Don't change
  this unless you know what you're doing.
- **Host-header guard.** Requests with a non-loopback `Host:` header are
  rejected — defense against DNS rebinding.
- **No telemetry.** This release sends nothing to anyone. The only
  outbound traffic is to the LLM/CRM/TTS providers you configure, and
  only when an agent run requires them.

## License

MIT. See `LICENSE`.

## Known limitations

- Single user, single machine. No multi-tenant / shared deployment story.
- Agents are defined in JSON. The successor product has a per-agent
  markdown format with a visual builder; this release does not.
- Jarvis's brain memory (personal notes, tasks, sessions) was stripped
  before distribution — it boots empty.
- The `frontend.backup-*` and `__pycache__` directories were excluded
  from this release.

## Heritage

This was the v1 ops surface for an AI-automation agency. It worked.
We outgrew it. Take it, run it, fork it, sell it — whatever you want.
