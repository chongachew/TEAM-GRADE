"""
Firestore Utilities (Postgres-backed)
Helper functions for writing ingestion pipeline data to Postgres.

NOTE ON THE FILENAME: this module now talks to Postgres, not Firestore (Pass
2a of the data-layer migration). Kept at this file path and with these exact
function signatures on purpose - ~20 files across ingest/stages/*.py and
processing/*.py import from `ingest.utils.firestore_utils` (some via
`ingest.utils`'s re-exports), and none of those call sites needed to change
as part of this migration. A rename is future cleanup, not done here.

Every function still takes a `firestore_client`-named first argument for the
same reason (positional call sites everywhere) - it's now expected to be a
`PostgresClient` instance (see ingest/postgres_client.py), whose `.db`
attribute exposes a small Firestore-compatible shim on top of the same
Postgres tables these functions write via SQLAlchemy Core directly.

write_analysis_results()/update_rep_analysis() from the old Firestore version
are NOT carried over here - both were confirmed dead code (zero real callers;
the actual per-rep analysis write path is biomechanics_stage_vectorized.py's
direct `firestore_client.db...collection("analysis")` batch write, serviced
by the .db compat shim, not by a firestore_utils function).
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from sqlalchemy import select, update as sa_update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from ingest.db_schema import (
    frames as frames_table,
    pose as pose_table,
    torso_crops as torso_crops_table,
    reps as reps_table,
    camera_motion as camera_motion_table,
    whistle_events as whistle_events_table,
    detections as detections_table,
    tracks as tracks_table,
    tracks_meta as tracks_meta_table,
    plays as plays_table,
)
from ingest.postgres_client import (
    upsert_stage_fields,
    upsert_row_nullable_track,
    upsert_row_coalesce_keys,
    tracks_meta_conflict_target,
)

logger = logging.getLogger(__name__)

# Collection name constants (kept for import-compatibility - several stage
# files import these directly, e.g. `from ingest.utils.firestore_utils import
# COLLECTION_TRACKS, COLLECTION_TRACKS_META` in api/server.py).
COLLECTION_VIDEOS = "videos"
COLLECTION_FRAMES = "frames"
COLLECTION_POSE = "pose"
COLLECTION_TORSO = "torso"
COLLECTION_REPS = "reps"
COLLECTION_CAMERA_MOTION = "camera_motion"
COLLECTION_DETECTIONS = "detections"
COLLECTION_TRACKS = "tracks"
COLLECTION_TRACKS_META = "tracks_meta"
COLLECTION_WHISTLE_EVENTS = "whistle_events"
COLLECTION_PLAYS = "plays"


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
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        metadata: Metadata dict

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        firestore_client.db.collection(COLLECTION_VIDEOS).document(safe_id).update(metadata)
        logger.info(f"[PG] Updated video metadata: {safe_id}")
        return True
    except ValueError as e:
        logger.error(f"[PG] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to write video metadata: {e}")
        return False


def _batched(items: List[Dict[str, Any]], batch_size: int):
    safe_batch_size = max(1, min(batch_size, 500))
    for i in range(0, len(items), safe_batch_size):
        yield items[i:i + safe_batch_size]


def write_frames_batch(
    firestore_client,
    video_id: str,
    frames: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write frame metadata to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        frames: List of frame dicts with keys: frame_index, timestamp_seconds, path, width, height
        batch_size: Number of rows per batch write (capped at 500, matching the old Firestore limit)

    Returns:
        Number of frames written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(frames, batch_size):
            rows = []
            for frame in batch:
                if 'frame_index' not in frame:
                    raise ValueError(f"Frame missing 'frame_index': {frame}")
                rows.append({
                    "video_id": safe_id,
                    "frame_index": frame["frame_index"],
                    "timestamp_seconds": frame.get("timestamp_seconds"),
                    "path": frame.get("path"),
                    "width": frame.get("width"),
                    "height": frame.get("height"),
                })
            with engine.begin() as conn:
                for row in rows:
                    stmt = pg_insert(frames_table).values(**row)
                    update_cols = {k: getattr(stmt.excluded, k) for k in row if k not in ("video_id", "frame_index")}
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["video_id", "frame_index"], set_=update_cols
                    )
                    conn.execute(stmt)
            written += len(rows)
            logger.debug(f"[PG] Wrote frame batch: {written}/{len(frames)}")

        logger.info(f"[PG] Wrote {written} frame rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write frames batch: {e}")
        return 0


def write_pose_batch(
    firestore_client,
    video_id: str,
    poses: List[Dict[str, Any]],
    batch_size: int = 20
) -> int:
    """Write pose keypoints to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        poses: List of pose dicts with keys: frame_index, track_id (optional),
               timestamp_seconds, landmarks, confidence_mean, created_at (optional)
        batch_size: Number of rows per batch write (capped at 500)

    Returns:
        Number of pose rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(poses, batch_size):
            with engine.begin() as conn:
                for pose in batch:
                    if 'frame_index' not in pose:
                        raise ValueError(f"Pose missing 'frame_index': {pose}")
                    track_id = pose.get("track_id")
                    values = {
                        "timestamp_seconds": pose.get("timestamp_seconds"),
                        "landmarks": pose.get("landmarks"),
                        "confidence_mean": pose.get("confidence_mean"),
                        "created_at": pose.get("created_at") or get_utc_timestamp(),
                        "play_index": pose.get("play_index"),
                    }
                    upsert_row_nullable_track(
                        conn, "pose",
                        keys={"video_id": safe_id, "frame_index": pose["frame_index"], "track_id": track_id},
                        values=values,
                    )
            written += len(batch)

        logger.info(f"[PG] Wrote {written} pose rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write poses batch: {e}")
        return 0


