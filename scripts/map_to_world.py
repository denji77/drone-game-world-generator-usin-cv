"""Phase 5: image tracks -> world tracks via time-INTERPOLATED homographies,
split on occlusion gaps, smooth per segment, stitch with a max-speed gate.

Nearest-neighbor H lookup gives every detection in a stride-window the same H, then
jumps: tracks stair-step, and no smoothing window can hide it. h_at() interpolates.
"""
import json

import cv2
import numpy as np

from .common import log


def h_at(n, stride, H_stack):
    """Homography for VIDEO frame n, interpolated between registration samples."""
    f = n / stride
    i0 = min(int(f), len(H_stack) - 1)
    i1 = min(i0 + 1, len(H_stack) - 1)
    a = f - i0
    H0 = H_stack[i0] / H_stack[i0][2, 2]
    H1 = H_stack[i1] / H_stack[i1][2, 2]
    return (1 - a) * H0 + a * H1  # adjacent H's are close -> normalized lerp is accurate


def smooth(seq, k):
    if len(seq) < k:
        return list(seq)
    half, out = k // 2, []
    for i in range(len(seq)):
        lo, hi = max(0, i - half), min(len(seq), i + half + 1)
        out.append(sum(seq[lo:hi]) / (hi - lo))
    return out


def split_on_gaps(pts, max_dt):
    """Smoothing must NOT blend across occlusion gaps (it would invent motion)."""
    segs, cur = [], [pts[0]]
    for q in pts[1:]:
        if q["t"] - cur[-1]["t"] <= max_dt:
            cur.append(q)
        else:
            segs.append(cur)
            cur = [q]
    segs.append(cur)
    return segs


def stitch_segments(segments, max_speed, max_gap_s):
    """Rejoin (class, pts) segments whose gap passes the max-speed gate in METERS."""
    segments = sorted(segments, key=lambda s: s[1][0]["t"])
    out = []
    for cls, seg in segments:
        prev = next((s for s in reversed(out) if s[0] == cls
                     and 0 < seg[0]["t"] - s[1][-1]["t"] <= max_gap_s
                     and np.hypot(seg[0]["x"] - s[1][-1]["x"], seg[0]["z"] - s[1][-1]["z"])
                     <= max_speed[cls] * (seg[0]["t"] - s[1][-1]["t"])), None)
        if prev:
            prev[1].extend(seg)
        else:
            out.append((cls, seg))
    return out


def map_tracks(tracks_json, H_stack, stride, cfg, out_json):
    mc = cfg["map"]
    data = json.load(open(tracks_json, encoding="utf-8"))
    max_speed = {k: float(v) for k, v in mc["max_speed"].items()}

    segments = []
    for tr in data["tracks"]:
        world = []
        for q in tr["pts"]:
            XZ = cv2.perspectiveTransform(np.float32([[[q["u"], q["v"]]]]),
                                          h_at(q["n"], stride, H_stack))[0][0]
            world.append({"t": q["t"], "x": float(XZ[0]), "z": float(XZ[1])})
        for seg in split_on_gaps(world, float(mc["max_gap_s"])):
            xs = smooth([q["x"] for q in seg], int(mc["smooth_window"]))
            zs = smooth([q["z"] for q in seg], int(mc["smooth_window"]))
            segments.append((tr["class"],
                             [{"t": q["t"], "x": round(x, 3), "z": round(z, 3)}
                              for q, x, z in zip(seg, xs, zs)]))

    if mc.get("stitch_gaps"):
        before = len(segments)
        segments = stitch_segments(segments, max_speed, float(mc["max_stitch_gap_s"]))
        log("map", f"stitched {before} segments -> {len(segments)} tracks")

    tracks = [{"id": i, "class": cls, "pts": seg} for i, (cls, seg) in enumerate(segments)]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"video_fps": data["video_fps"], "tracks": tracks}, f)
    log("map", f"wrote {len(tracks)} world tracks -> {out_json}")
    return len(tracks)
