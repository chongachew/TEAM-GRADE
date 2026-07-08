"""
Lightweight Pose Extraction Stage (Stage 4/9 - Optimized for Phase 3)
Extracts pose keypoints using MediaPipe Lite model (2MB) instead of full model (35MB).

Performance Improvement (Phase 3 Optimization #2):
- Lite model: 2MB (vs full 35MB)
- Inference: ~2x faster
- Accuracy: Slightly lower (~2-3% on difficult poses)
- Target: 5-10% speedup on pose estimation stage
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_pose_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "pose"
STAGE_NEXT = "torso_crop"


def run_pose_stage_lite(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute pose estimation stage using lightweight model.
    
    Stage 4/9: Run MediaPipe Lite on extracted frames.
    Uses 2MB lite model instead of 35MB full model for ~2x speedup.
    Extracts 33-point keypoints for each frame above confidence threshold.
    
    **Phase 3 Optimization #2:**
    - Lite model: 2MB vs 35MB (-94%)
    - Inference: ~15-25ms per frame vs ~30-40ms (-50%)
    - Trade-off: Slightly lower accuracy on difficult poses
    - Expected impact: 5-10% speedup on pose stage
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client
    - queue_manager: Queue manager instance
    - payload: Optional configuration
    
    **Output:**
    - Firestore: poses subcollection with keypoints
    - Updates: stages.pose.status, total_poses, model_type
    - Enqueues: torso_crop stage
    
    **Possible Return Values:**
    - (True, None): Pose estimation succeeded
    - (False, "VIDEO_ID_INVALID"): video_id failed validation
    - (False, "FRAMES_NOT_FOUND"): Extracted frames missing
    - (False, "MODEL_LOAD_FAILED"): Pose model failed to load
    - (False, "FRAME_PROCESSING_ERROR"): Frame processing error
    - (False, "FIRESTORE_ERROR"): Failed to write results
    - (False, "QUEUE_ERROR"): Failed to enqueue next stage
    - (False, "UNEXPECTED_ERROR"): Unexpected error
    
    Args:
        firestore_client: Google Cloud Firestore client
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration dict
    
    Returns:
        (success: bool, error_code: Optional[str])
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
        logger.info(f"[{STAGE_NAME}] Starting lightweight pose estimation (Phase 3)", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "model": "mediapipe_lite",
        })
        
        # -------- Load frames --------
        
        try:
            frames_dir = settings.get_frames_dir(video_id_safe)
            
            if not frames_dir.exists():
                error_code = "FRAMES_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Frames directory not found", extra={
                    "video_id": video_id_safe,
                    "path": str(frames_dir),
                    "error_code": error_code
                })
                return False, error_code
            
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
        
        except Exception as e:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] Failed to load frames: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Initialize lightweight pose estimator --------
        
        try:
            from processing.lightweight_pose import PoseEstimatorFactory
            
            logger.info(f"[{STAGE_NAME}] Creating lightweight pose estimator...", extra={
                "video_id": video_id_safe,
            })
            
            # Create lightweight estimator with fallback to full model
            use_lite = getattr(settings, 'POSE_USE_LITE_MODEL', True)
            device = getattr(settings, 'POSE_DEVICE', 'cpu')
            
            pose_estimator = PoseEstimatorFactory.create(
                use_lite=use_lite,
                device=device,
                fallback_to_full=True
            )
            
            if not pose_estimator:
                error_code = "MODEL_LOAD_FAILED"
                logger.error(f"[{STAGE_NAME}] Failed to create pose estimator", extra={
                    "video_id": video_id_safe,
                    "use_lite": use_lite,
                    "error_code": error_code
                })
                return False, error_code
            
            # Log model info
            model_info = pose_estimator.get_model_info()
            logger.info(f"[{STAGE_NAME}] Pose estimator created", extra={
                "video_id": video_id_safe,
                "model_type": model_info.get("model_type"),
                "model_size_mb": model_info.get("model_size_mb"),
            })
        
        except Exception as e:
            error_code = "MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to load pose model: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        # -------- Process frames --------
        
        poses = []
        high_confidence_count = 0
        confidence_threshold = getattr(
            settings,
            'POSE_CONFIDENCE_THRESHOLD',
            0.7
        )
        
        try:
            import cv2
            
            logger.info(f"[{STAGE_NAME}] Processing frames with confidence threshold {confidence_threshold}", extra={
                "video_id": video_id_safe,
            })
            
            for idx, frame_path in enumerate(frame_paths):
                try:
                    frame = cv2.imread(str(frame_path))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_path.name}")
                        continue
                    
                    # Estimate pose using lightweight model
                    landmarks, confidence = pose_estimator.estimate(frame)
                    
                    # Filter by confidence threshold
                    if confidence >= confidence_threshold and landmarks:
                        high_confidence_count += 1
                        poses.append({
                            "frame_index": idx,
                            "timestamp_seconds": idx / getattr(
                                settings, 'FRAME_EXTRACTION_FPS', 15
                            ),
                            "landmarks": landmarks,
                            "confidence_mean": float(confidence),
                            "created_at": get_utc_timestamp()
                        })
                    
                    # Log progress
                    log_interval = getattr(settings, 'POSE_PROGRESS_LOG_INTERVAL', 50)
                    if (idx + 1) % log_interval == 0:
                        logger.info(f"[{STAGE_NAME}] Progress", extra={
                            "video_id": video_id_safe,
                            "processed": idx + 1,
                            "total": len(frame_paths),
                            "poses_found": high_confidence_count,
                        })
                
                except Exception as frame_error:
                    logger.warning(f"[{STAGE_NAME}] Error processing frame {idx}: {frame_error}", extra={
                        "video_id": video_id_safe,
                        "frame_index": idx,
                    })
                    continue
            
            logger.info(f"[{STAGE_NAME}] Pose estimation complete", extra={
                "video_id": video_id_safe,
                "total_frames": len(frame_paths),
                "high_confidence": len(poses),
                "confidence_threshold": confidence_threshold,
            })
        
        except Exception as e:
            error_code = "FRAME_PROCESSING_ERROR"
            logger.error(f"[{STAGE_NAME}] Error during frame processing: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        finally:
            # Clean up model
            try:
                pose_estimator.close()
            except Exception as e:
                logger.debug(f"[{STAGE_NAME}] Error closing estimator: {e}")
        
        # -------- Write to Firestore --------
        
        try:
            db = firestore_client.db
            
            if poses:
                written = write_pose_batch(firestore_client, video_id_safe, poses)
                logger.info(f"[{STAGE_NAME}] Wrote poses to Firestore", extra={
                    "video_id": video_id_safe,
                    "poses_written": written,
                })
            
            # Update stage status with model info
            model_info = pose_estimator.get_model_info() if pose_estimator else {}
            
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.pose.status": "completed",
                "stages.pose.total_poses": len(poses),
                "stages.pose.model_type": model_info.get("model_type", "lite"),
                "stages.pose.model_size_mb": model_info.get("model_size_mb"),
                "stages.pose.confidence_threshold": confidence_threshold,
                "stages.pose.completed_at": get_utc_timestamp(),
            })
            
            logger.info(f"[{STAGE_NAME}] Updated stage status", extra={
                "video_id": video_id_safe,
                "poses": len(poses),
                "model": model_info.get("model_type", "lite"),
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write to Firestore: {e}", extra={
                "video_id": video_id_safe,
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
                metadata={
                    "previous_stage": STAGE_NAME,
                    "pose_count": len(poses),
                    "model_type": model_info.get("model_type", "lite"),
                }
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
                "model": model_info.get("model_type", "lite"),
            })
        
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "next_stage": STAGE_NEXT,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- SUCCESS --------
        
        logger.info(f"[{STAGE_NAME}] Stage completed successfully (lightweight)", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "next_stage": STAGE_NEXT,
            "poses_extracted": len(poses),
            "model": model_info.get("model_type", "lite"),
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
