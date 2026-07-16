"""
Firestore Cleanup Script - Remove Stale Ingestion Entries

Identifies and safely removes:
1. Videos stuck in "pending" for too long
2. Queue entries older than retention period
3. Provides detailed logging of all actions

Usage:
    python -m ingest.ingestion_cleanup  # Run with defaults
    
Or in code:
    from ingest.ingestion_cleanup import cleanup_stale_ingestion_entries
    cleanup_stale_ingestion_entries(
        firestore_client,
        max_pending_minutes=60,
        max_queue_age_minutes=120,
        dry_run=False
    )
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

# VideoStatus is a plain str-Enum with no Firestore SDK dependency at import
# time - safe to keep importing it from here (Pass 2a data-layer migration:
# FirestoreClient itself is no longer constructed below, PostgresClient is -
# see ingest/postgres_client.py). firestore_client.db... calls throughout
# this file keep working unmodified against PostgresClient's Firestore-
# compat .db shim.
from ingest.firestore_client import FirestoreClient, VideoStatus
from ingest.postgres_client import PostgresClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_age_minutes(iso_timestamp: str) -> Optional[float]:
    """
    Calculate age of an ISO timestamp in minutes.

    Args:
        iso_timestamp: ISO format timestamp string

    Returns:
        Age in minutes, or None if parsing fails
    """
    try:
        created_dt = datetime.fromisoformat(iso_timestamp)
        age = (datetime.utcnow() - created_dt).total_seconds() / 60
        return age
    except Exception as e:
        logger.warning(f"Could not parse timestamp '{iso_timestamp}': {e}")
        return None


def cleanup_stale_pending_videos(
    firestore_client: FirestoreClient,
    max_pending_minutes: int = 60,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Find and clean up videos stuck in pending status.

    Args:
        firestore_client: Firestore client instance
        max_pending_minutes: Videos pending longer than this are considered stale
        dry_run: If True, log actions but don't delete

    Returns:
        Summary of cleanup actions
    """
    try:
        logger.info(f"Scanning for pending videos older than {max_pending_minutes} minutes...")

        # Get all pending videos
        pending_videos = firestore_client.get_pending_videos()

        stale_videos = []
        for video_doc in pending_videos:
            created_at = video_doc.get("created_at")
            if not created_at:
                continue

            age_minutes = get_age_minutes(created_at)
            if age_minutes is None:
                continue

            if age_minutes > max_pending_minutes:
                stale_videos.append({
                    "video_id": video_doc.get("video_id"),
                    "age_minutes": round(age_minutes, 1),
                    "status": video_doc.get("status"),
                    "queue_exists": firestore_client.queue_entry_exists(
                        video_doc.get("video_id")
                    ),
                })

        logger.info(f"Found {len(stale_videos)} stale pending videos")

        cleaned = []

        for video in stale_videos:
            video_id = video["video_id"]
            age = video["age_minutes"]
            queue_exists = video["queue_exists"]

            action = "would delete" if dry_run else "deleting"
            logger.warning(
                f"Stale pending video: {video_id} "
                f"(age: {age} min, queue_exists: {queue_exists})"
            )

            if not dry_run:
                # Delete the video document and queue entry if it exists
                try:
                    firestore_client.db.collection("videos").document(video_id).delete()
                    logger.info(f"  ✓ Deleted video document: {video_id}")

                    if queue_exists:
                        firestore_client.delete_queue_entry_by_video_id(video_id)
                        logger.info(f"  ✓ Deleted queue entry: {video_id}")

                    cleaned.append(video_id)

                except Exception as e:
                    logger.error(f"  ✗ Failed to delete {video_id}: {e}")
            else:
                logger.info(f"  [DRY RUN] {action} {video_id}")
                cleaned.append(f"{video_id} (dry-run)")

        return {
            "stale_video_count": len(stale_videos),
            "cleaned_count": len(cleaned),
            "cleaned_videos": cleaned,
            "dry_run": dry_run,
        }

    except Exception as e:
        logger.error(f"Failed to cleanup stale pending videos: {e}")
        return {
            "stale_video_count": 0,
            "cleaned_count": 0,
            "cleaned_videos": [],
            "error": str(e),
        }


