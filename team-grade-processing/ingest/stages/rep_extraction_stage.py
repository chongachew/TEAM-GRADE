"""
Rep Extraction Stage (Stage 7/9) - Rep Segmentation
Groups frames into discrete reps using pose trajectory analysis.
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
from ingest.utils.firestore_utils import get_utc_timestamp, write_reps_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "rep_extraction"
STAGE_NEXT = "biomechanics"


def run_rep_extraction_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute rep extraction stage.
    
    Stage 7/9: Extract reps (repetitions) from pose trajectory.
    Uses motion detection and trajectory analysis to segment video.
    
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
            raise ValidationError("firestore_client must be initialized")
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError("queue_manager must have .enqueue_video method")
        
        video_id_safe = VideoIdValidator.validate(video_id)
    
    except ValidationError as e:
        logger.warning(f"[{STAGE_NAME}] Validation failed", extra={
            "error_code": e.error_code, "video_id": video_id
        })
        return False, e.error_code
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting", extra={"video_id": video_id_safe})

        from processing.rep_extraction import segment_reps_from_pose_docs

        try:
            poses_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe).collection("pose")
            poses_docs = list(poses_ref.stream())
            
            if not poses_docs:
                logger.warning(f"[{STAGE_NAME}] No poses found", extra={"video_id": video_id_safe})
                firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                    f"stages.{STAGE_NAME}.status": "completed",
                    f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                })
                queue_manager.enqueue_video(video_id_safe, stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY,
                    metadata={"previous_stage": STAGE_NAME, "total_reps": 0})
                return True, None
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore read failed", extra={"error_code": "FIRESTORE_ERROR"})
            return False, "FIRESTORE_ERROR"

        multi_player = getattr(settings, 'MULTI_PLAYER_TRACKING_ENABLED', False)

        try:
            reps_data, tracks_processed = segment_reps_from_pose_docs(
                firestore_client, video_id_safe, poses_docs, multi_player,
                settings.FRAME_EXTRACTION_FPS, settings.DEFAULT_PLAYER_POSITION,
            )
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Frame building failed", extra={"error_code": "FRAME_PROCESSING_ERROR"})
            return False, "FRAME_PROCESSING_ERROR"

        try:
            if reps_data:
                write_reps_batch(firestore_client, video_id_safe, reps_data)

            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.total_reps": len(reps_data),
                f"stages.{STAGE_NAME}.tracks_processed": tracks_processed,
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
            })
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore write failed", extra={"error_code": "FIRESTORE_ERROR"})
            return False, "FIRESTORE_ERROR"

        try:
            queue_manager.enqueue_video(video_id_safe, stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                metadata={"previous_stage": STAGE_NAME, "total_reps": len(reps_data)})
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Queue failed", extra={"error_code": "QUEUE_ERROR"})
            return False, "QUEUE_ERROR"

        logger.info(f"[{STAGE_NAME}] Complete", extra={"video_id": video_id_safe})
        return True, None

    except Exception as e:
        logger.error(f"[{STAGE_NAME}] Unexpected error", extra={"error_code": "UNEXPECTED_ERROR"})
        return False, "UNEXPECTED_ERROR"

