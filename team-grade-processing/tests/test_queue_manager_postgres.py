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

    def test_mark_failed_exhausted_for_one_play_does_not_fail_the_whole_video(self, pg_client):
        """Real regression test (2026-07-28, found live in production on a
        real 38-play video): a single play permanently failing (e.g. no
        pose data - nothing to analyze in a kickoff/replay-only play) must
        mark only THAT play failed, not cascade to videos.status - this
        cascade predates play_index entirely and was never updated for the
        per-play redesign. A second play is still genuinely pending here,
        so the video must also not finalize early - see the sibling test
        below for the "this WAS the last blocker" case."""
        from ingest.db_schema import ingestion_queue, videos, plays
        from ingest.queue_manager import QueueManager

        video_id = "playfailtest01"
        _insert_video(pg_client, video_id, status="processing")
        with pg_client.engine.begin() as conn:
            conn.execute(plays.insert().values(
                video_id=video_id, play_index=0, start_frame=0, end_frame=99, status="pending",
            ))
            conn.execute(plays.insert().values(
                video_id=video_id, play_index=1, start_frame=100, end_frame=199, status="pending",
            ))
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="torso_crop", priority=5, play_index=0)

        with pg_client.engine.begin() as conn:
            conn.execute(
                ingestion_queue.update()
                .where(ingestion_queue.c.video_id == video_id)
                .values(max_retries=1)
            )

        item = qm.dequeue_next_video()
        assert qm.mark_failed(item["_doc_id"], "POSE_DATA_NOT_FOUND", retry=True) is True

        with pg_client.engine.connect() as conn:
            video_row = conn.execute(videos.select().where(videos.c.video_id == video_id)).mappings().first()
            play_row = conn.execute(
                plays.select().where(plays.c.video_id == video_id).where(plays.c.play_index == 0)
            ).mappings().first()

        # The video's own status is untouched - still "processing", not
        # dragged down to "failed" by this one play.
        assert video_row["status"] == "processing"
        assert play_row["status"] == "failed"

    def test_mark_failed_finalizes_video_when_failed_play_was_the_last_blocker(self, pg_client):
        """The other side of the same fix: once a permanently-failed play
        was the LAST pending play, the video must still reach
        status="completed" with the real grade from the plays that DID
        succeed - nothing else would ever re-check this, since a
        permanently-failed play never reaches its own biomechanics stage to
        self-enqueue "complete"."""
        from ingest.db_schema import ingestion_queue, videos, plays, rep_analysis
        from ingest.queue_manager import QueueManager

        video_id = "playfailtest02"
        _insert_video(pg_client, video_id, status="processing")
        with pg_client.engine.begin() as conn:
            # Play 0 already succeeded (real grade exists); play 1 is the
            # one about to permanently fail - and is the LAST pending one.
            conn.execute(plays.insert().values(
                video_id=video_id, play_index=0, start_frame=0, end_frame=99, status="completed",
            ))
            conn.execute(plays.insert().values(
                video_id=video_id, play_index=1, start_frame=100, end_frame=199, status="pending",
            ))
            conn.execute(rep_analysis.insert().values(
                video_id=video_id, rep_index=0, track_id=None, play_index=0, overall_grade=88.0,
            ))
        qm = QueueManager(pg_client)
        qm.enqueue_video(video_id, stage="torso_crop", priority=5, play_index=1)

        with pg_client.engine.begin() as conn:
            conn.execute(
                ingestion_queue.update()
                .where(ingestion_queue.c.video_id == video_id)
                .values(max_retries=1)
            )

        item = qm.dequeue_next_video()
        assert qm.mark_failed(item["_doc_id"], "POSE_DATA_NOT_FOUND", retry=True) is True

        with pg_client.engine.connect() as conn:
            video_row = conn.execute(videos.select().where(videos.c.video_id == video_id)).mappings().first()

        assert video_row["status"] == "completed"
        assert video_row["overall_grade"] == 88.0

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

    def test_cleanup_stalled_items_respects_per_stage_timeout_override(self, pg_client):
        """Regression test for a real production bug: a single global 30min
        timeout declared a genuinely-still-running GPU tracking job (observed
        taking ~84 minutes on real footage) "stalled" and resubmitted it as a
        duplicate Batch job while the original kept running to completion
        anyway - wasting real GPU compute. A tracking item backdated 60min
        must survive a call with stage_timeouts={"tracking": 150}, while a
        metadata item backdated the same 60min (a real CPU stage with no
        override) must still get cleaned up under the 30min default."""
        from ingest.queue_manager import QueueManager
        from ingest.db_schema import ingestion_queue
        from datetime import datetime, timezone, timedelta

        tracking_video_id = "stalltest02t"
        metadata_video_id = "stalltest02m"
        _insert_video(pg_client, tracking_video_id)
        _insert_video(pg_client, metadata_video_id)

        qm = QueueManager(pg_client)
        qm.enqueue_video(tracking_video_id, stage="tracking", priority=5)
        qm.enqueue_video(metadata_video_id, stage="metadata", priority=5)
        tracking_item = qm.dequeue_next_video()
        metadata_item = qm.dequeue_next_video()

        stale_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        with pg_client.engine.begin() as conn:
            for item in (tracking_item, metadata_item):
                conn.execute(
                    ingestion_queue.update()
                    .where(ingestion_queue.c.id == int(item["_doc_id"]))
                    .values(processing_started_at=stale_time)
                )

        count = qm.cleanup_stalled_items(timeout_minutes=30, stage_timeouts={"tracking": 150})
        assert count == 1  # only the metadata item, not the tracking item

        with pg_client.engine.connect() as conn:
            rows = {
                row["id"]: row["status"]
                for row in conn.execute(ingestion_queue.select()).mappings()
            }
        assert rows[int(tracking_item["_doc_id"])] == "processing"  # protected by the override
        assert rows[int(metadata_item["_doc_id"])] == "queued"  # cleaned up under the default

    def test_enqueue_with_different_play_indexes_are_not_deduped_against_each_other(self, pg_client):
        """Two different real play_index values for the same (video, stage)
        must both succeed as distinct rows - not deduped against each other.
        Regression test for the NULL-vs-real-value dedup fix in add_to_queue."""
        from ingest.queue_manager import QueueManager

        video_id = "playdeduptest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        assert qm.enqueue_video(video_id, stage="detection", priority=5, play_index=0) is True
        assert qm.enqueue_video(video_id, stage="detection", priority=5, play_index=1) is True

        stats = pg_client.get_queue_status()
        assert stats["queued"] == 2

    def test_enqueue_same_play_index_twice_is_deduped(self, pg_client):
        """Re-enqueueing the exact same (video, stage, play_index) while the
        first is still queued/processing must be a no-op, matching the
        existing whole-video dedup behavior."""
        from ingest.queue_manager import QueueManager

        video_id = "playdeduptest02"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        assert qm.enqueue_video(video_id, stage="detection", priority=5, play_index=0) is True
        assert qm.enqueue_video(video_id, stage="detection", priority=5, play_index=0) is True

        stats = pg_client.get_queue_status()
        assert stats["queued"] == 1

    def test_dequeue_progresses_two_plays_of_the_same_video_independently(self, pg_client):
        """A later-STAGE_SEQUENCE job for one play must not be blocked by an
        earlier-STAGE_SEQUENCE job for a DIFFERENT play of the same video -
        the actual behavior change dequeue_next_video's new (video_id,
        play_index) grouping exists for."""
        from ingest.queue_manager import QueueManager

        video_id = "playindeptest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        qm.enqueue_video(video_id, stage="detection", priority=5, play_index=0)
        qm.enqueue_video(video_id, stage="motion_compensation", priority=5, play_index=1)

        item1 = qm.dequeue_next_video()
        item2 = qm.dequeue_next_video()

        assert item1 is not None and item2 is not None
        stages_by_play = {item1["play_index"]: item1["stage"], item2["play_index"]: item2["stage"]}
        assert stages_by_play == {0: "detection", 1: "motion_compensation"}

    def test_dequeue_still_respects_stage_sequence_within_one_play(self, pg_client):
        """Regression check: a single video/play with both an earlier and
        later stage queued still only ever surfaces the earlier one - the
        per-play grouping change must not break the existing single-play
        (play_index=None) stage-ordering guarantee."""
        from ingest.queue_manager import QueueManager

        video_id = "playseqtest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        qm.enqueue_video(video_id, stage="frame_extraction", priority=5)
        qm.enqueue_video(video_id, stage="download", priority=5)

        item = qm.dequeue_next_video()
        assert item is not None
        assert item["stage"] == "download"
        assert item["play_index"] is None


