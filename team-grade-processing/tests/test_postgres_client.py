"""
Real-Postgres Tests for ingest/postgres_client.py and
ingest/utils/firestore_utils.py (Pass 2a data-layer migration).

Requires $DATABASE_URL (see tests/conftest.py's pg_client fixture docstring)
- skipped automatically otherwise. Exercises the actual SQL (ON CONFLICT
upserts, the nullable-track_id partial-index handling, stages JSONB
merging) rather than the .db Firestore-compat shim other tests mock out.
"""

import pytest

pytestmark = pytest.mark.integration


class TestVideoMetadataCRUD:
    def test_save_and_get_video_metadata(self, pg_client):
        ok = pg_client.save_video_metadata("vid001", {"video_url": "https://x", "team_id": "t1"})
        assert ok is True

        data = pg_client.get_video_status("vid001")
        assert data is not None
        assert data["video_id"] == "vid001"
        assert data["video_url"] == "https://x"
        assert data["team_id"] == "t1"
        assert data["status"] == "pending"
        assert data["stages"] == {}

    def test_get_video_status_missing_returns_none(self, pg_client):
        assert pg_client.get_video_status("does-not-exist") is None

    def test_update_video_status_and_error(self, pg_client):
        pg_client.save_video_metadata("vid002", {})
        assert pg_client.update_video_status("vid002", "failed", error_message="boom") is True
        data = pg_client.get_video_status("vid002")
        assert data["status"] == "failed"
        assert data["error"] == "boom"

    def test_update_video_field_flat_column(self, pg_client):
        pg_client.save_video_metadata("vid003", {})
        assert pg_client.update_video_field("vid003", "frame_count", 123) is True
        assert pg_client.get_video_status("vid003")["frame_count"] == 123

    def test_update_video_field_dotted_stage_path(self, pg_client):
        pg_client.save_video_metadata("vid004", {})
        assert pg_client.update_video_field("vid004", "stages.download.status", "completed") is True
        data = pg_client.get_video_status("vid004")
        assert data["stages"]["download"]["status"] == "completed"

    def test_save_video_metadata_upserts_not_duplicates(self, pg_client):
        pg_client.save_video_metadata("vid005", {"team_id": "a"})
        pg_client.save_video_metadata("vid005", {"team_id": "b"})
        videos = pg_client.list_videos_by_status("pending", limit=100)
        matching = [v for v in videos if v["video_id"] == "vid005"]
        assert len(matching) == 1
        assert matching[0]["team_id"] == "b"

    def test_list_videos_by_status(self, pg_client):
        pg_client.save_video_metadata("vid006", {})
        pg_client.update_video_status("vid006", "completed")
        pg_client.save_video_metadata("vid007", {})

        completed = pg_client.list_videos_by_status("completed", limit=10)
        assert {v["video_id"] for v in completed} == {"vid006"}

    def test_get_pending_videos(self, pg_client):
        pg_client.save_video_metadata("vid008", {})
        pending = pg_client.get_pending_videos()
        ids = {v["video_id"] for v in pending}
        assert "vid008" in ids
        assert pending[0]["_doc_id"] == pending[0]["video_id"]


class TestQueueDiagnostics:
    def test_add_to_queue_and_status(self, pg_client):
        pg_client.save_video_metadata("vidq1", {})
        assert pg_client.add_to_queue("vidq1", "metadata", priority=5) is True
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1

    def test_add_to_queue_deduplicates(self, pg_client):
        pg_client.save_video_metadata("vidq2", {})
        pg_client.add_to_queue("vidq2", "metadata", priority=5)
        pg_client.add_to_queue("vidq2", "metadata", priority=5)
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1

    def test_check_queue_integrity(self, pg_client):
        pg_client.save_video_metadata("vidq3", {})
        pg_client.add_to_queue("vidq3", "metadata", priority=5)
        result = pg_client.check_queue_integrity("vidq3")
        assert result["video_exists"] is True
        assert result["queue_exists"] is True

    def test_verify_and_heal_queue_recreates_missing_entry(self, pg_client):
        pg_client.save_video_metadata("vidq4", {})
        assert pg_client.queue_entry_exists("vidq4") is False
        assert pg_client.verify_and_heal_queue("vidq4") is True
        assert pg_client.queue_entry_exists("vidq4") is True

    def test_delete_queue_entry_by_video_id(self, pg_client):
        pg_client.save_video_metadata("vidq5", {})
        pg_client.add_to_queue("vidq5", "metadata", priority=5)
        assert pg_client.delete_queue_entry_by_video_id("vidq5") is True
        assert pg_client.queue_entry_exists("vidq5") is False

    def test_add_to_queue_null_play_index_dedup_uses_is_none(self, pg_client):
        """Regression test: SQL NULL is never equal to NULL, so a naive
        play_index == None comparison in the dedup check would never match
        an existing whole-video-mode row and every such enqueue would insert
        a duplicate instead of deduplicating."""
        pg_client.save_video_metadata("vidq6", {})
        pg_client.add_to_queue("vidq6", "metadata", priority=5, play_index=None)
        pg_client.add_to_queue("vidq6", "metadata", priority=5, play_index=None)
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1

    def test_add_to_queue_different_play_indexes_not_deduped(self, pg_client):
        pg_client.save_video_metadata("vidq7", {})
        pg_client.add_to_queue("vidq7", "detection", priority=5, play_index=0)
        pg_client.add_to_queue("vidq7", "detection", priority=5, play_index=1)
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 2


