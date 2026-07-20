"""
Real-Postgres Tests for ingest/queue_manager.py

Requires a real, disposable Postgres reachable via $DATABASE_URL with the
schema already migrated (`alembic upgrade head`) - see tests/conftest.py's
pg_client fixture docstring for the local Docker setup. Every test here is
skipped automatically if DATABASE_URL isn't set.

The centerpiece is test_skip_locked_dequeue_only_one_worker_claims_the_row:
this is the actual bug fix Pass 2a of the data-layer migration exists for.
The old Firestore-based dequeue_next_video() read the "next" queue item and
then, in a SEPARATE call, marked it "processing" - no transaction tied the
two together, so two workers could both read the same "queued" item before
either write landed, and both would start processing it. The new
implementation does the read (`SELECT ... FOR UPDATE SKIP LOCKED`) and the
"processing" write in one Postgres transaction, so this is directly
testable: fire two real, concurrent dequeue_next_video() calls at the same
single queued row and assert only one of them ever gets it.
"""

import threading

import pytest

pytestmark = pytest.mark.integration


def _insert_video(pg_client, video_id, status="pending"):
    from ingest.db_schema import videos
    from datetime import datetime, timezone

    with pg_client.engine.begin() as conn:
        conn.execute(
            videos.insert().values(
                video_id=video_id, status=status,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
        )


class TestSkipLockedDequeue:
    def test_skip_locked_dequeue_only_one_worker_claims_the_row(self, pg_client):
        """Two threads race to dequeue the SAME single queued job - only one
        may succeed, the other must see an empty queue (None), never a
        duplicate claim of the same row."""
        from ingest.queue_manager import QueueManager

        video_id = "skiplocktest01"
        _insert_video(pg_client, video_id)

        qm = QueueManager(pg_client)
        assert qm.enqueue_video(video_id, stage="metadata", priority=5) is True

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # maximize the chance both threads hit FOR UPDATE together
            item = qm.dequeue_next_video()
            with results_lock:
                results.append(item)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        successes = [r for r in results if r is not None]
        failures = [r for r in results if r is None]

        assert len(results) == 2, "both worker threads must return"
        assert len(successes) == 1, f"exactly one worker should claim the job, got {len(successes)}: {results}"
        assert len(failures) == 1
        assert successes[0]["video_id"] == video_id
        assert successes[0]["status"] == "processing"

        # Confirm the row landed in "processing" exactly once in the DB too -
        # not left "queued" (would mean the claim didn't persist) and not
        # somehow duplicated.
        stats = pg_client.get_queue_status()
        assert stats["processing"] == 1
        assert stats["queued"] == 0

    def test_dequeue_returns_none_when_queue_empty(self, pg_client):
        from ingest.queue_manager import QueueManager

        qm = QueueManager(pg_client)
        assert qm.dequeue_next_video() is None

    def test_dequeue_respects_stage_sequence_per_video(self, pg_client):
        """A video with both "download" and "frame_extraction" queued should
        only ever surface the earlier stage ("download") - mirrors the old
        Firestore version's cross-video stage-sequencing filter.
        """
        from ingest.queue_manager import QueueManager

        video_id = "stageseqtest01"
        _insert_video(pg_client, video_id)

        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="frame_extraction", priority=5)
        qm.enqueue_video(video_id, stage="download", priority=5)

        item = qm.dequeue_next_video()
        assert item is not None
        assert item["stage"] == "download"

    def test_dequeue_prefers_higher_priority_across_videos(self, pg_client):
        from ingest.queue_manager import QueueManager

        _insert_video(pg_client, "priolow")
        _insert_video(pg_client, "priohigh")

        qm = QueueManager(pg_client)
        qm.enqueue_video("priolow", stage="metadata", priority=2)
        qm.enqueue_video("priohigh", stage="metadata", priority=9)

        item = qm.dequeue_next_video()
        assert item is not None
        assert item["video_id"] == "priohigh"

    def test_mark_completed_removes_row(self, pg_client):
        from ingest.queue_manager import QueueManager

        video_id = "completetest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="metadata", priority=5)
        item = qm.dequeue_next_video()

        assert qm.mark_completed(item["_doc_id"]) is True
        stats = pg_client.get_queue_status()
        assert stats["total"] == 0

    def test_mark_failed_with_retry_requeues(self, pg_client):
        from ingest.queue_manager import QueueManager

        video_id = "retrytest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="metadata", priority=5)
        item = qm.dequeue_next_video()

        assert qm.mark_failed(item["_doc_id"], "boom", retry=True) is True
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1
        assert stats["processing"] == 0

    def test_mark_failed_stops_requeueing_once_retries_exhausted(self, pg_client):
        """A stage that always fails the same way must eventually land in
        status='failed' rather than being requeued forever - previously
        retry=True requeued unconditionally on every call, so a permanently
        broken item would win dequeue_next_video's oldest-first ordering on
        every poll cycle and starve every other queued video indefinitely."""
        from ingest.db_schema import ingestion_queue
        from ingest.queue_manager import QueueManager

        video_id = "exhausttest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="metadata", priority=5)

        with pg_client.engine.begin() as conn:
            conn.execute(
                ingestion_queue.update()
                .where(ingestion_queue.c.video_id == video_id)
                .values(max_retries=3)
            )

        for _ in range(3):
            item = qm.dequeue_next_video()
            assert item is not None
            assert qm.mark_failed(item["_doc_id"], "boom", retry=True) is True

        # Retries exhausted (retry_count reached max_retries) - the item must
        # no longer be claimable, and must be permanently "failed".
        assert qm.dequeue_next_video() is None
        with pg_client.engine.connect() as conn:
            row = conn.execute(
                ingestion_queue.select().where(ingestion_queue.c.video_id == video_id)
            ).mappings().first()
        assert row["status"] == "failed"
        assert row["retry_count"] == 3

    def test_mark_failed_exhausted_propagates_to_video_stages_and_videos(self, pg_client):
        """GET /api/ingest/{id}/status reads videos.status/video_stages, not
        the queue - so a permanent queue failure must also be written there,
        or the API keeps reporting a stale "pending" status and a frozen
        progress % forever, indistinguishable from a video still processing."""
        from ingest.db_schema import ingestion_queue, video_stages, videos
        from ingest.queue_manager import QueueManager

        video_id = "propagatetest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="download", priority=5)

        with pg_client.engine.begin() as conn:
            conn.execute(
                ingestion_queue.update()
                .where(ingestion_queue.c.video_id == video_id)
                .values(max_retries=1)
            )

        item = qm.dequeue_next_video()
        assert qm.mark_failed(item["_doc_id"], "DOWNLOAD_FAILED", retry=True) is True

        with pg_client.engine.connect() as conn:
            video_row = conn.execute(
                videos.select().where(videos.c.video_id == video_id)
            ).mappings().first()
            stage_row = conn.execute(
                video_stages.select().where(
                    (video_stages.c.video_id == video_id)
                    & (video_stages.c.stage_name == "download")
                )
            ).mappings().first()

        assert video_row["status"] == "failed"
        assert video_row["error"] == "DOWNLOAD_FAILED"
        assert stage_row["status"] == "failed"

    def test_cleanup_stalled_items_requeues_old_processing_rows(self, pg_client):
        from ingest.queue_manager import QueueManager
        from ingest.db_schema import ingestion_queue
        from datetime import datetime, timezone, timedelta

        video_id = "stalltest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="metadata", priority=5)
        item = qm.dequeue_next_video()

        # Backdate processing_started_at so it looks stalled.
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        with pg_client.engine.begin() as conn:
            conn.execute(
                ingestion_queue.update()
                .where(ingestion_queue.c.id == int(item["_doc_id"]))
                .values(processing_started_at=stale_time)
            )

        count = qm.cleanup_stalled_items(timeout_minutes=30)
        assert count == 1
        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1