def cleanup_stale_queue_entries(
    firestore_client: FirestoreClient,
    max_queue_age_minutes: int = 120,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Find and clean up queue entries that are too old.

    Args:
        firestore_client: Firestore client instance
        max_queue_age_minutes: Queue entries older than this are deleted
        dry_run: If True, log actions but don't delete

    Returns:
        Summary of cleanup actions
    """
    try:
        logger.info(f"Scanning for queue entries older than {max_queue_age_minutes} minutes...")

        # Get stale queue entries
        stale_entries = firestore_client.get_stale_queue_entries(
            max_age_minutes=max_queue_age_minutes
        )

        logger.info(f"Found {len(stale_entries)} stale queue entries")

        cleaned = []

        for entry in stale_entries:
            queue_id = entry.get("_doc_id")
            video_id = entry.get("video_id")
            age_minutes = get_age_minutes(entry.get("created_at", ""))

            action = "would delete" if dry_run else "deleting"
            logger.warning(
                f"Stale queue entry: {video_id} "
                f"(age: {age_minutes} min, doc_id: {queue_id})"
            )

            if not dry_run:
                try:
                    firestore_client.db.collection("ingestion_queue").document(
                        queue_id
                    ).delete()
                    logger.info(f"  ✓ Deleted queue entry: {queue_id}")
                    cleaned.append(queue_id)

                except Exception as e:
                    logger.error(f"  ✗ Failed to delete queue entry {queue_id}: {e}")
            else:
                logger.info(f"  [DRY RUN] {action} {queue_id}")
                cleaned.append(f"{queue_id} (dry-run)")

        return {
            "stale_queue_count": len(stale_entries),
            "cleaned_count": len(cleaned),
            "cleaned_entries": cleaned,
            "dry_run": dry_run,
        }

    except Exception as e:
        logger.error(f"Failed to cleanup stale queue entries: {e}")
        return {
            "stale_queue_count": 0,
            "cleaned_count": 0,
            "cleaned_entries": [],
            "error": str(e),
        }


def cleanup_stale_ingestion_entries(
    firestore_client: FirestoreClient,
    max_pending_minutes: int = 60,
    max_queue_age_minutes: int = 120,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Full cleanup: remove stale pending videos and old queue entries.

    Args:
        firestore_client: Firestore client instance
        max_pending_minutes: Videos pending longer than this are stale
        max_queue_age_minutes: Queue entries older than this are stale
        dry_run: If True, don't actually delete anything

    Returns:
        Combined summary of all cleanup actions
    """
    try:
        logger.info("=" * 70)
        logger.info("Starting full ingestion cleanup")
        logger.info(f"Config: pending_max={max_pending_minutes}m, queue_max={max_queue_age_minutes}m")
        logger.info(f"Dry run: {dry_run}")
        logger.info("=" * 70)

        # Clean up stale pending videos
        pending_result = cleanup_stale_pending_videos(
            firestore_client,
            max_pending_minutes=max_pending_minutes,
            dry_run=dry_run
        )

        # Clean up stale queue entries
        queue_result = cleanup_stale_queue_entries(
            firestore_client,
            max_queue_age_minutes=max_queue_age_minutes,
            dry_run=dry_run
        )

        summary = {
            "pending_videos": pending_result,
            "queue_entries": queue_result,
            "timestamp": datetime.utcnow().isoformat(),
            "dry_run": dry_run,
        }

        logger.info("\n" + "=" * 70)
        logger.info("CLEANUP SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Stale pending videos: {pending_result['stale_video_count']}")
        logger.info(f"  Cleaned: {pending_result['cleaned_count']}")
        logger.info(f"Stale queue entries: {queue_result['stale_queue_count']}")
        logger.info(f"  Cleaned: {queue_result['cleaned_count']}")
        logger.info("=" * 70)

        return summary

    except Exception as e:
        logger.error(f"✗ Cleanup failed: {e}")
        return {
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


# CLI Entry Point
if __name__ == "__main__":
    import sys

    logger.info("=" * 70)
    logger.info("FIRESTORE CLEANUP - STALE ENTRY REMOVAL")
    logger.info("=" * 70)

    # Check for dry-run flag
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    if dry_run:
        logger.info("DRY RUN MODE: No changes will be made")
        logger.info("")

    try:
        client = PostgresClient()

        # Run cleanup
        summary = cleanup_stale_ingestion_entries(
            client,
            max_pending_minutes=60,  # Videos pending > 1 hour
            max_queue_age_minutes=120,  # Queue entries > 2 hours
            dry_run=dry_run
        )

        if dry_run:
            logger.info("\n✓ Dry run complete - review output above")

    except Exception as e:
        logger.error(f"✗ Cleanup script failed: {e}")
        exit(1)
