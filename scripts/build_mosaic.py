"""Phase 4B: masked median mosaic over auto-computed world bounds.

- Bounds come from warping every frame's corners (robust percentiles) - never guessed.
- Validity is a SEPARATE boolean mask: 0 is a real pixel value, not a sentinel
  (in-band zero-as-NaN eats legit black pixels per channel and color-skews shadows).
- Tiled exact median: memory is bounded by frames_used x tile^2 regardless of canvas size.
- The texture is flipped vertically ONCE: OpenCV top-left origin -> Unity bottom-left UV.
"""
import json
import os

import cv2
import numpy as np

from .common import log, die, mask_path, video_frame_of


def build_median_mosaic(frames, stride, H_world, cfg, out_png, masks_dir):
    """Returns bounds dict {x0, z0, W_m, H_m, ppm}; writes ground.png + world_bounds.json."""
    mo = cfg["mosaic"]
    ppm = float(cfg["calibrate"]["ppm"])
    margin = float(mo["margin_m"])
    tile = int(mo["tile"])

    # --- auto world bounds from warped frame corners (percentiles resist bad frames) ---
    h, w = cv2.imread(frames[0]).shape[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    pts = np.vstack([cv2.perspectiveTransform(corners, Hw)[:, 0] for Hw in H_world])
    x0 = float(np.percentile(pts[:, 0], 2) - margin)
    z0 = float(np.percentile(pts[:, 1], 2) - margin)
    x1 = float(np.percentile(pts[:, 0], 98) + margin)
    z1 = float(np.percentile(pts[:, 1], 98) + margin)
    W_m, H_m = x1 - x0, z1 - z0
    if W_m <= 0 or H_m <= 0 or W_m > 5000 or H_m > 5000:
        die(f"degenerate world bounds {W_m:.0f}x{H_m:.0f} m - registration failed badly")

    if max(W_m, H_m) * ppm > mo["max_canvas_px"]:
        ppm = mo["max_canvas_px"] / max(W_m, H_m)
        log("mosaic", f"canvas capped: ppm reduced to {ppm:.1f}")
    Wp, Hp = int(W_m * ppm), int(H_m * ppm)
    S = np.float32([[ppm, 0, -ppm * x0], [0, ppm, -ppm * z0], [0, 0, 1]])
    log("mosaic", f"bounds ({x0:.1f},{z0:.1f}) {W_m:.1f}x{H_m:.1f} m -> {Wp}x{Hp} px")

    # --- subsample frames for the median (60 evenly spaced samples is plenty) ---
    idxs = np.unique(np.linspace(0, len(frames) - 1, min(len(frames), int(mo["max_frames"])))
                     .astype(int))
    imgs, valids, Hs = [], [], []
    for i in idxs:
        img = cv2.imread(frames[i])
        m = cv2.imread(mask_path(masks_dir, video_frame_of(i, stride)), 0)
        valid = np.full(img.shape[:2], 255, np.uint8) if m is None or m.shape != img.shape[:2] \
            else cv2.bitwise_not(m)
        imgs.append(img); valids.append(valid); Hs.append(H_world[i])
    F = len(imgs)
    log("mosaic", f"median over {F} frames, tiled {tile}px")

    # --- tiled exact masked median ---
    ground = np.zeros((Hp, Wp, 3), np.uint8)
    for ty in range(0, Hp, tile):
        th = min(tile, Hp - ty)
        for tx in range(0, Wp, tile):
            tw = min(tile, Wp - tx)
            T = np.float32([[1, 0, -tx], [0, 1, -ty], [0, 0, 1]])
            stack = np.zeros((F, th, tw, 3), np.uint8)
            vstack = np.zeros((F, th, tw), bool)
            for k in range(F):
                M = T @ S @ Hs[k]
                stack[k] = cv2.warpPerspective(imgs[k], M, (tw, th))
                v = cv2.warpPerspective(valids[k], M, (tw, th), flags=cv2.INTER_NEAREST)
                vstack[k] = v > 200  # NEAREST + strict: no half-valid border pixels
            m = np.ma.array(stack, mask=~np.repeat(vstack[..., None], 3, axis=3))
            ground[ty:ty + th, tx:tx + tw] = np.ma.median(m, axis=0).filled(0).astype(np.uint8)

    cv2.imwrite(out_png, cv2.flip(ground, 0))  # flip ONCE: OpenCV top-left -> Unity bottom-left UV
    bounds = {"x0": round(x0, 3), "z0": round(z0, 3),
              "W_m": round(W_m, 3), "H_m": round(H_m, 3), "ppm": round(ppm, 3)}
    with open(os.path.join(os.path.dirname(out_png), "world_bounds.json"), "w",
              encoding="utf-8") as f:
        json.dump(bounds, f, indent=2)
    log("mosaic", f"wrote {out_png}")
    return bounds
