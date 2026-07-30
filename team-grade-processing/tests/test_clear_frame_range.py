"""
Real-Postgres tests for clear_detections_for_frame_range /
clear_tracks_for_frame_range (ingest/utils/firestore_utils.py).

Regression coverage for a real production bug (2026-07-29): a detection/
tracking re-run that finds fewer results for a frame than a prior run did
left the extra old rows stranded, since the write paths only ever upsert by
key and never delete. Confirmed live: 26 of one play's 175 frames were stuck
showing stale pre-fix boxes. These clear_*_for_frame_range() functions are
the fix - exercised here against a real Postgres, not mocks, since the bug
itself was a real SQL-semantics gap a mock can't catch.

Requires $DATABASE_URL (see tests/conftest.py's pg_client fixture docstring).
"""

import pytest

from ingest.db_schema import detections as detections_table, tracks as tracks_table, videos as videos_table
from ingest.utils.firestore_utils import (
    clear_detections_for_frame_range,
    clear_tracks_for_frame_range,
)


def _ensure_video(pg_engine, video_id):
    with pg_engine.begin() as conn:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(videos_table).values(video_id=video_id, status="processing")
        stmt = stmt.on_conflict_do_nothing(index_elements=["video_id"])
        conn.execute(stmt)


def _insert_detection(pg_engine, video_id, frame_index, detection_index):
    _ensure_video(pg_engine, video_id)
    with pg_engine.begin() as conn:
        conn.execute(detections_table.insert().values(
            video_id=video_id, frame_index=frame_index, detection_index=detection_index,
            bbox=[0, 0, 10, 10], confidence=0.9, class_name="person",
        ))


def _insert_track(pg_engine, video_id, frame_index, track_id):
    _ensure_video(pg_engine, video_id)
    with pg_engine.begin() as conn:
        conn.execute(tracks_table.insert().values(
            video_id=video_id, frame_index=frame_index, track_id=track_id,
            bbox=[0, 0, 10, 10], confidence=0.9,
        ))


class TestClearDetectionsForFrameRange:
    @pytest.mark.integration
    def test_deletes_only_rows_within_range(self, pg_client, pg_engine):
        video_id = "dQw4w9WgXcQ"
        _insert_detection(pg_engine, video_id, 5, 0)   # in range
        _insert_detection(pg_engine, video_id, 10, 0)  # in range (upper bound)
        _insert_detection(pg_engine, video_id, 11, 0)  # just outside range
        _insert_detection(pg_engine, video_id, 4, 0)   # just outside range

        deleted = clear_detections_for_frame_range(pg_client, video_id, 5, 10)

        assert deleted == 2
        with pg_engine.connect() as conn:
            from sqlalchemy import text
            remaining = conn.execute(
                text("select frame_index from detections where video_id = :v order by frame_index"),
                {"v": video_id},
            ).fetchall()
        assert [r[0] for r in remaining] == [4, 11]

    @pytest.mark.integration
    def test_only_affects_the_given_video(self, pg_client, pg_engine):
        _insert_detection(pg_engine, "videoAAAAAAA", 5, 0)
        _insert_detection(pg_engine, "videoBBBBBBB", 5, 0)

        clear_detections_for_frame_range(pg_client, "videoAAAAAAA", 0, 100)

        with pg_engine.connect() as conn:
            from sqlalchemy import text
            remaining = conn.execute(
                text("select video_id from detections order by video_id"),
            ).fetchall()
        assert [r[0] for r in remaining] == ["videoBBBBBBB"]

    @pytest.mark.integration
    def test_leaves_a_frame_with_zero_new_detections_actually_empty(self, pg_client, pg_engine):
        """The exact real-world scenario that was found live: a frame that
        used to have several detections now correctly has none (e.g. the
        field-boundary filter excluded all of them). Clearing first, then
        writing nothing for that frame, must leave it with zero rows - not
        the old stale ones."""
        video_id = "dQw4w9WgXcQ"
        for i in range(5):
            _insert_detection(pg_engine, video_id, 20, i)

        deleted = clear_detections_for_frame_range(pg_client, video_id, 20, 20)
        assert deleted == 5
        # Simulates the real run writing nothing for frame 20 (all filtered).

        with pg_engine.connect() as conn:
            from sqlalchemy import text
            remaining = conn.execute(
                text("select count(*) from detections where video_id = :v and frame_index = 20"),
                {"v": video_id},
            ).scalar()
        assert remaining == 0


class TestClearTracksForFrameRange:
    @pytest.mark.integration
    def test_deletes_only_rows_within_range(self, pg_client, pg_engine):
        video_id = "dQw4w9WgXcQ"
        _insert_track(pg_engine, video_id, 5, 0)
        _insert_track(pg_engine, video_id, 10, 0)
        _insert_track(pg_engine, video_id, 11, 0)

        deleted = clear_tracks_for_frame_range(pg_client, video_id, 5, 10)

        assert deleted == 2
        with pg_engine.connect() as conn:
            from sqlalchemy import text
            remaining = conn.execute(
                text("select frame_index from tracks where video_id = :v order by frame_index"),
                {"v": video_id},
            ).fetchall()
        assert [r[0] for r in remaining] == [11]