def write_torso_crops_batch(
    firestore_client,
    video_id: str,
    crops: List[Dict[str, Any]],
    batch_size: int = 50
) -> int:
    """Write torso crop metadata to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        crops: List of crop dicts with keys: frame_index, crop_path, crop_box, track_id (optional)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of crop rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(crops, batch_size):
            with engine.begin() as conn:
                for crop in batch:
                    if 'frame_index' not in crop:
                        raise ValueError(f"Crop missing 'frame_index': {crop}")
                    track_id = crop.get("track_id")
                    values = {
                        "crop_path": crop.get("crop_path"),
                        "crop_box": crop.get("crop_box"),
                        "created_at": crop.get("created_at") or get_utc_timestamp(),
                        "play_index": crop.get("play_index"),
                    }
                    upsert_row_nullable_track(
                        conn, "torso_crops",
                        keys={"video_id": safe_id, "frame_index": crop["frame_index"], "track_id": track_id},
                        values=values,
                    )
            written += len(batch)

        logger.info(f"[PG] Wrote {written} torso crop rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write torso crops: {e}")
        return 0


def write_reps_batch(
    firestore_client,
    video_id: str,
    reps: List[Dict[str, Any]],
    batch_size: int = 10
) -> int:
    """Write rep data to Postgres.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        reps: List of rep dicts with keys: rep_index, start_frame, end_frame,
              duration_frames, duration_seconds, track_id (optional), jersey_number (optional)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of reps written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(reps, batch_size):
            with engine.begin() as conn:
                for rep in batch:
                    if 'rep_index' not in rep:
                        raise ValueError(f"Rep missing 'rep_index': {rep}")
                    track_id = rep.get("track_id")
                    values = {
                        "start_frame": rep.get("start_frame"),
                        "end_frame": rep.get("end_frame"),
                        "duration_frames": rep.get("duration_frames"),
                        "duration_seconds": rep.get("duration_seconds"),
                        "jersey_number": rep.get("jersey_number"),
                    }
                    upsert_row_coalesce_keys(
                        conn, "reps",
                        keys={
                            "video_id": safe_id, "rep_index": rep["rep_index"],
                            "track_id": track_id, "play_index": rep.get("play_index"),
                        },
                        values=values,
                    )
            written += len(batch)

        logger.info(f"[PG] Wrote {written} rep rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write reps: {e}")
        return 0


