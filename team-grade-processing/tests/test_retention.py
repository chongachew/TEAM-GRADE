"""Real-Postgres tests for ingest/retention.py's purge_unclaimed_videos().

Requires a real, disposable Postgres reachable via $DATABASE_URL - see
tests/conftest.py's pg_client fixture docstring. Skipped automatically if
DATABASE_URL isn't set. S3 deletion is mocked (delete_video_prefix) - this
suite only verifies the DB-side purge/exemption logic.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


def _insert_video(pg_client, video_id, created_at, claimed_at=None):
    from ingest.db_schema import videos

    with pg_client.engine.begin() as conn:
        conn.execute(
            videos.insert().values(
                video_id=video_id, status="completed",
                created_at=created_at, updated_at=created_at, claimed_at=claimed_at,
            )
        )


class TestPurgeUnclaimedVideos:
    def test_purges_old_unclaimed_video(self, pg_client):
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos
        from sqlalchemy import select

        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _insert_video(pg_client, "old_unclaimed01", created_at=old)

        with patch("ingest.retention.delete_video_prefix", return_value=3) as mock_delete:
            purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 1
        mock_delete.assert_called_once_with("old_unclaimed01")
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                select(videos.c.video_id).where(videos.c.video_id == "old_unclaimed01")
            ).first()
        assert row is None

    def test_does_not_purge_claimed_video(self, pg_client):
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos
        from sqlalchemy import select

        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _insert_video(pg_client, "old_claimed01", created_at=old, claimed_at=old)

        with patch("ingest.retention.delete_video_prefix", return_value=0) as mock_delete:
            purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 0
        mock_delete.assert_not_called()
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                select(videos.c.video_id).where(videos.c.video_id == "old_claimed01")
            ).first()
        assert row is not None

    def test_does_not_purge_recent_unclaimed_video(self, pg_client):
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos
        from sqlalchemy import select

        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        _insert_video(pg_client, "recent_unclaimed01", created_at=recent)

        with patch("ingest.retention.delete_video_prefix", return_value=0) as mock_delete:
            purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 0
        mock_delete.assert_not_called()
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                select(videos.c.video_id).where(videos.c.video_id == "recent_unclaimed01")
            ).first()
        assert row is not None

    def test_purges_video_with_plays_row(self, pg_client):
        """Regression test: plays was missing from _CHILD_TABLES (added in
        the 2026-07-25 per-play redesign, never added here), so a video with
        a plays row failed the videos delete on a ForeignKeyViolation every
        sweep - and since S3 deletion ran BEFORE the DB transaction, that
        failure silently re-deleted the video's S3 objects on every single
        sweep while leaving the orphaned DB row (and its plays row) behind
        forever. Found 2026-08-07 investigating why rRDZymlc8aI's S3 data
        kept disappearing after being restored."""
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos, plays
        from sqlalchemy import select

        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _insert_video(pg_client, "with_plays01", created_at=old)
        with pg_client.engine.begin() as conn:
            conn.execute(
                plays.insert().values(
                    video_id="with_plays01", play_index=0,
                    start_frame=0, end_frame=100, status="completed",
                )
            )

        with patch("ingest.retention.delete_video_prefix", return_value=0) as mock_delete:
            purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 1
        mock_delete.assert_called_once_with("with_plays01")
        with pg_client.engine.connect() as conn:
            video_row = conn.execute(
                select(videos.c.video_id).where(videos.c.video_id == "with_plays01")
            ).first()
            play_row = conn.execute(
                select(plays.c.id).where(plays.c.video_id == "with_plays01")
            ).first()
        assert video_row is None
        assert play_row is None

    def test_s3_not_deleted_when_db_delete_fails(self, pg_client):
        """The DB transaction must commit BEFORE S3 objects are deleted -
        otherwise any future table missing from _CHILD_TABLES (the same
        class of bug as the plays regression above) silently destroys S3
        data every sweep while the DB row survives to be retried forever."""
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos
        from sqlalchemy import select

        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _insert_video(pg_client, "db_fail01", created_at=old)

        with patch("ingest.retention.delete_video_prefix", return_value=0) as mock_delete, \
             patch("ingest.retention._CHILD_TABLES", []):
            # With no child tables cleared, the videos delete itself succeeds
            # here (nothing references it), so simulate the failure directly
            # by making the videos delete blow up instead.
            with patch("ingest.retention.sa_delete", side_effect=RuntimeError("boom")):
                purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 0
        mock_delete.assert_not_called()
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                select(videos.c.video_id).where(videos.c.video_id == "db_fail01")
            ).first()
        assert row is not None

    def test_purges_child_rows_too(self, pg_client):
        from ingest.retention import purge_unclaimed_videos
        from ingest.db_schema import videos, reps
        from sqlalchemy import select

        old = datetime.now(timezone.utc) - timedelta(hours=72)
        _insert_video(pg_client, "with_reps01", created_at=old)
        with pg_client.engine.begin() as conn:
            conn.execute(
                reps.insert().values(
                    video_id="with_reps01", rep_index=0, track_id=None,
                    start_frame=0, end_frame=10, duration_frames=10, duration_seconds=1.0,
                )
            )

        with patch("ingest.retention.delete_video_prefix", return_value=0):
            purged = purge_unclaimed_videos(pg_client.engine, older_than_hours=48)

        assert purged == 1
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                select(reps.c.id).where(reps.c.video_id == "with_reps01")
            ).first()
        assert row is None
