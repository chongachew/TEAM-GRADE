"""
Ingestion Watchdog - Self-Healing System for Stuck Ingestion Jobs

Monitors videos stuck in "pending" status and automatically re-enqueues jobs
when queue entries are missing. This prevents UI from getting stuck and ensures
jobs always proceed to the worker.

Usage:
    python -m ingest.ingestion_watchdog  # Scan and repair all pending videos
    
Or in code:
    from ingest.ingestion_watchdog import repair_all_pending_videos
    repair_all_pending_videos(firestore_client, max_pending_minutes=30)
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from ingest.firestore_client import FirestoreClient, VideoStatus, IngestStage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_and_repair_video(
    firestore_client: FirestoreClient,
    video_id: str
) -> Dict[str, Any]:
    """
    Check a single video for integrity and repair if needed.

    Args:
        firestore_client: Firestore client instance
        video_id: YouTube video ID to check

    Returns:
        Dictionary with repair result:
        {
            "video_id": video_id,
            "repaired": bool,
            "reason": str,
            "action": str
        }
    """
    try:
        integrity = firestore_client.check_queue_integrity(video_id)
        video_exists = integrity["video_exists"]
        queue_exists = integrity["queue_exists"]
        status = integrity["status"]

        result = {
            "video_id": video_id,
            "repaired": False,
            "reason": "ok",
            "action": "none",
        }

        # Case 1: Video pending + no queue entry = needs repair
        if (video_exists and
            status == VideoStatus.PENDING.value and
            not queue_exists):

            logger.warning(
                f"⚠ Found stuck video: {video_id} "
                f"(pending={status}, queue_exists={queue_exists})"
            )

            # Re-enqueue the job
            try:
                success = firestore_client.add_to_queue(
                    video_id=video_id,
                    stage=IngestStage.METADATA,
                    priority=7,  # Slightly higher priority for recovery
                    metadata={"recovery": True, "recovered_at": datetime.utcnow().isoformat()}
                )

                if success:
                    logger.info(f"✓ Watchdog: repaired missing queue entry for {video_id}")
                    result["repaired"] = True
                    result["reason"] = f"pending with no queue entry"
                    result["action"] = "re-enqueued"
                else:
                    logger.error(f"✗ Failed to re-enqueue {video_id}")
                    result["repaired"] = False
                    result["reason"] = "re-enqueue failed"
                    result["action"] = "failed"

            except Exception as e:
                logger.error(f"✗ Error re-enqueueing {video_id}: {e}")
                result["repaired"] = False
                result["reason"] = str(e)
                result["action"] = "error"

        # Case 2: Video exists but no queue entry and not pending = orphaned
        elif (video_exists and
              not queue_exists and
              status not in [VideoStatus.COMPLETED.value, VideoStatus.FAILED.value]):

            logger.warning(
                f"⚠ Found orphaned video: {video_id} "
                f"(status={status}, queue_exists={queue_exists})"
            )

            # Re-enqueue if stuck in download/processing stages
            if status in [VideoStatus.DOWNLOADED.value, VideoStatus.PROCESSING.value]:
                try:
                    success = firestore_client.add_to_queue(
                        video_id=video_id,
                        stage=IngestStage.DOWNLOAD,
                        priority=7,
                        metadata={"recovery": True, "recovered_at": datetime.utcnow().isoformat()}
                    )

                    if success:
                        logger.info(f"✓ Watchdog: repaired orphaned video {video_id}")
                        result["repaired"] = True
                        result["reason"] = f"orphaned ({status})"
                        result["action"] = "re-enqueued"
                    else:
                        result["repaired"] = False
                        result["reason"] = "re-enqueue failed"
                        result["action"] = "failed"

                except Exception as e:
                    logger.error(f"✗ Error re-enqueueing orphaned {video_id}: {e}")
                    result["repaired"] = False
                    result["reason"] = str(e)
                    result["action"] = "error"

        return result

    except Exception as e:
        logger.error(f"✗ Failed to check video {video_id}: {e}")
        return {
            "video_id": video_id,
            "repaired": False,
            "reason": str(e),
            "action": "error",
        }


def repair_all_pending_videos(
    firestore_client: FirestoreClient,
    max_pending_minutes: int = 30
) -> Dict[str, Any]:
    """
    Scan all pending videos and repair missing queue entries.

    Args:
        firestore_client: Firestore client instance
        max_pending_minutes: Only check videos pending longer than this (0=all)

    Returns:
        Summary dictionary:
        {
            "total_pending": int,
            "total_repaired": int,
            "repairs": [list of repair results]
        }
    """
    try:
        logger.info(f"Starting watchdog scan (max_pending_minutes={max_pending_minutes})")

        # Get all pending videos
        pending_videos = firestore_client.get_pending_videos(
            max_age_minutes=max_pending_minutes if max_pending_minutes > 0 else None
        )

        logger.info(f"Found {len(pending_videos)} pending videos")

        repairs = []
        repaired_count = 0

        for video_doc in pending_videos:
            video_id = video_doc.get("video_id")
            
            if not video_id:
                logger.warning("Skipping video with no video_id")
                continue

            result = check_and_repair_video(firestore_client, video_id)
            repairs.append(result)

            if result["repaired"]:
                repaired_count += 1

        summary = {
            "total_pending": len(pending_videos),
            "total_repaired": repaired_count,
            "repairs": repairs,
            "timestamp": datetime.utcnow().isoformat(),
        }

        logger.info(
            f"✓ Watchdog scan complete: "
            f"found {len(pending_videos)} pending, "
            f"repaired {repaired_count}"
        )

        return summary

    except Exception as e:
        logger.error(f"✗ Watchdog scan failed: {e}")
        return {
            "total_pending": 0,
            "total_repaired": 0,
            "repairs": [],
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


# CLI Entry Point
if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("INGESTION WATCHDOG - SELF-HEALING SYSTEM")
    logger.info("=" * 70)

    try:
        client = FirestoreClient()
        summary = repair_all_pending_videos(client, max_pending_minutes=30)

        logger.info("\n" + "=" * 70)
        logger.info("WATCHDOG SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Total pending videos: {summary['total_pending']}")
        logger.info(f"Total repaired: {summary['total_repaired']}")

        if summary.get("repairs"):
            logger.info("\nRepair Details:")
            for repair in summary["repairs"]:
                status = "✓" if repair["repaired"] else "✗"
                logger.info(
                    f"  {status} {repair['video_id']}: "
                    f"{repair['reason']} → {repair['action']}"
                )

        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"✗ Watchdog failed to start: {e}")
        exit(1)
