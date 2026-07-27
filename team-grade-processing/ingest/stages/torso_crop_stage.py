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
from ingest.utils.firestore_utils import get_utc_timestamp, write_torso_crops_batch, get_play, update_stage_status
from ingest.s3_client import ensure_frames_local, upload_directory, torso_prefix
from ingest.frame_range import list_frame_paths_by_index

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

        play_index = (payload or {}).get("play_index")
        start_frame = end_frame = None
        if play_index is not None:
            play = get_play(firestore_client, video_id_safe, play_index)
            if play is None:
                error_code = "PLAY_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] No plays row found", extra={
                    "video_id": video_id_safe,
                    "play_index": play_index,
                    "error_code": error_code
                })
                return False, error_code
            start_frame, end_frame = play["start_frame"], play["end_frame"]

        # Defensive guard: this stage runs in the same worker process as
        # frame_extraction on the common path (frames already local), but a
        # retry could in principle be picked up by a different worker
        # instance - cheap check-then-fetch from S3 if that ever happens.
        try:
            ensure_frames_local(video_id_safe)
        except Exception as e:
            logger.debug(f"[{STAGE_NAME}] S3 frames fallback unavailable (non-fatal): {e}", extra={
                "video_id": video_id_safe,
            })
        # Dict lookup by real frame_index, not positional `frame_paths[frame_index]`
        # indexing - the latter silently assumed frame_paths was the full,
        # contiguous, zero-based frame list, which breaks once this is
        # scoped to one play's subset of frames.
        frame_paths_by_index = list_frame_paths_by_index(video_id_safe, start_frame, end_frame)

        if not frame_paths_by_index:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No frames found", extra={
                "video_id": video_id_safe,
                "directory": str(settings.get_frames_dir(video_id_safe)),
                "play_index": play_index,
                "error_code": error_code
            })
            return False, error_code

        # Get torso crops directory
        torso_dir = settings.get_torso_crops_dir(video_id_safe)
        torso_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"[{STAGE_NAME}] Found frames", extra={
            "video_id": video_id_safe,
            "frame_count": len(frame_paths_by_index),
            "play_index": play_index,
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
            if play_index is not None:
                poses_ref = poses_ref.where("play_index", "==", play_index)
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
                    track_id = pose_data.get("track_id")  # None on the single-athlete path

                    frame_path = frame_paths_by_index.get(frame_index)
                    if frame_path is None:
                        continue

                    frame = cv2.imread(str(frame_path))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_index}")
                        continue

                    # Crop torso region using landmarks.
                    # NOTE: crop_torso() returns a single Optional[np.ndarray], not
                    # a (crop, box) tuple - a pre-existing bug (confirmed by reading
                    # processing/torso_cropper.py directly) meant this line always
                    # raised on unpacking, silently caught by the per-frame except
                    # below, so torso_crop_stage never produced a crop regardless
                    # of the separate BASE_DIR bug fixed elsewhere in this file.
                    crop = cropper.crop_torso(frame, landmarks)
                    crop_box = cropper.get_torso_box(landmarks, frame.shape)

                    if crop is not None and crop.shape[0] > 0 and crop.shape[1] > 0:
                        if track_id is not None:
                            crop_path = torso_dir / f"torso_{frame_index:06d}_{track_id:03d}.jpg"
                        else:
                            crop_path = torso_dir / f"torso_{frame_index:06d}.jpg"
                        cv2.imwrite(str(crop_path), crop)

                        crops_created += 1
                        crop_doc = {
                            "frame_index": frame_index,
                            "crop_path": str(crop_path.relative_to(settings.PROJECT_ROOT)),
                            "crop_box": crop_box,
                            "created_at": get_utc_timestamp(),
                            "play_index": play_index,
                        }
                        if track_id is not None:
                            crop_doc["track_id"] = track_id
                        torso_crops.append(crop_doc)
                    
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

        # -------- Upload torso crops to S3 (best-effort; never fails the
        # stage) -------- One bulk upload_directory call, not per-crop -
        # durably persists them so the API container's thumbnail endpoint
        # (which never runs this stage on its own disk) can serve them.
        if crops_created:
            try:
                uploaded = upload_directory(torso_dir, torso_prefix(video_id_safe))
                logger.info(f"[{STAGE_NAME}] Uploaded torso crops to S3", extra={
                    "video_id": video_id_safe,
                    "uploaded_count": uploaded,
                })
            except Exception as e:
                logger.warning(f"[{STAGE_NAME}] Failed to upload torso crops to S3 (non-fatal): {e}", extra={
                    "video_id": video_id_safe,
                })

        # Write to Firestore
        try:
            if torso_crops:
                written = write_torso_crops_batch(firestore_client, video_id_safe, torso_crops)
                logger.info(f"[{STAGE_NAME}] Wrote crops to Firestore", extra={
                    "video_id": video_id_safe,
                    "crops_written": written,
                })
            
            # Update stage status
            update_stage_status(
                firestore_client, video_id_safe, STAGE_NAME, "completed",
                metadata={"total_crops": crops_created},
                play_index=play_index,
            )
        
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
                play_index=play_index,
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
