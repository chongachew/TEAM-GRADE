"""
Complete Stage (Stage 9/9) - Pipeline Finalization
Marks video processing as complete, logs summary statistics, and finalizes all data.
"""

import logging
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FirestoreError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

# Stage name constants (avoid circular import from ingest.stages.__init__)
STAGE_NAME = "complete"
STAGE_SEQUENCE = [
    "metadata", "download", "frame_extraction", "pose", "torso_crop",
    "jersey_ocr", "rep_extraction", "biomechanics", "complete"
]


def run_complete_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute pipeline completion stage.
    
    Stage 9/9 (Final): Mark all video processing as complete.
    Finalize data in Firestore and log completion summary with statistics.
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client with .db attribute
    - queue_manager: Queue manager instance
    - payload: Optional dict (not used)
    
    **Output Data:**
    - Updates Firestore document: videos/{video_id}
    - Sets: status="completed", completed_at=ISO timestamp
    - Logs: completion summary with jersey, frame_count, reps_analyzed
    
    **Possible Return Values:**
    - (True, None): Stage completed successfully
    - (False, "VIDEO_ID_INVALID"): video_id failed validation
    - (False, "VIDEO_DOC_NOT_FOUND"): Video document not found in Firestore
    - (False, "FIRESTORE_ERROR"): Failed to update Firestore
    - (False, "UNEXPECTED_ERROR"): Unexpected error occurred
    
    Args:
        firestore_client: Google Cloud Firestore client (must have .db attribute)
        video_id: YouTube video ID - will be sanitized and validated
        queue_manager: Queue manager instance (not used in this stage)
        payload: Optional stage configuration (not used)
    
    Returns:
        (success: bool, error_code: Optional[str])
        - On success: (True, None)
        - On failure: (False, error_code_str) where error_code is machine-readable
        
    Example:
        >>> from ingest.firestore_client import FirestoreClient
        >>> firestore = FirestoreClient()
        >>> queue = None  # Not used in complete stage
        >>> success, error = run_complete_stage(firestore, "dQw4w9WgXcQ", queue)
        >>> assert success is True
        >>> assert error is None
    
    Notes:
        - This is the final stage - no next stage is enqueued
        - Queue cleanup is delegated to worker's mark_completed() call
        - Logs summary: jersey number, frame count, reps analyzed
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
        logger.info(f"[{STAGE_NAME}] Starting pipeline finalization", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
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
        
        # -------- Verify video document exists --------
        
        try:
            doc = video_ref.get()
            if not doc.exists:
                error_code = "VIDEO_DOC_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Video document not found in Firestore", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                    "error_code": error_code
                })
                return False, error_code
            
            logger.info(f"[{STAGE_NAME}] Video document verified", extra={
                "video_id": video_id_safe,
                "document_exists": True,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to verify video document: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Mark pipeline as complete --------
        
        try:
            current_time = get_utc_timestamp()
            
            # Update video status to completed
            video_ref.update({
                "status": "completed",
                "stages.complete.status": "completed",
                "stages.complete.completed_at": current_time,
                "completed_at": current_time
            })
            
            logger.info(f"[{STAGE_NAME}] Video marked as completed in Firestore", extra={
                "video_id": video_id_safe,
                "timestamp": current_time,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to update Firestore: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Log completion summary --------
        
        try:
            final_doc = video_ref.get()
            if final_doc.exists:
                final_data = final_doc.to_dict()
                
                # Extract summary statistics
                jersey = final_data.get("jersey_number", "N/A")
                frame_count = final_data.get("frame_count", 0)
                analysis = final_data.get("analysis", {})
                reps_analyzed = len(analysis.get("per_rep", {})) if analysis else 0
                
                logger.info(f"[{STAGE_NAME}] Pipeline completed successfully", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                    "summary": {
                        "jersey": jersey,
                        "frame_count": frame_count,
                        "reps_analyzed": reps_analyzed,
                    }
                })
            else:
                logger.warning(f"[{STAGE_NAME}] Could not retrieve final document for logging", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                })
        
        except Exception as e:
            logger.warning(f"[{STAGE_NAME}] Failed to log completion summary: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
            })
            # Non-fatal error - continue
        
        # -------- SUCCESS --------
        
        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
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
