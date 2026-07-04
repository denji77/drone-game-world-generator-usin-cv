"""Phase 3B step 1: reference selection + calibration (manual clicks or auto-fallback).

Manual calibration is BOUND to its exact reference frame (filename + sha1): clicked
pixels mean nothing on any other frame, and 'ref_frame: auto' re-picking a different
frame must never silently invalidate them.

Run interactively for metric scale:   python -m scripts.calibrate [config.yaml]
(click 4+ ground points on the reference frame, type each point's real X Z in meters,
press 's' to save, 'u' to undo, ESC to abort)
"""
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

from .common import log, die, load_config, p


def _sha1(path):
    return hashlib.sha1(open(path, "rb").read()).hexdigest()


def pick_reference(frames, mode):
    """'auto' = frame with most ORB features (sampled); else an explicit index."""
    if mode != "auto":
        return int(mode)
    orb = cv2.ORB_create(2000)
    idxs = np.unique(np.linspace(0, len(frames) - 1, min(len(frames), 40)).astype(int))
    best, best_n = len(frames) // 2, -1
    for i in idxs:
        g = cv2.imread(frames[i], 0)
        kp = orb.detect(g, None)
        if len(kp) > best_n:
            best, best_n = int(i), len(kp)
    log("calib", f"auto reference: frame index {best} ({best_n} ORB features)")
    return best


def resolve_reference(cfg, frames, work_dir):
    """Returns (ref_idx, binding_or_None). The binding overrides ref_frame:auto."""
    bpath = os.path.join(work_dir, "calibration.json")
    if os.path.exists(bpath):
        b = json.load(open(bpath, encoding="utf-8"))
        ref_path = next((f for f in frames if Path(f).name == b["ref_frame_file"]), None)
        if ref_path is None:
            die("calibration is bound to a frame that no longer exists - "
                "delete out/calibration.json or recalibrate")
        if _sha1(ref_path) != b["ref_frame_sha1"]:
            die("reference frame content changed (crop/stride/re-encode) - "
                "delete out/calibration.json and recalibrate")
        log("calib", f"using bound reference {b['ref_frame_file']} (metric calibration)")
        return frames.index(ref_path), b
    return pick_reference(frames, cfg["geometry"]["ref_frame"]), None


def estimate_ppm_from_cars(cfg, ref_path, default_ppm):
    """Scale from car sizes, orientation-filtered: an end-on car contributes its ~1.8 m
    WIDTH as 'length' and would skew the scale by up to 2.5x."""
    from ultralytics import YOLO
    from .track_agents import resolve_device
    det = cfg["detect"]
    res = YOLO(det["model"])(ref_path, imgsz=int(det["imgsz"]),
                             device=resolve_device(det.get("device", "auto")),
                             verbose=False)[0]
    dims = [(float(x2 - x1), float(y2 - y1))
            for x1, y1, x2, y2, conf, c in res.boxes.data.tolist() if int(c) == 2]  # COCO 2 = car
    lengths = [max(a, b) for a, b in dims if max(a, b) / max(1.0, min(a, b)) > 1.6]
    if len(lengths) < 5:
        log("calib", f"only {len(lengths)} usable cars in reference - staying unscaled")
        return default_ppm, "unscaled"
    spread = np.percentile(lengths, 75) / max(1.0, np.percentile(lengths, 25))
    ppm = float(np.median(lengths)) / 4.5  # median car length ~= 4.5 m
    status = "auto_scaled" if spread < 1.4 else "auto_scaled_lowconf"
    log("calib", f"car-size scale: {ppm:.1f} px/m from {len(lengths)} cars ({status})")
    return ppm, status


