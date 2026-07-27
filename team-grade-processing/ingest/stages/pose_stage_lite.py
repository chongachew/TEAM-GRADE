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
from ingest.utils.firestore_utils import get_utc_timestamp, write_pose_batch, get_play, update_stage_status
from processing.pose_track_matching import match_poses_to_tracks
from ingest.s3_client import ensure_frames_local
from ingest.frame_range import list_frame_paths_by_index

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
        
        # -------- Scope to this play, if any --------

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

        # -------- Load frames --------

        try:
            frames_dir = settings.get_frames_dir(video_id_safe)
            # Defensive guard: normally already-local (same worker process as
            # frame_extraction), but a retry could be picked up by a
            # different worker instance - cheap check-then-fetch from S3.
            try:
                ensure_frames_local(video_id_safe)
            except Exception as e:
                logger.debug(f"[{STAGE_NAME}] S3 frames fallback unavailable (non-fatal): {e}", extra={
                    "video_id": video_id_safe,
                })

            if not frames_dir.exists():
                error_code = "FRAMES_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Frames directory not found", extra={
                    "video_id": video_id_safe,
                    "path": str(frames_dir),
                    "error_code": error_code
                })
                return False, error_code

            # Keyed by the real frame_index parsed from each filename, not
            # list position - see detection_stage.py's identical comment.
            frame_paths_by_index = list_frame_paths_by_index(video_id_safe, start_frame, end_frame)
            ordered_frames = sorted(frame_paths_by_index.items())
            frame_paths = [p for _, p in ordered_frames]  # positional list kept for len()/progress logging only

            if not ordered_frames:
                error_code = "FRAMES_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] No frames found", extra={
                    "video_id": video_id_safe,
                    "directory": str(frames_dir),
                    "play_index": play_index,
                    "error_code": error_code
                })
                return False, error_code

            logger.info(f"[{STAGE_NAME}] Found frames", extra={
                "video_id": video_id_safe,
                "frame_count": len(ordered_frames),
                "play_index": play_index,
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
            multi_player = getattr(settings, 'MULTI_PLAYER_TRACKING_ENABLED', False)
            num_poses = settings.POSE_MAX_PLAYERS if multi_player else 1

            pose_estimator = PoseEstimatorFactory.create(
                use_lite=use_lite,
                device=device,
                fallback_to_full=True,
                num_poses=num_poses
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
        
        # -------- Load per-frame tracks (multi-player path only) --------

        frame_tracks_by_index = {}
        if multi_player:
            try:
                tracks_ref = firestore_client.db.collection(
                    settings.COLLECTION_VIDEOS
                ).document(video_id_safe).collection("tracks")
                if play_index is not None:
                    tracks_ref = tracks_ref.where("play_index", "==", play_index)
                for doc in tracks_ref.stream():
                    data = doc.to_dict()
                    frame_tracks_by_index.setdefault(data["frame_index"], []).append(data)
            except Exception as e:
                error_code = "FIRESTORE_ERROR"
                logger.error(f"[{STAGE_NAME}] Failed to read tracks: {e}", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code

        # -------- Process frames --------

        poses = []
        poses_written_total = 0
        high_confidence_count = 0
        confidence_threshold = getattr(
            settings,
            'POSE_CONFIDENCE_THRESHOLD',
            0.7
        )
        # Flushed periodically during the frame loop below (not just once at
        # the end) so pose data becomes queryable while this stage is still
        # running - powers the analysis API's provisional-plays preview
        # (get_analysis in api/server.py). Reuses POSE_BATCH_FRAME_COUNT,
        # an existing settings constant that was never actually wired up
        # anywhere before this.
        streaming_batch_size = getattr(settings, 'POSE_BATCH_FRAME_COUNT', 10)

        try:
            import cv2

            logger.info(f"[{STAGE_NAME}] Processing frames with confidence threshold {confidence_threshold}", extra={
                "video_id": video_id_safe,
            })

            for idx, frame_path in ordered_frames:
                try:
                    frame = cv2.imread(str(frame_path))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_path.name}")
                        continue

                    if multi_player:
                        # One multi-pose call returns every detected person in
                        # this frame; assign each to a track_id via IoU against
                        # this frame's tracked boxes (see pose_track_matching.py)
                        # rather than one MediaPipe call per player.
                        pose_sets = pose_estimator.estimate_multi(frame)
                        # estimate_multi() returns (landmarks_dict, confidence)
                        # tuples; match_poses_to_tracks() operates on bare
                        # landmarks dicts (it computes its own per-keypoint
                        # bbox, not the whole-pose confidence estimate_multi
                        # already gave us) - drop the confidence element here.
                        landmark_sets = [landmarks for landmarks, _confidence in pose_sets]
                        frame_tracks = frame_tracks_by_index.get(idx, [])
                        matched = match_poses_to_tracks(
                            landmark_sets, frame_tracks, frame.shape,
                            iou_threshold=settings.POSE_TRACK_IOU_THRESHOLD
                        ) if landmark_sets and frame_tracks else []

                        for landmarks, track_id in matched:
                            confidences = [kp.get("confidence", 0.0) for kp in landmarks.values()]
                            confidence = sum(confidences) / len(confidences) if confidences else 0.0
                            if confidence >= confidence_threshold and landmarks:
                                high_confidence_count += 1
                                poses.append({
                                    "frame_index": idx,
                                    "track_id": track_id,
                                    "timestamp_seconds": idx / getattr(
                                        settings, 'FRAME_EXTRACTION_FPS', 15
                                    ),
                                    "landmarks": landmarks,
                                    "confidence_mean": float(confidence),
                                    "created_at": get_utc_timestamp(),
                                    "play_index": play_index,
                                })
                    else:
                        # Single-athlete path - unchanged from before this pivot.
                        landmarks, confidence = pose_estimator.estimate(frame)

                        if confidence >= confidence_threshold and landmarks:
                            high_confidence_count += 1
                            poses.append({
                                "frame_index": idx,
                                "timestamp_seconds": idx / getattr(
                                    settings, 'FRAME_EXTRACTION_FPS', 15
                                ),
                                "landmarks": landmarks,
                                "confidence_mean": float(confidence),
                                "created_at": get_utc_timestamp(),
                                "play_index": play_index,
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

                    # Flush this batch so it's queryable immediately, rather
                    # than holding everything in memory until the whole video
                    # is done. Non-fatal if it fails - logged and skipped,
                    # matching the rest of this stage's per-frame error
                    # handling; the final flush below still catches anything
                    # this batch missed.
                    if len(poses) >= streaming_batch_size:
                        try:
                            written = write_pose_batch(
                                firestore_client, video_id_safe, poses, batch_size=streaming_batch_size
                            )
                            poses_written_total += written
                            poses = []
                        except Exception as flush_error:
                            logger.warning(f"[{STAGE_NAME}] Streaming pose flush failed (non-fatal): {flush_error}", extra={
                                "video_id": video_id_safe,
                                "frame_index": idx,
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
                "high_confidence": poses_written_total + len(poses),
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
            # Final flush - whatever's left over from the last partial batch
            # (streaming_batch_size may not divide the frame count evenly).
            if poses:
                written = write_pose_batch(firestore_client, video_id_safe, poses)
                poses_written_total += written
                logger.info(f"[{STAGE_NAME}] Wrote final pose batch to Firestore", extra={
                    "video_id": video_id_safe,
                    "poses_written": written,
                })

            # Update stage status with model info
            model_info = pose_estimator.get_model_info() if pose_estimator else {}

            update_stage_status(
                firestore_client, video_id_safe, STAGE_NAME, "completed",
                metadata={
                    "total_poses": poses_written_total,
                    "model_type": model_info.get("model_type", "lite"),
                    "model_size_mb": model_info.get("model_size_mb"),
                    "confidence_threshold": confidence_threshold,
                },
                play_index=play_index,
            )

            logger.info(f"[{STAGE_NAME}] Updated stage status", extra={
                "video_id": video_id_safe,
                "poses": poses_written_total,
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
                play_index=play_index,
                metadata={
                    "previous_stage": STAGE_NAME,
                    "pose_count": poses_written_total,
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
            "poses_extracted": poses_written_total,
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