def write_camera_motion_batch(
    firestore_client,
    video_id: str,
    motion_docs: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write per-frame camera-motion (homography) data to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        motion_docs: List of dicts with keys: frame_index, homography_to_prev, homography_to_ref,
                     translation_x, translation_y, rotation_deg, scale, num_matches, num_inliers,
                     inlier_ratio, low_confidence (optional)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(motion_docs, batch_size):
            with engine.begin() as conn:
                for doc in batch:
                    if 'frame_index' not in doc:
                        raise ValueError(f"Camera motion doc missing 'frame_index': {doc}")
                    row = {
                        "video_id": safe_id,
                        "frame_index": doc["frame_index"],
                        "homography_to_prev": doc.get("homography_to_prev"),
                        "homography_to_ref": doc.get("homography_to_ref"),
                        "translation_x": doc.get("translation_x"),
                        "translation_y": doc.get("translation_y"),
                        "rotation_deg": doc.get("rotation_deg"),
                        "scale": doc.get("scale"),
                        "num_matches": doc.get("num_matches"),
                        "num_inliers": doc.get("num_inliers"),
                        "inlier_ratio": doc.get("inlier_ratio"),
                        "low_confidence": doc.get("low_confidence"),
                    }
                    stmt = pg_insert(camera_motion_table).values(**row)
                    update_cols = {k: getattr(stmt.excluded, k) for k in row if k not in ("video_id", "frame_index")}
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["video_id", "frame_index"], set_=update_cols
                    )
                    conn.execute(stmt)
            written += len(batch)

        logger.info(f"[PG] Wrote {written} camera_motion rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write camera_motion batch: {e}")
        return 0


def write_plays_batch(
    firestore_client,
    video_id: str,
    play_docs: List[Dict[str, Any]],
) -> int:
    """Write play_detection's discovered play ranges to Postgres.
    Idempotent on (video_id, play_index), so a stage retry re-writes the
    same rows rather than accumulating duplicates.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        play_docs: List of dicts with keys: play_index, start_frame,
            end_frame, status, detection_method, confidence (optional)

    Returns:
        Number of rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        with engine.begin() as conn:
            for doc in play_docs:
                if "play_index" not in doc:
                    raise ValueError(f"Play doc missing 'play_index': {doc}")
                row = {
                    "video_id": safe_id,
                    "play_index": doc["play_index"],
                    "start_frame": doc["start_frame"],
                    "end_frame": doc["end_frame"],
                    "status": doc.get("status"),
                    "detection_method": doc.get("detection_method"),
                    "confidence": doc.get("confidence"),
                    "created_at": get_utc_timestamp(),
                }
                stmt = pg_insert(plays_table).values(**row)
                update_cols = {
                    k: getattr(stmt.excluded, k) for k in row
                    if k not in ("video_id", "play_index", "created_at")
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=["video_id", "play_index"], set_=update_cols
                )
                conn.execute(stmt)
                written += 1

        logger.info(f"[PG] Wrote {written} plays rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write plays batch: {e}")
        return 0


def get_play(firestore_client, video_id: str, play_index: int) -> Optional[Dict[str, Any]]:
    """Read one `plays` row (start_frame/end_frame/status/etc.) so a per-play
    stage handler can scope its frame/doc loading to it.

    Returns None if no such (video_id, play_index) row exists.
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        with firestore_client.engine.connect() as conn:
            row = conn.execute(
                select(plays_table)
                .where(plays_table.c.video_id == safe_id)
                .where(plays_table.c.play_index == play_index)
            ).mappings().first()
        return dict(row) if row else None

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return None
    except Exception as e:
        logger.error(f"[PG] Failed to read play {play_index} for {video_id}: {e}")
        return None


def mark_play_status(firestore_client, video_id: str, play_index: int, status: str) -> bool:
    """Update one `plays` row's status (e.g. "completed")."""
    try:
        safe_id = settings.sanitize_id(video_id)
        with firestore_client.engine.begin() as conn:
            conn.execute(
                sa_update(plays_table)
                .where(plays_table.c.video_id == safe_id)
                .where(plays_table.c.play_index == play_index)
                .values(status=status)
            )
        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to mark play {play_index} status for {video_id}: {e}")
        return False


def set_play_clip_ready(firestore_client, video_id: str, play_index: int, ready: bool) -> bool:
    """Update one `plays` row's clip_ready flag, once play_clip_export has
    (or hasn't) successfully cut+uploaded that play's standalone clip."""
    try:
        safe_id = settings.sanitize_id(video_id)
        with firestore_client.engine.begin() as conn:
            conn.execute(
                sa_update(plays_table)
                .where(plays_table.c.video_id == safe_id)
                .where(plays_table.c.play_index == play_index)
                .values(clip_ready=ready)
            )
        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to set clip_ready for {video_id} play {play_index}: {e}")
        return False


