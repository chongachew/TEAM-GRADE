"""
Phase A extension - detection-only pile-motion candidate (see
C:\\Users\\ricky\\.claude\\plans\\adaptive-finding-planet.md and the
"per-play redesign" discussion in quirky-waddling-octopus.md).

snap_detector.py's tracking-pile signal is the strongest validated candidate
so far (84.2% combined recall with OCR), but it structurally requires the
`tracks` table - i.e. the full `tracking` stage (SAM2, ~84 min/video real
measured cost) must already have completed on the WHOLE video. That defeats
the goal of finding play boundaries cheaply and early, before the expensive
per-player stages run.

This module tests whether the same "calm pile, then motion burst" idea can
work on raw `detections` rows instead - i.e. after the much cheaper
`detection` stage alone, with NO persistent track_id across frames.
find_pile() already clusters boxes within one frame without needing
identity (track_id is just a dict-key label there); only
compute_pile_motion()'s common_ids matching genuinely needs identity. This
module replaces that one piece with compute_pile_centroid_motion(): the
displacement of the whole pile's AGGREGATE centroid (mean of all member
box-centroids) frame-to-frame, which needs no cross-frame identity at all.

Real, not-yet-resolved risk: an aggregate centroid is not obviously
equivalent to the identity-based signal minus identity. If players scatter
in different (or opposing) directions at the snap - very plausible, e.g.
o-line surges forward while skill players cut sideways - individual
displacements can partially cancel out in the aggregate centroid, understating
exactly the burst this signal needs to detect. The synthetic self-test below
includes a deliberate worst-case scattering scenario (symmetric opposing
motion) to surface this before any real-data validation is attempted.
"""

import json
import statistics
import sys
from collections import defaultdict

from snap_detector import (
    FPS,
    _box_width,
    _centroid,
    _dist,
    detect_bursts_from_motion_series,
    find_pile,
)

# --- Pile-finding tuning ---
# Inherited as a STARTING GUESS from snap_detector.py's real-data-calibrated
# values (same clustering logic - find_pile() doesn't care whether its input
# came from `tracks` or `detections` rows) - not yet independently verified
# against real detection-only data. See Step 6 of the plan file: re-sweep
# only if real coverage looks clearly bad (well under ~70%).
PILE_RADIUS_IN_BOX_WIDTHS = 8
MIN_PILE_SIZE = 6

# --- Calm-then-burst tuning ---
# Calibrated against real kJM5Uk9DtoQ detection data (2026-07-25), scored
# combined with OCR + the calibrated camera-motion signal against all 57
# ground-truth plays. Turned out to coincidentally land on the exact same
# values snap_detector.py's tracking-pile signal converged to
# (CALM_FRAMES_REQUIRED=4, BURST_MOTION_THRESHOLD=0.8,
# MIN_FRAMES_BETWEEN_SNAPS=7) - NOT assumed in advance (the module docstring's
# cancellation-risk concern meant this needed its own real sweep, not a
# copy), just a real coincidence this time. Contributes marginal recall on
# top of OCR+camera-motion (94.7%->98.2%) at a real precision cost (63.5%->
# 55.4%) - kept as an available, independently-usable signal, not required
# for Phase A's bar to pass (OCR+camera-motion alone already clears it).
CALM_FRAMES_REQUIRED = 4
CALM_MOTION_THRESHOLD = 0.15
BURST_MOTION_THRESHOLD = 0.8
MIN_FRAMES_BETWEEN_SNAPS = 7


def _pile_centroid(pile):
    """Mean of a {key: centroid} dict's centroid values - the pile's single
    aggregate point for this frame."""
    xs = [c[0] for c in pile.values()]
    ys = [c[1] for c in pile.values()]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def compute_pile_centroid_motion(prev_pile, curr_pile, median_width):
    """Displacement of the pile's AGGREGATE centroid between two consecutive
    frames, in box-widths - unlike compute_pile_motion, this needs NO
    persistent identity across frames, since it never matches individual
    members, only the whole cluster's center of mass."""
    if prev_pile is None or curr_pile is None or median_width <= 0:
        return None
    if not prev_pile or not curr_pile:
        return None
    prev_c = _pile_centroid(prev_pile)
    curr_c = _pile_centroid(curr_pile)
    return _dist(prev_c, curr_c) / median_width


def compute_motion_series(detection_rows):
    """detection_rows: flat list of {"frame_index", "detection_index", "bbox"}
    (the real `detections` table shape, no persistent identity across
    frames). Returns {frame_index: motion_value_or_None} - the raw
    aggregate-centroid motion series, exposed separately from
    detect_snaps_from_detections() so callers (e.g. the synthetic self-test
    below) can inspect it directly instead of only the final burst list."""
    by_frame = defaultdict(list)
    for row in detection_rows:
        # detection_index is aliased into the "track_id" dict-key find_pile()
        # expects - no identity meaning, just a per-frame label.
        by_frame[row["frame_index"]].append(
            {"track_id": row["detection_index"], "bbox": row["bbox"]}
        )

    frame_indices = sorted(by_frame.keys())

    piles = {}
    median_widths = {}
    for fi in frame_indices:
        frame_tracks = by_frame[fi]
        piles[fi] = find_pile(
            frame_tracks,
            radius_in_widths=PILE_RADIUS_IN_BOX_WIDTHS,
            min_pile_size=MIN_PILE_SIZE,
        )
        widths = [_box_width(t["bbox"]) for t in frame_tracks]
        median_widths[fi] = statistics.median(widths) if widths else 0

    motion_by_frame = {}
    prev_fi = None
    for fi in frame_indices:
        motion = None
        if prev_fi is not None and fi - prev_fi == 1:
            motion = compute_pile_centroid_motion(piles[prev_fi], piles[fi], median_widths[fi])
        motion_by_frame[fi] = motion
        prev_fi = fi

    return motion_by_frame


