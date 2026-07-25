"""
Phase A validation - combine any number of candidate boundary signals into
one merged list (union, deduplicated), then score the combination against
the same ground truth used to score each signal individually.

Generalized (see adaptive-finding-planet.md, Step 4) from an original
2-source-only (OCR + tracking) version to accept N sources, since Phase A's
extension added two more cheap candidates (detection-pile-centroid-motion,
formation-shape) plus camera-motion.

Usage: python combine_candidates.py <ground_truth.json> <ocr_log> <frame_style_log> [<frame_style_log> ...]
  <ocr_log>: play_boundary_validation.py's stdout (parsed via parse_ocr_candidates)
  <frame_style_log>: any script whose stdout has "frame N (T.Ts)" lines
    (snap_detector.py, detection_pile_motion.py, formation_signal.py,
    camera_motion_candidates.py all already match this format) - one or more
"""

import re
import sys

DEDUP_WINDOW_SECONDS = 5.0  # candidates from different signals within this window
                             # of each other are treated as the same real play,
                             # not counted twice


def parse_ocr_candidates(path):
    """Parses play_boundary_validation.py's "Kept: N, rejected..." section
    output - falls back to the "Candidate boundaries" section if no replay
    rejection section is present."""
    with open(path) as f:
        text = f.read()
    section = text.split("=== Candidate boundaries")[-1].split("=== Replay-rejection")[0]
    times = [float(m) for m in re.findall(r"\((\d+\.\d+)s\)", section)]
    return times


def parse_tracking_candidates(path):
    """Parses any "frame N (T.Ts)"-style stdout - shared by snap_detector.py,
    detection_pile_motion.py, formation_signal.py, and
    camera_motion_candidates.py."""
    with open(path) as f:
        text = f.read()
    return [float(m) for m in re.findall(r"frame \d+ \((\d+\.\d+)s\)", text)]


def merge_and_dedup(*candidate_lists, window=DEDUP_WINDOW_SECONDS):
    """Union of any number of candidate lists, merging any two candidates
    within `window` seconds of each other into one (keeps the earlier
    timestamp)."""
    combined = sorted(t for lst in candidate_lists for t in lst)
    merged = []
    for t in combined:
        if merged and t - merged[-1] <= window:
            continue  # treat as the same real play already captured
        merged.append(t)
    return merged


if __name__ == "__main__":
    ground_truth_path, ocr_log = sys.argv[1], sys.argv[2]
    frame_style_logs = sys.argv[3:]
    if not frame_style_logs:
        print("Usage: python combine_candidates.py <ground_truth.json> <ocr_log> <frame_style_log> [<frame_style_log> ...]")
        sys.exit(1)

    ocr_candidates = parse_ocr_candidates(ocr_log)
    frame_style_candidate_lists = [parse_tracking_candidates(log) for log in frame_style_logs]
    merged = merge_and_dedup(ocr_candidates, *frame_style_candidate_lists)

    print(f"OCR candidates: {len(ocr_candidates)}")
    for log, cands in zip(frame_style_logs, frame_style_candidate_lists):
        print(f"{log}: {len(cands)} candidates")
    print(f"Merged (deduped within {DEDUP_WINDOW_SECONDS}s): {len(merged)}")
    print()

    # Reuse score_boundary_candidates.py's scoring logic directly.
    sys.path.insert(0, ".")
    from score_boundary_candidates import load_ground_truth, score

    ground_truth = load_ground_truth(ground_truth_path)
    for tol in (2.0, 5.0, 10.0):
        result = score(ground_truth, merged, tolerance=tol)
        print(f"--- Tolerance +/-{tol:.0f}s ---")
        print(f"Recall:    {result['recall']*100:.1f}% ({result['matched_count']}/{result['ground_truth_count']})")
        fp = len(result["false_positive_candidates"])
        print(f"Precision: {(len(merged)-fp)/len(merged)*100:.1f}% ({len(merged)-fp}/{len(merged)})")
        print()

    final = score(ground_truth, merged, tolerance=10.0)
    print(f"Missed real boundaries even at +/-10s ({len(final['missed_ground_truth'])}):")
    for t in final["missed_ground_truth"]:
        print(f"  {t:.1f}s")
