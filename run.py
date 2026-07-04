#!/usr/bin/env python3
"""run.py [config.yaml] [--force] - one command: video in -> Unity-ready world out.

Stages (plan v3): ingest -> track -> calibrate -> geometry -> GATE -> mosaic -> map -> deploy.
Cached artifacts are reused (frames/, tracks) so the calibrate-then-rerun workflow is cheap;
--force redoes everything.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

from scripts.common import log, die, load_config, p
from scripts.ingest import ingest_video
from scripts.track_agents import run_tracking
from scripts.calibrate import resolve_reference, calibrate_H
from scripts.geometry import register_all
from scripts.build_mosaic import build_median_mosaic
from scripts.map_to_world import map_tracks


def blur_pct(frames, thresh):
    v = [cv2.Laplacian(cv2.imread(f, 0), cv2.CV_64F).var() for f in frames]
    return 100.0 * float(np.mean(np.array(v) < thresh))


def gate(cfg, inliers, n_tracks, blur):
    g = cfg["gates"]
    reg_pct = 100.0 * float(np.mean(np.array(inliers) >= cfg["geometry"]["min_inliers"]))
    ok = (reg_pct >= g["min_frames_registered_pct"]
          and n_tracks >= g["min_tracks"]
          and blur <= g["max_blur_pct"])
    return ok, {"frames_registered_pct": round(reg_pct, 1), "tracks": n_tracks,
                "blur_pct": round(blur, 1), "pass": bool(ok)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", nargs="?", default="config.yaml")
    ap.add_argument("--force", action="store_true", help="ignore cached artifacts, redo all stages")
    a = ap.parse_args()

    cfg = load_config(a.config)
    wd = p(cfg, cfg["work_dir"])
    os.makedirs(wd, exist_ok=True)
    frames_dir, masks_dir = p(cfg, "frames"), p(cfg, "masks")
    clean = os.path.join(wd, "shot_clean.mp4")
    stride = int(cfg["ingest"]["stride"])

    # 1) ingest -----------------------------------------------------------------
    meta_path = os.path.join(wd, "ingest_meta.json")
    if a.force or not (os.path.exists(clean) and os.path.exists(meta_path)
                       and glob.glob(os.path.join(frames_dir, "frame_*.jpg"))):
        video_fps, stride = ingest_video(cfg, clean, frames_dir)
    else:
        meta = json.load(open(meta_path, encoding="utf-8"))
        if int(meta["stride"]) != stride:
            die(f"frames/ were extracted with stride {meta['stride']} but config says "
                f"{stride} - rerun with --force (mis-keyed masks otherwise)")
        video_fps = float(meta["video_fps"])
        log("ingest", f"reusing {clean} + frames/ (use --force to redo)")
    frames = sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))
    if not frames:
        die("no frames extracted")

    # 2) detect + track on the full-fps video ------------------------------------
    tij = os.path.join(wd, "tracks_image.json")
    if a.force or not os.path.exists(tij):
        n_tracks = run_tracking(clean, cfg, video_fps, stride, tij, masks_dir)
    else:
        n_tracks = len(json.load(open(tij, encoding="utf-8"))["tracks"])
        log("track", f"reusing {tij}: {n_tracks} tracks (use --force to redo)")

    # 3) geometry: bound reference + chained registration ------------------------
    ref_idx, binding = resolve_reference(cfg, frames, wd)
    H_rw, scale_status = calibrate_H(cfg, binding, frames[ref_idx])
    np.save(os.path.join(wd, "H_ref_to_world.npy"), H_rw)
    H_world, inliers = register_all(frames, stride, ref_idx, H_rw, cfg["geometry"], masks_dir)
    np.save(os.path.join(wd, "H_per_frame.npy"), H_world)
    np.save(os.path.join(wd, "inliers.npy"), inliers)

    # 4) GATE - refuse to deploy garbage ------------------------------------------
    ok, report = gate(cfg, inliers, n_tracks, blur_pct(frames, cfg["gates"]["blur_threshold"]))
    report["scale_status"] = scale_status
    report["ref_index"] = int(ref_idx)
    with open(os.path.join(wd, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log("gate", f"REPORT: {report}")
    if not ok:
        log("gate", "FAIL: clip unsuitable - not deploying. See out/report.json")
        sys.exit(2)

    # 5) ground texture (auto bounds, UV-flipped) + world tracks ------------------
    bounds = build_median_mosaic(frames, stride, H_world, cfg,
                                 os.path.join(wd, "ground.png"), masks_dir)
    map_tracks(tij, H_world, stride, cfg, os.path.join(wd, "tracks_world.json"))

    # 6) deploy into the Unity project --------------------------------------------
    gen = p(cfg, cfg["unity_project"], "Assets", "Generated")
    os.makedirs(gen, exist_ok=True)
    shutil.copy(os.path.join(wd, "ground.png"), gen)
    shutil.copy(os.path.join(wd, "tracks_world.json"), gen)
    with open(os.path.join(gen, "scene_meta.json"), "w", encoding="utf-8") as f:
        json.dump({**bounds, "scale_status": scale_status}, f, indent=2)
    log("deploy", f"assets -> {gen}")

    # 7) optional headless Unity build ---------------------------------------------
    if cfg["unity"].get("run_build"):
        unity = os.environ.get("UNITY_BIN", "Unity")
        subprocess.run([unity, "-batchmode", "-quit",
                        "-projectPath", p(cfg, cfg["unity_project"]),
                        "-executeMethod", "BuildRunner.Build",
                        "-buildPlayer", str(cfg["unity"].get("build_player", False)).lower(),
                        "-buildTarget", cfg["unity"]["build_target"]], check=True)

    log("done", f"scale={scale_status}. For metric scale: python -m scripts.calibrate, then re-run.")


if __name__ == "__main__":
    main()