def count_incomplete_plays(
    firestore_client, video_id: str,
    completed_status: str = "completed", failed_status: str = "failed",
) -> int:
    """Count how many `plays` rows for this video are still genuinely in
    progress - neither `completed_status` nor `failed_status` - used by
    complete_stage.py's per-play completion guard to decide whether the
    whole video is actually done.

    A permanently-failed play (e.g. no pose data ever found - a kickoff or
    replay-only play with nothing to analyze) is a RESOLVED terminal state,
    same as completed - it must not block the rest of the video from ever
    finishing. See queue_manager.mark_failed's play-permanent-failure path,
    which marks the play "failed" and then re-checks this count itself
    (nothing else would ever re-trigger complete_stage.py for a play that
    never reaches its own biomechanics stage).
    """
    safe_id = settings.sanitize_id(video_id)
    with firestore_client.engine.connect() as conn:
        count = conn.execute(
            select(func.count())
            .select_from(plays_table)
            .where(plays_table.c.video_id == safe_id)
            .where(plays_table.c.status.notin_([completed_status, failed_status]))
        ).scalar_one()
    return count


def write_whistle_events_batch(
    firestore_client,
    video_id: str,
    events: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write detected whistle events to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        events: List of dicts with keys: timestamp, confidence, onset_strength,
                spectral_centroid, duration, type (see processing.audio_analyzer)
        batch_size: Batch size (capped at 500)

    Returns:
        Number of rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0
        event_index = 0

        for batch in _batched(events, batch_size):
            with engine.begin() as conn:
                for doc in batch:
                    if 'timestamp' not in doc:
                        raise ValueError(f"Whistle event doc missing 'timestamp': {doc}")
                    row = {
                        "video_id": safe_id,
                        "event_index": event_index,
                        "timestamp": doc.get("timestamp"),
                        "confidence": doc.get("confidence"),
                        "onset_strength": doc.get("onset_strength"),
                        "spectral_centroid": doc.get("spectral_centroid"),
                        "duration": doc.get("duration"),
                        "type": doc.get("type"),
                    }
                    stmt = pg_insert(whistle_events_table).values(**row)
                    update_cols = {k: getattr(stmt.excluded, k) for k in row if k not in ("video_id", "event_index")}
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["video_id", "event_index"], set_=update_cols
                    )
                    conn.execute(stmt)
                    event_index += 1
            written += len(batch)

        logger.info(f"[PG] Wrote {written} whistle_events rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write whistle_events batch: {e}")
        return 0