class TestFirestoreCompatShim:
    def test_video_doc_set_and_get_with_stages(self, pg_client):
        ref = pg_client.db.collection("videos").document("shimvid1")
        ref.set({
            "video_id": "shimvid1",
            "status": "processing",
            "stages": {
                "metadata": {"status": "completed", "attempt": 1},
                "download": {"status": "pending", "attempt": 0},
            },
        })
        doc = ref.get()
        assert doc.exists is True
        data = doc.to_dict()
        assert data["status"] == "processing"
        assert data["stages"]["metadata"]["status"] == "completed"
        assert data["stages"]["download"]["status"] == "pending"

    def test_video_doc_update_mixes_flat_and_dotted_stage_keys(self, pg_client):
        ref = pg_client.db.collection("videos").document("shimvid2")
        ref.set({"video_id": "shimvid2", "status": "processing"})
        ref.update({
            "stages.download.status": "completed",
            "stages.download.path": "/x.mp4",
            "download_path": "/x.mp4",
        })
        data = ref.get().to_dict()
        assert data["download_path"] == "/x.mp4"
        assert data["stages"]["download"]["status"] == "completed"
        assert data["stages"]["download"]["path"] == "/x.mp4"

    def test_video_doc_get_missing_not_exists(self, pg_client):
        ref = pg_client.db.collection("videos").document("does-not-exist-shim")
        doc = ref.get()
        assert doc.exists is False
        assert doc.to_dict() is None

    def test_subcollection_stream_pose(self, pg_client):
        pg_client.save_video_metadata("shimvid3", {})
        from ingest.utils.firestore_utils import write_pose_batch

        write_pose_batch(pg_client, "shimvid3", [
            {"frame_index": 0, "timestamp_seconds": 0.0, "landmarks": {"nose": {"x": 0.1}}, "confidence_mean": 0.9},
            {"frame_index": 1, "timestamp_seconds": 0.1, "landmarks": {"nose": {"x": 0.2}}, "confidence_mean": 0.95},
        ])

        pose_ref = pg_client.db.collection("videos").document("shimvid3").collection("pose")
        docs = pose_ref.stream()
        assert len(docs) == 2
        by_frame = {d.to_dict()["frame_index"]: d for d in docs}
        assert by_frame[0].to_dict()["confidence_mean"] == 0.9

    def test_subcollection_where_and_order_by(self, pg_client):
        pg_client.save_video_metadata("shimvid4", {})
        from ingest.utils.firestore_utils import write_pose_batch

        write_pose_batch(pg_client, "shimvid4", [
            {"frame_index": i, "timestamp_seconds": i / 15, "landmarks": {}, "confidence_mean": 0.8}
            for i in range(5)
        ])

        pose_ref = pg_client.db.collection("videos").document("shimvid4").collection("pose")
        query = pose_ref.where("frame_index", ">=", 1).where("frame_index", "<=", 3)
        docs = query.order_by("frame_index").stream()
        assert [d.to_dict()["frame_index"] for d in docs] == [1, 2, 3]

    def test_batch_set_writes_multiple_analysis_docs(self, pg_client):
        """Mirrors biomechanics_stage_vectorized.py's real write path for
        per-rep analysis: `firestore_client.db.batch()` + one `.set(ref,
        rep_data)` per rep + a single `.commit()`."""
        pg_client.save_video_metadata("shimvid6", {})

        batch = pg_client.db.batch()
        rep_payloads = [
            {"rep_index": 0, "track_id": None, "traits": {"speed": 88.0},
             "buckets": {"speed": "Strong"}, "overall_grade": 88.0},
            {"rep_index": 1, "track_id": None, "traits": {"speed": 70.0},
             "buckets": {"speed": "Average"}, "overall_grade": 70.0},
        ]
        for rep_data in rep_payloads:
            ref = (
                pg_client.db.collection("videos").document("shimvid6")
                .collection("analysis").document(f"rep_{rep_data['rep_index']}")
            )
            batch.set(ref, rep_data)
        batch.commit()

        analysis_ref = pg_client.db.collection("videos").document("shimvid6").collection("analysis")
        docs = analysis_ref.stream()
        assert len(docs) == 2
        by_id = {d.id: d.to_dict() for d in docs}
        assert by_id["rep_0"]["overall_grade"] == 88.0
        assert by_id["rep_1"]["overall_grade"] == 70.0

    def test_analysis_doc_set_upserts_in_place(self, pg_client):
        """Mirrors PATCH /api/reps/{video_id}'s rep-boundary-correction
        endpoint: re-scoring a rep calls `analysis_ref.set(analysis)` on the
        SAME doc id again - must overwrite, not create a second row."""
        pg_client.save_video_metadata("shimvid7", {})
        ref = (
            pg_client.db.collection("videos").document("shimvid7")
            .collection("analysis").document("rep_0")
        )
        ref.set({"rep_index": 0, "track_id": None, "traits": {}, "buckets": {}, "overall_grade": 50.0})
        ref.set({"rep_index": 0, "track_id": None, "traits": {}, "buckets": {}, "overall_grade": 95.0})

        docs = pg_client.db.collection("videos").document("shimvid7").collection("analysis").stream()
        assert len(docs) == 1
        assert docs[0].to_dict()["overall_grade"] == 95.0

    def test_tracks_meta_get_set_by_doc_id(self, pg_client):
        pg_client.save_video_metadata("shimvid5", {})
        meta_ref = pg_client.db.collection("videos").document("shimvid5").collection("tracks_meta").document("007")
        assert meta_ref.get().exists is False

        meta_ref.set({"jersey_number": "12", "jersey_confidence": 0.9}, merge=True)
        doc = meta_ref.get()
        assert doc.exists is True
        assert doc.to_dict()["jersey_number"] == "12"

        meta_ref.set({"track_id_conflict": True}, merge=True)
        assert meta_ref.get().to_dict()["track_id_conflict"] is True


