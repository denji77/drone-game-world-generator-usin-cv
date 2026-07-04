# Drone Video → Unity Recreation Pipeline

Turn a **pre-recorded drone video** into a schematic Unity world: a flat ground plane
textured with a clean top-down image stitched from your footage, plus every detected
person and vehicle replayed as a moving marker on their real-world path.

**You give it:** one video file.
**It gives you:** `ground.png` (top-down ground texture, moving objects erased),
`tracks_world.json` (every person/vehicle's path in meters, Unity axes), and
`report.json` (a quality report that refuses to deploy garbage).

The pipeline runs on **Windows with an NVIDIA GPU (CUDA)**. It also works on CPU or
Apple Silicon, just slower.

---

## Table of contents

1. [What you need before starting](#1-what-you-need-before-starting)
2. [Folder layout](#2-folder-layout)
3. [One-time setup on Windows](#3-one-time-setup-on-windows)
4. [Choosing and adding your video](#4-choosing-and-adding-your-video)
5. [First run (fully automatic)](#5-first-run-fully-automatic)
6. [Understanding the outputs](#6-understanding-the-outputs)
7. [If the gate FAILs](#7-if-the-gate-fails)
8. [Metric calibration (optional but recommended)](#8-metric-calibration-optional-but-recommended)
9. [Re-running: what is cached and when to use --force](#9-re-running-what-is-cached-and-when-to-use---force)
10. [Every config option explained](#10-every-config-option-explained)
11. [Phase 6 - build the Unity scene (ground and agents)](#11-phase-6---build-the-unity-scene-ground-and-agents)
12. [Phase 7 - zones and the detections CSV log](#12-phase-7---zones-and-the-detections-csv-log)
13. [Phase 8 - optional one-command headless build](#13-phase-8---optional-one-command-headless-build)
14. [Phase 9 - validate the result](#14-phase-9---validate-the-result)
15. [Tuning guide (symptom → knob)](#15-tuning-guide-symptom--knob)
16. [Troubleshooting](#16-troubleshooting)
17. [How the pipeline works internally](#17-how-the-pipeline-works-internally)

---

## 1. What you need before starting

- A **Windows PC with an NVIDIA GPU** and a reasonably recent driver
  (check: open a terminal, run `nvidia-smi` — if it prints a table, you're fine).
- **~6 GB free disk**: Python packages (~4 GB, mostly PyTorch), the YOLO model
  (~40 MB, auto-downloaded on first run), plus room for extracted frames.
- **Internet access** for the one-time setup and the first run (model download).
- A **drone video file** (see section 4 for what makes a good one).
- This project folder copied onto the Windows machine (any location, e.g.
  `C:\work\tempProject`). No git needed — it's plain files.

---

## 2. Folder layout

What the folder contains **before** any run:

```text
tempProject/
  run.py                  <- the one command you run
  config.yaml             <- all settings (section 10)
  requirements.txt        <- Python dependencies
  README.md               <- this file
  video/                  <- PUT YOUR VIDEO HERE (video/drone.mp4 by default)
  scripts/
    common.py             <- shared helpers (paths, ffprobe, the frame-index rule)
    ingest.py             <- Phase 1: clean the clip, extract frames
    track_agents.py       <- Phase 2: YOLO detection + tracking + mover masks
    calibrate.py          <- Phase 3a: scale/orientation (auto + click tool)
    geometry.py           <- Phase 3b: per-frame registration (the geometric core)
    build_mosaic.py       <- Phase 4: the ground texture
    map_to_world.py       <- Phase 5: pixel tracks -> world-meter tracks
    selfcheck.py          <- 2-second math sanity check, no video needed
  unity_scripts/          <- ready-made Unity C# scripts (Phases 6-8); copy into Unity (section 11)
    AgentPlayback.cs      <- replays tracks_world.json as moving prefabs
    ZoneVolume.cs         <- trigger-box zone definition
    DetectionLogger.cs    <- writes detections.csv with video-time stamps
    AgentZoneReporter.cs  <- per-agent zone membership -> logger
    Editor/
      BuildRunner.cs      <- headless scene/player builder (Phase 8)
```

What gets **created** by running it:

```text
  out/
    shot_clean.mp4        <- your clip: trimmed to one shot, cropped, constant fps
    ingest_meta.json      <- fps + stride marker (guards the cache)
    tracks_image.json     <- raw pixel-space tracks from YOLO
    calibration.json      <- only if you do metric calibration (section 8)
    H_ref_to_world.npy    <- pixel -> meters mapping for the reference frame
    H_per_frame.npy       <- pixel -> meters mapping for EVERY frame
    inliers.npy           <- per-frame registration quality
    report.json           <- the quality gate verdict (section 6)
    ground.png            <- the ground texture
    world_bounds.json     <- where the world rectangle sits, in meters
    tracks_world.json     <- the final agent paths
  frames/                 <- extracted registration frames (rebuilt each ingest)
  masks/                  <- moving-object masks (rebuilt each tracking run)
  UnityProject/
    Assets/Generated/     <- ground.png + tracks_world.json + scene_meta.json, ready for Unity
```

---

## 3. One-time setup on Windows

Do these in order. Each step ends with a check so you know it worked.

### 3.1 Install Python 3.12

1. Download **Python 3.12 (64-bit)** from <https://www.python.org/downloads/>.
   Avoid 3.13/3.14 — PyTorch wheels lag behind the newest Python.
2. In the installer, tick **"Add python.exe to PATH"**, then Install.
3. **Check:** open a new terminal (Windows key → type `cmd` → Enter) and run:

   ```bat
   py -3.12 --version
   ```

   Expected output: `Python 3.12.x`

### 3.2 Install ffmpeg

1. In the same terminal:

   ```bat
   winget install Gyan.FFmpeg
   ```

   (If winget is unavailable: download the "release full" build from
   <https://www.gyan.dev/ffmpeg/builds/>, unzip it, and add its `bin` folder to your
   PATH via *System Properties → Environment Variables*.)
2. **Close the terminal and open a new one** — PATH changes only apply to new terminals.
3. **Check:**

   ```bat
   ffmpeg -version
   ffprobe -version
   ```

   Both must print version info. If either says "not recognized", the PATH step failed.

### 3.3 Create the virtual environment

All commands from here on are run **inside the project folder**:

```bat
cd C:\work\tempProject
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
```

After activation your prompt starts with `(.venv)`.

- **PowerShell users:** use `.venv\Scripts\Activate.ps1` instead. If PowerShell refuses
  ("running scripts is disabled"), run once:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, then retry.
- **Every future session**: re-run the `activate` line before using the pipeline.

### 3.4 Install PyTorch with CUDA (order matters — do this BEFORE requirements.txt)

If you install `requirements.txt` first, pip pulls the **CPU-only** torch and tracking
will be ~20x slower. So:

```bat
python -m pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

`cu126` works with any reasonably recent NVIDIA driver. If it errors, open
<https://pytorch.org/get-started/locally/>, pick *Stable / Windows / Pip / CUDA*, and
copy the exact command it shows.

**Check:**

```bat
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected: something like `2.x.x+cu126 True NVIDIA GeForce RTX ...`
If it prints `False`, see [Troubleshooting](#16-troubleshooting).

### 3.5 Install the remaining dependencies

```bat
pip install -r requirements.txt
```

### 3.6 Verify the pipeline math (no video needed)

```bat
python -m scripts.selfcheck
```

Expected output: `selfcheck OK`. This exercises the coordinate/interpolation/stitching
math with synthetic data — if it passes, the environment and the core logic are sound.

Setup is done. You never repeat section 3 (except `activate` each new terminal).

---

## 4. Choosing and adding your video

### 4.1 What footage works

Best → worst, based on how the geometry behaves:

1. **High-altitude, near top-down, slow orbit/pan over flat ground** (parking lot,
   yard, intersection) — ideal.
2. **Hovering overhead shot** — ideal and even simpler.
3. **Moderate oblique (~30-50 degrees down), slow motion, flat ground filling the
   frame** — good.
4. **Fast/low FPV footage, horizon in frame, mostly sky/water, heavy motion blur** —
   expect the quality gate to reject it.

Also required: subjects large enough to detect (people should be more than a few
pixels — 1080p or better helps), and ground with visible texture (registration
matches features; bare uniform tarmac/water/snow gives it nothing to grab).

Cuts/edits are handled automatically: the pipeline detects scene cuts and keeps the
**longest continuous shot**. A burned-in HUD/watermark should be cropped via config
(section 10, `ingest.crop`).

### 4.2 Add the file

Copy your clip to:

```text
tempProject\video\drone.mp4
```

Any container ffmpeg reads is fine (`.mp4`, `.mov`, `.mkv`...). If your file has a
different name or location, open `config.yaml` and change the first line, e.g.:

```yaml
input: video/DJI_0042.MP4
```

Paths in `config.yaml` are relative to the project folder.

---

## 5. First run (fully automatic)

From the project folder, with the venv active:

```bat
python run.py
```

What happens, stage by stage (you'll see matching `[stage]` log lines):

| Stage | What it does | Rough time (70 s clip, decent GPU) |
|---|---|---|
| `ingest` | reads true fps, finds the longest continuous shot, crops HUD + 10% borders, re-encodes to constant fps, extracts every 6th frame | 1–3 min |
| `track` | YOLO11m detects + tracks people/vehicles on every frame at `imgsz 1920`; writes mover masks. **First run downloads the model (~40 MB)** | 2–10 min |
| `calib` | no clicked points yet, so it auto-estimates scale from detected car sizes (falls back to "unscaled" if too few cars) | seconds |
| `geometry` | registers every extracted frame to the reference frame (chained matching + drift re-anchoring) | 1–5 min |
| `gate` | computes quality metrics and **stops here if the clip is unusable** | seconds |
| `mosaic` | builds the ground texture: median of all frames with movers masked out | 1–3 min |
| `map` | converts pixel tracks to world meters, splits/smooths/stitches them | seconds |
| `deploy` | copies the results into `UnityProject/Assets/Generated/` | instant |

A successful run ends with a line like:

```text
[done] scale=auto_scaled. For metric scale: python -m scripts.calibrate, then re-run.
```

and exit code 0. A gate rejection ends with `FAIL: clip unsuitable` and exit code 2 —
see section 7.

---

## 6. Understanding the outputs

### 6.1 `out/report.json` — read this first

```json
{
  "frames_registered_pct": 84.2,
  "tracks": 17,
  "blur_pct": 6.1,
  "pass": true,
  "scale_status": "auto_scaled",
  "ref_index": 141
}
```

| Field | Meaning | Healthy value |
|---|---|---|
| `frames_registered_pct` | % of frames whose geometry was solved directly (the rest are interpolated) | above 60 (gate threshold), ideally 80+ |
| `tracks` | number of people/vehicles found | whatever matches reality |
| `blur_pct` | % of frames that are blurry | below 40 (gate threshold) |
| `pass` | did the gate accept the clip | `true` |
| `scale_status` | `metric` (you calibrated) / `auto_scaled` (car-size estimate) / `auto_scaled_lowconf` (estimate, don't trust it) / `unscaled` (consistent but arbitrary units) | `metric` after section 8 |
| `ref_index` | which extracted frame is the reference (used by the calibration tool) | — |

### 6.2 The three deliverables

- **`out/ground.png`** — top-down ground image in the world frame. Moving objects are
  erased (per-pixel median over time). Already vertically flipped to match Unity's
  texture orientation — do not flip it again.
- **`out/tracks_world.json`** — `{video_fps, tracks: [{id, class, pts: [{t, x, z}]}]}`.
  `t` is seconds from clip start; `x`/`z` are meters in Unity axes (X = right/east,
  Z = forward/north). `class` is `person` or `vehicle`.
- **`out/world_bounds.json`** — `{x0, z0, W_m, H_m, ppm}`: the texture covers the world
  rectangle from `(x0, z0)` to `(x0+W_m, z0+H_m)` at `ppm` pixels per meter.

All three (bounds merged into `scene_meta.json`) are also copied to
`UnityProject/Assets/Generated/`.

---

## 7. If the gate FAILs

The pipeline refuses to emit a broken world rather than producing garbage silently.
Match the failing metric in `out/report.json`:

| Failing metric | Cause | Fix, in order of preference |
|---|---|---|
| `frames_registered_pct` low | featureless ground, fast motion, too little overlap between frames | 1) lower `ingest.stride` to 3 or 4 (more overlap); 2) lower `geometry.reanchor_every` to 10; 3) pick a steadier / more textured clip |
| `tracks` = 0 | subjects too small/blurry, or wrong classes | raise `detect.imgsz` (2560), lower `detect.conf` (0.15); if people are single-digit pixels, no detector saves the clip — use lower-altitude footage |
| `blur_pct` high | motion blur / low light | slower clip, or (if the metric seems oversensitive on your footage) raise `gates.blur_threshold` |

After changing anything under `ingest:` or `detect:`, rerun with `--force`
(see section 9). Changes elsewhere just need a plain rerun.

---

## 8. Metric calibration (optional but recommended)

The first run guesses scale from car sizes. For **real meters** (correct distances,
speeds, zone sizes), click 4+ known ground points once:

### 8.1 Find real-world coordinates for 4 points

Pick 4+ points on the **ground** that you can recognize in the video *and* whose
mutual distances you know — parking-lot corners, lane markings, a building's base
corners. Two easy sources:

- **A known rectangle:** if a lot is 50 m x 30 m, its corners are
  `(0,0)`, `(50,0)`, `(50,30)`, `(0,30)`.
- **Google Maps:** right-click → *Measure distance* between your chosen landmarks,
  then assign coordinates consistent with those distances.

Convention: **X = right/east, Z = forward/north, meters.** Pick any point as origin
`(0,0)`. Spread the points out (never all on one line — collinear points are rejected).

### 8.2 Run the click tool

```bat
python -m scripts.calibrate
```

1. A window opens showing the **reference frame** (the same one the pipeline used —
   it may open behind other windows; check the taskbar).
2. **Click** a landmark in the image.
3. **Switch to the terminal** — it now asks
   `point 0 at pixel (812,455) -> world X Z [m]:` — type the two numbers separated by
   a space, e.g. `0 0`, press Enter.
4. Repeat for each point (4 minimum, more is better). The image window annotates each
   accepted point.
5. Keys: **`s`** = save (only with 4+ points), **`u`** = undo last point,
   **`Esc`** = abort without saving.

Saving writes `out/calibration.json`. The points are **bound to that exact reference
frame** (by filename + content hash): the pipeline will refuse to run if the frame
ever changes underneath them, instead of silently producing a shifted world.

### 8.3 Re-run

```bat
python run.py
```

Frame extraction and tracking are reused automatically — only geometry onward
recomputes (fast). `report.json` now shows `"scale_status": "metric"`.

**When to recalibrate:** if you change the input video, `ingest.crop`,
`ingest.border_crop_pct`, or `ingest.stride`, the frames change, the binding check
fires, and the run stops with a clear message. Delete `out\calibration.json`, run the
pipeline once, then redo 8.2.

---

## 9. Re-running: what is cached and when to use --force

To keep iteration fast, two expensive stages are cached:

| Cached artifact | Stage skipped when present | Invalidate by |
|---|---|---|
| `out/shot_clean.mp4` + `frames/` | ingest | `--force` |
| `out/tracks_image.json` + `masks/` | tracking | `--force`, or delete just `out\tracks_image.json` to redo tracking without re-ingesting |

Geometry, gate, mosaic, and mapping **always** recompute — they're fast.

Rules of thumb:

- Changed anything under `ingest:` (or swapped the video)? → `python run.py --force`
- Changed anything under `detect:`? → `del out\tracks_image.json` then `python run.py`
- Changed `geometry:` / `calibrate:` / `mosaic:` / `map:` / `gates:`? → plain `python run.py`
- Not sure? `--force` is always correct, just slower.

The stride guard: if `frames/` on disk were extracted with a different `stride` than
the config now says, the run **stops** and tells you to use `--force` — mismatched
stride would silently pair frames with the wrong masks.

---

## 10. Every config option explained

`config.yaml`, section by section:

```yaml
input: video/drone.mp4    # your clip, relative to the project folder
work_dir: out             # where intermediate + final artifacts go
unity_project: UnityProject   # deploy target; Assets/Generated is created inside it
```

### ingest

| Key | Default | What it does |
|---|---|---|
| `split_on_cuts` | `true` | detect scene cuts, keep the longest continuous shot (registration/tracking break across cuts) |
| `crop` | `null` | ffmpeg crop for burned-in HUDs, e.g. `"in_w:in_h-80:0:0"` removes the bottom 80 px |
| `border_crop_pct` | `10` | crops N% off every border — cheap lens-distortion mitigation (homographies assume a pinhole camera; wide drone lenses bend the edges). `0` disables |
| `stride` | `6` | geometry runs on every Nth video frame. Lower = more overlap = more robust registration, more compute. At 30 fps, 6 → 5 fps |

### detect

| Key | Default | What it does |
|---|---|---|
| `model` | `yolo11m.pt` | detector; `yolo11l.pt` if your GPU has headroom |
| `imgsz` | `1920` | inference resolution. The stock 640 destroys 10–25 px people; raise to 2560 for very high/small subjects |
| `device` | `auto` | `auto` picks CUDA → Apple MPS → CPU; or force `0` / `"cpu"` |
| `conf` | `0.25` | detection confidence threshold; lower finds more (and more false) objects |
| `dynamic_classes` | `[0,1,2,3,5,7]` | COCO ids: person, bicycle, car, motorcycle, bus, truck |
| `mask_dilate_px` | `25` | grows the mover masks so moving *shadows* don't corrupt registration or the mosaic |
| `foot_shift` | `{person: 0.02, vehicle: 0.18}` | moves the tracked ground point up from the box's bottom edge (fraction of box height). At oblique angles the box bottom is the object's *near edge*, not its center — wrong shift makes parked cars trace little circles during orbits |

### geometry

| Key | Default | What it does |
|---|---|---|
| `ref_frame` | `auto` | reference frame choice (most ORB features). Ignored once `out/calibration.json` exists — the binding wins |
| `min_matches` | `12` | minimum feature matches to even attempt a homography |
| `min_inliers` | `15` | below this a frame counts as unregistered and gets interpolated |
| `reanchor_every` | `25` | every N frames, try a direct match to the reference to kill accumulated drift; lower if the mosaic smears over long clips |
| `orb_features` | `5000` | features per frame; raise for low-texture ground |

### calibrate

| Key | Default | What it does |
|---|---|---|
| `auto` | `car_size` | fallback when no clicked points exist: `car_size` estimates pixels-per-meter from detected cars (orientation-filtered); `unscaled` skips estimation |
| `ppm` | `20` | ground-texture resolution in pixels per meter (also the fallback scale for `unscaled`) |

### mosaic

| Key | Default | What it does |
|---|---|---|
| `max_frames` | `60` | frames used in the median (evenly sampled; 60 is statistically plenty) |
| `max_canvas_px` | `6000` | texture size cap; `ppm` is auto-reduced if the world is too large |
| `tile` | `512` | median is computed in tiles to bound memory; lower to 256 if you hit RAM limits |
| `margin_m` | `5` | padding around the auto-computed world bounds |

### map

| Key | Default | What it does |
|---|---|---|
| `smooth_window` | `5` | moving-average window (samples) on world tracks |
| `max_gap_s` | `0.5` | a track with a time hole bigger than this is split before smoothing (never smooth across an occlusion) |
| `stitch_gaps` | `true` | rejoin split segments when plausible |
| `max_stitch_gap_s` | `3.0` | never stitch across a longer gap |
| `max_speed` | `{person: 3.0, vehicle: 15.0}` | m/s plausibility gate for stitching (a person can't cover 10 m in 0.3 s) |

### gates

| Key | Default | What it does |
|---|---|---|
| `min_frames_registered_pct` | `60` | reject if fewer % of frames registered directly |
| `min_tracks` | `1` | reject if nothing was detected |
| `max_blur_pct` | `40` | reject if more % of frames are blurry |
| `blur_threshold` | `100.0` | Laplacian-variance blur cutoff (higher = stricter) |

### unity

| Key | Default | What it does |
|---|---|---|
| `run_build` | `false` | if `true`, launches Unity in batch mode after deploy (requires Unity installed and a `BuildRunner.cs` in the project; set the `UNITY_BIN` env var if `Unity` isn't on PATH) |
| `build_target` | `StandaloneWindows64` | Unity build target when building a player |

---

## 11. Phase 6 - build the Unity scene (ground and agents)

All the C# you need is already written, in `unity_scripts/`. This section takes you
from "Unity not installed" to markers moving on your ground texture.

### 11.1 Install Unity and create the project

1. Install **Unity Hub** from <https://unity.com/download>, and through it a
   **Unity 6 LTS** (or 2022.3 LTS) editor with the *Windows Build Support* module.
2. In Unity Hub: *New project* → **Universal 3D** (URP) template.
   - **Important:** Unity Hub refuses to create a project in a non-empty folder. If
     the pipeline already ran, `tempProject\UnityProject` contains `Assets/Generated`
     and is non-empty. Two clean options:
     - **Option A (create Unity project first):** create the project at
       `tempProject\UnityProject` *before* the first pipeline run, then run the
       pipeline — it deploys into the existing project.
     - **Option B (create it anywhere):** create the project wherever you like, then
       either edit `unity_project:` in `config.yaml` to point at it and rerun
       `python run.py` (deploy stage only takes a second), or just copy the
       `UnityProject\Assets\Generated` folder into your project's `Assets\`.
3. Open the project and let it finish importing.

### 11.2 Bring in the scripts and the generated assets

1. Copy the entire `unity_scripts` folder into the Unity project's `Assets\` folder
   (in Explorer: `...\UnityProject\Assets\unity_scripts\`). Unity compiles it on
   focus; the `Editor` subfolder inside is picked up as editor code automatically.
2. Confirm `Assets\Generated\` contains `ground.png`, `tracks_world.json`,
   `scene_meta.json` (deployed by the pipeline). If not, see 11.1 step 2.
3. Wait for the compile spinner (bottom-right) to finish. Zero errors expected.

### 11.3 Make the two prefabs (once)

**Person prefab:**

1. *GameObject → Create Empty*, name it `Person`, reset its Transform.
2. Right-click `Person` → *3D Object → Capsule*. Set the capsule's **Position** to
   `(0, 0.9, 0)` and **Scale** to `(0.5, 0.9, 0.5)` — a ~1.8 m figure whose base
   sits at the parent's y=0 (so it stands *on* the ground, not in it).
3. Select the `Person` root: *Add Component → Rigidbody*, tick **Is Kinematic**
   (required — zone triggers never fire without a Rigidbody).
4. *Add Component → Agent Zone Reporter*, leave `objectType` = `person`.
5. In the Project window create a folder `Assets/Prefabs`, drag `Person` from the
   Hierarchy into it, then delete `Person` from the Hierarchy.

**Vehicle prefab:** same steps, but the child is a *Cube* with **Position**
`(0, 0.75, 0)` and **Scale** `(2, 1.5, 4.5)` (width, height, length — length on Z so
the box faces its direction of travel), and `objectType` = `vehicle`. Save as
`Assets/Prefabs/Vehicle.prefab`.

### 11.4 Assemble the scene

1. Open `Assets\Generated\scene_meta.json` (it's plain text) and note
   `x0, z0, W_m, H_m`.
2. *GameObject → 3D Object → Plane*, name it `Ground`. A Unity plane is 10x10 units,
   so set:
   - **Scale** = `(W_m/10, 1, H_m/10)`
   - **Position** = `(x0 + W_m/2, 0, z0 + H_m/2)`
   - **Rotation** = `(0, 0, 0)`
3. In `Assets`, right-click → *Create → Material*, name it `GroundMat`
   (URP/Lit is the default shader). Set its **Base Map** to `ground.png` and drag the
   material onto the plane. Do **not** flip or rotate the texture — orientation is
   pre-baked by the pipeline.
4. *GameObject → Create Empty*, name it `Playback`, *Add Component → Agent Playback*:
   - **Tracks Json** = drag `Assets/Generated/tracks_world.json` in
   - **Person Prefab** / **Vehicle Prefab** = drag the two prefabs in
   - `timeScale` 1 = real time; 2 = double speed; `loop` on by default
5. Point a camera at it: select `Main Camera`, set Position
   `(x0 + W_m/2, max(W_m, H_m), z0 + H_m/2)` and Rotation `(90, 0, 0)` for a
   top-down view.
6. Press **Play**: capsules and boxes move across your ground texture along the real
   paths. Quick sanity check: pick a car visible in the video and confirm its marker
   drives over the same spot of the texture.

---

## 12. Phase 7 - zones and the detections CSV log

Zones turn the replay into a surveillance log: a timestamped CSV of who was in which
area when.

### 12.1 Place zones

1. *GameObject → Create Empty*, name it after the area (e.g. `Zone_EntryGate`).
2. *Add Component → Zone Volume*. A BoxCollider is added automatically with
   *Is Trigger* already ticked.
3. Position the object over the area of interest and size the **Box Collider** to
   cover it. Set the collider's **Size Y** to ~4 and **Center Y** to ~1 so agents
   moving on the ground actually pass through the volume.
4. In the ZoneVolume component set:
   - `zoneType` — `EntryGate` / `Barracks` / `Perimeter` / `Unknown`
   - `zoneId` — any label you want in the CSV (e.g. `GATE-1`)
   - `priority` — when zones overlap, the higher number wins in the log
5. Repeat for as many zones as you want.

### 12.2 Add the logger

*GameObject → Create Empty*, name it `Logger`, *Add Component → Detection Logger*.
One per scene is enough.

### 12.3 Run and read the log

Press **Play**, let it run, stop. The Console prints the exact file path on start;
on Windows it is:

```text
%USERPROFILE%\AppData\LocalLow\DefaultCompany\<ProjectName>\detections.csv
```

Format, one row per agent per `logInterval` (0.5 s of video time by default,
adjustable on each prefab's AgentZoneReporter):

```csv
sim_time,object_type,zone_type,zone_id,x,y,z
12.50,person,EntryGate,GATE-1,23.41,0.00,8.77
```

`sim_time` is **video seconds** — it matches the source footage regardless of
`timeScale`, so "person entered the gate at 12.5 s" can be checked against the video
directly. Note the file is recreated on every Play; copy it out if you want to keep a
run.

If nothing ever logs: the agent prefabs are missing their kinematic Rigidbody
(section 11.3 step 3), or the zone collider is too flat to intersect the agents.

---

## 13. Phase 8 - optional one-command headless build

Instead of assembling the scene by hand (11.4), Unity can do it unattended at the end
of every pipeline run — useful once you're iterating on clips.

**Prerequisites (once):** sections 11.1–11.3 done (project exists, `unity_scripts`
copied in, both prefabs saved at `Assets/Prefabs/`), and the project has been opened
at least once so packages are imported. Unity requires a valid license even in batch
mode (a free Personal license activated via Unity Hub is fine).

1. Tell the pipeline where Unity.exe lives (one-time, in your terminal session):

   ```bat
   set UNITY_BIN=C:\Program Files\Unity\Hub\Editor\6000.0.x\Editor\Unity.exe
   ```

   (Find your exact version folder under `C:\Program Files\Unity\Hub\Editor\`.)
2. In `config.yaml` set:

   ```yaml
   unity:
     run_build: true
     build_player: false     # true = also produce a runnable Build/app.exe
     build_target: StandaloneWindows64
   ```

3. Run `python run.py`. After deploy, Unity launches windowless, executes
   `BuildRunner.Build` (from `unity_scripts/Editor/`), and saves a ready scene to
   `Assets/Generated/Recreation.unity` — ground plane, playback driver, logger, and a
   top-down camera, all positioned from `scene_meta.json`. Open the project and
   double-click that scene to play it.
4. With `build_player: true` it additionally compiles a standalone app to
   `UnityProject\Build\app.exe`.

The editor must be **closed** while the headless build runs (Unity locks the
project). If the step fails, run the same command by hand to see the log:

```bat
"%UNITY_BIN%" -batchmode -quit -projectPath UnityProject -executeMethod BuildRunner.Build -logFile -
```

---

## 14. Phase 9 - validate the result

Five checks, in order of payoff. Do 14.1 once per calibration; the rest when something
looks off.

### 14.1 Probe spheres (catches orientation/mirror bugs)

1. Take two of your calibration points (section 8) — you know their exact `(X, Z)`.
   If you used auto-scale, pick two landmarks you can identify on `ground.png` and
   estimate their world coords from `world_bounds.json` proportions.
2. In Unity: *GameObject → 3D Object → Sphere*, position `(X, 0.5, Z)`, bright
   material. Repeat for the second point.
3. Look straight down. Each sphere must sit on the matching landmark **of the
   rendered texture**.
   - Spheres mirrored *relative to the texture* → someone flipped/rotated the
     texture or plane in Unity; undo it (the pipeline pre-bakes orientation).
   - Spheres consistent with the texture, but the whole world is mirrored *versus
     reality* → negate one axis in your calibration points (all `Z` → `-Z`),
     recalibrate, re-run.

### 14.2 Scale check

A car marker is 4.5 m long. Compare it against the painted parking bays / lane widths
on the texture — cars roughly filling a bay means scale is right. With `metric`
calibration you can be exact: place two spheres at `(0,0)` and `(10,0)` and check the
gap spans 10 m worth of texture.

### 14.3 Stationary-object check (geometry + foot point)

Find a **parked** car's marker and watch it for the whole clip. It should not move.

- Slow drift + sudden snap back → registration gaps: see section 15, first row.
- Small circular wobble (on orbiting footage) → tune `detect.foot_shift` for
  `vehicle` until the circle collapses to a point, then `python run.py` (tracking
  cache makes this fast — but foot_shift lives in tracking, so
  `del out\tracks_image.json` first).

### 14.4 Track sanity

Watch the playback: motion should be smooth and plausible. Teleporting agents →
check `frames_registered_pct` in the report; one real person flickering between two
markers → loosen stitching (`map.max_stitch_gap_s` up, or check `map.max_speed`).

### 14.5 Overlay check (the full-loop test)

Screenshot the Unity top view at some sim-time `T`, pause the source video at the
same `T`, and compare side by side: static features aligned, every real mover having
a marker near its true position. This is the closest thing to a ground-truth test the
schematic approach has — if it passes, the recreation is as good as this method gets.

---

## 15. Tuning guide (symptom → knob)

| You see | Cause | Change |
|---|---|---|
| Agents drift then snap back | stretches of unregistered frames | lower `ingest.stride`; lower `geometry.reanchor_every` |
| Parked cars trace small circles | foot point sits on the box's near edge at oblique angles | tune `detect.foot_shift` (vehicle) until a parked car's track collapses to a point |
| Ghost cars/people in `ground.png` | movers not fully masked | raise `detect.mask_dilate_px`; lower `detect.conf` |
| Jittery agents | track noise | raise `map.smooth_window` |
| One person becomes several agents | occlusion split the track and stitching was too strict | raise `map.max_stitch_gap_s`; check `map.max_speed` |
| Too few detections | subjects too small | raise `detect.imgsz`; lower `detect.conf`; larger `detect.model` |
| Mosaic edges look bent/misaligned | lens distortion | raise `ingest.border_crop_pct` to 15 (then `--force`) |
| Texture too coarse | resolution cap | raise `calibrate.ppm` and/or `mosaic.max_canvas_px` |
| Run is slow on the mosaic / RAM spikes | large canvas | lower `mosaic.max_frames` (30), lower `mosaic.tile` (256) |

---

## 16. Troubleshooting

| Error / symptom | Fix |
|---|---|
| `'py' is not recognized` | Python not installed or PATH box wasn't ticked — reinstall Python, tick *Add to PATH*, open a new terminal |
| `ffprobe failed - is ffmpeg installed and on PATH?` | Section 3.2; remember to open a **new** terminal after installing |
| `ModuleNotFoundError: No module named 'ultralytics'` (or cv2/yaml) | venv not activated (`.venv\Scripts\activate.bat`) or `pip install -r requirements.txt` skipped |
| `torch.cuda.is_available()` prints `False` | CPU wheel got installed. Fix: `pip uninstall torch torchvision` then redo section 3.4. Also update the NVIDIA driver |
| Tracking crawls (minutes per second of video) | you're on CPU — same fix as above; confirm the `[track] ... device=0` log line |
| First run stalls at `track` with a download bar | it's fetching `yolo11m.pt` (~40 MB) — needs internet once |
| `FAIL: clip unsuitable` | section 7 |
| `frames/ were extracted with stride X but config says Y` | intentional guard — run `python run.py --force` |
| `calibration is bound to a frame that no longer exists` / `content changed` | frames were re-extracted since you calibrated — `del out\calibration.json`, run once, recalibrate (section 8) |
| Click-tool window is frozen/white | it's waiting for your terminal input — finish typing the `X Z` values in the terminal |
| Click-tool window never appears | check the taskbar; it can open behind the terminal |
| World is mirrored compared to reality | negate one axis in your calibration points (e.g. all `Z` → `-Z`), recalibrate, re-run |
| Agents walk mirrored **relative to the texture** | should not happen (the flip is baked in); if you flipped/rotated `ground.png` manually in Unity, undo that |
| `only N frames extracted` | clip too short after cut-splitting — check `out/shot_clean.mp4` is the shot you expected |
| Antivirus flags/slows the run | first-run model download + many small mask files; exclude the project folder if needed |
| Unity: zone log stays empty | agent prefabs missing the **kinematic Rigidbody** (11.3), no `DetectionLogger` in the scene (12.2), or zone collider too flat (12.1) |
| Unity Hub: "folder is not empty" when creating the project | the pipeline already deployed into `UnityProject/` — use Option A or B in section 11.1 |
| Headless build fails / `BuildRunner` not found | `unity_scripts` not copied into `Assets` (11.2), editor still open (close it), `UNITY_BIN` wrong, or no Unity license activated |

---

## 17. How the pipeline works internally

Thirty-second version, for debugging with understanding:

1. **One clock, one index.** Everything is keyed by the video frame number `n` at the
   normalized fps. Extracted frame *i* **is** video frame `(i-1)*stride`; the mask for
   it is `masks/frame_{n:06d}.png`; a detection at frame `n` gets the homography
   interpolated at `n/stride`. No stage declares its own frame rate — this kills a
   whole class of silent misalignment bugs.
2. **Two layers.** The *static* world (ground) and the *dynamic* world (movers) are
   built separately from the same footage: movers are masked out of the ground
   texture, and the same detections drive the animated agents.
3. **Geometry.** Because the ground is (roughly) flat, every frame relates to a
   reference frame by a homography. Frames are registered in a *chain* (neighbor to
   neighbor — always high overlap), periodically re-anchored to the reference to
   bound drift; failures are interpolated, never faked with a stale matrix.
   Calibration (clicked points or car-size estimate) converts the reference frame's
   pixels to meters; composing the two gives pixel→meter for every frame.
4. **Ground texture.** Every frame is warped into the world rectangle (auto-computed
   from the footage) and the per-pixel **median** over time is taken with movers
   masked — moving objects simply vanish. The result is flipped once to match Unity's
   texture orientation.
5. **Tracks.** Foot points (bottom-center of each detection, shifted for oblique
   views) go through the interpolated homographies into meters, get split at
   occlusion gaps, smoothed per segment, and stitched back when physically plausible.
6. **The gate.** Registration %, track count, and blur decide whether the result is
   trustworthy; the pipeline reports instead of silently emitting a broken world.

For the full design rationale, see the implementation plan document
(*"Drone Video to Unity Implementation Plan"* — v3, hardened edition) and its
companion *"Issues and Fixes"* review.
