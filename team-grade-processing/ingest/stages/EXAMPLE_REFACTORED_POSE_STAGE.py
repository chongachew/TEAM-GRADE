"""
EXAMPLE: Refactored Pose Stage Handler
Demonstrates the refactoring patterns recommended in REFACTORING_PLAN.md
Compare with: ingest/stages/pose_stage.py

KEY IMPROVEMENTS:
- Specific exception handling (not catch-all)
- Input validation using dedicated validator
- Comprehensive docstring with examples
- Proper error context in logs
- Structured error codes for recovery
"""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
import cv2

from config import settings
from ingest.exceptions import (
    StageExecutionError,
    ValidationError,
    FileNotFoundError,
    FirestoreError,
    is_retryable
)
from ingest.validation import VideoIdValidator, StagePayloadValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_pose_batch

logger = logging.getLogger(__name__)

# Stage constants
STAGE_NAME = "pose"
NEXT_STAGE = "torso_crop"


def run_pose_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute pose estimation stage.
    
    Stage 4/9: Run MediaPipe BlazePose on all extracted frames.
    Extracts 33-point keypoints for each frame above confidence threshold.
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client with .db attribute
    - queue_manager: Queue manager with .enqueue_video method
    - payload: Optional dict with:
        - confidence_threshold: float 0-1 (override default)
    
    **Output Data Written to Firestore:**
    - Collection: videos/{video_id}/pose
    - Document per frame with fields:
        - frame_index: int (0-based)
        - timestamp_seconds: float
        - landmarks: List[Dict] with keys x, y, z, confidence
        - confidence_mean: float (0-1)
    
    **Stages Remaining after Success:** 5 (torso_crop, jersey_ocr, rep_extraction, 
                                           biomechanics, complete)
    
    **Possible Return Values:**
    - (True, None): Stage completed successfully
    - (False, "VIDEO_ID_INVALID"): video_id didn't pass validation
    - (False, "FRAMES_NOT_FOUND"): No extracted frames available
    - (False, "MODEL_LOAD_FAILED"): PoseEstimator failed to initialize
    - (False, "FIRESTORE_ERROR"): Failed to write results
    
    Args:
        firestore_client: Google Cloud Firestore client instance (must have .db attribute)
        video_id: YouTube video ID - will be sanitized and validated
        queue_manager: Queue manager instance (must have .enqueue_video method)
        payload: Optional stage-specific configuration
            {
                "confidence_threshold": 0.7  # Override default 0.7
            }
    
    Returns:
        (success: bool, error_code: Optional[str])
        - On success: (True, None)
        - On failure: (False, error_code_str) where error_code is machine-readable
        
    Example Success Flow:
        >>> from ingest.firestore_client import FirestoreClient
        >>> from ingest.queue_manager import QueueManager
        >>> 
        >>> firestore = FirestoreClient()
        >>> queue = QueueManager(firestore)
        >>> success, error = run_pose_stage(firestore, "dQw4w9WgXcQ", queue)
        >>> assert success is True
        >>> assert error is None
        
    Example Error Flow:
        >>> success, error = run_pose_stage(firestore, "invalid_id", queue)
        >>> assert success is False
        >>> assert error == "VIDEO_ID_INVALID"
        
    Notes:
        - Automatically skips frames with low confidence (< threshold)
        - Logs detailed progress every N frames (configurable)
        - Writes to Firestore in batches (max 500 per batch)
        - Enqueues next stage (torso_crop) on success
    """
    
    # ========================================================================
    # INPUT VALIDATION
    # ========================================================================
    
    try:
        # Validate firestore_client
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized FirestoreClient with .db attribute"
            )
        
        # Validate queue_manager
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )
        
        # Validate and sanitize video_id
        video_id_safe = VideoIdValidator.validate(video_id)
        
        # Validate and normalize payload
        stage_config = StagePayloadValidator.validate_pose_estimation(payload)
        confidence_threshold = stage_config.get(
            "confidence_threshold",
            settings.POSE_CONFIDENCE_THRESHOLD
        )
    
    except ValidationError as e:
        # Return machine-readable error code for specific validation failures
        error_code = e.error_code
        logger.warning(f"[{STAGE_NAME}] {str(e)}", extra={
            "video_id": e.video_id or video_id,
            "stage": STAGE_NAME,
            "error_code": error_code
        })
        return False, error_code
    
    # ========================================================================
    # STAGE EXECUTION
    # ========================================================================
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting pose estimation", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "confidence_threshold": confidence_threshold
        })
        
        # -------- Load extracted frames --------
        
        try:
            from ingest.utils.firestore_utils import update_stage_status
            
            frames_dir = settings.get_frames_dir(video_id_safe)
            
            if not frames_dir.exists():
                raise FileNotFoundError(
                    str(frames_dir),
                    video_id=video_id_safe,
                    stage=STAGE_NAME
                )
            
            frame_paths = sorted(frames_dir.glob("frame_*.jpg"))
            
            if not frame_paths:
                raise StageExecutionError(
                    STAGE_NAME,
                    f"No frames found in {frames_dir}",
                    video_id=video_id_safe
                )
            
            logger.info(f"[{STAGE_NAME}] Found {len(frame_paths)} frames", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "frame_count": len(frame_paths)
            })
        
        except FileNotFoundError as e:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] {str(e)}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Initialize pose estimator --------
        
        try:
            from processing.pose_estimation import PoseEstimator
            
            pose_estimator = PoseEstimator(
                model_name=settings.POSE_MODEL_NAME,
                device=settings.POSE_DEVICE
            )
            
            logger.info(f"[{STAGE_NAME}] Pose estimator initialized", extra={
                "video_id": video_id_safe,
                "model": settings.POSE_MODEL_NAME,
                "device": settings.POSE_DEVICE
            })
        
        except Exception as e:
            error_code = "MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to load pose model: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Process frames --------
        
        poses: List[Dict[str, Any]] = []
        high_confidence_count = 0
        
        try:
            for idx, frame_path in enumerate(frame_paths):
                try:
                    # Load frame
                    frame = cv2.imread(str(frame_path))
                    if frame is None:
                        logger.warning(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_path.name}")
                        continue
                    
                    # Run pose estimation
                    landmarks, confidence = pose_estimator.estimate(frame)
                    
                    # Only keep high-confidence frames
                    if confidence >= confidence_threshold:
                        high_confidence_count += 1
                        
                        poses.append({
                            "frame_index": idx,
                            "timestamp_seconds": idx / settings.FRAME_EXTRACTION_FPS,
                            "landmarks": landmarks,
                            "confidence_mean": float(confidence),
                            "created_at": get_utc_timestamp()
                        })
                    
                    # Log progress
                    if (idx + 1) % settings.POSE_PROGRESS_LOG_INTERVAL == 0:
                        logger.info(f"[{STAGE_NAME}] Progress: {idx + 1}/{len(frame_paths)} frames", extra={
                            "video_id": video_id_safe,
                            "progress": f"{idx + 1}/{len(frame_paths)}",
                            "high_confidence": high_confidence_count
                        })
                
                except Exception as frame_error:
                    # Log individual frame errors but continue processing
                    logger.warning(f"[{STAGE_NAME}] Error processing frame {idx}: {frame_error}", extra={
                        "video_id": video_id_safe,
                        "frame_index": idx,
                        "error": str(frame_error)
                    })
                    continue
            
            logger.info(f"[{STAGE_NAME}] Pose estimation complete: {len(poses)} high-confidence frames", extra={
                "video_id": video_id_safe,
                "total_frames": len(frame_paths),
                "high_confidence_frames": len(poses),
                "confidence_percentage": round(100 * len(poses) / len(frame_paths), 1)
            })
        
        except Exception as e:
            # Unexpected error during frame processing
            error_code = "FRAME_PROCESSING_ERROR"
            logger.error(f"[{STAGE_NAME}] Unexpected error during frame processing: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            }, exc_info=True)  # Include stack trace for unexpected errors
            return False, error_code
        
        # -------- Write results to Firestore --------
        
        try:
            if not poses:
                logger.warning(f"[{STAGE_NAME}] No poses to write (all frames below threshold)", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME
                })
            else:
                written = write_pose_batch(firestore_client, video_id_safe, poses)
                logger.info(f"[{STAGE_NAME}] Wrote {written} poses to Firestore", extra={
                    "video_id": video_id_safe,
                    "poses_written": written
                })
            
            # Update stage status in Firestore
            update_stage_status(
                firestore_client,
                video_id_safe,
                STAGE_NAME,
                "completed"
            )
        
        except FirestoreError as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write results: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        except Exception as e:
            # Unexpected Firestore error
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Unexpected Firestore error: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        # -------- Enqueue next stage --------
        
        try:
            queue_manager.enqueue_video(video_id_safe, NEXT_STAGE)
            logger.info(f"[{STAGE_NAME}] Enqueued {NEXT_STAGE} stage", extra={
                "video_id": video_id_safe,
                "next_stage": NEXT_STAGE
            })
        
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "next_stage": NEXT_STAGE,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- SUCCESS --------
        
        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "duration_info": "check logs for detailed timing"
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
        
        # Check if error is retryable
        if is_retryable(e):
            logger.info(f"[{STAGE_NAME}] Error is retryable, worker will retry", extra={
                "video_id": video_id_safe,
                "is_retryable": True
            })
        
        return False, error_code