class TestClaimSiblingPlays:
    """GPU Batch-job batching (per-play redesign): after a normal dequeue
    claims one (video_id, stage, play_index) winner, claim_sibling_plays()
    grabs other already-ready plays for the same video+stage so one Batch
    job can cover all of them - see ingest_pipeline_worker.py's GPU-routing
    branch, the only real caller."""

    def test_claims_other_plays_same_video_and_stage(self, pg_client):
        from ingest.queue_manager import QueueManager

        video_id = "siblingclaimtest01"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        for play_index in (0, 1, 2):
            qm.enqueue_video(video_id, stage="detection", priority=5, play_index=play_index)

        winner = qm.dequeue_next_video()
        assert winner is not None

        siblings = qm.claim_sibling_plays(
            video_id, "detection", exclude_id=int(winner["_doc_id"]), max_extra=5
        )

        assert len(siblings) == 2
        assert {s["play_index"] for s in siblings} == {0, 1, 2} - {winner["play_index"]}
        assert all(s["status"] == "processing" for s in siblings)

        # A second call finds nothing left to claim - everything's already
        # "processing", not "queued".
        assert qm.claim_sibling_plays(video_id, "detection", exclude_id=int(winner["_doc_id"]), max_extra=5) == []

    def test_does_not_claim_a_different_video_or_stage(self, pg_client):
        from ingest.queue_manager import QueueManager

        video_id = "siblingclaimtest02"
        other_video_id = "siblingclaimtest02b"
        _insert_video(pg_client, video_id)
        _insert_video(pg_client, other_video_id)
        qm = QueueManager(pg_client)

        qm.enqueue_video(video_id, stage="detection", priority=5, play_index=0)
        qm.enqueue_video(video_id, stage="tracking", priority=5, play_index=1)  # different stage
        qm.enqueue_video(other_video_id, stage="detection", priority=5, play_index=0)  # different video

        winner = qm.dequeue_next_video()
        assert winner is not None and winner["video_id"] == video_id and winner["stage"] == "detection"

        siblings = qm.claim_sibling_plays(
            video_id, "detection", exclude_id=int(winner["_doc_id"]), max_extra=5
        )

        assert siblings == []
        stats = pg_client.get_queue_status()
        # Both the different-stage and different-video rows are still
        # sitting untouched as "queued".
        assert stats["queued"] == 2

    def test_respects_max_extra_cap(self, pg_client):
        from ingest.queue_manager import QueueManager

        video_id = "siblingclaimtest03"
        _insert_video(pg_client, video_id)
        qm = QueueManager(pg_client)

        for play_index in range(5):
            qm.enqueue_video(video_id, stage="detection", priority=5, play_index=play_index)

        winner = qm.dequeue_next_video()
        assert winner is not None

        siblings = qm.claim_sibling_plays(
            video_id, "detection", exclude_id=int(winner["_doc_id"]), max_extra=2
        )

        assert len(siblings) == 2
