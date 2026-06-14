# Schematic Drone-Video → Unity Recreation — Implementation Plan **v2 (Pre-Recorded Footage Edition)**

> **Goal:** take a **pre-recorded** drone video and produce a simple Unity recreation of the real place — a flat ground textured from the footage, with the people and vehicles re-created as moving stand-in shapes, plus zone labels and a log. **Not detailed. As light as possible, but robust to real footage.**

**What changed from v1 → v2 (read this):** v1 assumed you might shoot your own footage and that the camera could hover (one fixed mapping for the whole clip). You're using **pre-recorded shots**, and pre-recorded drone footage is *almost always filmed with a moving camera* (orbiting, flying forward, panning, changing altitude). That single fact reshapes the whole conversion: instead of one image→ground mapping, you generally need **per-frame registration**. v2 therefore:

1. Adds a deep section on **which pre-recorded clips work** and how to triage/prep a clip you already have (Part 1).
2. Adds a large, in-depth **"How the video becomes a Unity world"** section explaining the conversion conceptually + mathematically (Part 2) — this is the centerpiece you asked for.
3. Makes the **moving-camera pipeline** (per-frame homography + mosaic) the default path, with full code (Phase 3B / Phase 5).
4. Expands every phase, plus a worked example, an accuracy/validation phase, a coordinate cheat-sheet, and bigger troubleshooting.

Read **Part 0**, **Part 1**, and **Part 2** before doing anything — they decide everything else.

---

## Table of Contents

