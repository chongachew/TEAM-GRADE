"""
Rep Extraction Stage (Stage 7/9) - Rep Segmentation
Groups frames into discrete reps using pose trajectory analysis.
"""

import logging
from typing import Optional, Dict, Any
import numpy as np

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

        from processing.rep_extraction import RepExtractor, Frame
        from processing.fast_trajectory import FastTrajectoryAnalyzer

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

        try:
            frames = []
            for idx, pose_doc in enumerate(poses_docs):
                pose_data = pose_doc.to_dict()
                keypoints_dict = {k: v for k, v in pose_data.get("landmarks", {}).items()}
                frame = Frame(
                    frame_id=pose_data.get("frame_index", idx),
                    timestamp=pose_data.get("frame_index", idx) / settings.FRAME_EXTRACTION_FPS,
                    track_id=None, jersey_number=None, jersey_confidence=0.0,
                    position=settings.DEFAULT_PLAYER_POSITION, keypoints=keypoints_dict
                )
                frames.append(frame)
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Frame building failed", extra={"error_code": "FRAME_PROCESSING_ERROR"})
            return False, "FRAME_PROCESSING_ERROR"

        try:
            rep_extractor = RepExtractor(
                max_static_frames=settings.REP_MAX_STATIC_FRAMES,
                max_gap_frames=settings.REP_MAX_GAP_FRAMES,
                min_rep_duration=settings.REP_MIN_DURATION,
                max_rep_duration=settings.REP_MAX_DURATION
            )
            
            # ✅ OPTIMIZATION: Try fast trajectory analyzer first (Phase 2 #7, 60% faster)
            try:
                logger.info(f"[{STAGE_NAME}] Trying fast trajectory analysis...", extra={"video_id": video_id_safe})
                
                # Extract keypoints as dicts for fast analyzer
                keypoints_sequence = []
                for frame in frames:
                    keypoints_sequence.append(frame.keypoints)
                
                # Use fast trajectory analyzer (O(n log n) instead of O(n²))
                fast_analyzer = FastTrajectoryAnalyzer(
                    position_threshold=50.0,
                    movement_threshold=10.0,
                    rest_threshold=30.0
                )
                
                rep_segments = fast_analyzer.segment_into_reps_fast(
                    keypoints_sequence,
                    frame_shape=(540, 960, 3),  # Standard football video size
                    min_rep_length=settings.REP_MIN_DURATION
                )
                
                # Convert segments to Rep objects
                if rep_segments:
                    logger.info(f"[{STAGE_NAME}] Fast analyzer found {len(rep_segments)} reps", 
                               extra={"video_id": video_id_safe})
                    reps = []
                    for rep_idx, (start, end) in enumerate(rep_segments):
                        # Create minimal rep-like object
                        rep = type('Rep', (), {
                            'start_frame': start,
                            'end_frame': end,
                            'duration_frames': end - start + 1
                        })()
                        reps.append(rep)
                else:
                    logger.info(f"[{STAGE_NAME}] Fast analyzer found no reps, using standard extractor",
                               extra={"video_id": video_id_safe})
                    reps = rep_extractor.extract_reps(frames)
            except Exception as fast_error:
                logger.warning(f"[{STAGE_NAME}] Fast trajectory failed: {fast_error}, falling back to standard",
                              extra={"video_id": video_id_safe})
                reps = rep_extractor.extract_reps(frames)
                
        except Exception as e:
            logger.warning(f"[{STAGE_NAME}] Rep extraction failed: {e}")
            reps = []

        try:
            reps_data = []
            for rep_idx, rep in enumerate(reps):
                rep_meta = {
                    "rep_index": rep_idx,
                    "start_frame": rep.start_frame,
                    "end_frame": rep.end_frame,
                    "duration_frames": rep.duration_frames,
                    "duration_seconds": rep.duration_frames / settings.FRAME_EXTRACTION_FPS,
                }
                reps_data.append(rep_meta)
            
            if reps_data:
                write_reps_batch(firestore_client, video_id_safe, reps_data)
            
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.total_reps": len(reps_data),
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

        if not pose_docs:
            error_msg = "No pose data found (pose stage was skipped)"
            logger.warning(f"[REPS][video_id={video_id_safe}] {error_msg}")
            
            # Mark stage as skipped
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.rep_extraction.status": "skipped",
                "stages.rep_extraction.reason": error_msg,
                "stages.rep_extraction.skipped_at": get_utc_timestamp()
            })
            
            # Proceed to next stage (biomechanics)
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_BIOMECHANICS,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                metadata={
                    "previous_stage": STAGE_REP_EXTRACTION,
                    "rep_extraction_skipped": "true",
                    "reason": error_msg
                }
            )
            
            if success:
                logger.info(f"[REPS][video_id={video_id_safe}] Skipped rep extraction, continuing to biomechanics")
                return True, None
            else:
                return False, f"Failed to enqueue biomechanics stage"

        logger.info(
            f"[REPS][video_id={video_id_safe}] Found {len(pose_docs)} pose documents"
        )

        # Convert pose documents to Frame objects
        frames = []
        for idx, pose_doc in enumerate(pose_docs):
            pose_data = pose_doc.to_dict()
            frame_index = pose_data.get("frame_index", idx)

            # Convert keypoints to proper format
            keypoints_list = pose_data.get("keypoints", [])
            keypoints_dict = {}

            for kp in keypoints_list:
                if isinstance(kp, dict):
                    kp_name = kp.get("name", f"keypoint_{kp.get('index', 0)}")
                    keypoints_dict[kp_name] = {
                        "x": kp.get("x", 0.0),
                        "y": kp.get("y", 0.0),
                        "z": kp.get("z", 0.0),
                        "confidence": kp.get("confidence", 0.0)
                    }

            # Create Frame object
            frame = Frame(
                frame_id=frame_index,
                timestamp=frame_index / settings.VIDEO_FPS,  # Configurable FPS
                track_id=None,
                jersey_number=None,
                jersey_confidence=0.0,
                position=settings.DEFAULT_PLAYER_POSITION,  # Default position
                keypoints=keypoints_dict
            )

            frames.append(frame)

        logger.info(f"[REPS][video_id={video_id_safe}] Built {len(frames)} frame objects")

        # Initialize rep extractor
        try:
            rep_extractor = RepExtractor(
                max_static_frames=settings.REP_MAX_STATIC_FRAMES,  # 30
                max_gap_frames=settings.REP_MAX_GAP_FRAMES,  # 10
                min_rep_duration=settings.REP_MIN_DURATION,  # 15
                max_rep_duration=settings.REP_MAX_DURATION  # 300
            )
        except Exception as e:
            error_msg = f"Failed to initialize rep extractor: {e}"
            logger.error(f"[REPS][video_id={video_id_safe}] {error_msg}")
            return False, error_msg

        # Extract reps
        try:
            reps = rep_extractor.extract_reps(frames)
        except Exception as e:
            logger.warning(
                f"[REPS][video_id={video_id_safe}] Rep extraction failed: {e}, "
                f"continuing with any reps found"
            )
            reps = []

        if not reps:
            logger.warning(
                f"[REPS][video_id={video_id_safe}] No reps extracted from trajectory"
            )
            # Don't fail; just log warning

        logger.info(f"[REPS][video_id={video_id_safe}] Extracted {len(reps)} reps")

        # Convert reps to Firestore format
        reps_data = []
        for rep_idx, rep in enumerate(reps):
            rep_meta = {
                "rep_index": rep_idx,
                "start_frame": rep.start_frame,
                "end_frame": rep.end_frame,
                "duration_frames": rep.duration_frames,
                "duration_seconds": rep.duration_frames / settings.VIDEO_FPS,  # Configurable FPS
                "player_id": rep.player_id,
                "jersey_number": rep.jersey_number,
                "position": rep.position,
                "position_confidence": rep.position_confidence
            }

            if rep.metadata:
                rep_meta["metadata"] = rep.metadata

            reps_data.append(rep_meta)

        # Write reps to Firestore
        if reps_data:
            reps_written = write_reps_batch(
                firestore_client,
                video_id_safe,
                reps_data,
                batch_size=settings.REP_BATCH_SIZE  # 10
            )

            logger.info(
                f"[REPS][video_id={video_id_safe}] Wrote {reps_written} rep documents"
            )

        # Update stage status
        db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
            "stages.rep_extraction.status": "completed",
            "stages.rep_extraction.total_reps": len(reps_data),
            "stages.rep_extraction.completed_at": get_utc_timestamp()
        })

        logger.info(
            f"[REPS][video_id={video_id_safe}] Rep extraction stage completed, "
            f"reps={len(reps_data)}"
        )

        # Enqueue next stage: biomechanics
        success = queue_manager.enqueue_video(
            video_id_safe,
            stage=STAGE_BIOMECHANICS,
            priority=settings.QUEUE_DEFAULT_PRIORITY,
            metadata={
                "previous_stage": STAGE_REP_EXTRACTION,
                "total_reps": len(reps_data)
            }
        )

        if not success:
            logger.error(
                f"[REPS][video_id={video_id_safe}] Failed to enqueue {STAGE_BIOMECHANICS} stage"
            )
            return False, f"Failed to enqueue {STAGE_BIOMECHANICS} stage"

        return True, None

    except ValueError as e:
        # Validation/configuration error - don't retry
        error_msg = f"Rep extraction stage validation error: {str(e)}"
        logger.error(f"[REPS][video_id={video_id_safe}] {error_msg}")
        return False, error_msg
    except Exception as e:
        # Transient error - may be retried
        error_msg = f"Rep extraction stage failed: {str(e)}"
        logger.warning(f"[REPS][video_id={video_id_safe}] {error_msg}")
        return False, error_msg
