"""
Rep Extraction Stage (Stage 7/9) - Rep Segmentation
Groups frames into discrete reps using pose trajectory analysis.
"""

import logging
from collections import defaultdict
from typing import Optional, Dict, Any, List
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

        multi_player = getattr(settings, 'MULTI_PLAYER_TRACKING_ENABLED', False)

        try:
            frames_by_track: Dict[Optional[int], List[Frame]] = defaultdict(list)
            for idx, pose_doc in enumerate(poses_docs):
                pose_data = pose_doc.to_dict()
                keypoints_dict = {k: v for k, v in pose_data.get("landmarks", {}).items()}
                track_id = pose_data.get("track_id") if multi_player else None
                frame = Frame(
                    frame_id=pose_data.get("frame_index", idx),
                    timestamp=pose_data.get("frame_index", idx) / settings.FRAME_EXTRACTION_FPS,
                    track_id=track_id, jersey_number=None, jersey_confidence=0.0,
                    position=settings.DEFAULT_PLAYER_POSITION, keypoints=keypoints_dict
                )
                frames_by_track[track_id].append(frame)
            # Each track's frames are only the frames that track was actually
            # detected in (temporally sparse relative to the whole video) -
            # sort by frame_id since Firestore stream() order isn't guaranteed.
            for track_id in frames_by_track:
                frames_by_track[track_id].sort(key=lambda f: f.frame_id)
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Frame building failed", extra={"error_code": "FRAME_PROCESSING_ERROR"})
            return False, "FRAME_PROCESSING_ERROR"

        # Pull each track's jersey number once (not per rep) for convenience.
        jersey_by_track: Dict[Optional[int], Optional[str]] = {}
        if multi_player:
            for track_id in frames_by_track:
                if track_id is None:
                    continue
                try:
                    meta_doc = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                        video_id_safe).collection("tracks_meta").document(f"{track_id:03d}").get()
                    jersey_by_track[track_id] = meta_doc.to_dict().get("jersey_number") if meta_doc.exists else None
                except Exception:
                    jersey_by_track[track_id] = None

        reps_data = []

        for track_id, frames in frames_by_track.items():
            try:
                rep_extractor = RepExtractor(
                    max_static_frames=settings.REP_MAX_STATIC_FRAMES,
                    max_gap_frames=settings.REP_MAX_GAP_FRAMES,
                    min_rep_duration=settings.REP_MIN_DURATION,
                    max_rep_duration=settings.REP_MAX_DURATION
                )

                # ✅ OPTIMIZATION: Try fast trajectory analyzer first (Phase 2 #7, 60% faster)
                try:
                    logger.info(f"[{STAGE_NAME}] Trying fast trajectory analysis...", extra={
                        "video_id": video_id_safe, "track_id": track_id,
                    })

                    # Extract keypoints as dicts for fast analyzer
                    keypoints_sequence = [frame.keypoints for frame in frames]

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
                                   extra={"video_id": video_id_safe, "track_id": track_id})
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
                                   extra={"video_id": video_id_safe, "track_id": track_id})
                        reps = rep_extractor.extract_reps(frames)
                except Exception as fast_error:
                    logger.warning(f"[{STAGE_NAME}] Fast trajectory failed: {fast_error}, falling back to standard",
                                  extra={"video_id": video_id_safe, "track_id": track_id})
                    reps = rep_extractor.extract_reps(frames)

            except Exception as e:
                logger.warning(f"[{STAGE_NAME}] Rep extraction failed for track {track_id}: {e}")
                reps = []

            for rep_idx, rep in enumerate(reps):
                rep_meta = {
                    "rep_index": rep_idx,
                    "start_frame": rep.start_frame,
                    "end_frame": rep.end_frame,
                    "duration_frames": rep.duration_frames,
                    "duration_seconds": rep.duration_frames / settings.FRAME_EXTRACTION_FPS,
                }
                if track_id is not None:
                    rep_meta["track_id"] = track_id
                    rep_meta["jersey_number"] = jersey_by_track.get(track_id)
                reps_data.append(rep_meta)

        try:
            if reps_data:
                write_reps_batch(firestore_client, video_id_safe, reps_data)

            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.total_reps": len(reps_data),
                f"stages.{STAGE_NAME}.tracks_processed": len(frames_by_track),
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

