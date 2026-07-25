"""
Phase A extension - pre-snap formation / neutral-zone-gap candidate (see
C:\\Users\\ricky\\.claude\\plans\\adaptive-finding-planet.md, "Fourth
candidate added mid-session, user's idea").

NCAA/NFL rules require the offense to be set/stationary for ~1s before the
snap, lined up in a legal formation (>=7 players on the line of scrimmage)
with a required gap - the neutral zone - between offense and defense. This
is a structurally different signal from the motion-based candidates
(snap_detector.py, detection_pile_motion.py): instead of asking "did
something move a lot," it asks "does this frame's player layout look like
two lines facing off across a gap, and did that gap just collapse."

Uses the same {"frame_index", "detection_index", "bbox"} `detections` rows
detection_pile_motion.py uses - no persistent track identity needed, and no
extra data pull beyond what that signal already requires.

Per frame: split player centroids into two groups by finding the largest
gap along whichever axis (x or y) maximizes gap-to-spread ratio - the
presumed line-of-scrimmage-perpendicular axis - then check both groups are
big enough and the gap is real. The "motion" scalar fed into the shared
calm-then-burst detector (borrowed as-is from snap_detector.py) is the
gap's frame-to-frame SHRINKAGE, not displacement - a "burst" here means the
neutral zone collapsing quickly after being held open for the calm period.

Known, deliberate v1 simplification: if the two-group split fails outright
(e.g. players have already started colliding right at the snap), that
frame contributes no shrinkage measurement at all (treated as "no usable
measurement," same as a motion-signal gap) rather than itself counting as
a burst. This may under-detect snaps where formation recognition breaks
down before a shrinkage value can be computed - worth revisiting only if
real coverage numbers (see the plan file's Step 6) show this happening
often, not assumed to be a problem up front.
"""

import json
import statistics
import sys
from collections import defaultdict

from snap_detector import FPS, _box_width, _centroid, detect_bursts_from_motion_series

# --- Formation-shape tuning ---
# Calibrated against real kJM5Uk9DtoQ detection data (2026-07-25): the
# original guess (MIN_GROUP_SIZE=4, i.e. NCAA/NFL's "7 on the line" rule
# with some slack) assumed far more players get detected per frame than
# this zero-shot, un-fine-tuned RF-DETR pass actually finds - real median
# is only ~9 detected players/frame (not 22), so requiring 2x4=8 total
# players just to attempt a split left coverage at 0.2% (25/15304 frames) -
# essentially broken. A real coverage sweep across group-size/gap/span
# combinations found MIN_GROUP_SIZE=3/MIN_GAP_BOX_WIDTHS=1.5/
# MIN_SPAN_BOX_WIDTHS=5.0 gives ~7.1% coverage (1093/15304 frames) - in the
# right ballpark for "only genuine pre-snap moments should match" without
# requiring an unrealistic number of detected players. Not yet calibrated
# against the actual 57-play ground truth (that's the real target, done via
# the calm/burst constants below) - this is a coverage sanity check only.
MIN_GROUP_SIZE = 3
MIN_GAP_BOX_WIDTHS = 1.5  # minimum neutral-zone-like gap (box-widths) to count as two separate lines, not one blob
MIN_SPAN_BOX_WIDTHS = 5.0  # formation should be spread along the line of scrimmage, not narrow

# --- Calm-then-burst tuning (shared detector, different scalar meaning: gap SHRINKAGE, not displacement) ---
# Calibrated against real kJM5Uk9DtoQ detection data (2026-07-25), scored
# combined with OCR + the calibrated camera-motion signal against all 57
# ground-truth plays. The original placeholder (CALM_FRAMES_REQUIRED=15, a
# literal ~1s "set" requirement) left this signal almost entirely inert (1
# raw candidate across the whole video) - real coverage is only ~7% of
# frames (see MIN_GROUP_SIZE's comment above), so requiring 15 CONSECUTIVE
# valid+calm frames was far too strict given how often the formation-finder
# itself returns no reading at all. Loosening to CALM_FRAMES_REQUIRED=4 and
# BURST_SHRINK_THRESHOLD=0.3 raised it to a real, usable signal (26
# candidates). Contributes marginal recall on top of OCR+camera-motion
# (94.7%->96.5%) at a real precision cost (63.5%->59.8%) - kept as an
# available, independently-usable signal, not required for Phase A's bar to
# pass (OCR+camera-motion alone already clears it).
CALM_FRAMES_REQUIRED = 4
CALM_SHRINK_THRESHOLD = 0.15  # gap shrinkage per frame (box-widths) below which counts as "still held"
BURST_SHRINK_THRESHOLD = 0.3  # gap shrinkage per frame above which counts as "collapsing"
MIN_FRAMES_BETWEEN_SNAPS = 7


