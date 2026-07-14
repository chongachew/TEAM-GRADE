"""
Authenticity Check Stage - Whole-File Authenticity Gate

Runs processing/film_authenticity.py's analyze_file_integrity against the
downloaded video's container/codec/metadata (no pixel decode - cheap, file
headers only). Soft-flag only: the verdict is written to Firestore as data,
it never fails this stage or blocks the pipeline - only Bridge Athletics'
claim-to-profile gate treats a flag as a hard block. Only enqueued when
settings.AUTHENTICITY_CHECK_ENABLED is true.
"""

import logging
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import ValidationError
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

STAGE_NAME = "authenticity_check"
STAGE_NEXT = "whistle_detection" if getattr(settings, "WHISTLE_DETECTION_ENABLED", False) else "frame_extraction"


def run_authenticity_check_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute the whole-file authenticity gate.

    Loads the downloaded video file and runs
    processing.film_authenticity.analyze_file_integrity against its
    container/codec/metadata. The result (flagged/confidence/reasons) is
    written to Firestore; this stage always proceeds to the next stage
    regardless of the verdict - a flag is a soft signal for now, not a
    pipeline-blocking failure.

    Args:
        firestore_client: Google Cloud Firestore client
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
        logger.info(f"[{STAGE_NAME}] Starting authenticity check stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        # -------- Load video path from Firestore (same convention as
        # whistle_detection_stage.py / frame_extraction_stage_gpu.py:
        # download_path field, fallback to the default videos dir) --------
        try:
            db = firestore_client.db
            video_doc = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).get()

            if not video_doc.exists:
                error_code = "VIDEO_DOC_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Video document not found", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code

            video_data = video_doc.to_dict()
            video_path_str = video_data.get("download_path")
            if not video_path_str:
                videos_dir = settings.get_videos_dir()
                video_path_str = str(videos_dir / f"{video_id_safe}.mp4")
                logger.info(f"[{STAGE_NAME}] Using fallback video path", extra={
                    "video_id": video_id_safe,
                    "fallback_path": video_path_str,
                })
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to load video document: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        from pathlib import Path
        video_path = Path(video_path_str)
        if not video_path.exists():
            error_code = "VIDEO_FILE_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] Video file not found", extra={
                "video_id": video_id_safe,
                "path": str(video_path),
                "error_code": error_code
            })
            return False, error_code

        # -------- Run the heuristic check. Never raises (see
        # analyze_file_integrity's own docstring) - a broken check
        # environment degrades to confidence="unknown", it does not fail
        # this stage. --------
        from processing.film_authenticity import analyze_file_integrity
        result = analyze_file_integrity(video_path)

        logger.info(f"[{STAGE_NAME}] Authenticity check complete", extra={
            "video_id": video_id_safe,
            "flagged": result["flagged"],
            "confidence": result["confidence"],
        })

        try:
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.flagged": result["flagged"],
                f"stages.{STAGE_NAME}.confidence": result["confidence"],
                f"stages.{STAGE_NAME}.reasons": result["reasons"],
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                # Top-level convenience field, same promotion pattern as
                # download_path/frame_count/jersey_number - lets
                # GET /api/analysis read this without traversing stages.*.
                "authenticity_signals.file_integrity": {
                    "flagged": result["flagged"],
                    "confidence": result["confidence"],
                    "reasons": result["reasons"],
                },
            })
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write to Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        try:
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                metadata={"previous_stage": STAGE_NAME}
            )
            if not success:
                error_code = "QUEUE_ERROR"
                logger.error(f"[{STAGE_NAME}] enqueue_video returned False", extra={
                    "video_id": video_id_safe,
                    "next_stage": STAGE_NEXT,
                    "error_code": error_code
                })
                return False, error_code
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
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
