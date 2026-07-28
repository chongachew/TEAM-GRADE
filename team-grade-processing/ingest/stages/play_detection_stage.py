"""
Play Detection Stage (per-play pipeline redesign)
Finds real play boundaries using OCR (reads the broadcast scoreboard) plus
camera-motion (already computed and persisted by motion_compensation_stage.py
- read back here rather than recomputed), combines them, persists the
result to the `plays` table, and enqueues one `detection` job PER PLAY
instead of one job for the whole video. See
processing/play_boundary_detection.py for the actual signal logic, and
C:\\Users\\ricky\\.claude\\plans\\adaptive-finding-planet.md for the
validated recall/precision numbers this is built on (94.7% recall / 63.5%
precision combined, both signals need nothing more than extracted frames -
not even the `detection` stage).
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import (
    get_utc_timestamp,
    write_plays_batch,
    set_play_clip_ready,
    COLLECTION_VIDEOS,
    COLLECTION_CAMERA_MOTION,
)
from ingest.s3_client import ensure_frames_local, ensure_video_local, play_clip_key, upload_file
from processing.clip_cutter import cut_clip
from processing.play_boundary_detection import (
    extract_ocr_candidates,
    extract_camera_motion_candidates_from_rows,
    merge_and_dedup,
    candidates_to_play_ranges,
    frame_index_from_name,
)

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "play_detection"
STAGE_NEXT = "detection"


def run_play_detection_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute play-boundary detection: OCR + camera-motion combined, write
    `plays` rows, enqueue one `detection` job per play.

    Args:
        firestore_client: Postgres client (Firestore-compat .db shim)
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration

    Returns:
        (success: bool, error_code: Optional[str])
    """
    try:
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )

        video_id_safe = VideoIdValidator.validate(video_id)

    except ValidationError as e:
        error_code = e.error_code
        logger.warning(f"[{STAGE_NAME}] Validation failed: {e}", extra={
            "video_id": e.video_id or video_id,
            "stage": STAGE_NAME,
            "error_code": error_code
        })
        return False, error_code

    try:
        logger.info(f"[{STAGE_NAME}] Starting play detection stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        frames_dir = settings.get_frames_dir(video_id_safe)
        # Defensive guard: normally already-local (same worker process as
        # frame_extraction/motion_compensation), but a retry could be picked
        # up by a different worker instance - cheap check-then-fetch from S3.
        try:
            ensure_frames_local(video_id_safe)
        except Exception as e:
            logger.debug(f"[{STAGE_NAME}] S3 frames fallback unavailable (non-fatal): {e}", extra={
                "video_id": video_id_safe,
            })
        frame_paths = sorted(frames_dir.glob("frame_*.jpg"), key=frame_index_from_name)

        if not frame_paths:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No frames found", extra={
                "video_id": video_id_safe,
                "directory": str(frames_dir),
                "error_code": error_code
            })
            return False, error_code

        last_frame_index = frame_index_from_name(frame_paths[-1])

        try:
            ocr_candidates = extract_ocr_candidates(frame_paths)
        except Exception as e:
            error_code = "PLAY_DETECTION_FAILED"
            logger.error(f"[{STAGE_NAME}] OCR candidate extraction failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        try:
            camera_motion_rows = [
                doc.to_dict()
                for doc in firestore_client.db.collection(COLLECTION_VIDEOS)
                    .document(video_id_safe)
                    .collection(COLLECTION_CAMERA_MOTION)
                    .stream()
            ]
            camera_motion_candidates = extract_camera_motion_candidates_from_rows(camera_motion_rows)
        except Exception as e:
            error_code = "PLAY_DETECTION_FAILED"
            logger.error(f"[{STAGE_NAME}] Camera-motion candidate extraction failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        merged = merge_and_dedup(ocr_candidates, camera_motion_candidates)

        if merged:
            play_ranges = candidates_to_play_ranges(merged, last_frame_index)
            detection_method = "ocr+camera_motion"
        else:
            # Zero-candidates fallback: neither signal found anything (e.g.
            # a very short clip, or non-broadcast footage with no
            # scoreboard). One play spanning the whole video keeps the
            # video making forward progress instead of silently stalling.
            play_ranges = [(0, 0, last_frame_index)]
            detection_method = "fallback_whole_video"
            logger.warning(
                f"[{STAGE_NAME}] No play boundaries found, falling back to one whole-video play",
                extra={"video_id": video_id_safe},
            )

        play_docs = [
            {
                "play_index": play_index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "status": "pending",
                "detection_method": detection_method,
                "confidence": None,
            }
            for play_index, start_frame, end_frame in play_ranges
        ]

        try:
            written = write_plays_batch(firestore_client, video_id_safe, play_docs)
            if written != len(play_docs):
                error_code = "FIRESTORE_ERROR"
                logger.error(f"[{STAGE_NAME}] Wrote {written}/{len(play_docs)} plays rows", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code

            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.play_detection.status": "completed",
                "stages.play_detection.total_plays": len(play_docs),
                "stages.play_detection.detection_method": detection_method,
                "stages.play_detection.completed_at": get_utc_timestamp(),
            })

        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write plays: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Cut + upload each play's standalone clip inline, right here rather
        # than as its own queued stage. The queue's dequeue_next_video groups
        # jobs by (video_id, play_index) and always prefers whichever queued
        # job is earliest in STAGE_SEQUENCE - a clip-export stage enqueued
        # alongside "detection" would never win that comparison (it isn't in
        # STAGE_SEQUENCE at all) and would starve until the play's entire
        # detection->tracking->...->complete chain finished. Doing it inline
        # here, before "detection" is even enqueued, sidesteps that entirely.
        # Best-effort: a cut/upload failure for one play just leaves that
        # play's clip_ready False permanently (not retried) and never fails
        # this stage - the watch page falls back to seeking within the full
        # video for that one play.
        for doc in play_docs:
            # Belt-and-suspenders: _export_play_clip already swallows its own
            # errors internally, but this loop must survive even a bug in
            # that error handling - already-written plays and the detection
            # jobs about to be enqueued below must never be sacrificed over
            # a clip export problem.
            try:
                _export_play_clip(video_id_safe, doc["play_index"], doc["start_frame"], doc["end_frame"], firestore_client)
            except Exception as e:
                logger.warning(f"[{STAGE_NAME}] Play clip export raised past its own handling (non-fatal): {e}", extra={
                    "video_id": video_id_safe,
                    "play_index": doc["play_index"],
                })

        all_ok = True
        for doc in play_docs:
            try:
                ok = queue_manager.enqueue_video(
                    video_id_safe,
                    stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY,
                    play_index=doc["play_index"],
                    metadata={"previous_stage": STAGE_NAME},
                )
                if not ok:
                    all_ok = False
                    logger.error(f"[{STAGE_NAME}] enqueue_video returned False", extra={
                        "video_id": video_id_safe,
                        "next_stage": STAGE_NEXT,
                        "play_index": doc["play_index"],
                    })
            except Exception as e:
                all_ok = False
                logger.error(f"[{STAGE_NAME}] Failed to enqueue play {doc['play_index']}: {e}", extra={
                    "video_id": video_id_safe,
                })

        if not all_ok:
            # Safe to retry: add_to_queue's dedup check skips already-queued
            # play+stage combos, so a re-run of this stage won't duplicate
            # the plays that already enqueued successfully.
            return False, "QUEUE_ERROR"

        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "total_plays": len(play_docs),
            "next_stage": STAGE_NEXT,
        })

        return True, None

    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code


