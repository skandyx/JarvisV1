"""ffmpeg / ffprobe wrapper for video clipping, cleaning, captioning, concat.

Looks for ffmpeg in this order:
    1. NeuroLinked/bin/ffmpeg.exe   (bundled static build — preferred)
    2. PATH ffmpeg / ffmpeg.exe
    3. Common Windows install locations

Public API:
    have_ffmpeg() -> bool
    probe(path) -> {duration, width, height, fps, audio_channels, ...}
    extract(src, dst, start, end, *, fade=False, normalize_audio=True) -> {ok, path?, error?}
    concat(srcs, dst) -> {ok, path?}
    burn_captions(src, srt_path, dst, *, style="tiktok") -> {ok, path?}
    clean(src, dst) -> {ok, path?}      # loudnorm + minor color correction
    write_srt(segments, dst) -> path    # segments = [{start,end,text}, ...]
    write_ass_animated_words(segments, dst) -> path  # word-by-word TikTok-style

All operations are blocking subprocess calls. Designed to be invoked from
inside _run_custom_step branches.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate ffmpeg / ffprobe
# ---------------------------------------------------------------------------
_NEUROLINKED_BIN = Path(__file__).resolve().parents[3] / "bin"  # …/NeuroLinked/bin/

def _resolve(binary: str) -> str | None:
    # 1. Bundled
    cand = _NEUROLINKED_BIN / (f"{binary}.exe" if os.name == "nt" else binary)
    if cand.is_file():
        return str(cand)
    # 2. PATH
    found = shutil.which(binary)
    if found:
        return found
    # 3. Common Windows locations
    if os.name == "nt":
        for p in (
            r"C:\Program Files\ffmpeg\bin",
            r"C:\ffmpeg\bin",
            os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links"),
        ):
            cand = Path(p) / f"{binary}.exe"
            if cand.is_file():
                return str(cand)
    return None

FFMPEG  = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")


def have_ffmpeg() -> bool:
    return FFMPEG is not None and FFPROBE is not None


def _run(args: list[str], *, timeout: int = 600) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            errors="replace",
        )
        return proc.returncode == 0, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return False, "", f"binary not found: {e}"


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
def probe(path: str | Path) -> dict:
    if not FFPROBE:
        return {"ok": False, "error": "ffprobe not available"}
    ok, out, err = _run([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=60)
    if not ok:
        return {"ok": False, "error": err.strip()[:300]}
    try:
        data = json.loads(out)
        streams = data.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})
        result: dict = {
            "ok": True,
            "duration": float(fmt.get("duration") or 0),
            "size_bytes": int(fmt.get("size") or 0),
            "format_name": fmt.get("format_name"),
        }
        if v:
            result.update({
                "width": v.get("width"),
                "height": v.get("height"),
                "fps": _parse_fps(v.get("r_frame_rate") or v.get("avg_frame_rate") or "0/1"),
                "video_codec": v.get("codec_name"),
            })
        if a:
            result.update({
                "audio_codec": a.get("codec_name"),
                "audio_channels": a.get("channels"),
                "audio_sample_rate": a.get("sample_rate"),
            })
        return result
    except Exception as e:
        return {"ok": False, "error": f"parse: {e}"}


def _parse_fps(r: str) -> float:
    try:
        n, d = r.split("/")
        return round(float(n) / float(d or 1), 2) if float(d) else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Extract a single segment (with optional fade + audio normalize)
# ---------------------------------------------------------------------------
def extract(src: str | Path, dst: str | Path, start: float, end: float,
            *, fade: bool = False, normalize_audio: bool = True,
            target_aspect: str | None = None) -> dict:
    """Cut src[start..end] into dst. If target_aspect is "9:16" or "1:1" or "16:9",
    crop+pad to that ratio."""
    if not FFMPEG:
        return {"ok": False, "error": "ffmpeg not available"}
    duration = max(0.1, float(end) - float(start))
    src, dst = str(src), str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)

    vfilters: list[str] = []
    if target_aspect:
        ratio_map = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
        w, h = ratio_map.get(target_aspect, (1080, 1920))
        # Crop center to ratio, then scale to canonical resolution
        vfilters.append(f"crop='if(gt(a,{w}/{h}),ih*{w}/{h},iw)':'if(gt(a,{w}/{h}),ih,iw*{h}/{w})'")
        vfilters.append(f"scale={w}:{h}")
    if fade:
        # 0.3s fade in/out on video and audio
        vfilters.append(f"fade=in:st=0:d=0.3,fade=out:st={duration-0.3:.3f}:d=0.3")

    afilters: list[str] = []
    if normalize_audio:
        afilters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if fade:
        afilters.append(f"afade=in:st=0:d=0.3,afade=out:st={duration-0.3:.3f}:d=0.3")

    args = [FFMPEG, "-y", "-ss", f"{float(start):.3f}", "-i", src,
            "-t", f"{duration:.3f}"]
    if vfilters:
        args += ["-vf", ",".join(vfilters)]
    if afilters:
        args += ["-af", ",".join(afilters)]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    ok, _out, err = _run(args, timeout=300)
    if not ok:
        return {"ok": False, "error": err.strip().splitlines()[-1][:300] if err else "ffmpeg failed"}
    return {"ok": True, "path": dst, "duration": duration}


# ---------------------------------------------------------------------------
# Clean (audio loudnorm only — fast, safe for an existing clip)
# ---------------------------------------------------------------------------
def clean(src: str | Path, dst: str | Path,
          *, normalize_audio: bool = True, color_pop: bool = False) -> dict:
    if not FFMPEG:
        return {"ok": False, "error": "ffmpeg not available"}
    src, dst = str(src), str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    vfilters: list[str] = []
    if color_pop:
        # Mild saturation + contrast bump for social
        vfilters.append("eq=saturation=1.10:contrast=1.05:brightness=0.02")
    afilters: list[str] = []
    if normalize_audio:
        afilters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    args = [FFMPEG, "-y", "-i", src]
    if vfilters: args += ["-vf", ",".join(vfilters)]
    if afilters: args += ["-af", ",".join(afilters)]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    ok, _out, err = _run(args, timeout=600)
    if not ok:
        return {"ok": False, "error": err.strip().splitlines()[-1][:300] if err else "ffmpeg failed"}
    return {"ok": True, "path": dst}


# ---------------------------------------------------------------------------
# Burn captions (SRT) into video — TikTok / classic styles
# ---------------------------------------------------------------------------
_CAPTION_STYLES = {
    # Big bold yellow centered, standard SRT path
    "tiktok": "FontName=Arial,FontSize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=120",
    # Smaller white standard
    "classic": "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=60",
    # IG-style white block, semi-transparent background
    "ig": "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=4,Outline=8,Shadow=0,Alignment=2,MarginV=100",
}

def burn_captions(src: str | Path, srt_path: str | Path, dst: str | Path,
                  *, style: str = "tiktok") -> dict:
    if not FFMPEG:
        return {"ok": False, "error": "ffmpeg not available"}
    style_str = _CAPTION_STYLES.get(style, _CAPTION_STYLES["tiktok"])
    src, dst = str(src), str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg subtitles filter requires forward slashes + escaped colons on Windows
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", r"\:")
    vf = f"subtitles='{srt_escaped}':force_style='{style_str}'"
    args = [FFMPEG, "-y", "-i", src, "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", dst]
    ok, _out, err = _run(args, timeout=600)
    if not ok:
        return {"ok": False, "error": err.strip().splitlines()[-1][:300] if err else "ffmpeg failed"}
    return {"ok": True, "path": dst}


# ---------------------------------------------------------------------------
# Concat multiple clips (must share codec — works after extract())
# ---------------------------------------------------------------------------
def concat(srcs: list[str | Path], dst: str | Path) -> dict:
    if not FFMPEG:
        return {"ok": False, "error": "ffmpeg not available"}
    if len(srcs) < 2:
        return {"ok": False, "error": "need at least 2 clips to concat"}
    dst = str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    list_path = Path(dst).with_suffix(".concat.txt")
    list_path.write_text(
        "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in srcs),
        encoding="utf-8",
    )
    args = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", dst]
    ok, _out, err = _run(args, timeout=600)
    try: list_path.unlink()
    except Exception: pass
    if not ok:
        return {"ok": False, "error": err.strip().splitlines()[-1][:300] if err else "ffmpeg concat failed"}
    return {"ok": True, "path": dst}


# ---------------------------------------------------------------------------
# Write SRT from segments
# ---------------------------------------------------------------------------
def _fmt_srt_ts(t: float) -> str:
    if t < 0: t = 0
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60);   t -= m * 60
    s = int(t);         ms = int(round((t - s) * 1000))
    if ms == 1000: ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_keyframes(src: str | Path, dst_dir: str | Path,
                      *, every_seconds: float = 5.0,
                      max_frames: int = 24,
                      width: int = 512) -> dict:
    """Sample one frame every N seconds, scaled to `width`px wide. For vision LLM input.
    Returns {ok, frame_paths: [...], count}."""
    if not FFMPEG:
        return {"ok": False, "error": "ffmpeg not available"}
    src = str(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    # Clear any prior frames so we don't mix runs
    for old in dst_dir.glob("frame_*.jpg"):
        try: old.unlink()
        except Exception: pass
    fps = max(0.05, 1.0 / float(every_seconds))
    pattern = str(dst_dir / "frame_%03d.jpg")
    args = [FFMPEG, "-y", "-i", src,
            "-vf", f"fps={fps},scale={int(width)}:-1:flags=lanczos",
            "-q:v", "5",
            "-frames:v", str(int(max_frames)),
            pattern]
    ok, _out, err = _run(args, timeout=180)
    if not ok:
        return {"ok": False, "error": err.strip().splitlines()[-1][:300] if err else "ffmpeg failed"}
    frames = sorted(dst_dir.glob("frame_*.jpg"))
    return {"ok": True, "frame_paths": [str(p) for p in frames], "count": len(frames)}


def write_srt(segments: list[dict], dst: str | Path) -> str:
    """segments = [{start: float, end: float, text: str}, ...]"""
    dst = str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        text = (seg.get("text") or "").strip().replace("\r", "").replace("-->", "→")
        if not text: continue
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(float(seg['start']))} --> {_fmt_srt_ts(float(seg['end']))}")
        lines.append(text)
        lines.append("")
    Path(dst).write_text("\n".join(lines), encoding="utf-8")
    return dst
