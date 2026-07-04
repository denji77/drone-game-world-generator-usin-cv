"""Phase 3B step 2: chained per-frame registration with re-anchoring.

- Chain outward from the reference: consecutive extracted frames always overlap, so the
  chain never starves (direct-to-reference alone fails on orbits/sweeps).
- Failures are MARKED (None) and interpolated between registered neighbors afterwards -
  never given a stale copy of the previous H (that freezes the geometry, then snaps).
- Re-anchor: every reanchor_every frames (and whenever the chain broke) try a direct
  match to the reference to kill accumulated drift.
- inliers.npy is the per-frame quality record the gate reads.
"""
import cv2
import numpy as np

from .common import log, die, mask_path, video_frame_of


def register_all(frames, stride, ref_idx, H_rw, geo, masks_dir):
    """Returns (H_world (N,3,3) mapping pixel_t -> world meters, inliers (N,))."""
    N = len(frames)
    min_matches = int(geo["min_matches"])
    min_inliers = int(geo["min_inliers"])
    reanchor_k = int(geo["reanchor_every"])

    orb = cv2.ORB_create(int(geo.get("orb_features", 5000)))
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    cache = {}

    def feats(i):
        if i not in cache:
            g = cv2.imread(frames[i], 0)
            m = cv2.imread(mask_path(masks_dir, video_frame_of(i, stride)), 0)
            keep = None
            if m is not None and m.shape == g.shape:
                keep = cv2.bitwise_not(m)  # white = mover = excluded from matching
            cache[i] = orb.detectAndCompute(g, keep)
        return cache[i]

    def register(i, j):
        """ORB+RANSAC homography frame i -> frame j. Returns (H, n_inliers) or (None, 0)."""
        (kpA, desA), (kpB, desB) = feats(i), feats(j)
        if desA is None or desB is None:
            return None, 0
        good = [m for pair in bf.knnMatch(desA, desB, k=2) if len(pair) == 2
                for m, nn in [pair] if m.distance < 0.75 * nn.distance]
        if len(good) < min_matches:
            return None, 0
        src = np.float32([kpA[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kpB[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        return (H, int(inl.sum())) if H is not None else (None, 0)

    H_to_ref = [None] * N
    inliers = [0] * N
    H_to_ref[ref_idx] = np.eye(3)
    inliers[ref_idx] = 10 ** 6
    if feats(ref_idx)[1] is None:
        die("reference frame has no ORB features - featureless ground? pick another clip")

    for step in (+1, -1):
        i = ref_idx + step
        while 0 <= i < N:
            H_step, inl = register(i, i - step)  # frame i -> its already-registered neighbor
            if H_step is not None and H_to_ref[i - step] is not None:
                H_to_ref[i] = H_to_ref[i - step] @ H_step  # H_{i->R} = H_{i-1->R} . H_{i->i-1}
                inliers[i] = min(inl, inliers[i - step])   # a chain is its weakest link
            # re-anchor periodically AND whenever the chain broke
            if H_to_ref[i] is None or abs(i - ref_idx) % reanchor_k == 0:
                H_dir, inl_dir = register(i, ref_idx)
                if H_dir is not None and inl_dir >= min_inliers:
                    H_to_ref[i], inliers[i] = H_dir, inl_dir
            # bound memory: chain only ever needs the neighbor + the reference
            for k in list(cache):
                if k not in (ref_idx, i, i - step):
                    del cache[k]
            i += step
            if abs(i - ref_idx) % 50 == 0:
                done = sum(1 for H in H_to_ref if H is not None)
                log("geometry", f"registered {done}/{N}")

    # interpolate the frames that stayed None - NEVER reuse a stale H
    norm = lambda H: H / H[2, 2]
    reg = [i for i in range(N) if H_to_ref[i] is not None]
    if not reg:
        die("no frames registered at all - unusable clip (blur/featureless/cuts)")
    n_interp = N - len(reg)
    for i in range(N):
        if H_to_ref[i] is None:
            lo = max((r for r in reg if r < i), default=None)
            hi = min((r for r in reg if r > i), default=None)
            if lo is None:
                H_to_ref[i] = H_to_ref[hi]
            elif hi is None:
                H_to_ref[i] = H_to_ref[lo]
            else:
                a = (i - lo) / (hi - lo)
                H_to_ref[i] = (1 - a) * norm(H_to_ref[lo]) + a * norm(H_to_ref[hi])

    log("geometry", f"done: {len(reg)}/{N} registered, {n_interp} interpolated")
    H_world = np.array([H_rw @ H for H in H_to_ref])  # compose: pixel_t -> world meters
    return H_world, np.array(inliers, np.int64)
