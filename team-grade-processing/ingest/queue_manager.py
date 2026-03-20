"""
Queue Manager Module
Manages ingestion queue operations with priority and retry logic.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)


class QueueManager:
    """Manage ingestion queue with priority and retry logic."""

    def __init__(self, firestore_client):
        """
        Initialize queue manager.

        Args:
            firestore_client: FirestoreClient instance
        """
        self.firestore = firestore_client
        self.local_queue = deque()  # Local cache for quick operations
        logger.info("Queue manager initialized")

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

            # Validate priority
            if not (1 <= priority <= 10):
                logger.warning(f"Invalid priority {priority}, using default 5")
                priority = 5

            # Add to Firestore
            success = self.firestore.add_to_queue(
                video_id,
                stage,
                priority,
                metadata
            )

            if success:
                # Add to local queue
                self.local_queue.append({
                    "video_id": video_id,
                    "priority": priority,
                    "stage": stage,
                    "enqueued_at": datetime.utcnow().isoformat(),
                })

                logger.info(f"[OK] Enqueued {video_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to enqueue {video_id}: {e}")

        return False

    def dequeue_next_video(self) -> Optional[Dict[str, Any]]:
        """
        Get next video from queue (handles retries and failures).

        Returns:
            Queue item or None if empty
        """
        try:
            # Get from Firestore (primary source of truth)
            queue_item = self.firestore.dequeue_next_video()

            if queue_item:
                logger.info(f"Dequeued: {queue_item['video_id']} (stage: {queue_item['stage']})")

                # Mark as processing
                self._mark_processing(queue_item.get("_doc_id"))

                return queue_item
            else:
                logger.debug("Queue is empty")
                return None

        except Exception as e:
            logger.error(f"Failed to dequeue: {e}")
            return None

    def mark_completed(self, queue_doc_id: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Mark queue item as completed.

        Args:
            queue_doc_id: Queue document ID
            metadata: Optional result metadata

        Returns:
            True if successful
        """
        try:
            updates = {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            }

            if metadata:
                updates.update(metadata)

            success = self.firestore.update_queue_item(queue_doc_id, updates)

            if success:
                logger.info(f"[OK] Marked completed: {queue_doc_id}")
                self.firestore.remove_from_queue(queue_doc_id)

            return success

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
            queue_doc_id: Queue document ID
            error_message: Error description
            retry: Whether to retry (increments retry count)

        Returns:
            True if successful
        """
        try:
            # Get current retry count
            updates = {
                "status": "failed",
                "error": error_message,
                "failed_at": datetime.utcnow().isoformat(),
            }

            if retry:
                updates["retry_count"] = 1  # Will increment in Firestore
                updates["status"] = "queued"  # Re-queue for retry
                logger.info(f"Requeueing {queue_doc_id} for retry")
            else:
                logger.error(f"Marked as failed (no retry): {queue_doc_id}")

            success = self.firestore.update_queue_item(queue_doc_id, updates)
            return success

        except Exception as e:
            logger.error(f"Failed to mark as failed: {e}")
            return False

    def mark_processing(self, queue_doc_id: str) -> bool:
        """
        Mark queue item as currently processing.

        Args:
            queue_doc_id: Queue document ID

        Returns:
            True if successful
        """
        return self._mark_processing(queue_doc_id)

    def _mark_processing(self, queue_doc_id: Optional[str]) -> bool:
        """
        Internal method to mark as processing.

        Args:
            queue_doc_id: Queue document ID

        Returns:
            True if successful
        """
        if not queue_doc_id:
            return False

        try:
            updates = {
                "status": "processing",
                "processing_started_at": datetime.utcnow().isoformat(),
            }

            success = self.firestore.update_queue_item(queue_doc_id, updates)
            return success

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
            stats = self.firestore.get_queue_status()

            logger.info(f"Queue stats: {stats['queued']} queued, {stats['processing']} processing")

            return {
                **stats,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {"queued": 0, "processing": 0, "total": 0}

    def cleanup_stalled_items(self, timeout_minutes: int = 30) -> int:
        """
        Clean up items that have been processing too long.

        Args:
            timeout_minutes: Minutes after which to consider item stalled

        Returns:
            Number of items cleaned up
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            logger.info(f"Cleaning up items stalled since {cutoff_time}")

            # This would require a more complex Firestore query
            # For now, log the intent
            logger.info(f"Stalled item cleanup executed (timeout: {timeout_minutes}m)")
            return 0

        except Exception as e:
            logger.error(f"Failed to cleanup stalled items: {e}")
            return 0

    def get_queue_by_priority(self, priority: int, limit: int = 10) -> list:
        """
        Get queue items by priority level.

        Args:
            priority: Priority level
            limit: Maximum items to return

        Returns:
            List of queue items
        """
        try:
            # Note: This would require additional Firestore indexing
            # For now, return empty (implementation depends on Firestore schema)
            logger.debug(f"Fetching queue items with priority {priority}")
            return []

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

            # Get current priority
            metadata = self.firestore.get_video_metadata(video_id)
            if not metadata:
                logger.error(f"Video not found: {video_id}")
                return False

            current_priority = metadata.get("priority", 5)
            new_priority = max(1, min(10, current_priority + priority_adjustment))

            # Add back to queue
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

            # This would require finding and updating the queue item
            # Implementation depends on Firestore document structure
            return self.firestore.update_video_field(video_id, "priority", new_priority)

        except Exception as e:
            logger.error(f"Failed to change priority: {e}")
            return False


# Standalone functions
def enqueue_video(
    firestore_client,
    video_id: str,
    stage: str = "metadata",
    priority: int = 5
) -> bool:
    """Standalone function to enqueue video."""
    manager = QueueManager(firestore_client)
    return manager.enqueue_video(video_id, stage, priority)


def dequeue_next_video(firestore_client) -> Optional[Dict[str, Any]]:
    """Standalone function to dequeue next video."""
    manager = QueueManager(firestore_client)
    return manager.dequeue_next_video()


def get_queue_stats(firestore_client) -> Dict[str, Any]:
    """Standalone function to get queue stats."""
    manager = QueueManager(firestore_client)
    return manager.get_queue_stats()
