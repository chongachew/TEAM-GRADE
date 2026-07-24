"""
Phase A validation (per-play pipeline redesign, see
C:\\Users\\ricky\\.claude\\plans\\quirky-waddling-octopus.md).

Standalone, one-off script - NOT wired into the queue/stage pipeline. Tests
candidate early/cheap play-boundary signals against real footage before any
architecture change. Currently implements the scoreboard-OCR candidate:
OCR the whole bottom scoreboard strip (not a narrow fixed crop - a spot check
found the down-and-distance sub-graphic isn't always at the same pixel
position) and regex-parse for down & distance text, flagging a boundary
candidate whenever the recognized text changes between sampled frames. Also
parses the main game clock and rejects candidates whose clock moves backward
relative to the last kept one - the signature of a broadcast replay
re-showing an already-counted play, not a new one.

Raw per-frame OCR is embarrassingly parallel (each frame is independent), so
it runs across a process pool - only the debounce/boundary/replay-rejection
logic afterward needs to see frames in order, and that part is cheap pure
Python, not worth parallelizing.

Usage: point FRAMES_DIR at a directory of frame_NNNNNN.jpg files (a
contiguous or evenly-sampled window - doesn't need to be the whole video) and
run directly; no queue/DB/AWS credentials needed for this candidate.
"""

import re
import sys
from multiprocessing import Pool, cpu_count
from pathlib import Path

import cv2

FRAMES_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "window_frames")
FPS = 15  # matches settings.FRAME_EXTRACTION_FPS default

# NOT set to cpu_count()-1: each worker loads its own full EasyOCR model
# (detector + recognizer), and a real run on this machine found only
# ~450MB free RAM available (of 7.9GB total, most already in use by other
# processes) - 11 concurrent workers OOM-crashed 4 of them immediately.
# Override via WORKERS env var if you've confirmed more RAM is actually free.
import os
WORKERS = int(os.environ.get("OCR_WORKERS", "2"))

# Bottom strip containing the whole scoreboard graphic - not a narrow crop of
# one sub-element, since the down-and-distance box's position/presence shifts
# with broadcast state. Fraction-of-height based so it isn't hardcoded to one
# resolution.
BOTTOM_STRIP_TOP_FRAC = 0.85

# [1Il] tolerates EasyOCR's common "1st" -> "Ist"/"lst" misread (capital I or
# lowercase L for the digit 1) - found on real footage: "1st & 10" was never
# matched without this, since the regex silently required a literal digit.
DOWN_DISTANCE_RE = re.compile(
    r"\b([1Il234](?:st|nd|rd|th))\s*&\s*(\d{1,2}|goal)\b", re.IGNORECASE
)

# Main game clock, e.g. "3rd 12:.02" / "4th 13.25" - quarter word followed by
# a garbled MM:SS (OCR reads the colon as a period/comma; tolerate 1-2 stray
# separator chars between minutes and seconds). Same [1Il] "1st" tolerance as
# the down & distance regex.
GAME_CLOCK_RE = re.compile(
    r"\b([1Il234](?:st|nd|rd|th)|OT)\b\D{0,4}(\d{1,2})[:.,]{1,2}(\d{2})\b", re.IGNORECASE
)


def parse_game_clock(text_blob):
    """Returns (quarter_number, seconds_remaining_in_quarter) or None.
    quarter_number: 1-4, or 5 for OT (just needs to sort after 4th)."""
    m = GAME_CLOCK_RE.search(text_blob)
    if not m:
        return None
    q_raw, mm, ss = m.group(1).lower(), m.group(2), m.group(3)
    quarter_map = {"1st": 1, "ist": 1, "lst": 1, "2nd": 2, "3rd": 3, "4th": 4, "ot": 5}
    quarter = quarter_map.get(q_raw)
    if quarter is None:
        return None
    try:
        seconds_remaining = int(mm) * 60 + int(ss)
    except ValueError:
        return None
    if seconds_remaining > 20 * 60:  # sanity bound - reject obvious OCR garbage
        return None
    return (quarter, seconds_remaining)


def frame_index_from_name(path: Path) -> int:
    return int(path.stem.replace("frame_", ""))


_worker_reader = None


