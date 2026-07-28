"""
Play-boundary detection (Phase B of the per-play pipeline redesign).

Productionizes the two signals validated in scripts/play_boundary_validation.py
and scripts/camera_motion_candidates.py (see
C:\\Users\\ricky\\.claude\\plans\\quirky-waddling-octopus.md and
adaptive-finding-planet.md): OCR reads a broadcast scoreboard's
down-and-distance graphic; camera-motion flags real per-frame homography
spikes. Combined, they hit 94.7% recall / 63.5% precision against 57 real
hand-labeled plays, using nothing more than extracted frame JPEGs plus the
`camera_motion` table motion_compensation_stage.py already writes - no
`detection`/`tracking` stage needed.

`ingest/stages/play_detection_stage.py` calls these three functions in
order (OCR candidates, camera-motion candidates from already-written rows,
merge) then converts the result into frame ranges for the `plays` table.
"""

import logging
import os
import re
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

from config import settings

logger = logging.getLogger(__name__)

FPS = settings.FRAME_EXTRACTION_FPS

# --- OCR candidate tuning (calibrated against real footage, see
# scripts/play_boundary_validation.py's session notes) ---
BOTTOM_STRIP_TOP_FRAC = 0.85
DOWN_DISTANCE_RE = re.compile(
    r"\b([1Il234](?:st|nd|rd|th))\s*&\s*(\d{1,2}|goal)\b", re.IGNORECASE
)
GAME_CLOCK_RE = re.compile(
    r"\b([1Il234](?:st|nd|rd|th)|OT)\b\D{0,4}(\d{1,2})[:.,]{1,2}(\d{2})\b", re.IGNORECASE
)
# Each worker loads its own full EasyOCR model - keep this low, not
# cpu_count()-1, or concurrent model loads can OOM a modest worker instance.
OCR_WORKERS = int(os.environ.get("OCR_WORKERS", "2"))
# Phase A's own validation methodology (scripts/play_boundary_validation.py)
# scored OCR at 1fps, never at the full FRAME_EXTRACTION_FPS (15fps in
# production) - that distinction got lost when this was productionized, and
# running OCR on every single frame instead of a 1fps sample was the real
# bottleneck in play_detection_stage.py's runtime on a real video (down-
# and-distance doesn't change frame-to-frame; the debounce logic below only
# ever needed 2 consecutive SAMPLED reads to confirm a change, not frame-
# adjacent ones). Real production evidence (2026-07-27): 4,681 frames at
# 15fps meant 4,681 EasyOCR calls for a signal that only changes a few dozen
# times per video.
OCR_SAMPLE_FPS = float(os.environ.get("OCR_SAMPLE_FPS", "1"))

# --- Camera-motion candidate tuning (calibrated 2026-07-25, see
# scripts/camera_motion_candidates.py's session notes: translation=25px/
# rotation=3deg/cluster_window=3.0s kept combined-with-OCR recall at 94.7%
# while cutting raw candidates from 117 to 85) ---
TRANSLATION_SPIKE_PX = 25.0
ROTATION_SPIKE_DEG = 3.0
LOW_CONFIDENCE_AS_SPIKE = True
CLUSTER_WINDOW_SECONDS = 3.0

# --- Merge tuning ---
DEDUP_WINDOW_SECONDS = 5.0


def frame_index_from_name(path) -> int:
    return int(Path(path).stem.replace("frame_", ""))


def parse_game_clock(text_blob: str) -> Optional[Tuple[int, int]]:
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


_worker_reader = None


def _init_ocr_worker():
    # Each process loads its own EasyOCR reader ONCE (not per frame) - model
    # load is the expensive part; reuse it for every frame this worker gets.
    global _worker_reader
    import easyocr
    _worker_reader = easyocr.Reader(["en"], gpu=False, verbose=False)


def _ocr_one_frame(path_str: str) -> Tuple[int, Optional[str], Optional[Tuple[int, int]]]:
    path = Path(path_str)
    idx = frame_index_from_name(path)

    img = cv2.imread(str(path))
    if img is None:
        return (idx, None, None)

    h, w = img.shape[:2]
    strip = img[int(h * BOTTOM_STRIP_TOP_FRAC):h, 0:w]
    results = _worker_reader.readtext(strip, detail=0)
    text_blob = " ".join(results)

    match = DOWN_DISTANCE_RE.search(text_blob)
    down_distance = match.group(0).lower() if match else None
    clock = parse_game_clock(text_blob)
    return (idx, down_distance, clock)


def _reject_replays(candidates: List[Tuple[int, str, Optional[Tuple[int, int]]]]):
    """Filters candidates whose game clock doesn't move forward relative to
    the last KEPT candidate - the signature of a broadcast replay re-showing
    an already-counted play. Candidates with no readable clock are kept
    as-is (can't judge them) and don't update the "last kept" reference."""
    kept = []
    last_key = None
    for c in candidates:
        _idx, _dd, clock = c
        if clock is None:
            kept.append(c)
            continue
        key = (clock[0], -clock[1])  # (quarter, -seconds_remaining): increases with real game time
        if last_key is not None and key < last_key:
            continue
        last_key = key
        kept.append(c)
    return kept


