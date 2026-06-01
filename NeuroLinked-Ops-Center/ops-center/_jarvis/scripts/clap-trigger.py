#!/usr/bin/env python3
"""
Jarvis — Double Clap Trigger (persistent)

Listens to the mic forever. Detects two claps within 1.2s of each other
(min 0.1s apart). On trigger, fires scripts/launch-jarvis.ps1 and then
goes back to listening — so you can clap to open Jarvis any time,
not just once per session.

Run directly:
    python scripts/clap-trigger.py

Auto-start at Windows login (Task Scheduler):
    Trigger: At log on of <user>
    Action:  powershell.exe
    Args:    -ExecutionPolicy Bypass -WindowStyle Hidden
             -Command "python 'C:\\...\\jarvis\\scripts\\clap-trigger.py'"
"""

import sounddevice as sd
import numpy as np
import subprocess
import time
import os
import json

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

WORKSPACE_PATH = config["workspace_path"]
SCRIPT_PATH = os.path.join(WORKSPACE_PATH, "scripts", "launch-jarvis.ps1")

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
THRESHOLD = 0.15       # RMS volume spike threshold — lower = more sensitive
MIN_GAP = 0.1          # Minimum seconds between claps
MAX_GAP = 1.2          # Maximum seconds between claps
COOLDOWN = 5.0         # Seconds to ignore after trigger fires (prevents re-triggering on launch sound)

last_clap_time = 0.0
last_trigger_time = 0.0


def fire_launch():
    """Run the launcher script detached. Returns immediately."""
    global last_trigger_time
    last_trigger_time = time.time()
    try:
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", SCRIPT_PATH],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        print("[jarvis] Double clap detected — launching Jarvis.", flush=True)
    except Exception as e:
        print(f"[jarvis] Launch failed: {e}", flush=True)


def audio_callback(indata, frames, time_info, status):
    global last_clap_time, last_trigger_time

    now = time.time()
    # Cooldown — ignore claps right after a trigger (the app startup sounds etc.)
    if now - last_trigger_time < COOLDOWN:
        return

    rms = float(np.sqrt(np.mean(indata ** 2)))
    if rms < THRESHOLD:
        return

    gap = now - last_clap_time
    if gap < MIN_GAP:
        return

    if gap <= MAX_GAP and last_clap_time > 0:
        # Second clap in time window — FIRE
        last_clap_time = 0.0
        fire_launch()
    else:
        # First clap — arm for a second
        print(f"[jarvis] Clap detected (rms={rms:.3f}), waiting for second...", flush=True)
        last_clap_time = now


def main():
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype="float32",
        callback=audio_callback,
    ):
        print("[jarvis] Clap-trigger running. Double-clap anytime to launch Jarvis.", flush=True)
        print("[jarvis] (Ctrl+C to stop)", flush=True)
        while True:
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[jarvis] Clap-trigger stopped.")
