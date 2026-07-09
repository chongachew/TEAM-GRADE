"""
Motion Compensation Stage (multi-player pipeline) - Camera Motion Cancellation
Estimates per-frame camera motion (KLT optical flow + RANSAC homography, no neural
net) so downstream stages can express player positions relative to the field rather
than the camera. Only enqueued when settings.MULTI_PLAYER_TRACKING_ENABLED is true.
"""

import logging
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
from ingest.utils.firestore_utils import get_utc_timestamp, write_camera_motion_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "motion_compensation"
STAGE_NEXT = "detection"


def run_motion_compensation_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute camera motion compensation stage.

    Estimates a per-frame homography (relative to the previous frame, and
    cumulatively relative to frame 0) using classical KLT + RANSAC. No ML model,
    no GPU required.

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
        logger.info(f"[{STAGE_NAME}] Starting motion compensation stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        from processing.motion_compensation import compute_camera_motion_sequence

        frames_dir = settings.get_frames_dir(video_id_safe)
        frame_paths = sorted(frames_dir.glob("frame_*.jpg"))

        if not frame_paths:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No frames found", extra={
                "video_id": video_id_safe,
                "directory": str(frames_dir),
                "error_code": error_code
            })
            return False, error_code

        logger.info(f"[{STAGE_NAME}] Found frames", extra={
            "video_id": video_id_safe,
            "frame_count": len(frame_paths),
        })

        try:
            motion_docs = compute_camera_motion_sequence(frame_paths)
        except Exception as e:
            error_code = "MOTION_ESTIMATION_FAILED"
            logger.error(f"[{STAGE_NAME}] Motion estimation failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        mean_inlier_ratio = (
            sum(d["inlier_ratio"] for d in motion_docs) / len(motion_docs)
            if motion_docs else 0.0
        )

        try:
            written = write_camera_motion_batch(firestore_client, video_id_safe, motion_docs)
            logger.info(f"[{STAGE_NAME}] Wrote camera motion docs to Firestore", extra={
                "video_id": video_id_safe,
                "docs_written": written,
            })

            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.motion_compensation.status": "completed",
                "stages.motion_compensation.total_frames": len(motion_docs),
                "stages.motion_compensation.mean_inlier_ratio": mean_inlier_ratio,
                "stages.motion_compensation.completed_at": get_utc_timestamp(),
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
