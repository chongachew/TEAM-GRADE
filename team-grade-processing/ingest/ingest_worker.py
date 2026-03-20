"""
Ingestion Worker Module
Main orchestrator for YouTube → Firestore → Processing Queue workflow.
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

# Setup logging
logger = logging.getLogger(__name__)

# Configuration: Path to Google Cloud service account credentials
def get_credentials_path() -> Path:
    """
    Get the path to Google Cloud service account credentials.
    
    Expected locations (checked in order):
    1. credentials/service-account.json (relative to project root)
    2. Fallback to None (will use Application Default Credentials)
    """
    # Try relative to project root
    project_root = Path(__file__).parent.parent.parent
    creds_file = project_root / "credentials" / "service-account.json"
    
    if creds_file.exists():
        return creds_file
    
    return None


class IngestWorker:
    """Orchestrate complete ingestion pipeline."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        firestore_project: str = "the-bridge-athletics1"
    ):
        """
        Initialize ingestion worker.

        Args:
            output_dir: Directory for downloaded videos
            log_dir: Directory for logs
            firestore_project: Google Cloud project ID
        """
        self.output_dir = Path(output_dir or "raw_videos")
        self.log_dir = Path(log_dir or "logs")
        self.firestore_project = firestore_project

        # Create directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Setup logging
        self._setup_logging()

        # Import modules
        from .youtube_metadata import YouTubeMetadataExtractor
        from .youtube_downloader import YouTubeDownloader
        from .firestore_client import FirestoreClient
        from .queue_manager import QueueManager

        self.metadata_extractor = YouTubeMetadataExtractor()
        self.downloader = YouTubeDownloader(self.output_dir)

        try:
            creds_path = get_credentials_path()
            if creds_path:
                logger.info(f"Using credentials file: {creds_path}")
                self.firestore = FirestoreClient(
                    project_id=firestore_project,
                    credentials_path=creds_path
                )
            else:
                logger.warning("No credentials file found, attempting Application Default Credentials...")
                self.firestore = FirestoreClient(
                    project_id=firestore_project
                )
            self.queue_manager = QueueManager(self.firestore)
            logger.info("[OK] Firestore initialized")
        except FileNotFoundError as e:
            logger.error(f"Firestore credentials not found: {e}")
            logger.error("Please set up credentials at: team-grade-processing/credentials/service-account.json")
            self.firestore = None
            self.queue_manager = None
        except Exception as e:
            logger.error(f"Failed to initialize Firestore: {e}")
            self.firestore = None
            self.queue_manager = None

        logger.info("[OK] Ingestion worker initialized")

    def _setup_logging(self) -> None:
        """Setup logging to file and console."""
        log_file = self.log_dir / "ingest.log"

        # Create logger
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout),
            ]
        )

        logger.info("=" * 60)
        logger.info("TEAM-GRADE Ingestion Worker Started")
        logger.info(f"Log file: {log_file}")
        logger.info("=" * 60)

    def ingest_youtube_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Main ingestion orchestrator: URL → Metadata → Download → Firestore → Queue

        Args:
            url: YouTube URL or video ID

        Returns:
            Ingestion result or None if failed
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"Starting ingestion for: {url}")
        logger.info("=" * 60)

        try:
            # Step 1: Extract video ID
            logger.info("Step 1/5: Extracting video ID...")
            video_id = self.metadata_extractor.get_video_id(url)

            if not video_id:
                logger.error("[FAIL] Invalid YouTube URL or video ID")
                return self._result(False, "Invalid URL", None)

            logger.info(f"[OK] Video ID: {video_id}")

            # Step 2: Check if already ingested
            if self.firestore:
                existing = self.firestore.get_video_metadata(video_id)
                if existing:
                    logger.warning(f"Video already ingested: {video_id}")
                    return self._result(False, "Video already ingested", video_id)

            # Step 3: Fetch and parse metadata
            logger.info("Step 2/5: Fetching metadata...")
            yt_metadata = self.downloader.get_video_info(url)

            if not yt_metadata:
                logger.error("[FAIL] Failed to fetch video metadata")
                return self._result(False, "Failed to fetch metadata", video_id)

            logger.info(f"[OK] Got video info: {yt_metadata['title'][:60]}...")

            # Parse football-specific metadata
            logger.info("Step 3/5: Parsing football metadata...")
            football_metadata = self.metadata_extractor.parse_football_metadata(
                title=yt_metadata.get("title", ""),
                description=yt_metadata.get("description", ""),
                video_id=video_id
            )

            logger.info(f"  Teams: {football_metadata.get('team1')} vs {football_metadata.get('team2')}")
            logger.info(f"  Year: {football_metadata.get('year')}")
            logger.info(f"  Level: {football_metadata.get('level')}")

            # Combined metadata
            full_metadata = {**yt_metadata, **football_metadata}

            # Step 4: Download video
            logger.info("Step 4/5: Downloading video...")
            video_path = self.downloader.download_video(url)

            if not video_path:
                logger.error("[FAIL] Failed to download video")
                self._save_metadata_only(video_id, full_metadata)
                return self._result(False, "Download failed", video_id)

            logger.info(f"[OK] Downloaded to: {video_path}")
            full_metadata["download_path"] = str(video_path)
            full_metadata["file_size_mb"] = video_path.stat().st_size / (1024 * 1024)

            # Step 5: Save to Firestore and queue
            logger.info("Step 5/5: Saving to Firestore and queueing...")

            if self.firestore:
                # Save metadata
                if not self.firestore.save_video_metadata(video_id, full_metadata):
                    logger.error("[FAIL] Failed to save metadata")
                    return self._result(False, "Firestore save failed", video_id)

                # Add to queue
                from .firestore_client import IngestStage
                if not self.queue_manager.enqueue_video(
                    video_id,
                    stage=IngestStage.FRAME_EXTRACTION.value,  # Start after download
                    priority=5
                ):
                    logger.error("[FAIL] Failed to queue video")
                    return self._result(False, "Queue failed", video_id)

                logger.info("[OK] Saved to Firestore and queued")
            else:
                logger.warning("[WARN] Firestore not available, skipping queue")

            # Success!
            result = self._result(
                True,
                "Success",
                video_id,
                extra={
                    "file_size_mb": full_metadata.get("file_size_mb"),
                    "teams": f"{football_metadata.get('team1')} vs {football_metadata.get('team2')}",
                    "download_path": str(video_path),
                }
            )

            logger.info("\n" + "=" * 60)
            logger.info("[OK] Ingestion completed successfully")
            logger.info(f"Video ID: {video_id}")
            logger.info("=" * 60 + "\n")

            return result

        except Exception as e:
            logger.error(f"[FAIL] Ingestion failed: {e}", exc_info=True)
            return self._result(False, f"Exception: {str(e)}", None)

    def _save_metadata_only(self, video_id: str, metadata: Dict[str, Any]) -> None:
        """Save metadata to Firestore without download/queue."""
        if self.firestore:
            try:
                self.firestore.save_video_metadata(video_id, metadata)
                logger.info("Saved metadata despite download failure")
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")

    def _result(
        self,
        success: bool,
        message: str,
        video_id: Optional[str],
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Format result dictionary."""
        result = {
            "success": success,
            "message": message,
            "video_id": video_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if extra:
            result.update(extra)

        return result

    def batch_ingest(self, urls: list) -> Dict[str, Any]:
        """
        Ingest multiple videos.

        Args:
            urls: List of YouTube URLs

        Returns:
            Batch results summary
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"Batch ingestion: {len(urls)} videos")
        logger.info("=" * 60)

        results = []
        successes = 0
        failures = 0

        for i, url in enumerate(urls, 1):
            logger.info(f"\n[{i}/{len(urls)}] Processing...")

            result = self.ingest_youtube_url(url)
            results.append(result)

            if result["success"]:
                successes += 1
            else:
                failures += 1

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("Batch Ingestion Summary")
        logger.info("=" * 60)
        logger.info(f"Total URLs: {len(urls)}")
        logger.info(f"Successful: {successes}")
        logger.info(f"Failed: {failures}")
        logger.info("=" * 60 + "\n")

        return {
            "total": len(urls),
            "successful": successes,
            "failed": failures,
            "results": results,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_queue_status(self) -> Optional[Dict[str, Any]]:
        """Get current queue status."""
        if not self.queue_manager:
            logger.error("Queue manager not available")
            return None

        return self.queue_manager.get_queue_stats()

    def process_queue_item(self, queue_item: Dict[str, Any]) -> bool:
        """
        Process next item from queue (usually called by separate worker process).

        Args:
            queue_item: Queue item from dequeue_next_video()

        Returns:
            True if processing completed
        """
        video_id = queue_item.get("video_id")
        stage = queue_item.get("stage")
        queue_doc_id = queue_item.get("_doc_id")

        if not all([video_id, stage, queue_doc_id]):
            logger.error("Invalid queue item")
            return False

        try:
            logger.info(f"Processing {video_id} at stage {stage}")

            # Get metadata
            metadata = self.firestore.get_video_metadata(video_id)
            if not metadata:
                logger.error(f"Video metadata not found: {video_id}")
                self.queue_manager.mark_failed(queue_doc_id, "Metadata not found")
                return False

            # Stage-specific processing would go here
            # For now, mark completed
            from .firestore_client import VideoStatus
            self.firestore.update_video_status(video_id, VideoStatus.COMPLETED)
            self.queue_manager.mark_completed(queue_doc_id)

            logger.info(f"[OK] Completed processing {video_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to process {video_id}: {e}")
            if self.queue_manager:
                self.queue_manager.mark_failed(queue_doc_id, str(e), retry=True)
            return False

    def start_worker_loop(self, poll_interval: int = 5) -> None:
        """
        Start worker listener loop - continuously processes queue items.
        
        This is the default mode when the worker is run with no arguments.
        
        Args:
            poll_interval: Seconds to wait between queue checks (default: 5)
        """
        import time
        
        if not self.firestore or not self.queue_manager:
            logger.error("Firestore not initialized - cannot start worker loop")
            return
        
        logger.info("\n" + "=" * 60)
        logger.info("[OK] Ingestion Worker Started (Listener Mode)")
        logger.info("Listening for jobs in queue...")
        logger.info("=" * 60 + "\n")
        
        try:
            while True:
                try:
                    # Check for next item in queue
                    logger.debug("Checking queue for jobs...")
                    queue_item = self.queue_manager.dequeue_next_video()
                    
                    if queue_item:
                        # Got a job - process it
                        video_id = queue_item.get('video_id')
                        stage = queue_item.get('stage')
                        logger.info(f"[WORK] New job found: {video_id} (stage: {stage})")
                        logger.debug("Processing job...")
                        
                        success = self.process_queue_item(queue_item)
                        
                        if success:
                            logger.info(f"[DONE] Job completed: {video_id}")
                        else:
                            logger.warning(f"[WARN] Job processing failed: {video_id}")
                    else:
                        # No items in queue, wait and retry
                        logger.debug(f"Queue empty, waiting {poll_interval} seconds...")
                        time.sleep(poll_interval)
                
                except Exception as e:
                    logger.error(f"[ERROR] Worker loop exception: {e}", exc_info=True)
                    logger.info(f"Retrying in {poll_interval} seconds...")
                    time.sleep(poll_interval)
        
        except KeyboardInterrupt:
            logger.info("\n" + "=" * 60)
            logger.info("Worker stopped by user (CTRL+C)")
            logger.info("=" * 60)


def main():
    """Command-line interface and worker startup."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TEAM-GRADE YouTube Ingestion Worker",
        add_help=False  # We'll handle help manually for better control
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube URL to ingest"
    )
    parser.add_argument(
        "--batch",
        nargs="+",
        help="Ingest multiple URLs"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory for videos"
    )
    parser.add_argument(
        "--help", "-h",
        action="store_true",
        help="Show this help message"
    )

    args = parser.parse_args()

    # Initialize worker
    worker = IngestWorker(output_dir=args.output)

    # Show help if requested
    if args.help:
        parser.print_help()
        return 0

    # Single URL
    if args.url:
        result = worker.ingest_youtube_url(args.url)
        print(f"\nResult: {result}")
        return 0 if result["success"] else 1

    # Batch URLs
    elif args.batch:
        results = worker.batch_ingest(args.batch)
        print(f"\nResults: {results}")
        return 0 if results["failed"] == 0 else 1

    # Queue status
    elif args.status:
        status = worker.get_queue_status()
        print(f"\nQueue Status: {status}")
        return 0

    # No arguments - start worker listener loop
    else:
        worker.start_worker_loop()
        return 0


if __name__ == "__main__":
    sys.exit(main())