def _init_worker():
    # Each process loads its own EasyOCR reader ONCE (not per frame) - model
    # load is the expensive part; reuse it for every frame this worker gets.
    global _worker_reader
    import easyocr
    _worker_reader = easyocr.Reader(["en"], gpu=False, verbose=False)


def _ocr_one_frame(path_str):
    path = Path(path_str)
    idx = frame_index_from_name(path)
    ts = idx / FPS

    img = cv2.imread(str(path))
    if img is None:
        return (idx, ts, None, None, None)

    h, w = img.shape[:2]
    strip = img[int(h * BOTTOM_STRIP_TOP_FRAC):h, 0:w]
    results = _worker_reader.readtext(strip, detail=0)
    text_blob = " ".join(results)

    match = DOWN_DISTANCE_RE.search(text_blob)
    down_distance = match.group(0).lower() if match else None
    clock = parse_game_clock(text_blob)
    return (idx, ts, down_distance, clock, text_blob)


def main():
    frame_paths = sorted(FRAMES_DIR.glob("frame_*.jpg"), key=frame_index_from_name)
    if not frame_paths:
        print(f"No frames found in {FRAMES_DIR}")
        return

    print(f"Running OCR across {len(frame_paths)} frames with {WORKERS} worker processes...")
    with Pool(processes=WORKERS, initializer=_init_worker) as pool:
        raw_results = []
        for i, result in enumerate(pool.imap(_ocr_one_frame, [str(p) for p in frame_paths], chunksize=4)):
            raw_results.append(result)
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(frame_paths)} frames done")

    raw_results.sort(key=lambda r: r[0])  # imap preserves order already, but be explicit
    print(f"OCR pass complete: {len(raw_results)} frames.\n")

    # ---- Sequential pass: debouncing + boundary detection (needs frame order) ----

    prev_confirmed = None
    pending_value = None
    pending_count = 0
    boundary_candidates = []

    for idx, ts, down_distance, clock, _text_blob in raw_results:
        if down_distance == pending_value:
            pending_count += 1
        else:
            pending_value = down_distance
            pending_count = 1

        confirmed = pending_value if pending_count >= 2 else prev_confirmed
        newly_confirmed = confirmed != prev_confirmed
        marker = " <-- CONFIRMED BOUNDARY" if newly_confirmed and confirmed else ""
        clock_str = f"Q{clock[0]} {clock[1]//60}:{clock[1]%60:02d}" if clock else "clock=None"
        print(f"frame {idx} ({ts:.1f}s): raw={down_distance!r} confirmed={confirmed!r} {clock_str}{marker}")

        if newly_confirmed and confirmed:
            boundary_candidates.append((idx, ts, confirmed, clock))

        prev_confirmed = confirmed

    print("\n=== Candidate boundaries (down & distance changed) ===")
    for idx, ts, dd, clock in boundary_candidates:
        clock_str = f"Q{clock[0]} {clock[1]//60}:{clock[1]%60:02d}" if clock else "clock=None"
        print(f"  frame {idx} ({ts:.1f}s): {dd} [{clock_str}]")
    print(f"\nTotal candidate boundaries: {len(boundary_candidates)}")

    print("\n=== Replay-rejection pass (game clock must not move backward) ===")
    kept, rejected = reject_replays(boundary_candidates)
    print(f"Kept: {len(kept)}, rejected as likely replays: {len(rejected)}")
    for idx, ts, dd, clock in rejected:
        clock_str = f"Q{clock[0]} {clock[1]//60}:{clock[1]%60:02d}" if clock else "clock=None"
        print(f"  REJECTED frame {idx} ({ts:.1f}s): {dd} [{clock_str}]")


def reject_replays(candidates):
    """Filters candidates whose game clock doesn't move forward relative to
    the last KEPT candidate - the signature of a replay re-showing an
    already-counted play. Candidates with no readable clock are kept as-is
    (can't judge them, so don't discard information we have no basis to
    reject) and don't update the "last kept" reference point."""
    kept, rejected = [], []
    last_key = None
    for c in candidates:
        idx, ts, dd, clock = c
        if clock is None:
            kept.append(c)
            continue
        key = (clock[0], -clock[1])  # (quarter, -seconds_remaining): increases with real game time
        if last_key is not None and key < last_key:
            rejected.append(c)
            continue
        last_key = key
        kept.append(c)
    return kept, rejected


if __name__ == "__main__":
    main()
