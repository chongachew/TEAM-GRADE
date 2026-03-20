"""
Firestore Utilities
Helper functions for writing ingestion pipeline data to Firestore.
Handles batching, transactions, and structured data writes.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from google.cloud import firestore
from config import settings

logger = logging.getLogger(__name__)

# Collection name constants (aligned with config)
COLLECTION_VIDEOS = "videos"
COLLECTION_FRAMES = "frames"
COLLECTION_POSE = "pose"
COLLECTION_TORSO = "torso"
COLLECTION_REPS = "reps"


def get_utc_timestamp() -> str:
    """Get current UTC timestamp as ISO string.

    Returns:
        ISO format UTC datetime string
    """
    return datetime.now(timezone.utc).isoformat()


def write_video_metadata(
    firestore_client,
    video_id: str,
    metadata: Dict[str, Any]
) -> bool:
    """Write or update video metadata document.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        metadata: Metadata dict

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id to prevent path traversal
        safe_id = settings.sanitize_id(video_id)
        doc_ref = firestore_client.db.collection(COLLECTION_VIDEOS).document(safe_id)
        doc_ref.update(metadata)
        logger.info(f"[FS] Updated video metadata: {safe_id}")
        return True
    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to write video metadata: {e}")
        return False


def write_frames_batch(
    firestore_client,
    video_id: str,
    frames: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write frame metadata to Firestore in batches.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        frames: List of frame dicts with keys: frame_index, timestamp_seconds, path, width, height
        batch_size: Number of documents per batch write (capped at 500 for Firestore limit)

    Returns:
        Number of frames written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        # Validate and clamp batch size (Firestore limit is 500 ops/batch)
        safe_batch_size = max(1, min(batch_size, 500))
        if batch_size != safe_batch_size:
            logger.warning(f"[FS] Batch size {batch_size} adjusted to {safe_batch_size}")
        
        db = firestore_client.db
        written = 0

        for i in range(0, len(frames), safe_batch_size):
            batch = db.batch()
            batch_frames = frames[i : i + safe_batch_size]

            for frame in batch_frames:
                # Validate frame_index exists
                if 'frame_index' not in frame:
                    raise ValueError(f"Frame missing 'frame_index': {frame}")
                
                frame_id = f"{frame['frame_index']:06d}"
                doc_ref = (
                    db.collection(COLLECTION_VIDEOS)
                    .document(safe_id)
                    .collection(COLLECTION_FRAMES)
                    .document(frame_id)
                )
                batch.set(doc_ref, frame)

            batch.commit()
            written += len(batch_frames)
            logger.debug(f"[FS] Wrote frame batch: {written}/{len(frames)}")

        logger.info(f"[FS] Wrote {written} frame documents for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[FS] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[FS] Failed to write frames batch: {e}")
        return 0


def write_pose_batch(
    firestore_client,
    video_id: str,
    poses: List[Dict[str, Any]],
    batch_size: int = 20
) -> int:
    """Write pose keypoints to Firestore in batches.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        poses: List of pose dicts with keys: frame_index, model, keypoints, confidence, processing_time_ms (optional)
        batch_size: Number of documents per batch write (capped at 500)

    Returns:
        Number of pose documents written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        # Validate and clamp batch size
        safe_batch_size = max(1, min(batch_size, 500))
        
        db = firestore_client.db
        written = 0

        for i in range(0, len(poses), safe_batch_size):
            batch = db.batch()
            batch_poses = poses[i : i + safe_batch_size]

            for pose in batch_poses:
                # Validate frame_index exists
                if 'frame_index' not in pose:
                    raise ValueError(f"Pose missing 'frame_index': {pose}")
                
                frame_id = f"{pose['frame_index']:06d}"
                doc_ref = (
                    db.collection(COLLECTION_VIDEOS)
                    .document(safe_id)
                    .collection(COLLECTION_POSE)
                    .document(frame_id)
                )
                batch.set(doc_ref, pose)

            batch.commit()
            written += len(batch_poses)

        logger.info(f"[FS] Wrote {written} pose documents for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[FS] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[FS] Failed to write poses batch: {e}")
        return 0


def write_torso_crops_batch(
    firestore_client,
    video_id: str,
    crops: List[Dict[str, Any]],
    batch_size: int = 50
) -> int:
    """Write torso crop metadata to Firestore in batches.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        crops: List of crop dicts with keys: frame_index, crop_path, pose_confidence, bbox, processing_time_ms (optional)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of crop documents written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        # Validate and clamp batch size
        safe_batch_size = max(1, min(batch_size, 500))
        
        db = firestore_client.db
        written = 0

        for i in range(0, len(crops), safe_batch_size):
            batch = db.batch()
            batch_crops = crops[i : i + safe_batch_size]

            for crop in batch_crops:
                # Validate frame_index exists
                if 'frame_index' not in crop:
                    raise ValueError(f"Crop missing 'frame_index': {crop}")
                
                frame_id = f"{crop['frame_index']:06d}"
                doc_ref = (
                    db.collection(COLLECTION_VIDEOS)
                    .document(safe_id)
                    .collection(COLLECTION_TORSO)
                    .document(frame_id)
                )
                batch.set(doc_ref, crop)

            batch.commit()
            written += len(batch_crops)

        logger.info(f"[FS] Wrote {written} torso crop documents for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[FS] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[FS] Failed to write torso crops: {e}")
        return 0


def write_reps_batch(
    firestore_client,
    video_id: str,
    reps: List[Dict[str, Any]],
    batch_size: int = 10
) -> int:
    """Write rep data to Firestore.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        reps: List of rep dicts with keys: rep_index, start_frame, end_frame, duration_frames, duration_seconds, play_type (optional), metrics (optional)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of reps written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        # Validate and clamp batch size
        safe_batch_size = max(1, min(batch_size, 500))
        
        db = firestore_client.db
        written = 0

        for i in range(0, len(reps), safe_batch_size):
            batch = db.batch()
            batch_reps = reps[i : i + safe_batch_size]

            for rep in batch_reps:
                # Validate rep_index exists
                if 'rep_index' not in rep:
                    raise ValueError(f"Rep missing 'rep_index': {rep}")
                
                rep_id = f"{rep['rep_index']:06d}"
                doc_ref = (
                    db.collection(COLLECTION_VIDEOS)
                    .document(safe_id)
                    .collection(COLLECTION_REPS)
                    .document(rep_id)
                )
                batch.set(doc_ref, rep)

            batch.commit()
            written += len(batch_reps)

        logger.info(f"[FS] Wrote {written} rep documents for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[FS] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[FS] Failed to write reps: {e}")
        return 0


def update_stage_status(
    firestore_client,
    video_id: str,
    stage_name: str,
    status: str,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """Update stage status in video document.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        stage_name: Stage name (e.g., "download", "pose")
        status: Status ("pending", "completed", "failed", "skipped")
        metadata: Additional metadata to store

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        db = firestore_client.db
        doc_ref = db.collection(COLLECTION_VIDEOS).document(safe_id)

        # Build stage update
        stage_data = {
            "status": status,
            "completed_at": get_utc_timestamp()
        }

        if metadata:
            stage_data.update(metadata)

        # Update nested stage field
        doc_ref.update({f"stages.{stage_name}": stage_data})

        logger.info(
            f"[FS] Updated stage status: {safe_id}/{stage_name} = {status}"
        )
        return True

    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to update stage status: {e}")
        return False


def write_analysis_results(
    firestore_client,
    video_id: str,
    analysis: Dict[str, Any]
) -> bool:
    """Write biomechanics analysis results to video document.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        analysis: Analysis dict with keys:
                  - per_rep (dict)
                  - summary (dict)

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        db = firestore_client.db
        doc_ref = db.collection(COLLECTION_VIDEOS).document(safe_id)
        doc_ref.update({"analysis": analysis})

        logger.info(f"[FS] Wrote analysis results for {safe_id}")
        return True

    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to write analysis results: {e}")
        return False


def update_rep_analysis(
    firestore_client,
    video_id: str,
    rep_id: str,
    analysis: Dict[str, Any]
) -> bool:
    """Update biomechanics analysis data for a specific rep.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        rep_id: Rep ID
        analysis: Analysis data (traits, metrics, position, etc.)

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        db = firestore_client.db
        doc_ref = (
            db.collection(COLLECTION_VIDEOS)
            .document(safe_id)
            .collection(COLLECTION_REPS)
            .document(rep_id)
        )
        doc_ref.update({"analysis": analysis})

        logger.info(f"[FS] Updated analysis for rep {rep_id}")
        return True

    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to update rep analysis: {e}")
        return False


def write_jersey_number(
    firestore_client,
    video_id: str,
    jersey_number: str,
    confidence: float,
    source_frame_id: int
) -> bool:
    """Write detected jersey number to video document.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        jersey_number: Detected jersey number (str)
        confidence: OCR confidence [0.0-1.0]
        source_frame_id: Frame where jersey was detected

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        db = firestore_client.db
        doc_ref = db.collection(COLLECTION_VIDEOS).document(safe_id)
        doc_ref.update({
            "jersey_number": jersey_number,
            "jersey_confidence": confidence,
            "jersey_source_frame": source_frame_id
        })

        logger.info(f"[FS] Wrote jersey number: {jersey_number} (conf={confidence:.2f})")
        return True

    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to write jersey number: {e}")
        return False


def mark_stage_attempt(
    firestore_client,
    video_id: str,
    stage_name: str
) -> bool:
    """Mark a stage attempt (for retry tracking).

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID (will be sanitized)
        stage_name: Stage name

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        # Sanitize video_id
        safe_id = settings.sanitize_id(video_id)
        
        db = firestore_client.db
        doc_ref = db.collection(COLLECTION_VIDEOS).document(safe_id)

        # Increment attempt counter
        doc_ref.update({f"stages.{stage_name}.attempt": firestore.Increment(1)})

        return True

    except ValueError as e:
        logger.error(f"[FS] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[FS] Failed to mark stage attempt: {e}")
        return False
