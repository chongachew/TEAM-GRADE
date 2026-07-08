"""
Pose Estimation Stage (Stage 4/9) - Pose Keypoint Detection
Runs MediaPipe BlazePose to extract 33-point keypoints from frames.
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
from ingest.utils.firestore_utils import get_utc_timestamp, write_pose_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "pose"
STAGE_NEXT = "torso_crop"


def run_pose_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute pose estimation stage.
    
    Stage 4/9: Run MediaPipe BlazePose on all extracted frames.
    Extracts 33-point keypoints for each frame above confidence threshold.
    
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
        logger.info(f"[{STAGE_NAME}] Starting pose estimation", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        from processing.pose_estimation import PoseEstimator
        import cv2

        # Get frames directory and validate
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
            logger.error(f"[{STAGE_NAME}] No frames found in directory", extra={
                "video_id": video_id_safe,
                "directory": str(frames_dir),
                "error_code": error_code
            })
            return False, error_code

        logger.info(f"[{STAGE_NAME}] Found frames", extra={
            "video_id": video_id_safe,
            "frame_count": len(frame_paths),
        })

        # Initialize pose estimator
        try:
            pose_estimator = PoseEstimator(
                model_name=settings.POSE_MODEL_NAME,
                device=settings.POSE_DEVICE
            )
        except Exception as e:
            error_code = "MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to load pose model: {e}", extra={
                "video_id": video_id_safe,
                "model": settings.POSE_MODEL_NAME,
                "device": settings.POSE_DEVICE,
                "error_code": error_code
            })
            return False, error_code

        # Process frames
        poses = []
        high_confidence_count = 0
        confidence_threshold = settings.POSE_CONFIDENCE_THRESHOLD

        try:
            for idx, frame_path in enumerate(frame_paths):
                try:
                    frame = cv2.imread(str(frame_path))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_path.name}")
                        continue
                    
                    landmarks, confidence = pose_estimator.estimate(frame)
                    
                    if confidence >= confidence_threshold:
                        high_confidence_count += 1
                        poses.append({
                            "frame_index": idx,
                            "timestamp_seconds": idx / settings.FRAME_EXTRACTION_FPS,
                            "landmarks": landmarks,
                            "confidence_mean": float(confidence),
                            "created_at": get_utc_timestamp()
                        })
                    
                    if (idx + 1) % settings.POSE_PROGRESS_LOG_INTERVAL == 0:
                        logger.info(f"[{STAGE_NAME}] Progress", extra={
                            "video_id": video_id_safe,
                            "processed": idx + 1,
                            "total": len(frame_paths),
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
            if poses:
                written = write_pose_batch(firestore_client, video_id_safe, poses)
                logger.info(f"[{STAGE_NAME}] Wrote poses to Firestore", extra={
                    "video_id": video_id_safe,
                    "poses_written": written,
                })
            
            # Update stage status
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.pose.status": "completed",
                "stages.pose.total_poses": len(poses),
                "stages.pose.completed_at": get_utc_timestamp(),
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
        confidence_threshold = settings.POSE_CONFIDENCE_THRESHOLD  # 0.7
        log_interval = settings.POSE_PROGRESS_LOG_INTERVAL  # 50
        batch_size = settings.FRAME_EXTRACTION_BATCH_SIZE  # 100
        poses_written = 0

        # Process each frame with streaming writes
        for frame_idx, frame_path in enumerate(frame_paths):
            try:
                # Load frame
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    logger.warning(
                        f"[POSE][video_id={video_id_safe}][frame={frame_idx}] "
                        f"Failed to load frame"
                    )
                    continue

                # Run pose estimation
                pose_result = pose_estimator.estimate_pose(frame)

                # Extract keypoints from result
                keypoints = []
                overall_confidence = 0.0

                if pose_result:
                    # Format depends on pose_estimation.py implementation
                    # Assuming it returns a dict with keypoints
                    if isinstance(pose_result, dict):
                        if "keypoints" in pose_result:
                            keypoints = pose_result["keypoints"]
                            overall_confidence = pose_result.get("confidence", 0.0)
                        else:
                            # Fallback: use all keys as keypoints
                            keypoints = pose_result

                if not keypoints:
                    logger.debug(
                        f"[POSE][video_id={video_id_safe}][frame={frame_idx}] "
                        f"No keypoints detected"
                    )
                    continue

                # Track high-confidence frames
                if overall_confidence >= confidence_threshold:
                    high_confidence_count += 1

                # Store pose data
                pose_data = {
                    "frame_index": frame_idx,
                    "model": settings.POSE_MODEL_NAME,  # "mediapipe"
                    "keypoints": keypoints,
                    "confidence": overall_confidence,
                    "timestamp": get_utc_timestamp(),
                    "processing_time_ms": 0  # Would need timing if needed
                }

                poses.append(pose_data)

                # Stream write in batches to prevent memory explosion
                if len(poses) >= batch_size:
                    batch_written = write_pose_batch(
                        firestore_client,
                        video_id_safe,
                        poses,
                        batch_size=batch_size
                    )
                    poses_written += batch_written
                    poses = []  # Clear batch immediately after writing
                    logger.debug(
                        f"[POSE][video_id={video_id_safe}] Batch write: {batch_written} poses, "
                        f"total_written={poses_written}"
                    )

                # Log progress at configured interval
                if (frame_idx + 1) % log_interval == 0:
                    logger.info(
                        f"[POSE][video_id={video_id_safe}][frame={frame_idx + 1}/"
                        f"{len(frame_paths)}] Processed {frame_idx + 1} frames, "
                        f"written={poses_written}"
                    )

            except Exception as e:
                logger.warning(
                    f"[POSE][video_id={video_id_safe}][frame={frame_idx}] "
                    f"Error processing frame: {e}"
                )
                continue

        # Close pose estimator
        try:
            pose_estimator.close()
        except Exception:
            pass

        # Write any remaining poses (final batch)
        if poses:
            final_batch = write_pose_batch(
                firestore_client,
                video_id_safe,
                poses,
                batch_size=len(poses)
            )
            poses_written += final_batch
            logger.info(f"[POSE][video_id={video_id_safe}] Final batch: {final_batch} poses")

        if poses_written == 0:
            error_msg = "No poses estimated"
            logger.error(f"[POSE][video_id={video_id_safe}] {error_msg}")
            return False, error_msg

        total_frames = len(frame_paths)
        logger.info(
            f"[POSE][video_id={video_id_safe}] Completed: estimated poses for {poses_written} frames, "
            f"high_confidence={high_confidence_count}, "
            f"coverage={100*poses_written/total_frames:.1f}%"
        )

        # Write pose data to Firestore in batches
        # NOTE: Already written via streaming writes above, this is just for reference
        # poses_written calculated during loop

        logger.info(
            f"[POSE][video_id={video_id_safe}] Wrote {poses_written} pose documents "
            f"to Firestore"
        )

        # Update stage status
        db = firestore_client.db
        db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
            "stages.pose.status": "completed",
            "stages.pose.frames_processed": len(poses),
            "stages.pose.high_confidence_frames": high_confidence_count,
            "stages.pose.completed_at": get_utc_timestamp()
        })

        logger.info(f"[POSE][video_id={video_id_safe}] Pose stage completed")

        # Enqueue next stage: torso_crop
        success = queue_manager.enqueue_video(
            video_id_safe,
            stage=STAGE_TORSO_CROP,
            priority=settings.QUEUE_DEFAULT_PRIORITY,
            metadata={
                "previous_stage": STAGE_POSE,
                "frames_processed": len(poses),
                "high_confidence_frames": high_confidence_count
            }
        )

        if not success:
            logger.error(
                f"[POSE][video_id={video_id_safe}] Failed to enqueue {STAGE_TORSO_CROP} stage"
            )
            return False, f"Failed to enqueue {STAGE_TORSO_CROP} stage"

        return True, None

    except ValueError as e:
        # Validation/configuration error - don't retry
        error_msg = f"Pose stage validation error: {str(e)}"
        logger.error(f"[POSE][video_id={video_id_safe}] {error_msg}")
        return False, error_msg
    except Exception as e:
        # Transient error - may be retried
        error_msg = f"Pose estimation stage failed: {str(e)}"
        logger.warning(f"[POSE][video_id={video_id_safe}] {error_msg}")
        return False, error_msg
