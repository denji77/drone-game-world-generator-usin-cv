"""Phase 2: detect + track people/vehicles on the full-fps video; write mover masks.

Points carry the VIDEO frame number n (the one index, Part 2.8). Masks are named by n;
they are only materialized for registration frames (n % stride == 0) since geometry and
the mosaic never read any other frame.  # ponytail: skip 5/6 of mask writes; write all if needed later
"""
import json
import os
import shutil

import cv2
import numpy as np

from .common import log, mask_path


def resolve_device(want):
    if want not in (None, "auto"):
        return want
    try:
        import torch
        if torch.cuda.is_available():
            return 0
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def run_tracking(clip, cfg, video_fps, stride, out_json, masks_dir):
    """Returns the number of raw tracks found."""
    from ultralytics import YOLO  # lazy: heavy import

    det = cfg["detect"]
    class_map = {int(c): ("person" if int(c) == 0 else "vehicle")
                 for c in det["dynamic_classes"]}
    foot_shift = det["foot_shift"]
    kernel = np.ones((int(det["mask_dilate_px"]),) * 2, np.uint8)
    device = resolve_device(det.get("device", "auto"))
    log("track", f"model={det['model']} imgsz={det['imgsz']} device={device}")

    if os.path.isdir(masks_dir):
        shutil.rmtree(masks_dir)
    os.makedirs(masks_dir)

    model = YOLO(det["model"])
    by_id, n = {}, 0  # n = VIDEO frame number
    for res in model.track(clip, persist=True, stream=True, verbose=False,
                           imgsz=int(det["imgsz"]), device=device, conf=float(det["conf"]),
                           tracker="botsort.yaml",  # ships global motion compensation
                           classes=list(det["dynamic_classes"])):
        boxes = []
        if res.boxes is not None and res.boxes.id is not None:
            for box, cid, tid in zip(res.boxes.xyxy.cpu().numpy(),
                                     res.boxes.cls.cpu().numpy(),
                                     res.boxes.id.cpu().numpy()):
                cid = int(cid)
                if cid not in class_map:
                    continue
                x1, y1, x2, y2 = map(float, box)
                cls = class_map[cid]
                u = (x1 + x2) / 2.0
                # foot point shifted off the near edge: bbox bottom != footprint center when oblique
                v = y2 - float(foot_shift[cls]) * (y2 - y1)
                d = by_id.setdefault(int(tid), {"id": int(tid), "class": cls, "pts": []})
                d["pts"].append({"n": n, "t": round(n / video_fps, 3),
                                 "u": round(u, 2), "v": round(v, 2)})
                boxes.append((x1, y1, x2, y2))

        if n % stride == 0:  # only registration frames ever read a mask
            h, w = res.orig_img.shape[:2]
            mask = np.zeros((h, w), np.uint8)
            for x1, y1, x2, y2 in boxes:
                cv2.rectangle(mask, (int(x1), int(y1)), (int(x2), int(y2)), 255, -1)
            mask = cv2.dilate(mask, kernel)  # tight boxes leave moving shadows unmasked
            cv2.imwrite(mask_path(masks_dir, n), mask)

        n += 1
        if n % 300 == 0:
            log("track", f"frame {n} ({n / video_fps:.0f}s), {len(by_id)} tracks so far")

    tracks = [t for t in by_id.values() if len(t["pts"]) >= 3]  # drop 1-2 point flickers
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"video_fps": video_fps, "tracks": tracks}, f)
    log("track", f"done: {n} frames, {len(tracks)} tracks -> {out_json}")
    return len(tracks)
