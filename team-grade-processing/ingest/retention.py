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
    plays,
)
from ingest.s3_client import delete_video_prefix

logger = logging.getLogger(__name__)

# Deletion order doesn't matter functionally (no FK ON DELETE CASCADE is
# relied upon here - each table is deleted explicitly), but children before
# the parent `videos` row reads more naturally and avoids ever leaving an
# orphaned child row if this function is interrupted partway through.
#
# This list must stay in sync with every table that has a video_id FK to
# `videos` - a table missing here (like `plays` was, 2026-08-07) makes the
# `videos` delete below fail on a ForeignKeyViolation every sweep, forever
# (the row is never claimed, so it never ages out of the next cutoff either).
# Since S3 deletion used to run before this transaction, that failure mode
# silently re-deleted the video's S3 objects on every single sweep while
# leaving the orphaned DB row untouched - see delete-then-DB-commit ordering
# below for why that can no longer happen even if a future table is missed.
_CHILD_TABLES = [
    video_stages, ingestion_queue, frames, pose, torso_crops, reps,
    rep_analysis, camera_motion, whistle_events, detections, tracks, tracks_meta,
    plays,
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
            # DB rows first: if any table's delete fails (e.g. a future
            # table missing from _CHILD_TABLES hits a FK violation), the
            # whole transaction rolls back and S3 is never touched - the
            # video just gets retried on the next sweep instead of losing
            # its S3 data with no matching DB cleanup.
            with engine.begin() as conn:
                for table in _CHILD_TABLES:
                    conn.execute(sa_delete(table).where(table.c.video_id == video_id))
                conn.execute(sa_delete(videos).where(videos.c.video_id == video_id))
            deleted_objects = delete_video_prefix(video_id)
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