class TestNullableTrackIdUpsert:
    """The given schema's UNIQUE(video_id, frame_index, track_id) constraints
    don't de-dupe NULL track_id (Postgres treats every NULL as distinct) -
    the partial "..._null_track" indexes in ingest/db_schema.py close that
    gap for single-athlete-mode (the default) videos. Verify a repeated
    write for the same (video, frame) with no track_id upserts in place
    instead of accumulating duplicate rows.
    """

    def test_pose_write_is_idempotent_for_single_athlete_mode(self, pg_client):
        from ingest.utils.firestore_utils import write_pose_batch

        pg_client.save_video_metadata("nulltrackvid1", {})
        write_pose_batch(pg_client, "nulltrackvid1", [
            {"frame_index": 5, "timestamp_seconds": 0.33, "landmarks": {"a": 1}, "confidence_mean": 0.7},
        ])
        write_pose_batch(pg_client, "nulltrackvid1", [
            {"frame_index": 5, "timestamp_seconds": 0.33, "landmarks": {"a": 2}, "confidence_mean": 0.99},
        ])

        docs = pg_client.db.collection("videos").document("nulltrackvid1").collection("pose").stream()
        assert len(docs) == 1
        assert docs[0].to_dict()["confidence_mean"] == 0.99

    def test_pose_write_multi_player_distinct_track_ids_coexist(self, pg_client):
        from ingest.utils.firestore_utils import write_pose_batch

        pg_client.save_video_metadata("nulltrackvid2", {})
        write_pose_batch(pg_client, "nulltrackvid2", [
            {"frame_index": 5, "track_id": 1, "timestamp_seconds": 0.33, "landmarks": {}, "confidence_mean": 0.7},
            {"frame_index": 5, "track_id": 2, "timestamp_seconds": 0.33, "landmarks": {}, "confidence_mean": 0.8},
        ])
        docs = pg_client.db.collection("videos").document("nulltrackvid2").collection("pose").stream()
        assert len(docs) == 2