- **Part 0 — The mental model (what you're building)**
- **Part 1 — Choosing your video: what type is preferred (in depth)**
- **Part 2 — HOW THE VIDEO BECOMES A UNITY WORLD (the conversion, in depth)**
- **Part 3 — Tools & project layout**
- **Phase 0 — Validate the Unity side first (fake data)**
- **Phase 1 — Ingest & pre-process the pre-recorded video**
- **Phase 2 — Detect & track people/vehicles (+ masks)**
- **Phase 3 — Establish the geometry (the heart of the conversion)**
- **Phase 4 — Build the static ground (rectified frame or mosaic)**
- **Phase 5 — Map image tracks → world tracks (+ smoothing & gaps)**
- **Phase 6 — Spawn & animate the agents in Unity**
- **Phase 7 — Zone System + logging (the surveillance layer)**
- **Phase 8 — Automate the pipeline (one command)**
- **Phase 9 — Validate & measure accuracy**
- **Phase 10 — Optional upgrades**
- **Worked example (end-to-end)**
- **Appendix A — Troubleshooting (expanded)**
- **Appendix B — Coordinate-system cheat sheet**
- **Appendix C — Glossary**
- **Appendix D — Tools & references**

---

## Part 0 — The mental model (what you're building)

### 0.1 The idea in one picture

```
                         ┌──────────────────────────────────────────┐
                         │  STATIC LAYER (built once)                 │
  pre-recorded ────────▶ │  • a flat ground plane in Unity            │
  drone video            │  • textured with a top-down image          │
                         │    (a rectified frame, or a stitched        │
                         │     mosaic) derived from the footage        │
                         └──────────────────────────────────────────┘
                                            +
                         ┌──────────────────────────────────────────┐
  pre-recorded ────────▶ │  DYNAMIC LAYER                             │
  drone video            │  • detect + track people/vehicles          │
                         │  • map each one's ground position per       │
                         │    frame into the world frame               │
                         │  • spawn capsule (person) / box (vehicle)    │
                         │    that moves along its real trajectory      │
                         │  • zone labels + CSV log                     │
                         └──────────────────────────────────────────┘
```

You rebuild the **ground as a flat map**, and the **moving things as simple shapes that move the way they did in the video.** Nothing more.

### 0.2 The two-layer principle (why it's split this way)

Reconstructing a static scene and tracking moving things are *different problems that interfere with each other*. So the world is two independent layers:

- **Static layer:** the ground/buildings, which don't move. Built once.
- **Dynamic layer:** people/vehicles, which move. Rebuilt as animated markers.

Keeping them separate is what makes a moving-camera, surveillance-style video tractable.

### 0.3 What this approach does well / does not do

**Well:** fast; no heavy GPU reconstruction; robust to footage that 3D reconstruction chokes on; gives positions, zones, and a who-was-where-when log.

**Not:** no real 3D terrain/buildings (ground is flat; structures are optional plain boxes); everything is positioned *on the ground plane*; positions are approximate, not survey-grade.

### 0.4 The pre-recorded constraint

Because you can't re-shoot:
- You **select and triage** clips instead of capturing ideal ones (Part 1).
- You must **handle whatever camera motion** the clip has (Part 2 / Phase 3).
- You usually have **no camera calibration, no GPS/telemetry, possibly edits and overlays** — all handled in Phase 1.

---

## Part 1 — Choosing your video: what type is preferred (in depth)

You asked specifically what video is preferred. The short answer and the reasoning:

### 1.1 The deciding factor is **camera motion**, then **angle**, then **content**

A homography (image→flat-ground mapping) is the engine of the schematic method. It is exact only for a **flat ground**, and a *single* homography is valid only for a **non-moving camera**. So clips sort into three **motion regimes**, each needing a different amount of work (full details in Part 2):

| Regime | Camera motion | What it needs | Difficulty |
|---|---|---|---|
| **A — Static** | Locked-off hover, no movement | **One** homography for the whole clip | Easiest (rare in pre-recorded) |
| **B — Moving, planar** | Orbit / pan / fly-over of a roughly **flat** area | **Per-frame** homography + a stitched ground | **Default for pre-recorded** |
| **C — Moving, 3D** | Big parallax around tall structures / strong terrain | Camera **pose (SfM) + depth** | Advanced upgrade |

> **Expect Regime B.** Most pre-recorded drone "shots" move. Plan for per-frame registration; treat A as a lucky simplification and C as an optional upgrade.

### 1.2 Ranked preference for **pre-recorded** clips

**Preferred (pick these):**
1. **High-altitude, near top-down (nadir), slow steady orbit or slow lateral pan over a flat area** (parking lot, yard, field, base, intersection, rooftop). The ground dominates the frame, motion is smooth, the flat-ground assumption holds → Regime B works beautifully, and a clean mosaic is easy.
2. **Locked-off / hovering overhead shot** → Regime A, the simplest possible case. Uncommon but ideal when you find one.
3. **Moderate oblique (≈30–50° down), slow pan, flat-ish ground filling most of the frame** → Regime B with mild foreshortening; acceptable.

**Acceptable with extra work:**
4. **Steady forward fly-over at altitude, near-nadir** → mosaic/registration works, but long clips accumulate drift; process in segments and re-anchor (Part 2.3-B).
5. **Slow orbit around a building/object** → this contains real 3D (parallax). Ground-level objects still map okay on the flat plane, but anything off the ground is misplaced → consider Regime C.

**Poor fits (avoid or expect failure):**
6. **Low-altitude FPV / fast cinematic / racing-drone footage** → fast motion, motion blur, large perspective change frame-to-frame → registration and detection both suffer.
7. **Low-oblique / horizon-in-frame shots** → the flat-ground assumption breaks badly for distant ground; far objects compress to nonsense.
8. **Heavily edited clips** (hard cuts, transitions, speed ramps, picture-in-picture, burned-in text/HUD) → cuts break tracking and registration; overlays confuse features and detection. Must be split/cleaned first (Phase 1).

### 1.3 The content rules (independent of motion)

- **Ground should fill most of the frame and be roughly flat.** The method positions everything *on a plane*.
- **Resolution and clarity good enough for YOLO** to detect people/vehicles. 1080p+ is comfortable; tiny, blurry, or heavily compressed subjects detect poorly.
- **Avoid scenes that are mostly sky, water, or featureless tarmac** — registration (feature matching) needs texture on the ground.

### 1.4 Pre-recorded-specific gotchas (and fixes — see Phase 1)

- **Editing cuts** → split into continuous single-shot segments first (PySceneDetect / manual trim).
- **Variable frame rate (VFR) or re-encoded files** → normalize to constant fps; read the true fps with `ffprobe`.
- **Burned-in overlays / watermarks / HUD / logos** → crop or mask them so they don't pollute feature matching, the mosaic, or detection.
- **No GPS/telemetry, no camera calibration** → you won't get metric scale automatically. You calibrate scale manually (Part 2.6): from known object sizes, or by measuring the same location on a satellite map if it's identifiable.
- **Unknown intrinsics** → not needed for the homography path (a relief!); only needed for the Regime-C pose+depth upgrade, where you estimate them via SfM.

### 1.5 Triage checklist for a clip you already have

Run through this before committing to a clip:

1. Is it **one continuous shot**? If not → split (Phase 1).
2. Does the **ground fill the frame and look roughly flat**? If not (lots of sky/buildings/horizon) → poor fit or Regime C.
3. Is the camera **static, slow-moving, or fast/erratic**? Static→A, slow→B, fast→risky.
4. Are **people/vehicles clearly visible** (not 3-pixel specks)? If too small → detection will fail; pick a lower-altitude or higher-res clip.
5. Any **overlays/HUD/text**? → crop/mask.
6. Do you have **any way to get real scale** (known object, identifiable location on a map)? If not, you'll have an *unscaled* but still usable world.

If a clip passes 1–4, it's a good candidate. 5–6 are fixable.

---

## Part 2 — HOW THE VIDEO BECOMES A UNITY WORLD (the conversion, in depth)

This is the core of your request. We'll build the concept from the ground up: the problem, the coordinate frames, the three geometric regimes (with math + intuition), the unifying data flow, why moving objects are separated, and how scale/orientation land in Unity.

### 2.1 The core problem stated precisely

A video is a sequence of 2D images `I_0, I_1, … I_N`, each captured by the drone camera at a *different position and orientation* (because it's moving). A point on the real ground appears at **different pixel locations in different frames.** To place anything into a single Unity world, you need a **fixed world coordinate frame** and, for **every frame**, a rule that converts "pixel in this frame" → "position in the world frame."

Formally, for each frame `t` you want a function:

```
   world_position  =  G_t( pixel )
```

- If the camera is **static**, `G_t` is the **same** for all `t` → one mapping.
- If the camera **moves over flat ground**, `G_t` is a **per-frame homography** (a 3×3 matrix that changes each frame).
- If the camera **moves through real 3D**, `G_t` requires the camera's **pose** plus **per-pixel depth**.

Everything else (detection, tracking, ground texture) feeds into or consumes this `G_t`.

### 2.2 The coordinate systems you must keep straight

Four frames are in play. Mixing them up is the #1 source of bugs.

1. **Image / pixel frame** — 2D, origin top-left, `(u, v)` in pixels, `u` right, `v` **down**. This is where detections live.
2. **World / ground frame** — 2D metric ground plane, `(X, Z)` in **meters**. This is the fixed reference everything maps into.
3. **Unity world frame** — 3D, **left-handed**, `+X` right, `+Y` **up**, `+Z` forward, 1 unit = 1 meter. Agents go at `(X, groundY, Z)` and the ground plane lies in the `X–Z` plane at `y = groundY`.
4. **(Regime C only) Camera 3D frame** — 3D camera-centered coordinates, used when unprojecting with depth.

**Key alignment rule:** define your world/ground `(X, Z)` *directly in Unity's convention* (e.g., `+X` = east/right, `+Z` = north/away, looking down the `-Y` axis). Then the homography output is already in Unity coordinates and no axis-swapping is needed. If the result looks **mirrored** in Unity, flip the sign of one axis in your calibration points (Appendix B).

### 2.3 The three geometric regimes (math + intuition + when)

#### Regime A — Static camera → a single homography

**Intuition:** if the camera never moves, the relationship between the image and the flat ground is fixed. A **homography** `H` (3×3) relates a ground point to its pixel via projective geometry:

```
   [u]           [X]
   [v]  ~   H_w  [Z]            (homogeneous; "~" means up to scale)
   [1]           [1]
```

Invert it to go pixel→ground. In practice you compute the pixel→ground homography `H` directly from **4+ correspondences** (4 ground points whose pixel `(u,v)` *and* metric `(X,Z)` you know):

```python
H, _ = cv2.findHomography(image_pts, world_pts)         # maps (u,v) -> (X,Z)
XZ   = cv2.perspectiveTransform([[ (u,v) ]], H)          # apply to any pixel
```

**When:** locked-off hover only. **Cost:** trivial. **Limitation:** flat-ground assumption (objects must be on the ground plane).

#### Regime B — Moving camera + flat ground → per-frame homography (the workhorse)

**Intuition:** the camera moves, so the image↔ground relationship is **different in every frame**. But because the ground is (approximately) **flat**, the relationship between **any two frames** of that ground is *also* a homography (this is the key theorem: images of a plane are related by homographies). So we:

1. **Calibrate once:** pick a **reference frame** `R` and compute the reference→world homography `H_{R→W}` from 4 known ground points (as in Regime A, but only for the reference frame).
2. **Register every frame to the reference:** for each frame `t`, compute the frame→reference homography `H_{t→R}` by matching image features.
3. **Compose:** the full per-frame mapping is the matrix product

```
   H_{t→W}  =  H_{R→W} · H_{t→R}
```

   So any pixel in frame `t` maps to world meters via `XZ = perspectiveTransform(pixel, H_{t→W})`.

**How `H_{t→R}` is computed (feature registration):**

```python
# ORB features + RANSAC homography from frame t to reference R
orb = cv2.ORB_create(4000)
kpR, desR = orb.detectAndCompute(ref_gray, refMask)      # refMask hides movers/overlays
kpT, desT = orb.detectAndCompute(frame_gray, frameMask)
matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desT, desR, k=2)
good = [m for m, n in matches if m.distance < 0.75 * n.distance]   # Lowe's ratio test
src = np.float32([kpT[m.queryIdx].pt for m in good]).reshape(-1,1,2)
dst = np.float32([kpR[m.trainIdx].pt for m in good]).reshape(-1,1,2)
H_t_to_R, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)   # robust to outliers
```

**Drift & long clips:** matching a far-away frame *directly* to a single reference fails when the views barely overlap. Two robust remedies:
- **Sequential chaining with re-anchoring:** register frame `t` to frame `t-1` (always high overlap), and keep a running product to the reference; periodically re-anchor to a real reference/mosaic to limit accumulated error.
- **Incremental mosaic registration:** register each frame to the *growing mosaic* (below), which always contains the overlapping region. More robust for big sweeps.

**The mosaic (your ground texture, and a clean static plate):** warp every (masked) frame into the **world frame** with `H_{t→W}` and blend them onto one big canvas. Taking the **per-pixel temporal median** across frames makes **moving objects disappear** (they're only in a few frames; the median is the static background) — giving a clean ground map. (Part 2.5 explains why this matters.)

**When:** the common pre-recorded case — orbits, pans, fly-overs of flat-ish areas. **Cost:** moderate (feature matching per frame). **Limitation:** still flat-ground; off-ground objects get misplaced; fast motion/blur degrades matching.

#### Regime C — Moving camera + real 3D → camera pose + depth (upgrade)

**Intuition:** when the scene has real height variation/parallax (orbit around a building, hilly terrain), a flat homography can't be correct for everything. You instead recover the **camera's 3D pose** for each frame and a **per-pixel depth**, then back-project pixels into 3D world space.

1. **Poses + intrinsics** from Structure-from-Motion (COLMAP) or a feed-forward model (VGGT). You get camera intrinsics `K` and, per frame, rotation `R_t` and translation `t_t` (note conventions below).
2. **Per-pixel depth** `d(u,v)` from a monocular depth model (Depth Anything V2, or Video Depth Anything for temporal consistency / metric scale).
3. **Back-project** a detection foot pixel:

```
   X_cam   = d(u,v) · K⁻¹ · [u, v, 1]ᵀ          # camera-space 3D point
   X_world = R_tᵀ · (X_cam − t_t)               # if convention is  X_cam = R_t·X_world + t_t  (COLMAP-style)
```

**Conventions matter:** COLMAP stores **world-to-camera** `[R|t]` (i.e., `X_cam = R·X_world + t`), so world = `Rᵀ(X_cam − t)`. Always verify on a known point; sign/handedness mistakes here are common.

**Scale:** monocular SfM scale is arbitrary; fix it with a known distance or a **metric** depth model (Depth Pro / metric Video Depth Anything).

**When:** off-ground structures you care about, or terrain with relief. **Cost:** high (SfM + depth + a GPU). **Limitation:** more moving parts; still imperfect on thin/reflective surfaces.

### 2.4 The unifying data-flow diagram

```
                    pre-recorded drone video (one continuous shot)
                                     │
                         ┌───────────┴────────────┐
                         │  Phase 1: ffmpeg →      │
                         │  frames + true fps      │
                         └───────────┬────────────┘
              ┌────────────────────── │ ──────────────────────┐
              ▼                       ▼                        ▼
   ┌────────────────────┐  ┌──────────────────────┐  ┌─────────────────────┐
   │ Phase 2: DETECT+    │  │ Phase 3: GEOMETRY     │  │ Phase 4: STATIC      │
   │ TRACK (YOLO)        │  │  pick regime A/B/C    │  │ GROUND               │
   │ → image-space       │  │  → G_t (per-frame      │  │ → rectified frame    │
   │   tracks {id,cls,    │  │     mapping) + masks   │  │   or median mosaic   │
   │   foot (u,v), t}     │  │                        │  │   (uses G_t)         │
   └─────────┬──────────┘  └───────────┬───────────┘  └──────────┬──────────┘
             │                         │                         │
             └──────────┬──────────────┘                         │
                        ▼                                         │
            ┌──────────────────────────┐                         │
            │ Phase 5: MAP image tracks │                         │
            │ → WORLD tracks via G_t,    │                         │
            │ smooth, stitch gaps        │                         │
            │ → tracks_world.json        │                         │
            └─────────────┬─────────────┘                         │
                          │                                       │
                          ▼                                       ▼
            ┌─────────────────────────────────────────────────────────────┐
            │ Phase 6–7: UNITY                                              │
            │  ground plane (texture from Phase 4)                          │
            │  + agents animated along tracks_world.json (foot → position)  │
            │  + zone volumes + CSV log                                     │
            └─────────────────────────────────────────────────────────────┘
```

Three independent producers — **detection/tracking**, **geometry**, **ground texture** — meet at the world frame and assemble in Unity. The **geometry** (`G_t`) is the linchpin both for the ground texture and for placing agents, which is why Part 2 and Phase 3 dwell on it.

### 2.5 Why moving objects are handled separately

If you stitched raw frames into the mosaic, moving people/cars would smear into **ghosts** (they're in different places each frame). Two reasons to separate them:

1. **For the static ground:** mask movers out (YOLO masks, Phase 2) and/or take the **temporal median** across registered frames — moving things vanish, leaving a clean background plate.
2. **For the dynamic layer:** the same detections that you masked are *exactly* the things you re-inject as moving agents (Phase 5–6).

So one detection pass serves both: it cleans the ground **and** drives the agents. This is the elegant core of the two-layer design.

### 2.6 Scale & orientation: getting meters and aligning to Unity

- **Scale (meters):** pre-recorded clips usually have no telemetry, so set scale from one known real distance:
  - A known object: car ≈ 4.5 m long, parking space ≈ 2.5 m wide, road lane ≈ 3.5 m, soccer goal ≈ 7.32 m.
  - If the location is identifiable, measure a distance on **Google/Bing Maps** and use those coordinates for your 4 calibration points.
  - If you truly can't, pick arbitrary but consistent meters; the world will be self-consistent, just not real-scale.
- **Orientation:** assign your 4 calibration world points in **Unity's axes** (`+X` right/east, `+Z` forward/north). Verify with a known landmark; if mirrored, flip one axis sign (Appendix B).
- **Origin:** put one corner of your area at world `(0,0)` so the Unity plane, the mosaic texture, and the agent coordinates all share an origin (prevents misalignment and large-coordinate jitter).

### 2.7 How each regime degrades (so you can predict results)

- **Regime A on a slightly moving camera** → everything slowly slides; positions drift. Fix: switch to B.
- **Regime B on non-flat ground / off-ground objects** → things *on* the ground are fine; things *above* it (rooftops, drone-height objects) are pushed outward from the camera. Acceptable for "ground agents only."
- **Regime B on fast/blurred motion** → feature matching fails on some frames → gaps/jumps. Fix: lower fps, segment the clip, or upgrade to C.
- **Regime C with wrong pose convention/scale** → mirrored or wrongly-scaled world. Fix: verify on known points; use metric depth.

---

## Part 3 — Tools & project layout

### 3.1 Tools (lightweight)

- **Unity Hub + Unity 6 LTS** (or 2022.3 LTS), **URP** template.
- **Python 3.10+** venv:
  ```bash
  python -m venv .venv
  # Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
  pip install --upgrade pip
  pip install ultralytics opencv-contrib-python numpy scenedetect
  # opencv-contrib gives SIFT/extra features; scenedetect splits edited clips
  # NVIDIA GPU: install CUDA torch from pytorch.org for faster YOLO
  ```
- **ffmpeg / ffprobe** — ingest, fps, cropping, splitting.
- **Optional (Regime C only):** COLMAP, and a depth model (Depth Anything V2 / Video Depth Anything). A GPU helps a lot here.

### 3.2 Suggested project layout

```
project/
  video/           drone.mp4
  frames/          frame_0001.jpg ...
  masks/           frame_0001.png ...        (mover/overlay masks)
  out/
    tracks_image.json
    H_per_frame.npy           (Regime B: H_{t→R} stack)
    H_ref_to_world.npy        (calibration)
    ground.png                (rectified frame or mosaic)
    tracks_world.json
  scripts/         ingest.py, track_agents.py, geometry.py, calibrate.py, map_to_world.py, run.py
  UnityProject/    Assets/Generated/ (ground.png + tracks_world.json copied here)
```

---

## Phase 0 — Validate the Unity side first (fake data, ~1 hour)

Prove the Unity half before touching CV, so the halves never block each other.

1. New Unity URP project. Add a **Plane** (default 10×10 units).
2. Make a **Capsule** prefab (`Person`) and a car-sized **Cube** prefab (`Vehicle`).
3. Hardcode 2–3 fake tracks (lists of `(t, x, z)`) and move the prefabs along them with a tiny script (replaced by the real loader in Phase 6).
4. Add a top-down camera; press Play.

**Success:** a capsule and a box glide along set paths on the plane.

---

## Phase 1 — Ingest & pre-process the pre-recorded video

**Goal:** one clean, continuous, constant-fps clip → frames, with overlays removed.

### 1.1 Inspect the file

```bash
# true (possibly fractional) frame rate, resolution, duration
ffprobe -v 0 -of csv=p=0 -select_streams v:0 \
  -show_entries stream=r_frame_rate,width,height,duration drone.mp4
```

### 1.2 Split edited clips into single continuous shots

Pre-recorded clips often contain cuts. Detect/split first:

```bash
# auto-detect scene cuts and split into separate files
scenedetect -i drone.mp4 detect-adaptive split-video
# or manually trim one continuous shot:
ffmpeg -ss 00:00:12 -to 00:01:10 -i drone.mp4 -c copy shot.mp4
```

Process **one continuous shot at a time** — registration and tracking both break across cuts.

### 1.3 Normalize fps & crop overlays/HUD

```bash
# force constant 30 fps and crop a burned-in HUD (crop=W:H:X:Y), if any
ffmpeg -i shot.mp4 -filter:v "crop=in_w:in_h-80:0:0,fps=30" -qscale:v 2 shot_clean.mp4
```

### 1.4 Extract frames

```bash
mkdir frames
# 3–5 fps is a good start; raise for fast motion (better registration), lower to save compute
ffmpeg -i shot_clean.mp4 -vf "fps=4" -qscale:v 2 frames/frame_%04d.jpg
```

> For **registration (Regime B)** you generally want **more** frames than v1 suggested (high overlap helps feature matching), so 3–5 fps is reasonable. Detection runs on the video directly (Phase 2) at full fps.

---

## Phase 2 — Detect & track people/vehicles (+ masks)

**Goal:** (a) image-space tracks (foot points over time) for the dynamic layer; (b) per-frame **masks** of movers + overlays for clean registration/mosaic.

```python
# track_agents.py -> out/tracks_image.json  AND  masks/*.png
from ultralytics import YOLO
import cv2, json, os, numpy as np

model = YOLO("yolo11m.pt")          # or yolo11s/yolo12m; pick for your hardware
FPS   = 30.0                        # MUST match shot_clean.mp4's fps (Phase 1.3)
CLASS_MAP = {0:"person", 1:"vehicle", 2:"vehicle", 3:"vehicle", 5:"vehicle", 7:"vehicle"}
os.makedirs("masks", exist_ok=True)

seg = YOLO("yolo11m-seg.pt")        # segmentation for masks (optional but recommended)
by_id, frame = {}, 0

for res in model.track("video/shot_clean.mp4", persist=True, stream=True, verbose=False):
    img = res.orig_img
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    if res.boxes is not None and res.boxes.id is not None:
        for box, cid, tid in zip(res.boxes.xyxy.cpu().numpy(),
                                 res.boxes.cls.cpu().numpy(),
                                 res.boxes.id.cpu().numpy()):
            cid = int(cid)
            if cid not in CLASS_MAP:    # ignore classes we don't care about
                continue
            x1,y1,x2,y2 = box
            u, v = (x1+x2)/2.0, y2       # FOOT POINT (bottom-center = where it meets ground)
            t = frame / FPS
            d = by_id.setdefault(int(tid), {"id":int(tid), "class":CLASS_MAP[cid], "pts":[]})
            d["pts"].append({"t":round(t,3), "u":float(u), "v":float(v)})
            cv2.rectangle(mask, (int(x1),int(y1)), (int(x2),int(y2)), 255, -1)  # box mask fallback
    cv2.imwrite(f"masks/frame_{frame:04d}.png", mask)   # white = EXCLUDE from registration/mosaic
    frame += 1

json.dump({"fps":FPS, "tracks":list(by_id.values())}, open("out/tracks_image.json","w"), indent=2)
```

(For tighter masks, run `yolo11m-seg.pt` and paint polygon masks instead of boxes; optionally refine with **SAM2** — Phase 10.)

---

## Phase 3 — Establish the geometry (the heart of the conversion)

Choose your regime from Part 1.1. **For pre-recorded footage, Phase 3B is your default.**

### 3A — Static camera (single homography)

Only if the camera is locked off. Compute one `H` from 4 known points (see Phase 3B-step-1 / calibrate.py) and skip registration. Every frame uses the same `H`.

### 3B — Moving camera + flat ground (per-frame homography) — **the workhorse**

**Step 1 — Calibrate the reference frame to world meters** (once):

```python
# calibrate.py -> out/H_ref_to_world.npy
import cv2, numpy as np
# pick a reference frame index R (one that shows most of the area), e.g. middle of the clip
# get 4+ (u,v) by clicking; assign their real (X,Z) in METERS, in Unity axes
image_pts = np.array([[ux1,vy1],[ux2,vy2],[ux3,vy3],[ux4,vy4]], np.float32)
world_pts = np.array([[X1, Z1],[X2, Z2],[X3, Z3],[X4, Z4]], np.float32)   # meters
H_ref_to_world, _ = cv2.findHomography(image_pts, world_pts)
np.save("out/H_ref_to_world.npy", H_ref_to_world)
```

*(Tip: a 10-line `cv2.setMouseCallback` click tool beats reading pixel coords by eye.)*

**Step 2 — Register every frame to the reference** and compose to world:

```python
# geometry.py -> out/H_per_frame.npy   (stack of H_{t→World}, one 3x3 per frame)
import cv2, numpy as np, glob

REF = 60                                   # reference frame index
frames = sorted(glob.glob("frames/*.jpg"))
masks  = sorted(glob.glob("masks/*.png"))
H_rw   = np.load("out/H_ref_to_world.npy")

orb = cv2.ORB_create(5000)
def feats(path, mpath):
    g = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2GRAY)
    m = cv2.imread(mpath, 0) if mpath else None
    keep = cv2.bitwise_not(m) if m is not None else None  # exclude white(=movers/overlays)
    return g, *orb.detectAndCompute(g, keep)

_, kpR, desR = feats(frames[REF], masks[REF])
bf = cv2.BFMatcher(cv2.NORM_HAMMING)

H_world = []
prev_H_t_to_R = np.eye(3)
for i, (fp, mp) in enumerate(zip(frames, masks)):
    _, kp, des = feats(fp, mp)
    H_t_to_R = None
    if des is not None and desR is not None:
        good = [m for m,n in bf.knnMatch(des, desR, k=2) if m.distance < 0.75*n.distance]
        if len(good) >= 12:
            src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1,1,2)
            dst = np.float32([kpR[m.trainIdx].pt for m in good]).reshape(-1,1,2)
            H_t_to_R, _ = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if H_t_to_R is None:                    # direct-to-ref failed (low overlap/blur)
        H_t_to_R = prev_H_t_to_R            # fall back to previous (see note on chaining)
    prev_H_t_to_R = H_t_to_R
    H_world.append(H_rw @ H_t_to_R)         # compose: pixel_t -> world meters
np.save("out/H_per_frame.npy", np.array(H_world))
```

> **Robustness notes.** (1) If direct-to-reference matching fails for distant frames, switch to **frame-to-frame chaining** (`H_{t→R} = H_{t→t-1} · H_{t-1→R}`) and re-anchor periodically, or register to the **growing mosaic** (Phase 4). (2) Always check inlier counts; frames with too few inliers are unreliable — mark them and interpolate `H` across them.

### 3C — Moving camera + real 3D (pose + depth) — upgrade

Only if off-ground structures or terrain relief matter. Run COLMAP (or VGGT) for poses+intrinsics, run Depth Anything V2 / Video Depth Anything for depth, then back-project foot points (Part 2.3-C). Output the same `tracks_world.json` shape so Phases 6–7 are unchanged. (Details in Phase 10.)

---

## Phase 4 — Build the static ground (rectified frame or mosaic)

**Goal:** one top-down ground image in the **world frame** to texture the Unity plane, aligned to the agent coordinates.

### 4A — Single rectified frame (quick; fine for small areas / near-nadir)

Warp your reference frame into the world frame:

```python
import cv2, numpy as np
H_rw = np.load("out/H_ref_to_world.npy")
PPM  = 20                                  # pixels per meter in the texture
W_m, H_m = 60.0, 40.0                      # world rectangle (meters), origin at (0,0)
S = np.array([[PPM,0,0],[0,PPM,0],[0,0,1]], np.float32)   # meters -> texture pixels
ref = cv2.imread("frames/frame_0060.jpg")
ground = cv2.warpPerspective(ref, S @ H_rw, (int(W_m*PPM), int(H_m*PPM)))
cv2.imwrite("out/ground.png", ground)
```

### 4B — Median mosaic (recommended for moving cameras; removes movers)

Warp **every masked frame** into the world frame and take the per-pixel **median** → a clean, larger ground plate with people/cars erased:

```python
# build_mosaic.py -> out/ground.png   (clean static background in world frame)
import cv2, numpy as np, glob
H_world = np.load("out/H_per_frame.npy")
frames  = sorted(glob.glob("frames/*.jpg"))
masks   = sorted(glob.glob("masks/*.png"))
PPM, W_m, H_m = 20, 60.0, 40.0
S = np.array([[PPM,0,0],[0,PPM,0],[0,0,1]], np.float32)
Wp, Hp = int(W_m*PPM), int(H_m*PPM)

stack = []                                  # memory note: subsample frames if RAM-limited
for fp, mp, Hw in zip(frames, masks, H_world):
    img  = cv2.imread(fp)
    warp = cv2.warpPerspective(img, S @ Hw, (Wp, Hp))
    mv   = cv2.warpPerspective(255 - cv2.imread(mp,0), S @ Hw, (Wp, Hp))  # valid (non-mover) px
    warp[mv < 128] = 0                       # zero out movers/no-data -> ignored by median
    stack.append(warp)
arr = np.array(stack, np.float32)
arr[arr == 0] = np.nan                       # treat zeros as "no data"
ground = np.nan_to_num(np.nanmedian(arr, axis=0)).astype(np.uint8)
cv2.imwrite("out/ground.png", ground)
```

> If RAM is tight, process in tiles, use a subset of frames (every Nth), or accumulate a running median approximation. The median is what makes moving objects vanish.

---

## Phase 5 — Map image tracks → world tracks (+ smoothing & gaps)

**Goal:** convert image-space foot points into world meters using the geometry, then clean the trajectories.

```python
# map_to_world.py -> out/tracks_world.json
import cv2, json, numpy as np

data    = json.load(open("out/tracks_image.json"))
FPS     = data["fps"]
H_world = np.load("out/H_per_frame.npy")     # Regime B; for A use a single H for all frames

def frame_index(t):  # map a timestamp back to the nearest extracted-frame's homography
    return min(int(round(t * EXTRACT_FPS)), len(H_world) - 1)   # EXTRACT_FPS from Phase 1.4

EXTRACT_FPS = 4.0

def smooth(seq, k=5):                          # simple moving average to kill jitter
    if len(seq) < k: return seq
    out, half = [], k // 2
    for i in range(len(seq)):
        lo, hi = max(0, i-half), min(len(seq), i+half+1)
        out.append(sum(seq[lo:hi]) / (hi-lo))
    return out

for tr in data["tracks"]:
    xs, zs, ts = [], [], []
    for p in tr["pts"]:
        H = H_world[frame_index(p["t"])]
        XZ = cv2.perspectiveTransform(np.array([[[p["u"], p["v"]]]], np.float32), H)[0][0]
        xs.append(float(XZ[0])); zs.append(float(XZ[1])); ts.append(p["t"])
    xs, zs = smooth(xs), smooth(zs)            # smooth in world space
    tr["pts"] = [{"t":t, "x":x, "z":z} for t,x,z in zip(ts, xs, zs)]

json.dump(data, open("out/tracks_world.json","w"), indent=2)
```

**Gaps & broken tracks:** when a person is occluded, the tracker may give them a **new ID** afterward, splitting one path into two. Options:
- **Accept it** (two short markers) — fine for schematic.
- **Stitch** tracks whose end/start are close in time and space (simple nearest-neighbor join).

**Output:** `out/tracks_world.json` — `{fps, tracks:[{id, class, pts:[{t,x,z}]}]}` in meters, Unity axes.

---

## Phase 6 — Spawn & animate the agents in Unity

1. Copy `out/ground.png` and `out/tracks_world.json` into `UnityProject/Assets/Generated/`.
2. Ground: a **Plane** scaled to `(W_m/10, 1, H_m/10)`, material Base Map = `ground.png`, positioned so its area starts at world `(0,0,0)` (matches the texture & track origin).
3. Agents: `Person` (capsule) + `Vehicle` (cube) prefabs, animated by:

```csharp
// AgentPlayback.cs — animate prefabs along world-space tracks
using System.Collections.Generic;
using UnityEngine;

[System.Serializable] public class Pt    { public float t, x, z; }
[System.Serializable] public class Track { public int id; public string @class; public List<Pt> pts; }
[System.Serializable] public class Data  { public float fps; public List<Track> tracks; }

public class AgentPlayback : MonoBehaviour
{
    public TextAsset tracksJson;                 // Assets/Generated/tracks_world.json
    public GameObject personPrefab, vehiclePrefab;
    public float groundY = 0f, timeScale = 1f;

    readonly List<(Track tr, Transform go)> agents = new();
    float clock;

    void Start()
    {
        var data = JsonUtility.FromJson<Data>(tracksJson.text);
        foreach (var tr in data.tracks)
        {
            var go = Instantiate(tr.@class == "person" ? personPrefab : vehiclePrefab).transform;
            go.name = $"{tr.@class}_{tr.id}";
            go.gameObject.SetActive(false);
            agents.Add((tr, go));
        }
    }

    void Update()
    {
        clock += Time.deltaTime * timeScale;
        foreach (var (tr, go) in agents)
        {
            var p = tr.pts;
            if (clock < p[0].t || clock > p[p.Count - 1].t) { go.gameObject.SetActive(false); continue; }
            go.gameObject.SetActive(true);
            int i = 0; while (i < p.Count - 1 && p[i + 1].t < clock) i++;
            var a = p[i]; var b = p[Mathf.Min(i + 1, p.Count - 1)];
            float u = Mathf.Approximately(b.t, a.t) ? 0f : (clock - a.t) / (b.t - a.t);
            Vector3 pos = new Vector3(Mathf.Lerp(a.x, b.x, u), groundY, Mathf.Lerp(a.z, b.z, u));
            Vector3 dir = pos - go.position;
            if (dir.sqrMagnitude > 1e-5f) go.rotation = Quaternion.LookRotation(dir.normalized);
            go.position = pos;
        }
    }
}
```

**Pivot/scale tips:** offset each prefab so its base sits at `y = 0` (so it stands *on* the plane). Size the capsule to ~1.8 m, the vehicle cube to ~4.5 × 1.5 × 2 m, so the scene reads correctly.

**Success:** people and vehicles move across your flat map following their real trajectories — a Unity recreation driven entirely by the pre-recorded footage.

---

## Phase 7 — Zone System + logging (the surveillance layer)

Identical in spirit to v1; this is where the source document's ideas pay off.

```csharp
// ZoneVolume.cs
using UnityEngine;
public enum ZoneType { EntryGate, Barracks, Perimeter, Unknown }
[RequireComponent(typeof(BoxCollider))]
public class ZoneVolume : MonoBehaviour {
    public ZoneType zoneType = ZoneType.Perimeter;
    public string zoneId = "Z0"; public int priority = 0;   // higher wins on overlap
    void Reset() => GetComponent<BoxCollider>().isTrigger = true;
}
```
```csharp
// DetectionLogger.cs
using System.IO; using UnityEngine;
public class DetectionLogger : MonoBehaviour {
    public static DetectionLogger I; StreamWriter w;
    void Awake() {
        I = this;
        w = new StreamWriter(Path.Combine(Application.persistentDataPath, "detections.csv")){AutoFlush=true};
        w.WriteLine("sim_time,object_type,zone_type,zone_id,x,y,z");
    }
    public void Log(string type, ZoneType zt, string zid, Vector3 p) =>
        w.WriteLine($"{Time.time:F2},{type},{zt},{zid},{p.x:F2},{p.y:F2},{p.z:F2}");
    void OnApplicationQuit() => w?.Dispose();
}
```
```csharp
// AgentZoneReporter.cs — on each agent prefab (needs a Collider + kinematic Rigidbody)
using UnityEngine;
public class AgentZoneReporter : MonoBehaviour {
    public string objectType = "person"; ZoneVolume current;
    void OnTriggerStay(Collider o){ var z=o.GetComponent<ZoneVolume>();
        if(z!=null && (current==null || z.priority>=current.priority)) current=z; }
    void LateUpdate(){ if(current!=null && DetectionLogger.I!=null)
        DetectionLogger.I.Log(objectType, current.zoneType, current.zoneId, transform.position);
        current=null; }
}
```

Place a few trigger boxes (gate/barracks/perimeter) over the map, set priorities, and you get a timestamped CSV of who was in which zone — the surveillance replay log.

---

## Phase 8 — Automate the pipeline (one command)

```
run.py shot.mp4 :
  1) ingest.py        ffprobe + (split/crop/fps) + ffmpeg frames
  2) track_agents.py  -> out/tracks_image.json, masks/*.png
  3) calibrate.py     -> out/H_ref_to_world.npy   (interactive once; cache it)
  4) geometry.py      -> out/H_per_frame.npy
  5) build_mosaic.py  -> out/ground.png
  6) map_to_world.py  -> out/tracks_world.json
  7) copy ground.png + tracks_world.json -> UnityProject/Assets/Generated/
```

Chain with `subprocess.run(..., check=True)`; fail on first error. Optionally end with a headless Unity run (`Unity -batchmode -quit -projectPath <p> -executeMethod BuildRunner.Build`) to auto-build the scene and produce the CSV.

### 8.1 Can it be fully "video in → Unity world out"? (the honest verdict)

**Yes, ~90% of it can run hands-off from a single command.** Frame extraction, detection + tracking, per-frame registration, the mosaic, mapping to world coordinates, and the Unity scene assembly all run unattended. **Two things qualify "fully":** one step resists full automation (**calibration** — real-world scale & orientation), and one reality means you shouldn't blindly emit output (**quality depends on the clip**, so the pipeline must *gate*, not just run).

### 8.2 What's automatic vs. the one manual step

| Stage | Auto? |
|---|---|
| Ingest / split cuts / crop HUD / fps / frames | ✅ (heuristics; per-clip config sometimes) |
| Detect + track (YOLO) + masks | ✅ |
| Per-frame registration (ORB+RANSAC) | ✅ (with inlier gating + interpolation) |
| Median mosaic / ground texture | ✅ |
| Map image tracks → world tracks + smoothing | ✅ |
| Unity scene assembly (batch mode) | ✅ (build script + Unity installed) |
| **Calibration (4 known-world points)** | ⚠️ **the human-in-the-loop step** |

**Three ways to remove the calibration step:**
- **(a) Unscaled, auto-oriented world** — skip metric calibration entirely; define the world frame automatically from the reference frame's pixel plane scaled by a guessed pixels-per-meter. Agents still move correctly *relative* to each other and the ground; it's just not real-scale. Usually fine for "not detailed." Fully automatic.
- **(b) Auto-estimate scale** — from telemetry if the clip carries altitude/focal data (ground sampling distance), or from a heuristic such as median detected **car length ≈ 4.5 m** (bootstrap the unknown scale factor from object sizes in the reference frame).
- **(c) Satellite geo-registration** — feature-match the mosaic against satellite imagery to recover real scale + orientation + geo-coordinates. Most automatic *and* real-scale, but advanced.

### 8.3 Don't output garbage: robustness gates

"*Any* video → a *good* world" is **not** guaranteed (output depends on the clip — Part 1). A robust pipeline gates and reports "unsuitable clip" instead of emitting a broken scene. Cheap automated checks:
- registration **inlier counts** per frame (too few → unreliable),
- **motion/blur** estimate (Laplacian variance; large frame-to-frame jumps),
- **detection counts** (subjects too small/absent),
- **featureless-ground** detection (too few ORB features → water/tarmac/snow).

Surface a short report ("82% frames registered; 9 tracks; scale = unscaled") so a human can trust or reject the result.

### 8.4 Architecture & the two output flavors

**Two kinds of "world out the other end," both achievable:**
- **A — a Unity project/scene you open:** the pipeline drops `ground.png` + `tracks_world.json` into `Assets/Generated/` and a build script assembles the scene (plane + agents + zones) in batch mode.
- **B — a built, runnable app (desktop / WebGL):** the truest "video in → world out," produced by a **headless Unity build** (`-batchmode -quit -executeMethod`). Note: Unity needs a **license even in batch mode**; the standard way to containerize this on a server is **GameCI** Docker images.

**Shape of the system:**
- **Now:** a **CLI tool** — `run.py video.mp4` → emits the assets / scene / build.
- **Later (the "upload here, download there" experience):** wrap it as **upload → job queue → worker (Python pipeline + Unity batch build) → download link**. Standard plumbing (a small web front end + a background worker).

**Advice:** get **one clip working manually end-to-end first**, then automate. Automating before the manual flow works means debugging the factory and the product simultaneously — the classic trap. Build phases 1→7 by hand once; *then* write `run.py` and the batch build.

---

### 8.5 The automatable 90%, built out

Everything below is the concrete implementation of the automatic stages. The **only** non-automatic input is calibration (the 4 known-world points), and even that has an auto-fallback (8.7) — with one honest limit: the fallback gives a *correct* (just unscaled) world **only when the reference frame is roughly top-down**; for an oblique reference view you genuinely need the 4-point calibration (or satellite geo-registration) to get the shape right, not merely the scale.

#### Stage contract (the execution DAG + file artifacts)

```
 input video
    │  ingest()         → frames/*.jpg            (+ true fps)
    ├─ track()          → out/tracks_image.json, masks/*.png
    ├─ calibrate()      → out/H_ref_to_world.npy   (from config points, OR auto-fallback)
    ├─ geometry()       → out/H_per_frame.npy      (+ per-frame inlier counts)
    ├─ mosaic()         → out/ground.png
    ├─ map_world()      → out/tracks_world.json
    ├─ gate()           → out/report.json          (PASS/FAIL + metrics)   ← stops here if FAIL
    ├─ deploy()         → UnityProject/Assets/Generated/{ground.png, tracks_world.json, scene_meta.json}
    └─ unity_build()    → Recreation.unity  (and optionally a player build)
```

Each producer is independent except for the obvious data dependencies; `gate()` is the safety valve that prevents emitting a broken world.

#### 8.6 `config.yaml` — one place to drive a run

```yaml
input: video/shot.mp4
work_dir: out
unity_project: UnityProject

ingest:
  split_on_cuts: true          # PySceneDetect; process the longest segment
  crop: null                   # e.g. "in_w:in_h-80:0:0" to remove a HUD, or null
  fps: 4                       # extraction fps for registration/mosaic

detect:
  model: yolo11m.pt
  seg_model: yolo11m-seg.pt
  dynamic_classes: [0,1,2,3,5,7]   # COCO ids: person + vehicles

geometry:
  ref_frame: auto              # 'auto' = pick the frame with most ORB features, or an index
  min_inliers: 12              # below this, a frame's homography is interpolated

calibrate:                     # the only manual bit — leave 'points' null to auto-fallback
  points: null                 # or: [[u,v,X,Z], [u,v,X,Z], [u,v,X,Z], [u,v,X,Z]]  (meters)
  auto: car_size               # used only if points==null: 'car_size' | 'unscaled'
  ppm: 20                      # pixels-per-meter for the ground texture
  world_size_m: [60, 40]       # [W, H] world rectangle, origin at (0,0)

map:
  smooth_window: 5
  stitch_gaps: true

gates:
  min_frames_registered_pct: 60
  min_tracks: 1
  max_blur_warn_pct: 40

unity:
  build_player: false          # true = also produce a runnable app
  build_target: StandaloneWindows64
```

#### 8.7 `run.py` — the full orchestrator

```python
#!/usr/bin/env python3
"""run.py <config.yaml> — one command: video in → Unity-ready world out.
Heavy CV stages reuse the Phase 1–5 scripts' logic (import them as functions or
call as subprocesses); the glue, auto-calibration, gating, and deployment are here."""
import sys, os, json, shutil, subprocess
import numpy as np, cv2, yaml

# --- import the per-phase logic (refactor Phase 1–5 scripts into importable functions) ---
from scripts.ingest        import ingest_video           # -> frames/, returns extract_fps
from scripts.track_agents  import run_tracking           # -> tracks_image.json, masks/
from scripts.geometry      import register_all, pick_reference   # -> H_per_frame.npy, inliers
from scripts.build_mosaic  import build_median_mosaic    # -> ground.png
from scripts.map_to_world  import map_tracks             # -> tracks_world.json

def calibrate(cfg, frames, masks, ref_idx):
    """The only step that can need a human. Returns H_ref_to_world (pixels->meters)."""
    c, ppm = cfg["calibrate"], cfg["calibrate"]["ppm"]
    if c["points"]:                                    # manual override: 4 known points
        pts = np.array(c["points"], np.float32)
        H, _ = cv2.findHomography(pts[:, :2], pts[:, 2:])
        return H, "metric"
    # ---- auto-fallback (valid for near-nadir reference frames) ----
    if c["auto"] == "car_size":                        # estimate scale from detected cars
        ppm = estimate_ppm_from_cars(frames[ref_idx], default=ppm)  # median car px / 4.5 m
        status = "auto_scaled"
    else:
        status = "unscaled"
    H = np.array([[1/ppm, 0, 0], [0, 1/ppm, 0], [0, 0, 1]], np.float32)  # pixels -> meters
    return H, status

def gate(cfg, inliers, n_tracks):
    reg_pct = 100.0 * np.mean([1 if i >= cfg["geometry"]["min_inliers"] else 0 for i in inliers])
    g = cfg["gates"]
    ok = reg_pct >= g["min_frames_registered_pct"] and n_tracks >= g["min_tracks"]
    return ok, {"frames_registered_pct": round(reg_pct, 1), "tracks": n_tracks, "pass": bool(ok)}

def main(cfg_path):
    cfg = yaml.safe_load(open(cfg_path))
    wd  = cfg["work_dir"]; os.makedirs(wd, exist_ok=True)

    # 1) ingest ----------------------------------------------------------------
    clip = ingest_video(cfg["input"], cfg["ingest"])            # split/crop/fps inside
    extract_fps = cfg["ingest"]["fps"]

    # 2) detect + track --------------------------------------------------------
    n_tracks = run_tracking(clip, cfg["detect"], out=f"{wd}/tracks_image.json", masks="masks")

    # 3) geometry: reference + per-frame registration --------------------------
    ref_idx = pick_reference("frames", cfg["geometry"]["ref_frame"])
    H_rw, scale_status = calibrate(cfg, sorted(os.listdir("frames")), "masks", ref_idx)
    np.save(f"{wd}/H_ref_to_world.npy", H_rw)
    H_world, inliers = register_all("frames", "masks", ref_idx, H_rw, cfg["geometry"])
    np.save(f"{wd}/H_per_frame.npy", H_world)

    # 4) GATE — stop before producing anything if the clip is unsuitable -------
    ok, report = gate(cfg, inliers, n_tracks)
    report["scale_status"] = scale_status
    json.dump(report, open(f"{wd}/report.json", "w"), indent=2)
    print("REPORT:", report)
    if not ok:
        print("FAIL: clip unsuitable — not deploying. See report.json."); sys.exit(2)

    # 5) ground texture + world tracks -----------------------------------------
    W_m, Hm = cfg["calibrate"]["world_size_m"]; ppm = cfg["calibrate"]["ppm"]
    build_median_mosaic("frames", "masks", H_world, ppm, W_m, Hm, out=f"{wd}/ground.png")
    map_tracks(f"{wd}/tracks_image.json", H_world, extract_fps, cfg["map"],
               out=f"{wd}/tracks_world.json")

    # 6) deploy artifacts into the Unity project -------------------------------
    gen = f"{cfg['unity_project']}/Assets/Generated"; os.makedirs(gen, exist_ok=True)
    shutil.copy(f"{wd}/ground.png", gen); shutil.copy(f"{wd}/tracks_world.json", gen)
    json.dump({"W_m": W_m, "H_m": Hm, "ppm": ppm, "scale_status": scale_status},
              open(f"{gen}/scene_meta.json", "w"))

    # 7) optional headless Unity build (assemble scene; optionally build a player)
    unity = os.environ.get("UNITY_BIN", "Unity")
    subprocess.run([unity, "-batchmode", "-quit", "-projectPath", cfg["unity_project"],
                    "-executeMethod", "BuildRunner.Build",
                    "-buildPlayer", str(cfg["unity"]["build_player"]).lower(),
                    "-buildTarget", cfg["unity"]["build_target"]], check=True)
    print("DONE → open Assets/Generated/Recreation.unity"
          + ("  (+ player build in Build/)" if cfg["unity"]["build_player"] else ""))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
```

> To make this runnable, refactor the Phase 1–5 snippets into the imported functions (`ingest_video`, `run_tracking`, `pick_reference`, `register_all`, `build_median_mosaic`, `map_tracks`) — they're the same code, wrapped in `def`s that take paths/config and return their outputs. `estimate_ppm_from_cars` is a few lines: detect cars in the reference frame, take the median box length in pixels, divide by ~4.5 m.

#### 8.8 `BuildRunner.cs` — Unity batch scene assembly (the headless build step)

```csharp
// Assets/Editor/BuildRunner.cs — invoked by: Unity -batchmode -quit -executeMethod BuildRunner.Build
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildRunner
{
    const string GEN = "Assets/Generated";

    [System.Serializable] class Meta { public float W_m, H_m, ppm; public string scale_status; }

    public static void Build()
    {
        // read CLI args
        bool buildPlayer = HasFlag("-buildPlayer", "true");
        var meta = JsonUtility.FromJson<Meta>(File.ReadAllText($"{GEN}/scene_meta.json"));

        // fresh scene
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);

        // ground plane (Unity Plane = 10x10 units) sized to the world rectangle
        AssetDatabase.ImportAsset($"{GEN}/ground.png");
        var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
        ground.name = "Ground";
        ground.transform.localScale = new Vector3(meta.W_m / 10f, 1f, meta.H_m / 10f);
        ground.transform.position   = new Vector3(meta.W_m / 2f, 0f, meta.H_m / 2f); // origin at corner
        var mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
        mat.SetTexture("_BaseMap", AssetDatabase.LoadAssetAtPath<Texture2D>($"{GEN}/ground.png"));
        ground.GetComponent<MeshRenderer>().sharedMaterial = mat;

        // agent playback driver
        var driver = new GameObject("AgentPlayback").AddComponent<AgentPlayback>();
        driver.tracksJson    = AssetDatabase.LoadAssetAtPath<TextAsset>($"{GEN}/tracks_world.json");
        driver.personPrefab  = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Person.prefab");
        driver.vehiclePrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Vehicle.prefab");

        // logger + (optional) zone boxes could be instantiated here from a zones config
        new GameObject("DetectionLogger").AddComponent<DetectionLogger>();

        // top-down camera
        var cam = new GameObject("TopCam").AddComponent<Camera>();
        cam.transform.position = new Vector3(meta.W_m / 2f, Mathf.Max(meta.W_m, meta.H_m), meta.H_m / 2f);
        cam.transform.eulerAngles = new Vector3(90, 0, 0);

        Directory.CreateDirectory(GEN);
        EditorSceneManager.SaveScene(scene, $"{GEN}/Recreation.unity");

        if (buildPlayer)
        {
            var target = ParseTarget();
            string outPath = target == BuildTarget.WebGL ? "Build/web" : "Build/app.exe";
            BuildPipeline.BuildPlayer(new[] { $"{GEN}/Recreation.unity" }, outPath, target, BuildOptions.None);
        }
    }

    static bool HasFlag(string flag, string val)
    {
        var a = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++) if (a[i] == flag && a[i + 1] == val) return true;
        return false;
    }
    static BuildTarget ParseTarget()
    {
        var a = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++)
            if (a[i] == "-buildTarget" && System.Enum.TryParse(a[i + 1], out BuildTarget t)) return t;
        return BuildTarget.StandaloneWindows64;
    }
}
```

This assembles the whole scene from the three generated files (`ground.png`, `tracks_world.json`, `scene_meta.json`) with **no clicks**. It assumes your `Person`/`Vehicle` prefabs exist at `Assets/Prefabs/` (made once in Phase 0/6) and the scripts from Phases 6–7 are in the project. Running on a server needs a Unity **license even in batch mode** — use **GameCI** Docker images to containerize it (8.4).

#### 8.9 The run report (what "automated judgment" produces)

```json
{ "frames_registered_pct": 84.2, "tracks": 17, "scale_status": "auto_scaled", "pass": true }
```

`run.py` writes this every run and **refuses to deploy** when `pass` is false (too few registered frames, no tracks, etc.). That's what turns "automated *steps*" into "automated *judgment*" — the pipeline either hands you a trustworthy world or tells you the clip wasn't good enough, instead of silently emitting garbage.

**Net result:** `python run.py config.yaml` → frames, detections, geometry, a clean ground texture, world-space tracks, a quality report, and an assembled `Recreation.unity` (optionally a runnable build) — the full automatable 90%, with calibration as the single optional human input.

---

## Phase 9 — Validate & measure accuracy

- **Reprojection check (geometry):** take a known ground landmark, map it through `H_{t→W}` in several frames; the world position should be stable across frames. Big variation = bad registration in those frames.
- **Overlay check (visual):** in Unity, screenshot the top-down view and overlay a video frame — the ground texture and a few static features should line up.
- **Scale check:** measure a known object in Unity; it should match reality.
- **Track sanity:** play it back; agents should move smoothly and stay on plausible paths. Jitter → increase smoothing; jumps → registration gaps (interpolate `H`).
- **Iterate the *clip choice* first:** most quality comes from picking a steadier, more top-down, flatter clip (Part 1) before tuning code.

---

## Phase 10 — Optional upgrades

- **Regime C (pose + depth)** for off-ground/relief scenes: COLMAP or VGGT poses + Depth Anything V2 / Video Depth Anything, back-project foot points (Part 2.3-C). Same `tracks_world.json` output → Unity unchanged.
- **SAM2 masks** for precise mover cutouts (cleaner mosaic; better registration).
- **Better mosaic** via OpenCV stitching / multiband blending for large sweeps.
- **One-Euro / Kalman smoothing** for nicer agent motion than a moving average.
- **In-Unity live detection** (opposite direction; optional): Unity **Inference Engine** (`com.unity.ai.inference`, formerly Sentis — Barracuda is deprecated) + YOLO ONNX.
- **Georeferencing:** if you ever get a clip *with* telemetry, you can place the world in real map coordinates.

---

## Worked example (end-to-end)

**Clip:** 70 s pre-recorded shot, drone slowly **orbiting** a parking lot from ~60 m, camera ~40° down. Regime **B**.

1. **Phase 1:** `ffprobe` → 29.97 fps; one continuous shot (no cuts); a small bottom HUD → crop it; normalize to 30 fps; extract 4 fps → ~280 frames.
2. **Phase 2:** YOLO11m tracks ~9 people + ~12 cars → `tracks_image.json`; box/seg masks → `masks/`.
3. **Phase 3B:** pick reference frame #140 (mid-orbit, most lot visible); click 4 lot corners, assign `(0,0),(50,0),(50,30),(0,30)` meters → `H_ref_to_world`; ORB+RANSAC register all 280 frames → `H_per_frame`.
4. **Phase 4B:** median mosaic → clean lot image with all moving cars erased → `ground.png`.
5. **Phase 5:** map foot points through per-frame `H`, smooth → `tracks_world.json`.
6. **Phase 6:** Unity plane scaled to `(5, 1, 3)` (50×30 m), textured with the mosaic; capsules/cubes animate along the tracks.
7. **Phase 7:** drop an `EntryGate` box at the lot entrance, a `Perimeter` box around the edge → `detections.csv` logs cars entering the gate, people crossing the perimeter.

**Result:** a flat, correctly-scaled parking-lot map in Unity with the real cars and pedestrians replaying their paths, and a zone log — built without any 3D reconstruction.

---

## Appendix A — Troubleshooting (expanded)

| Symptom | Likely cause | Fix |
|---|---|---|
| Whole scene slowly slides over time | Used a single homography on a moving camera (Regime A on B footage) | Switch to per-frame registration (Phase 3B) |
| Agents drift / positions wrong on some frames | Registration failed (few inliers) on those frames | Increase frame rate (more overlap); mask overlays; interpolate `H` across bad frames; try frame-to-frame chaining or mosaic registration |
| Ground mosaic has ghost cars/people | Movers not masked before median | Ensure `masks/` exclude movers; rely on the temporal **median**, not mean |
| Mosaic has seams / blur | Pure overwrite blending / drift | Use median; multiband blending; shorter segment; re-anchor registration |
| Off-ground objects placed wrong (rooftops, etc.) | Flat-ground assumption (Regime B) | Accept (ground agents only) or upgrade to Regime C |
| Map looks **mirrored** | World axes don't match Unity's | Flip the sign of one axis in the 4 calibration world points (Appendix B) |
| Wrong overall scale | No telemetry; approximate calibration | Calibrate from a known object size or a satellite-map measurement |
| Few/no detections | Subjects too small / blurry / model too small | Higher-res or lower-altitude clip; larger YOLO (m/l) |
| One person becomes several IDs | Occlusion breaks the track | Accept short tracks, or stitch by time+proximity (Phase 5) |
| Agents jitter | Raw track noise | Increase smoothing window; One-Euro/Kalman (Phase 10) |
| Registration totally fails | Featureless ground (water/tarmac/snow), fast motion, or hidden cut in the clip | Pick a textured/flat/steady clip; split on cuts (Phase 1.2) |
| Trigger logging never fires | Missing kinematic Rigidbody / zone not `isTrigger` | Agent needs Collider + kinematic Rigidbody; zone box `isTrigger = true` |
| Agents float / sink | Wrong `groundY` or prefab pivot | Set `groundY` to plane Y; offset pivot so base is at y=0 |
| VFR weirdness / time mismatch | Variable frame rate or fps mismatch | Normalize fps (Phase 1.3); ensure Python `FPS`/`EXTRACT_FPS` match the files |

---

## Appendix B — Coordinate-system cheat sheet

| Frame | Dims | Axes | Units | Used in |
|---|---|---|---|---|
| Image / pixel | 2D | u→right, v→**down**, origin top-left | pixels | detections, feature matching |
| World / ground | 2D | X→right/east, Z→forward/north | **meters** | the fixed reference; homography output |
| Unity world | 3D | X→right, Y→**up**, Z→forward (**left-handed**) | meters (1 unit) | scene placement |
| Camera (Regime C) | 3D | camera-centered | depends | back-projection |

- **Define world (X,Z) in Unity's convention** so no conversion is needed downstream.
- Agent at world `(X,Z)` → Unity `(X, groundY, Z)`.
- **Mirror fix:** if the scene is flipped, negate one axis in your 4 calibration `world_pts` (e.g., `Z → -Z`) and re-run; re-verify on a known landmark.
- **Homography composition is right-to-left:** `H_{t→W} = H_{R→W} · H_{t→R}` (apply `H_{t→R}` to the pixel first).

---

## Appendix C — Glossary

- **Schematic recreation:** rebuilding a scene as a flat map + simple moving markers, not real 3D geometry.
- **Homography:** a 3×3 projective transform mapping one flat plane to another (image pixels ↔ ground meters, or frame ↔ frame). Exact only for a flat plane.
- **Registration:** computing the transform that aligns one image to another (here, each frame to a reference) via feature matching.
- **Feature matching / ORB / SIFT / RANSAC:** finding corresponding points between images and robustly fitting a transform that ignores outliers.
- **Mosaic / orthomosaic:** many frames warped into one coordinate frame and blended into a single large top-down image.
- **Temporal median (background estimation):** taking the median pixel over time so moving objects (present only briefly) disappear, leaving the static background.
- **Foot point:** bottom-center of a detection box — where the object meets the ground; the point the homography maps correctly.
- **Camera pose / extrinsics (R, t):** the camera's 3D orientation and position; needed for the Regime-C back-projection.
- **Intrinsics (K):** focal length & principal point; map camera-3D ↔ pixels.
- **Back-projection:** turning a pixel + its depth into a 3D point.
- **Drift:** accumulated error when chaining many frame-to-frame transforms.
- **Track:** one object's path across frames under a single ID.
- **NMS:** removes duplicate overlapping detection boxes (inside YOLO).

---

## Appendix D — Tools & references

- Unity (Hub + 6 LTS, URP) — unity.com
- ffmpeg / ffprobe — ffmpeg.org
- PySceneDetect (split edited clips) — scenedetect.com
- Ultralytics YOLO (v11 / v12; detect, track, segment) — docs.ultralytics.com
- OpenCV (`findHomography`, `perspectiveTransform`, `warpPerspective`, ORB/SIFT, RANSAC, stitching) — opencv.org
- SAM 2 (optional fine masks) — github.com/facebookresearch/sam2
- Depth Anything V2 / Video Depth Anything (Regime C depth) — github.com/DepthAnything
- COLMAP (Regime C poses) — colmap.github.io ; VGGT (feed-forward poses+geometry) — github.com/facebookresearch/vggt
- Unity Inference Engine (optional in-engine detection; formerly Sentis) — docs.unity3d.com/Packages/com.unity.ai.inference@latest

---

### Where to start (do this in order)

**Phase 0** (fake data on a plane) → **Phase 1** (clean one continuous shot) → **Phase 2** (track agents) → **Phase 3B** (calibrate + per-frame registration) → **Phase 4B** (median mosaic) → **Phase 5** (map + smooth) → **Phase 6** (agents on the map) → **Phase 7** (zones + log). Pick a **steady, near-top-down, flat-area** clip first — it's the easiest input and makes the per-frame-homography path reliable.

> This v2 supersedes the earlier schematic plan; it's scoped to pre-recorded footage, makes per-frame registration the default, and explains the video→world conversion in depth (Part 2).