def detect_snaps_from_detections(detection_rows):
    """detection_rows: flat list of {"frame_index", "detection_index", "bbox"}
    across the whole video (or a window). Returns list of
    (frame_index, timestamp_seconds) snap candidates from the detection-only
    pile-centroid-motion signal."""
    motion_by_frame = compute_motion_series(detection_rows)
    return detect_bursts_from_motion_series(
        motion_by_frame,
        CALM_MOTION_THRESHOLD,
        BURST_MOTION_THRESHOLD,
        calm_frames_required=CALM_FRAMES_REQUIRED,
        min_frames_between=MIN_FRAMES_BETWEEN_SNAPS,
    )


def _synthetic_calm_phase(rows, frame, base_positions, box_w, num_frames=30):
    import random
    random.seed(0)
    for _ in range(num_frames):
        for tid, (x, y) in enumerate(base_positions):
            jx, jy = random.uniform(-1, 1), random.uniform(-1, 1)
            rows.append({
                "frame_index": frame, "detection_index": tid,
                "bbox": [x + jx, y + jy, x + jx + box_w, y + jy + box_w],
            })
        frame += 1
    return frame


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            detection_rows = json.load(f)
        snaps = detect_snaps_from_detections(detection_rows)
        print(f"Found {len(snaps)} snap candidates:")
        for fi, ts in snaps:
            print(f"  frame {fi} ({ts:.1f}s)")
        sys.exit(0)

    # --- Synthetic self-test, run with no args ---
    box_w = 40
    base_positions = [(500 + i * 10, 400) for i in range(8)]

    # Scenario 1: uniform translation (same shape as snap_detector.py's
    # synthetic test) - every player moves by the SAME delta each burst
    # frame, so the aggregate centroid moves by that same delta too. This is
    # the best case for a centroid-based signal - confirms the mechanics
    # work at all before stress-testing the harder case below.
    rows_uniform = []
    frame = _synthetic_calm_phase(rows_uniform, 0, base_positions, box_w)
    for step in range(5):
        for tid, (x, y) in enumerate(base_positions):
            dx = step * 45
            rows_uniform.append({
                "frame_index": frame, "detection_index": tid,
                "bbox": [x + dx, y, x + dx + box_w, y + box_w],
            })
        frame += 1

    snaps_uniform = detect_snaps_from_detections(rows_uniform)
    motion_uniform = compute_motion_series(rows_uniform)
    peak_uniform = max((v for v in motion_uniform.values() if v is not None), default=0.0)
    print(f"Uniform-translation scenario: found {len(snaps_uniform)} snap candidate(s): {snaps_uniform}")
    print(f"  peak motion value: {peak_uniform:.3f}")
    assert len(snaps_uniform) == 1, f"Expected exactly 1 snap in the uniform scenario, got {len(snaps_uniform)}"
    assert 28 <= snaps_uniform[0][0] <= 32, f"Expected the snap around frame 30, got frame {snaps_uniform[0][0]}"
    print("  PASS")

    # Scenario 2: scattering (worst-case stress test) - half the players
    # surge one way, half surge the exact opposite way, same magnitude. Real
    # individual motion is large, but symmetric opposing displacement can
    # cancel out entirely in the aggregate centroid. This is deliberately
    # NOT asserted pass/fail - it's diagnostic, per the plan file: if the
    # peak motion here is well below the uniform case's peak, that's a real,
    # early warning that the centroid-of-pile approach may be too insensitive
    # to non-uniform real snap motion, worth knowing before any real-data
    # validation effort.
    rows_scatter = []
    frame = _synthetic_calm_phase(rows_scatter, 0, base_positions, box_w)
    for step in range(5):
        for tid, (x, y) in enumerate(base_positions):
            dx = step * 45 if tid % 2 == 0 else -step * 45
            rows_scatter.append({
                "frame_index": frame, "detection_index": tid,
                "bbox": [x + dx, y, x + dx + box_w, y + box_w],
            })
        frame += 1

    snaps_scatter = detect_snaps_from_detections(rows_scatter)
    motion_scatter = compute_motion_series(rows_scatter)
    peak_scatter = max((v for v in motion_scatter.values() if v is not None), default=0.0)
    print(f"\nScattering scenario: found {len(snaps_scatter)} snap candidate(s): {snaps_scatter}")
    print(f"  peak motion value: {peak_scatter:.3f} (uniform case's peak was {peak_uniform:.3f})")
    if peak_scatter < peak_uniform * 0.5:
        print("  WARNING: scattering peak is well below the uniform case's peak - "
              "real risk of the aggregate-centroid signal being too insensitive to "
              "non-uniform snap motion. Consider a nearest-neighbor-matched "
              "displacement fallback before real-data validation.")
    else:
        print("  Scattering peak is not dramatically suppressed relative to the "
              "uniform case - no early red flag from this synthetic check.")