def write_detections_batch(
    firestore_client,
    video_id: str,
    detections: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write per-frame player detections to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        detections: List of dicts with keys: frame_index, detection_index, bbox, confidence, class_name
        batch_size: Batch size (capped at 500)

    Returns:
        Number of rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(detections, batch_size):
            with engine.begin() as conn:
                for det in batch:
                    if 'frame_index' not in det or 'detection_index' not in det:
                        raise ValueError(f"Detection missing 'frame_index'/'detection_index': {det}")
                    row = {
                        "video_id": safe_id,
                        "frame_index": det["frame_index"],
                        "detection_index": det["detection_index"],
                        "bbox": det.get("bbox"),
                        "confidence": det.get("confidence"),
                        "class_name": det.get("class_name"),
                        "created_at": det.get("created_at") or get_utc_timestamp(),
                        "play_index": det.get("play_index"),
                    }
                    stmt = pg_insert(detections_table).values(**row)
                    update_cols = {
                        k: getattr(stmt.excluded, k)
                        for k in row if k not in ("video_id", "frame_index", "detection_index")
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["video_id", "frame_index", "detection_index"], set_=update_cols
                    )
                    conn.execute(stmt)
            written += len(batch)

        logger.info(f"[PG] Wrote {written} detection rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write detections batch: {e}")
        return 0


def write_tracks_batch(
    firestore_client,
    video_id: str,
    tracks: List[Dict[str, Any]],
    batch_size: int = 100
) -> int:
    """Write per-frame track assignments to Postgres in batches.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        tracks: List of dicts with keys: frame_index, track_id, bbox, mask_area_px, confidence,
                matched_via, occlusion_state, frames_since_last_seen
        batch_size: Batch size (capped at 500)

    Returns:
        Number of rows written

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        written = 0

        for batch in _batched(tracks, batch_size):
            with engine.begin() as conn:
                for track in batch:
                    if 'frame_index' not in track or 'track_id' not in track:
                        raise ValueError(f"Track doc missing 'frame_index'/'track_id': {track}")
                    row = {
                        "video_id": safe_id,
                        "frame_index": track["frame_index"],
                        "track_id": track["track_id"],
                        "bbox": track.get("bbox"),
                        "mask_area_px": track.get("mask_area_px"),
                        "confidence": track.get("confidence"),
                        "matched_via": track.get("matched_via"),
                        "occlusion_state": track.get("occlusion_state"),
                        "frames_since_last_seen": track.get("frames_since_last_seen"),
                        "created_at": track.get("created_at") or get_utc_timestamp(),
                        "play_index": track.get("play_index"),
                    }
                    stmt = pg_insert(tracks_table).values(**row)
                    update_cols = {
                        k: getattr(stmt.excluded, k)
                        for k in row if k not in ("video_id", "frame_index", "track_id")
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["video_id", "frame_index", "track_id"], set_=update_cols
                    )
                    conn.execute(stmt)
            written += len(batch)

        logger.info(f"[PG] Wrote {written} track rows for {safe_id}")
        return written

    except ValueError as e:
        logger.error(f"[PG] Invalid input: {e}")
        return 0
    except Exception as e:
        logger.error(f"[PG] Failed to write tracks batch: {e}")
        return 0


def upsert_track_meta(
    firestore_client,
    video_id: str,
    track_id: int,
    fields: Dict[str, Any],
    play_index: Optional[int] = None,
) -> bool:
    """Merge-update the per-track summary row (tracks_meta, one row per
    (video_id, track_id, play_index)).

    Called repeatedly as tracking proceeds (e.g. to extend last_frame) and later by
    jersey_ocr_stage / the tracking stage's re-ID hook (e.g. to set jersey_number).

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        track_id: Track ID
        fields: Fields to merge into the track_meta row
        play_index: Which play this track belongs to, or None for
            whole-video-mode (see postgres_client.tracks_meta_conflict_target)

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        engine = firestore_client.engine
        known_cols = {c.name for c in tracks_meta_table.columns}
        values = {k: v for k, v in fields.items() if k in known_cols and k != "play_index"}

        with engine.begin() as conn:
            stmt = pg_insert(tracks_meta_table).values(
                video_id=safe_id, track_id=track_id, play_index=play_index, **values
            )
            update_cols = {k: getattr(stmt.excluded, k) for k in values}
            index_elements, index_where = tracks_meta_conflict_target(play_index)
            stmt = stmt.on_conflict_do_update(
                index_elements=index_elements, index_where=index_where, set_=update_cols
            ) if update_cols else stmt.on_conflict_do_nothing(
                index_elements=index_elements, index_where=index_where
            )
            conn.execute(stmt)

        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to upsert track_meta: {e}")
        return False


def write_jersey_number_for_track(
    firestore_client,
    video_id: str,
    track_id: int,
    jersey_number: str,
    confidence: float,
    source_frame_id: int,
    play_index: Optional[int] = None,
) -> bool:
    """Write a detected jersey number to a track's summary row.

    Multi-player equivalent of write_jersey_number (which wrote to the video root
    row, only sensible for a single-athlete video).

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        track_id: Track ID the jersey number was read for
        jersey_number: Detected jersey number (str)
        confidence: OCR confidence [0.0-1.0]
        source_frame_id: Frame where jersey was detected
        play_index: Which play this track belongs to, or None for
            whole-video-mode - must match the play_index tracking_stage.py
            used when it created this track's row, or this will target a
            different (video_id, track_id, play_index) row than intended.

    Returns:
        True if successful
    """
    success = upsert_track_meta(firestore_client, video_id, track_id, {
        "jersey_number": jersey_number,
        "jersey_confidence": confidence,
        "jersey_source_frame": source_frame_id,
    }, play_index=play_index)
    if success:
        logger.info(
            f"[PG] Wrote jersey number for track {track_id}: {jersey_number} (conf={confidence:.2f})"
        )
    return success


def update_stage_status(
    firestore_client,
    video_id: str,
    stage_name: str,
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
    play_index: Optional[int] = None,
) -> bool:
    """Update stage status for a video's video_stages row.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        stage_name: Stage name (e.g., "download", "pose")
        status: Status ("pending", "completed", "failed", "skipped")
        metadata: Additional metadata to store
        play_index: Which play this stage attempt belongs to, or None for
            whole-video-mode (see postgres_client.video_stages_conflict_target)

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)

        stage_data = {
            "status": status,
            "completed_at": get_utc_timestamp(),
        }
        if metadata:
            stage_data.update(metadata)

        # Firestore's old doc_ref.update({f"stages.{stage_name}": stage_data})
        # REPLACES the whole stages.<stage_name> sub-map (single one-level-deep
        # dotted key) rather than merging leaf fields - mirror that by
        # overwriting the known columns wholesale and replacing "extra" outright
        # (not merging it), unlike the .db shim's per-leaf-field update().
        known = {"status", "attempt", "started_at", "completed_at"}
        known_fields = {k: v for k, v in stage_data.items() if k in known}
        extra_fields = {k: v for k, v in stage_data.items() if k not in known}

        with firestore_client.engine.begin() as conn:
            from ingest.db_schema import video_stages as video_stages_table
            from ingest.postgres_client import video_stages_conflict_target
            row = {
                "video_id": safe_id,
                "stage_name": stage_name,
                "status": known_fields.get("status"),
                "attempt": known_fields.get("attempt", 0),
                "started_at": known_fields.get("started_at"),
                "completed_at": known_fields.get("completed_at"),
                "extra": extra_fields,
                "play_index": play_index,
            }
            stmt = pg_insert(video_stages_table).values(**row)
            update_cols = {k: getattr(stmt.excluded, k) for k in ("status", "completed_at", "extra")}
            index_elements, index_where = video_stages_conflict_target(play_index)
            stmt = stmt.on_conflict_do_update(
                index_elements=index_elements, index_where=index_where, set_=update_cols
            )
            conn.execute(stmt)

        logger.info(f"[PG] Updated stage status: {safe_id}/{stage_name} = {status}")
        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to update stage status: {e}")
        return False


def write_jersey_number(
    firestore_client,
    video_id: str,
    jersey_number: str,
    confidence: float,
    source_frame_id: int
) -> bool:
    """Write detected jersey number to the video row (single-athlete path).

    Args:
        firestore_client: PostgresClient instance
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
        safe_id = settings.sanitize_id(video_id)
        firestore_client.db.collection(COLLECTION_VIDEOS).document(safe_id).update({
            "jersey_number": jersey_number,
            "jersey_confidence": confidence,
            "jersey_source_frame": source_frame_id,
        })
        logger.info(f"[PG] Wrote jersey number: {jersey_number} (conf={confidence:.2f})")
        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to write jersey number: {e}")
        return False


def mark_stage_attempt(
    firestore_client,
    video_id: str,
    stage_name: str,
    play_index: Optional[int] = None,
) -> bool:
    """Mark a stage attempt (for retry tracking) - increments video_stages.attempt.

    Args:
        firestore_client: PostgresClient instance
        video_id: YouTube video ID (will be sanitized)
        stage_name: Stage name
        play_index: Which play this attempt belongs to, or None for
            whole-video-mode (see postgres_client.video_stages_conflict_target)

    Returns:
        True if successful

    Raises:
        ValueError: If video_id is invalid
    """
    try:
        safe_id = settings.sanitize_id(video_id)
        from ingest.db_schema import video_stages as video_stages_table
        from ingest.postgres_client import video_stages_conflict_target

        with firestore_client.engine.begin() as conn:
            stmt = pg_insert(video_stages_table).values(
                video_id=safe_id, stage_name=stage_name, status=None, attempt=1,
                started_at=None, completed_at=None, extra={}, play_index=play_index,
            )
            index_elements, index_where = video_stages_conflict_target(play_index)
            stmt = stmt.on_conflict_do_update(
                index_elements=index_elements,
                index_where=index_where,
                set_={"attempt": video_stages_table.c.attempt + 1},
            )
            conn.execute(stmt)

        return True

    except ValueError as e:
        logger.error(f"[PG] Invalid video_id: {e}")
        return False
    except Exception as e:
        logger.error(f"[PG] Failed to mark stage attempt: {e}")
        return False