def _export_play_clip(
    video_id_safe: str, play_index: int, start_frame: int, end_frame: int, firestore_client,
) -> None:
    """Cut this play's frame range out of the source video and upload it to
    S3 at ingest.s3_client.play_clip_key(), so the watch page can play just
    this one play. Errors are logged and swallowed - see the call site's
    comment for why this is best-effort and non-blocking."""
    output_path: Optional[Path] = None
    try:
        source_path = settings.get_video_path(video_id_safe)
        if not source_path.exists():
            source_path = ensure_video_local(video_id_safe)

        tmp_dir = Path(tempfile.gettempdir())
        output_path = tmp_dir / f"{video_id_safe}_play{play_index:03d}_clip.mp4"

        cut_clip(source_path, start_frame, end_frame, output_path, settings.FRAME_EXTRACTION_FPS)
        upload_file(output_path, play_clip_key(video_id_safe, play_index))
        set_play_clip_ready(firestore_client, video_id_safe, play_index, True)

        logger.info(f"[{STAGE_NAME}] Exported play clip", extra={
            "video_id": video_id_safe,
            "play_index": play_index,
        })

    except Exception as e:
        logger.warning(f"[{STAGE_NAME}] Play clip export failed (non-fatal): {e}", extra={
            "video_id": video_id_safe,
            "play_index": play_index,
        })

    finally:
        if output_path is not None:
            output_path.unlink(missing_ok=True)