class TestStageStatusHelpers:
    def test_mark_stage_attempt_increments(self, pg_client):
        from ingest.utils.firestore_utils import mark_stage_attempt

        pg_client.save_video_metadata("stagevid1", {})
        mark_stage_attempt(pg_client, "stagevid1", "download")
        mark_stage_attempt(pg_client, "stagevid1", "download")
        mark_stage_attempt(pg_client, "stagevid1", "download")

        data = pg_client.get_video_status("stagevid1")
        assert data["stages"]["download"]["attempt"] == 3

    def test_update_stage_status_replaces_whole_stage_map(self, pg_client):
        from ingest.utils.firestore_utils import update_stage_status, mark_stage_attempt

        pg_client.save_video_metadata("stagevid2", {})
        mark_stage_attempt(pg_client, "stagevid2", "pose")
        update_stage_status(pg_client, "stagevid2", "pose", "completed", metadata={"total_poses": 42})

        data = pg_client.get_video_status("stagevid2")
        assert data["stages"]["pose"]["status"] == "completed"
        assert data["stages"]["pose"]["total_poses"] == 42


class TestVideoStagesPlayIndexUniqueness:
    """Regression tests for the video_stages NULL-uniqueness fix: Postgres
    never treats two NULLs as conflicting under a multi-column UNIQUE
    constraint, so adding play_index to video_stages' uniqueness required a
    partial index for the play_index IS NULL case (see
    video_stages_conflict_target in ingest/postgres_client.py)."""

    def test_upsert_stage_fields_null_play_index_updates_same_row_twice(self, pg_client):
        from ingest.postgres_client import upsert_stage_fields
        from ingest.db_schema import video_stages

        pg_client.save_video_metadata("nullplayvid1", {})
        with pg_client.engine.begin() as conn:
            upsert_stage_fields(conn, "nullplayvid1", "detection", {"status": "processing"}, play_index=None)
        with pg_client.engine.begin() as conn:
            upsert_stage_fields(conn, "nullplayvid1", "detection", {"status": "completed"}, play_index=None)

        with pg_client.engine.connect() as conn:
            rows = conn.execute(
                video_stages.select().where(
                    (video_stages.c.video_id == "nullplayvid1")
                    & (video_stages.c.stage_name == "detection")
                )
            ).mappings().all()

        assert len(rows) == 1
        assert rows[0]["status"] == "completed"

    def test_upsert_stage_fields_real_play_index_does_not_collide_with_null_row(self, pg_client):
        from ingest.postgres_client import upsert_stage_fields
        from ingest.db_schema import video_stages

        pg_client.save_video_metadata("nullplayvid2", {})
        with pg_client.engine.begin() as conn:
            upsert_stage_fields(conn, "nullplayvid2", "detection", {"status": "processing"}, play_index=None)
        with pg_client.engine.begin() as conn:
            upsert_stage_fields(conn, "nullplayvid2", "detection", {"status": "processing"}, play_index=0)

        with pg_client.engine.connect() as conn:
            rows = conn.execute(
                video_stages.select().where(
                    (video_stages.c.video_id == "nullplayvid2")
                    & (video_stages.c.stage_name == "detection")
                )
            ).mappings().all()

        assert len(rows) == 2
        play_indexes = {r["play_index"] for r in rows}
        assert play_indexes == {None, 0}

    def test_mark_stage_attempt_with_play_index_scopes_independently(self, pg_client):
        from ingest.utils.firestore_utils import mark_stage_attempt
        from ingest.db_schema import video_stages

        pg_client.save_video_metadata("nullplayvid3", {})
        mark_stage_attempt(pg_client, "nullplayvid3", "detection", play_index=0)
        mark_stage_attempt(pg_client, "nullplayvid3", "detection", play_index=0)
        mark_stage_attempt(pg_client, "nullplayvid3", "detection", play_index=1)

        with pg_client.engine.connect() as conn:
            rows = {
                r["play_index"]: r["attempt"]
                for r in conn.execute(
                    video_stages.select().where(
                        (video_stages.c.video_id == "nullplayvid3")
                        & (video_stages.c.stage_name == "detection")
                    )
                ).mappings()
            }

        assert rows == {0: 2, 1: 1}
