"""
Metadata Stage (Stage 1/9) - Pipeline Initialization
Initializes video document in Firestore with stage tracking structure.
"""

import logging
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

# Stage name constants (avoid circular import from ingest.stages.__init__)
STAGE_NAME = "metadata"
STAGE_NEXT = "download"

# When MULTI_PLAYER_TRACKING_ENABLED, three extra stages (motion_compensation, detection,
# tracking) run between frame_extraction and pose. Gated so existing single-athlete videos
# keep the original 9-stage `stages` map shape until the new stages are proven.
if getattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False):
    STAGE_SEQUENCE = [
        "metadata", "download", "frame_extraction",
        "motion_compensation", "detection", "tracking",
        "pose", "torso_crop", "jersey_ocr", "rep_extraction", "biomechanics", "complete"
    ]
else:
    STAGE_SEQUENCE = [
        "metadata", "download", "frame_extraction", "pose", "torso_crop",
        "jersey_ocr", "rep_extraction", "biomechanics", "complete"
    ]


def run_metadata_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute video initialization stage.
    
    Stage 1/9 (First): Initialize video document in Firestore with stage tracking.
    Creates the root document for the video and initializes all stage statuses.
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client with .db attribute
    - queue_manager: Queue manager with .enqueue_video method
    - payload: Optional dict (not used)
    
    **Output Data Written to Firestore:**
    - Creates collection: videos/{video_id}
    - Initializes 9 stages with status="pending"
    - Sets metadata stage to "completed"
    - Enqueues next stage (download)
    
    **Possible Return Values:**
    - (True, None): Stage completed and download queued
    - (False, "VIDEO_ID_INVALID"): video_id failed validation
    - (False, "FIRESTORE_CLIENT_INVALID"): firestore_client is None/invalid
    - (False, "FIRESTORE_ERROR"): Failed to create/update document
    - (False, "QUEUE_ERROR"): Failed to enqueue next stage
    - (False, "UNEXPECTED_ERROR"): Unexpected error occurred
    
    Args:
        firestore_client: Google Cloud Firestore client (must have .db attribute)
        video_id: YouTube video ID - will be sanitized and validated
        queue_manager: Queue manager instance (must have .enqueue_video method)
        payload: Optional stage configuration (not used)
    
    Returns:
        (success: bool, error_code: Optional[str])
        - On success: (True, None)
        - On failure: (False, error_code_str) where error_code is machine-readable
        
    Example:
        >>> from ingest.firestore_client import FirestoreClient
        >>> from ingest.queue_manager import QueueManager
        >>> firestore = FirestoreClient()
        >>> queue = QueueManager(firestore)
        >>> success, error = run_metadata_stage(firestore, "dQw4w9WgXcQ", queue)
        >>> assert success is True
        >>> assert error is None
    
    Notes:
        - Creates document if it doesn't exist, skips if already created
        - Initializes all 9 stages with status tracking
        - Enqueues download stage as next step
        - If document already exists, still marks metadata as complete
    """
    
    # ========================================================================
    # INPUT VALIDATION
    # ========================================================================
    
    try:
        # Validate firestore_client
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
        
        # Validate queue_manager
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )
        
        # Validate and sanitize video_id
        video_id_safe = VideoIdValidator.validate(video_id)
    
    except ValidationError as e:
        error_code = e.error_code
        logger.warning(f"[{STAGE_NAME}] Validation failed: {e}", extra={
            "video_id": e.video_id or video_id,
            "stage": STAGE_NAME,
            "error_code": error_code
        })
        return False, error_code
    
    # ========================================================================
    # STAGE EXECUTION
    # ========================================================================
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting video initialization", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "total_stages": len(STAGE_SEQUENCE),
        })
        
        # -------- Get Firestore reference --------
        
        try:
            db = firestore_client.db
            video_ref = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe)
            
            logger.debug(f"[{STAGE_NAME}] Firestore reference created", extra={
                "video_id": video_id_safe,
                "collection": settings.COLLECTION_VIDEOS,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to create Firestore reference: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Check if document exists or create --------
        
        try:
            doc = video_ref.get()
            
            if doc.exists:
                logger.info(f"[{STAGE_NAME}] Video document already exists, skipping create", extra={
                    "video_id": video_id_safe,
                    "action": "reuse_existing"
                })
            else:
                # Build stage statuses dynamically from STAGE_SEQUENCE
                stages_dict = {}
                for stage_name in STAGE_SEQUENCE:
                    if stage_name == STAGE_NAME:
                        # Current stage is in-progress
                        stages_dict[stage_name] = {
                            "status": "in-progress",
                            "attempt": 1,
                            "started_at": get_utc_timestamp()
                        }
                    else:
                        # All other stages start pending
                        stages_dict[stage_name] = {
                            "status": "pending",
                            "attempt": 0
                        }
                
                initial_data = {
                    "video_id": video_id_safe,
                    "status": "processing",
                    "created_at": get_utc_timestamp(),
                    "stages": stages_dict
                }
                
                video_ref.set(initial_data)
                
                logger.info(f"[{STAGE_NAME}] Created video document", extra={
                    "video_id": video_id_safe,
                    "stages_initialized": len(STAGE_SEQUENCE),
                })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to create/check video document: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Mark metadata stage as complete --------
        
        try:
            current_time = get_utc_timestamp()
            
            video_ref.update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.completed_at": current_time,
                f"stages.{STAGE_NAME}.attempt": 1
            })
            
            logger.info(f"[{STAGE_NAME}] Marked metadata stage as completed", extra={
                "video_id": video_id_safe,
                "timestamp": current_time,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to mark metadata complete: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Enqueue next stage --------
        
        try:
            priority = getattr(settings, 'QUEUE_DEFAULT_PRIORITY', 5)
            
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=priority,
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
            
            logger.info(f"[{STAGE_NAME}] Enqueued next stage", extra={
                "video_id": video_id_safe,
                "next_stage": STAGE_NEXT,
                "priority": priority,
            })
        
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "next_stage": STAGE_NEXT,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        # -------- SUCCESS --------
        
        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "next_stage": STAGE_NEXT,
        })
        
        return True, None
    
    # ========================================================================
    # UNEXPECTED ERRORS
    # ========================================================================
    
    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code