def extract_ocr_candidates(frame_paths: List[Path], fps: float = FPS) -> List[Tuple[int, float]]:
    """Runs OCR across a fixed subsample of frame_paths (OCR_SAMPLE_FPS,
    default 1fps - matching Phase A's own validated methodology, not the
    full frame rate), debounces down-and-distance graphic changes (only
    confirmed after 2 consecutive matching SAMPLED reads, tolerating
    EasyOCR's "1st" -> "Ist"/"lst" misread), rejects replay-cutaway false
    positives via game-clock backward movement. Returns sorted
    (frame_index, timestamp_seconds) candidates."""
    if not frame_paths:
        return []

    step = max(1, round(fps / OCR_SAMPLE_FPS)) if OCR_SAMPLE_FPS > 0 else 1
    sampled_paths = frame_paths[::step]

    with Pool(processes=OCR_WORKERS, initializer=_init_ocr_worker) as pool:
        raw_results = list(pool.imap(_ocr_one_frame, [str(p) for p in sampled_paths], chunksize=4))
    raw_results.sort(key=lambda r: r[0])

    prev_confirmed = None
    pending_value = None
    pending_count = 0
    boundary_candidates: List[Tuple[int, Optional[str], Optional[Tuple[int, int]]]] = []

    for idx, down_distance, clock in raw_results:
        if down_distance == pending_value:
            pending_count += 1
        else:
            pending_value = down_distance
            pending_count = 1

        confirmed = pending_value if pending_count >= 2 else prev_confirmed
        newly_confirmed = confirmed != prev_confirmed
        if newly_confirmed and confirmed:
            boundary_candidates.append((idx, confirmed, clock))
        prev_confirmed = confirmed

    kept = _reject_replays(boundary_candidates)
    return [(idx, idx / fps) for idx, _dd, _clock in kept]


def extract_camera_motion_candidates_from_rows(
    camera_motion_rows: List[Dict[str, Any]], fps: float = FPS
) -> List[Tuple[int, float]]:
    """camera_motion_rows: dicts with frame_index/translation_x/
    translation_y/rotation_deg/low_confidence, read back from the
    `camera_motion` table (motion_compensation_stage.py already wrote
    real per-video frame indices there - no positional remapping needed,
    unlike scripts/camera_motion_candidates.py's raw
    compute_camera_motion_sequence() output). Returns sorted
    (frame_index, timestamp_seconds) candidates."""
    spikes = []
    for row in camera_motion_rows:
        translation_mag = (row["translation_x"] ** 2 + row["translation_y"] ** 2) ** 0.5
        is_spike = (
            translation_mag > TRANSLATION_SPIKE_PX
            or abs(row["rotation_deg"]) > ROTATION_SPIKE_DEG
            or (LOW_CONFIDENCE_AS_SPIKE and row["low_confidence"])
        )
        if is_spike:
            spikes.append(row["frame_index"])

    spikes.sort()
    clustered: List[Tuple[int, float]] = []
    for fi in spikes:
        ts = fi / fps
        if clustered and ts - clustered[-1][1] <= CLUSTER_WINDOW_SECONDS:
            continue
        clustered.append((fi, ts))
    return clustered


def merge_and_dedup(
    *candidate_lists: List[Tuple[int, float]], window: float = DEDUP_WINDOW_SECONDS
) -> List[Tuple[int, float]]:
    """Union of any number of (frame_index, timestamp_seconds) candidate
    lists, merging any two within `window` seconds of each other into one
    (keeps the earlier one). Same logic as scripts/combine_candidates.py's
    merge_and_dedup, adapted to keep frame_index through the merge (needed
    to build real frame ranges below) instead of flattening to bare
    timestamps."""
    combined = sorted((c for lst in candidate_lists for c in lst), key=lambda c: c[1])
    merged: List[Tuple[int, float]] = []
    for c in combined:
        if merged and c[1] - merged[-1][1] <= window:
            continue
        merged.append(c)
    return merged


def candidates_to_play_ranges(
    candidates: List[Tuple[int, float]], last_frame_index: int
) -> List[Tuple[int, int, int]]:
    """candidates: merged play-start candidates. Returns
    (play_index, start_frame, end_frame) tuples covering the WHOLE video
    (frame 0 through last_frame_index) with no gaps - the first play's
    start_frame is forced to 0 even if the first real candidate is later
    (e.g. broadcast intro/pregame footage), so every frame belongs to
    exactly one play. Each play's end_frame is the frame right before the
    next play's start, or the video's real last frame for the final play."""
    if not candidates:
        return []
    sorted_candidates = sorted(candidates, key=lambda c: c[0])
    ranges = []
    for i, (frame_index, _ts) in enumerate(sorted_candidates):
        start_frame = 0 if i == 0 else frame_index
        end_frame = (
            sorted_candidates[i + 1][0] - 1 if i + 1 < len(sorted_candidates)
            else last_frame_index
        )
        ranges.append((i, start_frame, end_frame))
    return ranges
