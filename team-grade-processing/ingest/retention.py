"""Retention sweep for anonymous, never-claimed videos.

The free "paste a link" tool on the-bridge.app processes videos with no
login required. Once a user claims a video (POST /video/film-stats or
POST /video/reels/from-team-grade on the Bridge Athletics side), that side
calls this project's own POST /api/videos/{video_id}/mark-claimed, which
sets videos.claimed_at. Anything still unclaimed past the retention window
gets purged here - both the S3 objects (raw video, frames, torso crops) and
every DB row referencing that video_id, across every table this project
writes to.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete as sa_delete

from ingest.db_schema import (
    videos, video_stages, ingestion_queue, frames, pose, torso_crops, reps,
    rep_analysis, camera_motion, whistle_events, detections, tracks, tracks_meta,
)
from ingest.s3_client import delete_video_prefix

logger = logging.getLogger(__name__)

# Deletion order doesn't matter functionally (no FK ON DELETE CASCADE is
# relied upon here - each table is deleted explicitly), but children before
# the parent `videos` row reads more naturally and avoids ever leaving an
# orphaned child row if this function is interrupted partway through.
_CHILD_TABLES = [
    video_stages, ingestion_queue, frames, pose, torso_crops, reps,
    rep_analysis, camera_motion, whistle_events, detections, tracks, tracks_meta,
]


def purge_unclaimed_videos(engine, older_than_hours: int = 48) -> int:
    """Delete every video (DB rows + S3 objects) whose videos.claimed_at is
    still NULL and videos.created_at is older than `older_than_hours`.

    Returns the number of videos purged.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    purged = 0

    with engine.begin() as conn:
        stale_ids = conn.execute(
            select(videos.c.video_id).where(
                videos.c.claimed_at.is_(None),
                videos.c.created_at < cutoff,
            )
        ).scalars().all()

    for video_id in stale_ids:
        try:
            deleted_objects = delete_video_prefix(video_id)
            with engine.begin() as conn:
                for table in _CHILD_TABLES:
                    conn.execute(sa_delete(table).where(table.c.video_id == video_id))
                conn.execute(sa_delete(videos).where(videos.c.video_id == video_id))
            logger.info(
                f"[retention] Purged unclaimed video {video_id} "
                f"({deleted_objects} S3 objects removed)"
            )
            purged += 1
        except Exception as e:
            logger.warning(f"[retention] Failed to purge {video_id}: {e}")

    if purged:
        logger.info(f"[retention] Purge sweep complete: {purged} unclaimed video(s) removed")
    return purged
