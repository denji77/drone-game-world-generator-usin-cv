"""Smallest runnable check of the pipeline math - no video, no torch needed.
Run:  python -m scripts.selfcheck
"""
import numpy as np

from .common import mask_path, video_frame_of
from .map_to_world import h_at, smooth, split_on_gaps, stitch_segments


def main():
    # frame-index convention
    assert mask_path("masks", 12).replace("\\", "/").endswith("masks/frame_000012.png")
    assert video_frame_of(3, 6) == 18

    # h_at: midway between identity and a +3m X-translation -> +1.5m
    H_stack = np.array([np.eye(3), np.float32([[1, 0, 3], [0, 1, 0], [0, 0, 1]])])
    H = h_at(3, 6, H_stack)  # video frame 3, stride 6 -> halfway
    pt = H @ np.array([0.0, 0.0, 1.0])
    assert abs(pt[0] / pt[2] - 1.5) < 1e-6, pt

    # h_at clamps beyond the last sample
    assert np.allclose(h_at(999, 6, H_stack), H_stack[1] / H_stack[1][2, 2])

    # split_on_gaps: 0.9s gap with max_dt=0.5 -> two segments
    pts = [{"t": 0.0}, {"t": 0.1}, {"t": 0.2}, {"t": 1.1}]
    segs = split_on_gaps(pts, 0.5)
    assert [len(s) for s in segs] == [3, 1]

    # smooth: constant sequence is unchanged, length preserved
    assert smooth([2.0] * 7, 5) == [2.0] * 7

    # stitch: person moving 2 m over 1 s (2 m/s <= 3) joins; 10 m over 0.3 s does not
    a = ("person", [{"t": 0.0, "x": 0.0, "z": 0.0}, {"t": 1.0, "x": 1.0, "z": 0.0}])
    b = ("person", [{"t": 2.0, "x": 3.0, "z": 0.0}])
    c = ("person", [{"t": 2.3, "x": 13.0, "z": 0.0}])
    out = stitch_segments([a, b], {"person": 3.0}, 3.0)
    assert len(out) == 1 and len(out[0][1]) == 3
    out = stitch_segments([a, c], {"person": 3.0}, 3.0)
    assert len(out) == 2

    print("selfcheck OK")


if __name__ == "__main__":
    main()
