"""
Phase A validation - combine the OCR and tracking-based candidate signals
into one merged list (union, deduplicated), then score the combination
against the same ground truth used to score each signal individually.

Usage: python combine_candidates.py <ocr_log> <tracking_log> <ground_truth.json>
"""

import re
import sys

DEDUP_WINDOW_SECONDS = 5.0  # candidates from the two signals within this window
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
    """Parses snap_detector.py's "frame N (T.Ts)" output lines."""
    with open(path) as f:
        text = f.read()
    return [float(m) for m in re.findall(r"frame \d+ \((\d+\.\d+)s\)", text)]


def merge_and_dedup(list_a, list_b, window=DEDUP_WINDOW_SECONDS):
    """Union of both lists, merging any two candidates within `window`
    seconds of each other into one (keeps the earlier timestamp)."""
    combined = sorted(list_a + list_b)
    merged = []
    for t in combined:
        if merged and t - merged[-1] <= window:
            continue  # treat as the same real play already captured
        merged.append(t)
    return merged


if __name__ == "__main__":
    ocr_log, tracking_log, ground_truth_path = sys.argv[1], sys.argv[2], sys.argv[3]

    ocr_candidates = parse_ocr_candidates(ocr_log)
    tracking_candidates = parse_tracking_candidates(tracking_log)
    merged = merge_and_dedup(ocr_candidates, tracking_candidates)

    print(f"OCR candidates: {len(ocr_candidates)}")
    print(f"Tracking candidates: {len(tracking_candidates)}")
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
