"""
the successor product scheduler.

Reads the `schedule:` field on every loaded agent and registers a job per agent
with APScheduler. Job action = call `start_custom_agent_task(agent_id)`.

Schedule grammar (case-insensitive, parsed in `parse_schedule`):
    on-demand                  → not scheduled (default)
    daily                      → daily at 09:00 local
    daily 17:00                → daily at 17:00
    weekly                     → Mon 09:00
    weekly fri 16:00           → Fri 16:00
    hourly                     → top of every hour
    every 15 min               → every 15 minutes
    every 30 minutes           → every 30 minutes
    every N hours              → every N hours
    cron: 0 9 * * 1-5          → explicit cron expression

Persists last-run timestamps to state.json under `agent_schedules` so the UI
can show "last ran X ago" and compute drift.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    APS_OK = True
except Exception as _e:
    APS_OK = False
    BackgroundScheduler = None
    CronTrigger = None
    IntervalTrigger = None


_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_NAMES = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def parse_schedule(s: str):
    """Parse a schedule string into an APScheduler trigger or None.
    Returns (trigger, human_label) or (None, "on-demand"/"<error>")."""
    if not APS_OK:
        return None, "apscheduler not installed"
    if not s:
        return None, "on-demand"
    s = s.strip().lower()
    if s in ("on-demand", "manual", "off", "none", ""):
        return None, "on-demand"

    # cron: explicit
    m = re.match(r"^cron[:\s]+(.+)$", s)
    if m:
        try:
            return CronTrigger.from_crontab(m.group(1).strip()), f"cron({m.group(1).strip()})"
        except Exception as e:
            return None, f"bad cron: {e}"

    # every N (sec|secs|second|seconds|min|minute|minutes|hour|hours)
    m = re.match(r"^every\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("sec"):
            # Floor seconds at 15 so we don't hammer Discord / Anthropic APIs
            n = max(15, n)
            return IntervalTrigger(seconds=n), f"every {n} sec"
        if unit.startswith("min"):
            return IntervalTrigger(minutes=n), f"every {n} min"
        return IntervalTrigger(hours=n), f"every {n} hr"

    # hourly
    if s == "hourly":
        return CronTrigger(minute=0), "hourly"

    # daily [HH:MM]
    m = re.match(r"^daily(?:\s+(\d{1,2}):(\d{2}))?$", s)
    if m:
        h = int(m.group(1)) if m.group(1) else 9
        mn = int(m.group(2)) if m.group(2) else 0
        return CronTrigger(hour=h, minute=mn), f"daily {h:02d}:{mn:02d}"

    # weekly [day] [HH:MM]
    m = re.match(r"^weekly(?:\s+([a-z]{3}))?(?:\s+(\d{1,2}):(\d{2}))?$", s)
    if m:
        d = _DAYS.get(m.group(1) or "mon", 0)
        h = int(m.group(2)) if m.group(2) else 9
        mn = int(m.group(3)) if m.group(3) else 0
        return CronTrigger(day_of_week=d, hour=h, minute=mn), f"weekly {_DAY_NAMES[d]} {h:02d}:{mn:02d}"

    return None, f"unrecognized schedule: {s!r}"


class AgentScheduler:
    """Wraps APScheduler with our agent registry. Thread-safe; one instance per server."""

    def __init__(self, *, custom_agents: dict, start_fn, state_path: Path):
        self.agents = custom_agents          # reference to live CUSTOM_AGENTS dict
        self.start_fn = start_fn             # callable: start_fn(agent_id) -> task_id
        self.state_path = Path(state_path)
        self._sched = None
        self._registered: dict[str, str] = {}  # agent_id -> human label
        self._lock = threading.Lock()

    def start(self):
        if not APS_OK:
            print("[scheduler] APScheduler unavailable; pip install apscheduler. Skipping.", flush=True)
            return
        if self._sched is not None:
            return
        self._sched = BackgroundScheduler(daemon=True)
        self._sched.start()
        self.refresh()
        print(f"[scheduler] running with {len(self._registered)} jobs", flush=True)

    def shutdown(self):
        if self._sched:
            try:
                self._sched.shutdown(wait=False)
            except Exception:
                pass
            self._sched = None

    def refresh(self):
        """Re-scan CUSTOM_AGENTS and reconcile registered jobs."""
        if not self._sched:
            return
        with self._lock:
            wanted: dict[str, str] = {}
            for aid, agent in list(self.agents.items()):
                if not agent.get("enabled", True):
                    continue
                trigger, label = parse_schedule(agent.get("schedule"))
                if trigger is None:
                    continue
                wanted[aid] = label
                # (Re-)register
                try:
                    self._sched.add_job(
                        self._fire_agent, trigger=trigger,
                        id=f"agent::{aid}", args=[aid], replace_existing=True,
                        max_instances=1, coalesce=True, misfire_grace_time=60,
                    )
                except Exception as e:
                    print(f"[scheduler] failed to register {aid}: {e}", flush=True)
            # Remove jobs for agents no longer wanted
            for aid in list(self._registered.keys()):
                if aid not in wanted:
                    try:
                        self._sched.remove_job(f"agent::{aid}")
                    except Exception:
                        pass
            self._registered = wanted

    def _fire_agent(self, agent_id: str):
        try:
            tid = self.start_fn(agent_id)
            self._record_run(agent_id, tid, ok=True)
        except Exception as e:
            self._record_run(agent_id, None, ok=False, err=str(e))
            traceback.print_exc()

    def _record_run(self, agent_id: str, task_id, ok: bool, err: str = ""):
        try:
            state = {}
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            agg = state.setdefault("agent_schedules", {})
            agg[agent_id] = {
                "last_fired_at": datetime.now().isoformat(timespec="seconds"),
                "last_task_id": task_id,
                "last_ok": ok,
                "last_error": err[:200] if err else None,
            }
            self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------- public introspection (for /api/schedules) -------
    def list_jobs(self) -> list:
        out = []
        if not self._sched:
            return out
        for aid, label in self._registered.items():
            agent = self.agents.get(aid) or {}
            try:
                job = self._sched.get_job(f"agent::{aid}")
                next_fire = job.next_run_time.isoformat() if job and job.next_run_time else None
            except Exception:
                next_fire = None
            last = self._read_last(aid)
            out.append({
                "agent_id": aid,
                "name": agent.get("name", aid),
                "section": agent.get("section"),
                "role": agent.get("role", "worker"),
                "schedule": agent.get("schedule"),
                "schedule_label": label,
                "enabled": bool(agent.get("enabled", True)),
                "next_fire_at": next_fire,
                "last_fired_at": last.get("last_fired_at"),
                "last_ok": last.get("last_ok"),
                "last_error": last.get("last_error"),
                "last_task_id": last.get("last_task_id"),
            })
        return sorted(out, key=lambda j: j["next_fire_at"] or "")

    def _read_last(self, agent_id: str) -> dict:
        try:
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                return (state.get("agent_schedules") or {}).get(agent_id) or {}
        except Exception:
            pass
        return {}

    def pause(self, agent_id: str) -> bool:
        if not self._sched:
            return False
        try:
            self._sched.pause_job(f"agent::{agent_id}")
            return True
        except Exception:
            return False

    def resume(self, agent_id: str) -> bool:
        if not self._sched:
            return False
        try:
            self._sched.resume_job(f"agent::{agent_id}")
            return True
        except Exception:
            return False

    def fire_now(self, agent_id: str) -> str | None:
        """Manually fire an agent through the scheduler path (useful for the UI run-now button)."""
        try:
            return self.start_fn(agent_id)
        except Exception:
            return None
