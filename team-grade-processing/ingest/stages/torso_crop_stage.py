"""
Torso Crop Stage (Stage 5/9) - Region Of Interest Extraction
Crops torso region from frames using pose keypoints as guides.
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
from ingest.utils.firestore_utils import get_utc_timestamp, write_torso_crops_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "torso_crop"
STAGE_NEXT = "jersey_ocr"


def run_torso_crop_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute torso crop stage.
    
    Stage 5/9: Crop torso region from frames using pose keypoints.
    Uses shoulder and hip keypoints to define crop region.
    
    Args:
        firestore_client: Google Cloud Firestore client
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration
    
    Returns:
        (success: bool, error_code: Optional[str])
    """
    try:
        # Validate inputs
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
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
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting torso crop stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        import cv2
        from processing.torso_cropper import TorsoCropper

        # Get frames directory
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

        # Get torso crops directory
        torso_dir = settings.get_torso_crops_dir(video_id_safe)
        torso_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"[{STAGE_NAME}] Found frames", extra={
            "video_id": video_id_safe,
            "frame_count": len(frame_paths),
        })

        # Initialize torso cropper
        try:
            cropper = TorsoCropper()
        except Exception as e:
            error_code = "CROPPER_INIT_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to initialize cropper: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Read pose data from Firestore
        try:
            poses_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe
            ).collection("pose")
            poses_docs = list(poses_ref.stream())
            
            if not poses_docs:
                error_code = "POSE_DATA_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] No pose data found in Firestore", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code
            
            logger.info(f"[{STAGE_NAME}] Retrieved pose data", extra={
                "video_id": video_id_safe,
                "pose_count": len(poses_docs),
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to read poses from Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Process frames and crop
        torso_crops = []
        crops_created = 0

        try:
            for pose_doc in poses_docs:
                try:
                    pose_data = pose_doc.to_dict()
                    frame_index = pose_data.get("frame_index")
                    landmarks = pose_data.get("landmarks", [])
                    
                    if frame_index >= len(frame_paths):
                        continue
                    
                    frame = cv2.imread(str(frame_paths[frame_index]))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_index}")
                        continue
                    
                    # Crop torso region using landmarks
                    crop, crop_box = cropper.crop_torso(frame, landmarks)
                    
                    if crop is not None and crop.shape[0] > 0 and crop.shape[1] > 0:
                        crop_path = torso_dir / f"torso_{frame_index:06d}.jpg"
                        cv2.imwrite(str(crop_path), crop)
                        
                        crops_created += 1
                        torso_crops.append({
                            "frame_index": frame_index,
                            "crop_path": str(crop_path.relative_to(settings.BASE_DIR)),
                            "crop_box": crop_box,
                            "created_at": get_utc_timestamp()
                        })
                    
                    if crops_created % settings.POSE_PROGRESS_LOG_INTERVAL == 0:
                        logger.info(f"[{STAGE_NAME}] Progress", extra={
                            "video_id": video_id_safe,
                            "crops_created": crops_created,
                        })
                
                except Exception as frame_error:
                    logger.warning(f"[{STAGE_NAME}] Error processing frame {frame_index}: {frame_error}", extra={
                        "video_id": video_id_safe,
                        "frame_index": frame_index,
                    })
                    continue
            
            logger.info(f"[{STAGE_NAME}] Torso cropping complete", extra={
                "video_id": video_id_safe,
                "total_processed": len(poses_docs),
                "crops_created": crops_created,
            })
        
        except Exception as e:
            error_code = "FRAME_PROCESSING_ERROR"
            logger.error(f"[{STAGE_NAME}] Error during frame processing: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        # Write to Firestore
        try:
            if torso_crops:
                written = write_torso_crops_batch(firestore_client, video_id_safe, torso_crops)
                logger.info(f"[{STAGE_NAME}] Wrote crops to Firestore", extra={
                    "video_id": video_id_safe,
                    "crops_written": written,
                })
            
            # Update stage status
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.torso_crop.status": "completed",
                "stages.torso_crop.total_crops": crops_created,
                "stages.torso_crop.completed_at": get_utc_timestamp(),
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write to Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Enqueue next stage
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