def find_formation(frame_players, min_group_size=MIN_GROUP_SIZE,
                    min_gap_box_widths=MIN_GAP_BOX_WIDTHS,
                    min_span_box_widths=MIN_SPAN_BOX_WIDTHS):
    """frame_players: list of {"track_id"/label, "bbox"} for ONE frame.
    Returns a dict describing a two-group split (group sizes, gap, span), or
    None if this frame doesn't look like a valid pre-snap formation - most
    likely a single clustered pileup (a tackle), not two lines facing off."""
    if len(frame_players) < 2 * min_group_size:
        return None

    widths = [_box_width(p["bbox"]) for p in frame_players]
    median_width = statistics.median(widths)
    if median_width <= 0:
        return None

    centroids = [_centroid(p["bbox"]) for p in frame_players]

    best = None
    for axis in (0, 1):  # 0 = x, 1 = y
        values = sorted(c[axis] for c in centroids)
        total_span = values[-1] - values[0]
        if total_span <= 0:
            continue
        best_gap, best_gap_idx = 0.0, None
        for i in range(1, len(values)):
            gap = values[i] - values[i - 1]
            if gap > best_gap:
                best_gap, best_gap_idx = gap, i
        if best_gap_idx is None:
            continue
        gap_ratio = best_gap / total_span
        split_value = (values[best_gap_idx - 1] + values[best_gap_idx]) / 2.0
        if best is None or gap_ratio > best["gap_ratio"]:
            best = {"axis": axis, "split_value": split_value, "gap_ratio": gap_ratio, "gap_px": best_gap}

    if best is None:
        return None

    axis = best["axis"]
    group_a = [c for c in centroids if c[axis] < best["split_value"]]
    group_b = [c for c in centroids if c[axis] >= best["split_value"]]

    if len(group_a) < min_group_size or len(group_b) < min_group_size:
        return None

    gap_box_widths = best["gap_px"] / median_width
    if gap_box_widths < min_gap_box_widths:
        return None

    other_axis = 1 - axis
    other_values = [c[other_axis] for c in centroids]
    span_box_widths = (max(other_values) - min(other_values)) / median_width
    if span_box_widths < min_span_box_widths:
        return None

    return {
        "group_a_count": len(group_a),
        "group_b_count": len(group_b),
        "gap_box_widths": gap_box_widths,
        "span_box_widths": span_box_widths,
        "split_axis": axis,
    }


def compute_shrink_series(detection_rows, **find_formation_kwargs):
    """detection_rows: flat list of {"frame_index", "detection_index", "bbox"}.
    Returns {frame_index: shrinkage_value_or_None} - how much the neutral-
    zone gap shrank relative to the PREVIOUS frame (0 if it held steady or
    grew), in box-widths. None where no valid formation could be found in
    either the current or previous frame."""
    by_frame = defaultdict(list)
    for row in detection_rows:
        by_frame[row["frame_index"]].append(
            {"track_id": row["detection_index"], "bbox": row["bbox"]}
        )
    frame_indices = sorted(by_frame.keys())

    gap_by_frame = {}
    for fi in frame_indices:
        formation = find_formation(by_frame[fi], **find_formation_kwargs)
        gap_by_frame[fi] = formation["gap_box_widths"] if formation else None

    shrink_by_frame = {}
    prev_fi = None
    for fi in frame_indices:
        shrink = None
        if prev_fi is not None and fi - prev_fi == 1:
            prev_gap, curr_gap = gap_by_frame[prev_fi], gap_by_frame[fi]
            if prev_gap is not None and curr_gap is not None:
                shrink = max(0.0, prev_gap - curr_gap)
        shrink_by_frame[fi] = shrink
        prev_fi = fi

    return shrink_by_frame


