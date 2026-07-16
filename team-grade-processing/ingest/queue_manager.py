"""
Queue Manager Module (Postgres-backed)
Manages ingestion queue operations with priority and retry logic.

Pass 2a of the data-layer migration: replaces the old Firestore-based queue
(ingest/firestore_client.py's dequeue_next_video(), still present but no
longer constructed anywhere) with a real Postgres queue.

The critical fix this migration exists for: the old Firestore version's
dequeue_next_video() did a plain query-and-filter read, then a SEPARATE
update() call to mark the chosen item "processing" - two round-trips with no
transaction tying them together, so two workers could both read the same
"queued" item before either one's update() landed, and both would start
processing it. dequeue_next_video() below does the read (with STAGE_SEQUENCE
filtering, same behavior as before) and the "processing" update inside ONE
Postgres transaction using `SELECT ... FOR UPDATE SKIP LOCKED`: a second,
concurrent dequeue_next_video() call simply won't see rows the first
transaction is holding (they're skipped, not blocked), so it's impossible for
two workers to claim the same item.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from collections import deque

from sqlalchemy import select, update as sa_update, delete as sa_delete, func, text

from ingest.db_schema import ingestion_queue

logger = logging.getLogger(__name__)

_FALLBACK_STAGE_SEQUENCE = [
    "metadata", "download", "authenticity_check", "whistle_detection", "frame_extraction",
    "motion_compensation", "detection", "tracking", "pose", "torso_crop",
    "jersey_ocr", "rep_extraction", "biomechanics", "complete",
]


def _now():
    return datetime.now(timezone.utc)


class QueueManager:
    """Manage ingestion queue with priority and retry logic."""

    def __init__(self, postgres_client):
        """
        Initialize queue manager.

        Args:
            postgres_client: PostgresClient instance (provides both the
                convenience add_to_queue()/get_queue_status() methods and the
                shared SQLAlchemy engine this class uses directly for the
                atomic dequeue).
        """
        self.postgres = postgres_client
        self.engine = postgres_client.engine
        self.local_queue = deque()  # Local cache for quick operations
        logger.info("Queue manager initialized (Postgres)")

    def enqueue_video(
        self,
        video_id: str,
        stage: str = "metadata",
        priority: int = 5,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add video to ingestion queue.

        Priority levels:
        1 = highest priority (urgent)
        5 = normal priority (default)
        10 = lowest priority (background)

        Args:
            video_id: YouTube video ID
            stage: Starting stage
            priority: Priority level (1-10)
            metadata: Optional metadata

        Returns:
            True if successful
        """
        try:
            logger.info(f"Enqueueing video: {video_id} (priority: {priority})")

            if not (1 <= priority <= 10):
                logger.warning(f"Invalid priority {priority}, using default 5")
                priority = 5

            success = self.postgres.add_to_queue(video_id, stage, priority, metadata)

            if success:
                self.local_queue.append({
                    "video_id": video_id,
                    "priority": priority,
                    "stage": stage,
                    "enqueued_at": _now().isoformat(),
                })
                logger.info(f"[OK] Enqueued {video_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to enqueue {video_id}: {e}")

        return False

    def dequeue_next_video(self) -> Optional[Dict[str, Any]]:
        """
        Get next video from queue respecting STAGE SEQUENCE, atomically.

        CRITICAL: Dequeues jobs in stage order, not just by priority/time,
        same behavior as the old Firestore version:
        1. Lock every currently-claimable ("queued", not already locked by a
           concurrent dequeue) row in one SELECT ... FOR UPDATE SKIP LOCKED
        2. Group by video_id, keep only the earliest-stage-in-STAGE_SEQUENCE
           job per video (prevents re-processing "download" while
           "frame_extraction" is waiting for the same video)
        3. Pick the highest-priority/earliest-created survivor and flip it to
           "processing" - in the SAME transaction as step 1's lock, so no
           other worker can ever observe or claim that row as "queued"
           between the read and the write.

        Returns:
            Queue item dictionary or None if queue empty
        """
        try:
            from ingest.stages import STAGE_SEQUENCE
        except ImportError:
            logger.error("Could not import STAGE_SEQUENCE, using fallback")
            STAGE_SEQUENCE = _FALLBACK_STAGE_SEQUENCE

        try:
            with self.engine.begin() as conn:
                stmt = (
                    select(ingestion_queue)
                    .where(ingestion_queue.c.status == "queued")
                    .order_by(ingestion_queue.c.priority.desc(), ingestion_queue.c.created_at.asc())
                    .with_for_update(skip_locked=True)
                )
                rows = [dict(r) for r in conn.execute(stmt).mappings().all()]

                if not rows:
                    logger.debug("Queue is empty")
                    return None

                # GROUP by video_id to find next stage for each video
                videos: Dict[str, List[Dict[str, Any]]] = {}
                for row in rows:
                    videos.setdefault(row["video_id"], []).append(row)

                # FILTER: for each video, keep only the job at the earliest
                # stage in STAGE_SEQUENCE (identical logic to the old
                # FirestoreClient.dequeue_next_video()).
                next_stage_items: List[Dict[str, Any]] = []
                for vid, jobs in videos.items():
                    stages_for_video = [j["stage"] for j in jobs]
                    earliest_idx = min(
                        (STAGE_SEQUENCE.index(s) if s in STAGE_SEQUENCE else 999)
                        for s in stages_for_video
                    )
                    earliest_stage = (
                        STAGE_SEQUENCE[earliest_idx] if earliest_idx < len(STAGE_SEQUENCE) else None
                    )
                    for job in jobs:
                        if job["stage"] == earliest_stage:
                            next_stage_items.append(job)
                            break

                if not next_stage_items:
                    logger.debug("Queue is empty or all jobs already assigned")
                    return None

                def sort_key(item):
                    priority = -item.get("priority", 0)
                    created_at = item.get("created_at")
                    timestamp = created_at.timestamp() if created_at is not None else 0
                    return (priority, timestamp)

                next_stage_items.sort(key=sort_key)
                chosen = next_stage_items[0]

                now = _now()
                conn.execute(
                    sa_update(ingestion_queue)
                    .where(ingestion_queue.c.id == chosen["id"])
                    .values(status="processing", processing_started_at=now, updated_at=now)
                )
                # transaction commits (and releases every lock acquired
                # above, including the rows we didn't pick) when the `with
                # engine.begin()` block exits normally.

            result = dict(chosen)
            result["status"] = "processing"
            result["_doc_id"] = str(result["id"])
            logger.info(
                f"[OK] Dequeued video: {result['video_id']} (stage: {result['stage']}) "
                f"[filtered to next-stage-in-sequence, atomically claimed]"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to dequeue video: {e}")
            return None

    def mark_completed(self, queue_doc_id: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Mark queue item as completed and remove it from the queue.

        Args:
            queue_doc_id: Queue row id (stringified)
            metadata: Optional result metadata

        Returns:
            True if successful
        """
        try:
            qid = int(queue_doc_id)
            now = _now()
            known_cols = {c.name for c in ingestion_queue.columns}
            values: Dict[str, Any] = {"status": "completed", "completed_at": now, "updated_at": now}
            extra_meta = {}
            if metadata:
                for k, v in metadata.items():
                    if k in known_cols:
                        values[k] = v
                    else:
                        extra_meta[k] = v

            with self.engine.begin() as conn:
                stmt = sa_update(ingestion_queue).where(ingestion_queue.c.id == qid)
                if extra_meta:
                    stmt = stmt.values(
                        **values,
                        metadata=func.coalesce(ingestion_queue.c.metadata, text("'{}'::jsonb")).op("||")(extra_meta),
                    )
                else:
                    stmt = stmt.values(**values)
                result = conn.execute(stmt)
                if result.rowcount == 0:
                    logger.error(f"[CRITICAL] Queue row {qid} not found for mark_completed")
                    return False

                logger.info(f"[OK] Marked completed: {qid}")
                # Remove from queue (critical - must succeed; same transaction
                # as the status update above, so this can never leave a
                # "completed" row lingering in the active queue).
                deleted = conn.execute(sa_delete(ingestion_queue).where(ingestion_queue.c.id == qid))
                if deleted.rowcount == 0:
                    logger.error(f"[CRITICAL] Failed to remove {qid} from queue (job will be re-processed!)")
                    return False
                logger.info(f"[OK] Removed {qid} from queue")

            return True

        except Exception as e:
            logger.error(f"Failed to mark completed: {e}")
            return False

    def mark_failed(
        self,
        queue_doc_id: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """
        Mark queue item as failed with optional retry.

        Args:
            queue_doc_id: Queue row id (stringified)
            error_message: Error description
            retry: Whether to retry (increments retry count, re-queues)

        Returns:
            True if successful
        """
        try:
            qid = int(queue_doc_id)
            now = _now()
            with self.engine.begin() as conn:
                if retry:
                    logger.info(f"Requeueing {qid} for retry")
                    stmt = (
                        sa_update(ingestion_queue)
                        .where(ingestion_queue.c.id == qid)
                        .values(
                            status="queued",
                            error=error_message,
                            failed_at=now,
                            updated_at=now,
                            retry_count=ingestion_queue.c.retry_count + 1,
                        )
                    )
                else:
                    logger.error(f"Marked as failed (no retry): {qid}")
                    stmt = (
                        sa_update(ingestion_queue)
                        .where(ingestion_queue.c.id == qid)
                        .values(status="failed", error=error_message, failed_at=now, updated_at=now)
                    )
                result = conn.execute(stmt)
                return result.rowcount > 0

        except Exception as e:
            logger.error(f"Failed to mark as failed: {e}")
            return False

    def mark_processing(self, queue_doc_id: str) -> bool:
        """
        Mark queue item as currently processing.

        Args:
            queue_doc_id: Queue row id (stringified)

        Returns:
            True if successful
        """
        return self._mark_processing(queue_doc_id)

    def _mark_processing(self, queue_doc_id: Optional[str]) -> bool:
        """Internal method to mark as processing."""
        if not queue_doc_id:
            return False
        try:
            qid = int(queue_doc_id)
            now = _now()
            with self.engine.begin() as conn:
                result = conn.execute(
                    sa_update(ingestion_queue)
                    .where(ingestion_queue.c.id == qid)
                    .values(status="processing", processing_started_at=now, updated_at=now)
                )
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to mark as processing: {e}")
            return False

    def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue status
        """
        try:
            stats = self.postgres.get_queue_status()
            logger.info(f"Queue stats: {stats['queued']} queued, {stats['processing']} processing")
            return {**stats, "timestamp": _now().isoformat()}
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {"queued": 0, "processing": 0, "total": 0}

    def cleanup_stalled_items(self, timeout_minutes: int = 30) -> int:
        """
        Clean up items that have been "processing" too long by requeueing
        them - a real Postgres backend makes this a plain conditional UPDATE
        (this was a dead stub returning 0 in the Firestore version, which
        didn't have an efficient query for "processing since before X" without
        extra indexes it never had).

        Args:
            timeout_minutes: Minutes after which to consider item stalled

        Returns:
            Number of items cleaned up
        """
        try:
            cutoff = _now() - timedelta(minutes=timeout_minutes)
            logger.info(f"Cleaning up items stalled since {cutoff}")
            with self.engine.begin() as conn:
                result = conn.execute(
                    sa_update(ingestion_queue)
                    .where(ingestion_queue.c.status == "processing")
                    .where(ingestion_queue.c.processing_started_at < cutoff)
                    .values(status="queued", updated_at=_now())
                )
                count = result.rowcount or 0
            logger.info(f"Stalled item cleanup executed (timeout: {timeout_minutes}m): {count} requeued")
            return count
        except Exception as e:
            logger.error(f"Failed to cleanup stalled items: {e}")
            return 0

    def get_queue_by_priority(self, priority: int, limit: int = 10) -> list:
        """
        Get queue items by priority level - a real Postgres backend makes
        this a plain indexed query (dead stub in the Firestore version).

        Args:
            priority: Priority level
            limit: Maximum items to return

        Returns:
            List of queue items
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(ingestion_queue)
                    .where(ingestion_queue.c.priority == priority)
                    .order_by(ingestion_queue.c.created_at.asc())
                    .limit(limit)
                ).mappings().all()
            results = []
            for r in rows:
                d = dict(r)
                d["_doc_id"] = str(d["id"])
                results.append(d)
            return results
        except Exception as e:
            logger.error(f"Failed to get queue by priority: {e}")
            return []

    def requeue_video(
        self,
        video_id: str,
        stage: str,
        priority_adjustment: int = 0
    ) -> bool:
        """
        Manually requeue a video.

        Args:
            video_id: YouTube video ID
            stage: Stage to requeue at
            priority_adjustment: Increase priority by this amount (negative = lower priority)

        Returns:
            True if successful
        """
        try:
            logger.info(f"Requeuing {video_id} at stage {stage}")

            metadata = self.postgres.get_video_metadata(video_id)
            if not metadata:
                logger.error(f"Video not found: {video_id}")
                return False

            current_priority = metadata.get("priority", 5)
            new_priority = max(1, min(10, current_priority + priority_adjustment))

            return self.enqueue_video(
                video_id,
                stage=stage,
                priority=new_priority,
                metadata={"requeued": True}
            )

        except Exception as e:
            logger.error(f"Failed to requeue {video_id}: {e}")
            return False

    def prioritize_video(self, video_id: str, new_priority: int) -> bool:
        """
        Change priority of queued video.

        Args:
            video_id: YouTube video ID
            new_priority: New priority level

        Returns:
            True if successful
        """
        try:
            if not (1 <= new_priority <= 10):
                logger.error(f"Invalid priority: {new_priority}")
                return False

            logger.info(f"Updating priority for {video_id}: {new_priority}")
            return self.postgres.update_video_field(video_id, "priority", new_priority)

        except Exception as e:
            logger.error(f"Failed to change priority: {e}")
            return False


# Standalone functions
def enqueue_video(
    postgres_client,
    video_id: str,
    stage: str = "metadata",
    priority: int = 5
) -> bool:
    """Standalone function to enqueue video."""
    manager = QueueManager(postgres_client)
    return manager.enqueue_video(video_id, stage, priority)


def dequeue_next_video(postgres_client) -> Optional[Dict[str, Any]]:
    """Standalone function to dequeue next video."""
    manager = QueueManager(postgres_client)
    return manager.dequeue_next_video()


def get_queue_stats(postgres_client) -> Dict[str, Any]:
    """Standalone function to get queue stats."""
    manager = QueueManager(postgres_client)
    return manager.get_queue_stats()
