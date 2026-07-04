"""Phase 1: inspect, split on cuts, crop, normalize fps, extract stride frames.

One ffmpeg encode produces out/shot_clean.mp4 (constant fps, cropped), then a second
pass extracts every STRIDE-th frame. Extracted file i (1-based frame_%06d) is video
frame n = (i-1)*stride - exact by construction, never by directory-listing zip.
"""
import glob
import json
import os
import shutil
from pathlib import Path

from .common import log, die, ffprobe_info, run_cmd


def _longest_shot(video):
    """Return (start_s, end_s) of the longest continuous shot, or None if uncut /
    scenedetect unavailable."""
    try:
        from scenedetect import detect, AdaptiveDetector
    except ImportError:
        log("ingest", "WARNING: scenedetect not installed - assuming one continuous shot")
        return None
    scenes = detect(str(video), AdaptiveDetector())
    if len(scenes) <= 1:
        return None
    start, end = max(scenes, key=lambda s: s[1].get_seconds() - s[0].get_seconds())
    log("ingest", f"{len(scenes)} shots detected; keeping longest "
                  f"{start.get_seconds():.1f}s - {end.get_seconds():.1f}s")
    return start.get_seconds(), end.get_seconds()


def ingest_video(cfg, clean_out, frames_dir):
    """video -> shot_clean.mp4 + frames/. Returns (video_fps, stride)."""
    ing = cfg["ingest"]
    src = Path(cfg["_root"], cfg["input"])
    if not src.exists():
        die(f"input video not found: {src}\nPut your clip there or edit 'input' in config.yaml")

    info = ffprobe_info(src)
    if abs(info["fps"] - info["avg_fps"]) > 0.1:
        log("ingest", f"WARNING: variable frame rate detected "
                      f"(r={info['fps']:.2f} avg={info['avg_fps']:.2f}) - normalizing")
    video_fps = int(round(info["fps"]))
    log("ingest", f"{src.name}: {info['width']}x{info['height']} @ {info['fps']:.3f} fps, "
                  f"{info['duration']:.1f}s -> normalizing to {video_fps} fps")

    # --- one encode: optional trim to longest shot + crops + constant fps ---
    trim = _longest_shot(src) if ing.get("split_on_cuts") else None
    filters = []
    if ing.get("crop"):
        filters.append(f"crop={ing['crop']}")
    bc = int(ing.get("border_crop_pct") or 0)
    if bc > 0:
        k = (100 - 2 * bc) / 100.0  # lens distortion is worst at the edges (Phase 1.3b)
        filters.append(f"crop=floor(iw*{k}/2)*2:floor(ih*{k}/2)*2")
    filters.append(f"fps={video_fps}")

    args = ["ffmpeg", "-y", "-i", src]
    if trim:
        args += ["-ss", f"{trim[0]:.3f}", "-to", f"{trim[1]:.3f}"]
    args += ["-vf", ",".join(filters),
             "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-an", clean_out]
    run_cmd(args, "ingest")

    # --- extract every STRIDE-th frame, keyed to video frame numbers (Part 2.8) ---
    stride = int(ing["stride"])
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir)  # stale frames from an older stride would silently mis-key
    os.makedirs(frames_dir)
    run_cmd(["ffmpeg", "-y", "-i", clean_out,
             "-vf", f"select=not(mod(n\\,{stride}))", "-fps_mode", "vfr",
             "-q:v", "2", str(Path(frames_dir) / "frame_%06d.jpg")], "ingest")

    n = len(glob.glob(str(Path(frames_dir) / "frame_*.jpg")))
    if n < 10:
        die(f"only {n} frames extracted - clip too short or extraction failed")
    log("ingest", f"extracted {n} registration frames (stride {stride}, "
                  f"~{video_fps / stride:.1f} fps)")

    # marker so a later run can't silently reuse frames extracted with a different stride
    with open(Path(clean_out).parent / "ingest_meta.json", "w", encoding="utf-8") as f:
        json.dump({"video_fps": float(video_fps), "stride": stride}, f)
    return float(video_fps), stride