def detect_snaps_from_formations(detection_rows):
    """detection_rows: flat list of {"frame_index", "detection_index", "bbox"}
    across the whole video (or a window). Returns list of
    (frame_index, timestamp_seconds) snap candidates from the pre-snap
    formation / neutral-zone-gap-collapse signal."""
    shrink_by_frame = compute_shrink_series(detection_rows)
    return detect_bursts_from_motion_series(
        shrink_by_frame,
        CALM_SHRINK_THRESHOLD,
        BURST_SHRINK_THRESHOLD,
        calm_frames_required=CALM_FRAMES_REQUIRED,
        min_frames_between=MIN_FRAMES_BETWEEN_SNAPS,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            detection_rows = json.load(f)
        snaps = detect_snaps_from_formations(detection_rows)
        print(f"Found {len(snaps)} snap candidates:")
        for fi, ts in snaps:
            print(f"  frame {fi} ({ts:.1f}s)")
        sys.exit(0)

    # --- Synthetic self-test, run with no args ---
    box_w = 40

    # Scenario 1: two lines of 7, held stationary with a real gap for ~1s
    # (30 frames), then the gap collapses over a few frames (the "snap").
    import random
    random.seed(0)

    rows_formation = []
    offense_x = [500 + i * 120 for i in range(7)]  # wide spread across x (720px = 18 box-widths), offense
    defense_x = [500 + i * 120 for i in range(7)]  # same x-spread, defense
    offense_y = 400
    defense_y_start = 700  # 300px gap = 7.5 box-widths, above MIN_GAP_BOX_WIDTHS

    frame = 0
    # Calm phase: formation held, gap steady
    for _ in range(30):
        for tid, x in enumerate(offense_x):
            jx, jy = random.uniform(-1, 1), random.uniform(-1, 1)
            rows_formation.append({
                "frame_index": frame, "detection_index": tid,
                "bbox": [x + jx, offense_y + jy, x + jx + box_w, offense_y + jy + box_w],
            })
        for tid, x in enumerate(defense_x):
            jx, jy = random.uniform(-1, 1), random.uniform(-1, 1)
            rows_formation.append({
                "frame_index": frame, "detection_index": 100 + tid,
                "bbox": [x + jx, defense_y_start + jy, x + jx + box_w, defense_y_start + jy + box_w],
            })
        frame += 1

    # Snap phase: defense (and offense) surge toward each other, gap collapses
    for step in range(5):
        defense_y = defense_y_start - step * 60  # gap closes by 60px/frame
        for tid, x in enumerate(offense_x):
            rows_formation.append({
                "frame_index": frame, "detection_index": tid,
                "bbox": [x, offense_y, x + box_w, offense_y + box_w],
            })
        for tid, x in enumerate(defense_x):
            rows_formation.append({
                "frame_index": frame, "detection_index": 100 + tid,
                "bbox": [x, defense_y, x + box_w, defense_y + box_w],
            })
        frame += 1

    snaps_formation = detect_snaps_from_formations(rows_formation)
    print(f"Formation-collapse scenario: found {len(snaps_formation)} snap candidate(s): {snaps_formation}")
    assert len(snaps_formation) == 1, f"Expected exactly 1 snap in the formation-collapse scenario, got {len(snaps_formation)}"
    assert 28 <= snaps_formation[0][0] <= 33, f"Expected the snap around frame 30, got frame {snaps_formation[0][0]}"
    print("  PASS")

    # Scenario 2: a single dense pileup (a tackle, NOT a formation) - random
    # jitter around one center point, no two-line structure at all. This
    # should never be recognized as a formation in the first place.
    rows_pileup = []
    center_x, center_y = 600, 500
    for f in range(40):
        for tid in range(14):
            jx = random.uniform(-60, 60)
            jy = random.uniform(-60, 60)
            rows_pileup.append({
                "frame_index": f, "detection_index": tid,
                "bbox": [center_x + jx, center_y + jy, center_x + jx + box_w, center_y + jy + box_w],
            })

    by_frame_pileup = defaultdict(list)
    for row in rows_pileup:
        by_frame_pileup[row["frame_index"]].append(
            {"track_id": row["detection_index"], "bbox": row["bbox"]}
        )
    formations_found = sum(1 for fi, rows in by_frame_pileup.items() if find_formation(rows) is not None)
    snaps_pileup = detect_snaps_from_formations(rows_pileup)
    print(f"\nNon-formation pileup scenario: {formations_found}/{len(by_frame_pileup)} frames recognized as a formation")
    print(f"  found {len(snaps_pileup)} snap candidate(s): {snaps_pileup}")
    assert formations_found == 0, f"Expected 0 frames to be recognized as a formation in the pileup scenario, got {formations_found}"
    assert len(snaps_pileup) == 0, f"Expected 0 snap candidates in the pileup scenario, got {len(snaps_pileup)}"
    print("  PASS")
