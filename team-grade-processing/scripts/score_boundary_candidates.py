"""
Phase A validation scoring: compare a candidate signal's detected play
boundaries against hand-labeled ground truth (see
scripts/boundary_labeler.html), computing recall/precision within a
tolerance window - the actual pass/fail bar from
C:\\Users\\ricky\\.claude\\plans\\quirky-waddling-octopus.md.

Usage: python score_boundary_candidates.py ground_truth.json candidates.txt
  ground_truth.json: list of {"timestamp_seconds": float, ...} (labeler export)
  candidates.txt: play_boundary_validation.py's stdout log (parses the
    "=== Candidate boundaries ===" section's "frame N (T.Ts): ..." lines)
"""

import json
import re
import sys

TOLERANCE_SECONDS = 2.0


def load_ground_truth(path):
    with open(path) as f:
        data = json.load(f)
    return sorted(d["timestamp_seconds"] for d in data)


def load_candidates(path):
    with open(path) as f:
        text = f.read()
    section = text.split("=== Candidate boundaries")[-1]
    times = [float(m) for m in re.findall(r"\((\d+\.\d+)s\)", section)]
    return sorted(times)


def score(ground_truth, candidates, tolerance=TOLERANCE_SECONDS):
    matched_gt = set()
    matched_cand = set()

    for ci, c in enumerate(candidates):
        best_gi, best_dist = None, tolerance + 1
        for gi, g in enumerate(ground_truth):
            if gi in matched_gt:
                continue
            dist = abs(c - g)
            if dist <= tolerance and dist < best_dist:
                best_gi, best_dist = gi, dist
        if best_gi is not None:
            matched_gt.add(best_gi)
            matched_cand.add(ci)

    recall = len(matched_gt) / len(ground_truth) if ground_truth else 0.0
    precision = len(matched_cand) / len(candidates) if candidates else 0.0

    missed = [g for gi, g in enumerate(ground_truth) if gi not in matched_gt]
    false_positives = [c for ci, c in enumerate(candidates) if ci not in matched_cand]

    return {
        "recall": recall,
        "precision": precision,
        "ground_truth_count": len(ground_truth),
        "candidate_count": len(candidates),
        "matched_count": len(matched_gt),
        "missed_ground_truth": missed,
        "false_positive_candidates": false_positives,
    }


def main():
    gt_path, cand_path = sys.argv[1], sys.argv[2]
    ground_truth = load_ground_truth(gt_path)
    candidates = load_candidates(cand_path)

    # Multiple tolerance windows: manual labels have their own imprecision
    # (confirmed by the labeler - "wasn't very accurate, but within the
    # vicinity"), so a single strict +/-2s score would conflate "the signal
    # missed a real boundary" with "the label itself was a few seconds off
    # from the true snap frame." Reporting recall across a few tolerances
    # separates those two explanations - if recall jumps sharply between two
    # tolerances, that gap is mostly label slop, not signal failure.
    tolerances = [2.0, 5.0, 10.0]
    print(f"Ground truth boundaries: {len(ground_truth)}")
    print(f"Candidate boundaries:    {len(candidates)}\n")

    for tol in tolerances:
        result = score(ground_truth, candidates, tolerance=tol)
        print(f"--- Tolerance +/-{tol:.0f}s ---")
        print(f"Recall:    {result['recall']*100:.1f}% ({result['matched_count']}/{result['ground_truth_count']})")
        print(f"Precision: {result['precision']*100:.1f}% ({len(candidates) - len(result['false_positive_candidates'])}/{result['candidate_count']})")
        print()

    final = score(ground_truth, candidates, tolerance=tolerances[-1])
    print(f"Missed real boundaries even at +/-{tolerances[-1]:.0f}s ({len(final['missed_ground_truth'])}):")
    for t in final["missed_ground_truth"]:
        print(f"  {t:.1f}s")
    print(f"\nFalse-positive candidates even at +/-{tolerances[-1]:.0f}s ({len(final['false_positive_candidates'])}):")
    for t in final["false_positive_candidates"]:
        print(f"  {t:.1f}s")


if __name__ == "__main__":
    main()
