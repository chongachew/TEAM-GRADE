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
