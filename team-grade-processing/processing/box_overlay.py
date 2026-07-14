"""
Player-highlighting box overlay - burns a moving bounding box around one
tracked player into an exported highlight clip.

Per docs/team-grade-integration-blueprint.md's "Player-highlighting box
overlay" section: optional, off by default, drawn only at export time
(GET /api/reps/{video_id}/clip) - never during normal playback, which already
has its own separate live canvas preview (BoxOverlay.jsx/tsx) unrelated to
this and unaffected by it.

Standalone module (same convention as processing/film_authenticity.py,
processing/audio_analyzer.py): pure functions, independently testable, no
FastAPI/queue coupling. The Firestore client is passed in the same way every
other stage's Firestore-touching helper already does - there's no way to
fetch a document without one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from config import settings
from ingest.utils.ffmpeg_utils import FFmpegFrameExtractor
from ingest.utils.firestore_utils import COLLECTION_FRAMES, COLLECTION_TRACKS

logger = logging.getLogger(__name__)

# Below this fraction of frames in range having a stored box, don't bother -
# a box missing for more than a handful of frames would flicker on and off
# more than it would actually help identify the player. Ties into the same
# "soft-flag over hard-fail" philosophy as the authenticity-check feature:
# this is an optional enhancement, never worth blocking the plain clip over.
MIN_COVERAGE_FRACTION = 0.9

BOX_COLOR = "yellow"
BOX_THICKNESS = 3


def fetch_track_boxes(
    firestore_client: "FirestoreClient",
    video_id: str,
    track_id: int,
    start_frame: int,
    end_frame: int,
) -> dict[int, list[float]]:
    """Batch-fetch stored per-frame boxes for one track across a frame range.

    Uses direct document-reference gets (tracks/{frame_index:06d}_{track_id:03d}
    is a deterministic ID, written by write_tracks_batch in firestore_utils.py)
    rather than a range+equality where() query - avoids ever needing a
    composite Firestore index, and a get_all() batch read is one round trip
    instead of a streamed query.

    Returns:
        {frame_index: [x1, y1, x2, y2]} for every frame in range that has a
        stored box - frames with no doc are simply absent from the dict, not
        an error. build_drawbox_filter decides what to do about gaps.
    """
    db = firestore_client.db
    base = (
        db.collection(settings.COLLECTION_VIDEOS)
        .document(video_id)
        .collection(COLLECTION_TRACKS)
    )
    frame_indices = list(range(start_frame, end_frame + 1))
    refs = [base.document(f"{fi:06d}_{track_id:03d}") for fi in frame_indices]

    boxes: dict[int, list[float]] = {}
    for fi, snap in zip(frame_indices, db.get_all(refs)):
        if snap.exists:
            data = snap.to_dict() or {}
            bbox = data.get("bbox")
            if bbox and len(bbox) == 4:
                boxes[fi] = list(bbox)
    return boxes


def get_source_resolution_scale(
    firestore_client: "FirestoreClient",
    video_id: str,
    source_path: Path,
    reference_frame_index: int,
) -> Optional[tuple[float, float]]:
    """Defensive scale factor: (current source width, height) / (resolution
    the stored boxes were computed at). Almost always (1.0, 1.0) in practice -
    frame extraction never resizes, and reads the exact same local file this
    clip endpoint cuts from - but the local file could in principle have been
    re-downloaded at a different quality since tracking last ran, so this is
    checked per-request rather than assumed.

    Returns None (treated by the caller as "can't verify, don't draw") if
    either the reference frame doc or a fresh ffprobe read is missing/unusable
    - never raises.
    """
    try:
        db = firestore_client.db
        frame_doc = (
            db.collection(settings.COLLECTION_VIDEOS)
            .document(video_id)
            .collection(COLLECTION_FRAMES)
            .document(f"{reference_frame_index:06d}")
            .get()
        )
        if not frame_doc.exists:
            return None
        frame_data = frame_doc.to_dict() or {}
        stored_width = frame_data.get("width")
        stored_height = frame_data.get("height")
        if not stored_width or not stored_height:
            return None

        extractor = FFmpegFrameExtractor()
        info = extractor.get_video_info(source_path)
        if not info or not info.get("width") or not info.get("height"):
            return None

        return (info["width"] / stored_width, info["height"] / stored_height)
    except Exception as e:
        logger.warning(f"[box_overlay] Resolution-scale check failed: {e}")
        return None


def build_drawbox_filter(
    boxes_by_frame: dict[int, list[float]],
    start_frame: int,
    end_frame: int,
    fps: float,
    scale_x: float,
    scale_y: float,
) -> Optional[str]:
    """Build a self-contained ffmpeg -vf filter string: one drawbox per frame
    of the clip, each active only for its own time slice
    (enable='between(t,t0,t1)'), holding the last-known box steady across any
    inner gap frames. Assumes the clip is cut with -ss BEFORE -i (input
    seeking), so the filter graph's `t` is rebased near 0 at the clip's own
    start - matching every t0/t1 computed here as relative-to-clip-start
    seconds, not absolute source-file seconds.

    Returns None if coverage is too sparse to bother (see MIN_COVERAGE_FRACTION)
    or the range is empty.
    """
    frame_indices = list(range(start_frame, end_frame + 1))
    total = len(frame_indices)
    if total == 0:
        return None

    covered = sum(1 for fi in frame_indices if fi in boxes_by_frame)
    if covered / total < MIN_COVERAGE_FRACTION:
        return None

    filters = []
    last_box: Optional[list[float]] = None
    for i, fi in enumerate(frame_indices):
        box = boxes_by_frame.get(fi, last_box)
        if box is None:
            # No box yet at all (a gap right at the very start, before any
            # frame in range has been seen) - nothing to hold steady from yet,
            # skip this frame rather than drawing a box at (0,0).
            continue
        last_box = box

        x1, y1, x2, y2 = box
        x, y = x1 * scale_x, y1 * scale_y
        w, h = (x2 - x1) * scale_x, (y2 - y1) * scale_y
        if w <= 0 or h <= 0:
            continue

        t0 = i / fps
        t1 = (i + 1) / fps
        filters.append(
            f"drawbox=x={x:.1f}:y={y:.1f}:w={w:.1f}:h={h:.1f}:"
            f"color={BOX_COLOR}:thickness={BOX_THICKNESS}:"
            f"enable='between(t,{t0:.4f},{t1:.4f})'"
        )

    if not filters:
        return None
    return ",".join(filters)
