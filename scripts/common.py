"""Shared helpers: config, project paths, ffprobe, logging, the frame-index convention."""
import json
import subprocess
import sys
import time
from pathlib import Path


def log(stage, msg):
    # ASCII-only output: Windows consoles on cp1252 choke on fancy characters
    print(f"[{time.strftime('%H:%M:%S')}] [{stage}] {msg}", flush=True)


def die(msg, code=1):
    print(f"FATAL: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def load_config(path):
    import yaml  # lazy: lets selfcheck run with only numpy+opencv installed
    path = Path(path).resolve()
    if not path.exists():
        die(f"config not found: {path}")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = str(path.parent)
    return cfg


def p(cfg, *parts):
    """Project-rooted path (everything is relative to the config file's directory)."""
    return str(Path(cfg["_root"], *parts))


def mask_path(masks_dir, n):
    """The one naming convention: masks are keyed by VIDEO frame number n (Part 2.8)."""
    return str(Path(masks_dir) / f"frame_{n:06d}.png")


def video_frame_of(extracted_index, stride):
    """Extracted frame i (0-based, sorted order) IS video frame i*stride - by construction."""
    return extracted_index * stride


def run_cmd(args, stage="cmd"):
    args = [str(a) for a in args]
    log(stage, " ".join(args))
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"command failed ({args[0]}):\n{r.stderr[-2000:]}")
    return r


def ffprobe_info(video):
    r = subprocess.run(
        ["ffprobe", "-v", "0", "-of", "json", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,avg_frame_rate,width,height,duration",
         str(video)],
        capture_output=True, text=True)
    if r.returncode != 0:
        die("ffprobe failed - is ffmpeg installed and on PATH?\n" + r.stderr[-500:])
    streams = json.loads(r.stdout).get("streams") or []
    if not streams:
        die(f"no video stream found in {video}")
    s = streams[0]

    def fps_of(rate):
        num, _, den = str(rate).partition("/")
        return float(num) / float(den or 1)

    return {
        "fps": fps_of(s["r_frame_rate"]),
        "avg_fps": fps_of(s.get("avg_frame_rate") or s["r_frame_rate"]),
        "width": int(s["width"]),
        "height": int(s["height"]),
        "duration": float(s.get("duration") or 0),
    }