def calibrate_H(cfg, binding, ref_path):
    """Returns (H_ref_to_world pixels->meters, scale_status)."""
    if binding:
        pts = np.array(binding["points"], np.float32)
        H, _ = cv2.findHomography(pts[:, :2], pts[:, 2:])
        if H is None:
            die("calibration points are degenerate (collinear?) - recalibrate")
        return H, "metric"
    c = cfg["calibrate"]
    if c["auto"] == "car_size":
        ppm, status = estimate_ppm_from_cars(cfg, ref_path, float(c["ppm"]))
    else:
        ppm, status = float(c["ppm"]), "unscaled"
    img_h = cv2.imread(ref_path).shape[0]
    # v-down pixels mapped straight to +Z would build a MIRRORED world: negate v, shift so Z >= 0
    H = np.float32([[1 / ppm, 0, 0], [0, -1 / ppm, img_h / ppm], [0, 0, 1]])
    return H, status


# ---------------------------------------------------------------- interactive click tool
def _click_tool(cfg):
    frames = sorted(glob.glob(p(cfg, "frames", "frame_*.jpg")))
    if not frames:
        die("no frames/ yet - run the pipeline once first (python run.py)")
    wd = p(cfg, cfg["work_dir"])

    # prefer the reference the pipeline already used (report.json), else re-pick
    ref_idx = None
    rpath = os.path.join(wd, "report.json")
    if os.path.exists(rpath):
        ref_idx = json.load(open(rpath, encoding="utf-8")).get("ref_index")
    if ref_idx is None:
        ref_idx = pick_reference(frames, "auto")
    ref_path = frames[ref_idx]
    img = cv2.imread(ref_path)
    h, w = img.shape[:2]
    scale = min(1.0, 1600.0 / w)
    disp_base = cv2.resize(img, (int(w * scale), int(h * scale)))

    image_pts, world_pts = [], []
    clicked = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x / scale, y / scale))

    win = "calibrate - click ground points (s=save, u=undo, ESC=abort)"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)
    print("Click 4+ recognizable GROUND points; after each click type its real-world")
    print("X Z in meters (Unity axes: X=right/east, Z=forward/north), e.g.:  0 0")

    while True:
        disp = disp_base.copy()
        for k, (u, v) in enumerate(image_pts):
            c = (int(u * scale), int(v * scale))
            cv2.circle(disp, c, 6, (0, 0, 255), -1)
            cv2.putText(disp, f"{k}:({world_pts[k][0]:g},{world_pts[k][1]:g})",
                        (c[0] + 8, c[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow(win, disp)
        key = cv2.waitKey(30) & 0xFF

        if clicked:
            u, v = clicked.pop(0)
            try:
                raw = input(f"point {len(image_pts)} at pixel ({u:.0f},{v:.0f}) -> world X Z [m]: ")
                X, Z = map(float, raw.split())
            except (ValueError, EOFError):
                print("  bad input, point discarded")
                continue
            image_pts.append((u, v)); world_pts.append((X, Z))

        if key == ord("u") and image_pts:
            image_pts.pop(); world_pts.pop()
        elif key == ord("s"):
            if len(image_pts) < 4:
                print(f"  need at least 4 points, have {len(image_pts)}")
                continue
            break
        elif key == 27:
            cv2.destroyAllWindows()
            die("aborted - nothing saved")

    cv2.destroyAllWindows()
    ipts = np.array(image_pts, np.float32)
    wpts = np.array(world_pts, np.float32)
    H, _ = cv2.findHomography(ipts, wpts)
    if H is None:
        die("points are degenerate (collinear?) - nothing saved")
    os.makedirs(wd, exist_ok=True)
    np.save(os.path.join(wd, "H_ref_to_world.npy"), H)
    with open(os.path.join(wd, "calibration.json"), "w", encoding="utf-8") as f:
        json.dump({"ref_index": ref_idx,
                   "ref_frame_file": Path(ref_path).name,
                   "ref_frame_sha1": _sha1(ref_path),
                   "points": np.hstack([ipts, wpts]).tolist()}, f, indent=2)
    log("calib", f"saved {len(image_pts)} points bound to {Path(ref_path).name}")
    log("calib", "re-run the pipeline (python run.py) - tracking is reused, geometry recomputes")


if __name__ == "__main__":
    _click_tool(load_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml"))
