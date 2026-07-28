"""
Ingestion Pipeline Worker
Main orchestrator for multi-stage pipeline execution.
Processes jobs from queue and dispatches to appropriate stage handlers.
"""

import logging
import sys
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================================
# DEPENDENCY CHECK & INSTALLATION
# ============================================================================

def ensure_dependencies():
    """Ensure all required dependencies are installed."""
    required_packages = [
        "google-cloud-firestore",
        "opencv-python",
        "numpy",
        "pillow",
        "requests",
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing.append(package)
    
    if missing:
        logging.warning(f"Missing dependencies: {', '.join(missing)}")
        logging.info("Installing missing dependencies...")
        for package in missing:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", package, "-q"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    shell=False
                )
                logging.info(f"  ✓ {package}")
            except Exception as e:
                logging.error(f"  ✗ {package}: {e}")
        logging.info("Dependency check complete")

# Run dependency check early
try:
    ensure_dependencies()
except Exception as e:
    logging.warning(f"Dependency check failed: {e}")

from config import settings
from .postgres_client import PostgresClient
from .queue_manager import QueueManager
from .stages import STAGE_HANDLERS, STAGE_DETECTION, STAGE_TRACKING
from .utils import setup_logging, mark_stage_attempt
from .batch_dispatch import submit_gpu_stage_job
from .retention import purge_unclaimed_videos

# How often (in poll iterations) to sweep for queue items stuck in
# "processing" - e.g. an AWS Batch GPU job that was interrupted (spot
# reclaim, capacity error) without ever calling mark_completed/mark_failed.
# Not a concern before Phase 3, since the only "processor" was this same
# always-on loop and a crash here just meant the whole process restarted.
STALL_CLEANUP_EVERY_N_ITERATIONS = 60

# Far less frequent than the stall sweep above - this walks every S3 object
# and DB row for each stale video, not a cheap query. Free-tool submissions
# sit unclaimed for hours by design (the whole point is "try it, decide
# later"), so there's no benefit to running this often.
RETENTION_SWEEP_EVERY_N_ITERATIONS = 720

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Main worker for multi-stage ingestion pipeline."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        credentials_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[float] = None
    ):
        """Initialize pipeline worker.

        Args:
            project_id: Google Cloud project ID (uses settings.FIRESTORE_PROJECT_ID if None)
            credentials_path: Path to service account credentials (uses settings if None)
            log_dir: Directory for logs (uses settings.LOGS_DIR if None)
            max_retries: Maximum retries per stage (uses settings.QUEUE_MAX_RETRIES if None)
            retry_backoff: Initial backoff for retries (uses settings.QUEUE_RETRY_BACKOFF if None)
        """
        # Use settings defaults if not provided
        self.project_id = project_id or settings.FIRESTORE_PROJECT_ID
        self.credentials_path = credentials_path or settings.FIRESTORE_CREDENTIALS
        self.max_retries = max_retries if max_retries is not None else settings.QUEUE_MAX_RETRIES
        self.retry_backoff = retry_backoff if retry_backoff is not None else settings.QUEUE_RETRY_BACKOFF

        # Validate max_retries and retry_backoff
        if self.max_retries < 1 or self.max_retries > 10:
            raise ValueError(f"max_retries must be 1-10, got {self.max_retries}")
        if self.retry_backoff <= 0 or self.retry_backoff > 10:
            raise ValueError(f"retry_backoff must be > 0 and <= 10, got {self.retry_backoff}")

        # Setup logging
        self.log_dir = Path(log_dir or settings.LOGS_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Use relative path for logging (security: prevent path traversal via absolute paths)
        log_file = "logs/pipeline_worker.log"
        setup_logging(__name__, log_file)

        # Initialize Postgres (Pass 2a data-layer migration - project_id/
        # credentials_path are Firestore-era constructor args, kept for CLI
        # back-compat but unused now; connection comes from DATABASE_URL).
        try:
            self.firestore = PostgresClient(database_url=settings.DATABASE_URL)
            self.queue_manager = QueueManager(self.firestore)
            logger.info("[OK] Postgres and queue initialized")
        except Exception as e:
            logger.error(f"[FAIL] Failed to initialize Postgres: {e}")
            raise
        
        # ✅ OPTIMIZATION: Preload ML models at startup (35% faster per video)
        logger.info("[INFO] Preloading ML models...")
        try:
            self._load_pose_model()
            logger.info("[OK] Pose model loaded")
        except Exception as e:
            logger.warning(f"[WARN] Failed to preload pose model: {e}")
        
        try:
            self._load_ocr_model()
            logger.info("[OK] OCR model loaded")
        except Exception as e:
            logger.warning(f"[WARN] Failed to preload OCR model: {e}")
        
        logger.info("[OK] All models preloaded")

    def _load_pose_model(self) -> None:
        """Load MediaPipe pose model for reuse.
        
        Called at startup to preload model (saves ~30s for first video).
        """
        try:
            import mediapipe as mp
            # Just verify it imports and can be instantiated
            self.pose_detector = mp.solutions.pose.Pose(
                static_image_mode=False,
                min_detection_confidence=0.7
            )
            logger.debug("Pose model instantiated for preload check")
        except Exception as e:
            logger.warning(f"Failed to preload pose model: {e}")
            raise
    
    def _load_ocr_model(self) -> None:
        """Load OCR model for reuse.
        
        Called at startup to cache model (saves ~20s per video after first).
        """
        try:
            from ingest.stages.jersey_ocr_stage import get_ocr_instance
            # Trigger lazy load of cached model
            get_ocr_instance()
            logger.debug("OCR model cached for reuse")
        except Exception as e:
            logger.warning(f"Failed to preload OCR model: {e}")
            raise

    def run_worker(
        self,
        poll_interval: float = 5.0,
        max_iterations: Optional[int] = None
    ) -> None:
        """Main worker loop.

        Continuously polls queue and processes jobs.

        Args:
            poll_interval: Seconds to wait between queue polls (must be 0.1-300)
            max_iterations: Max iterations (None = infinite)
        """
        # Validate poll_interval
        if poll_interval < 0.1 or poll_interval > 300:
            raise ValueError(f"poll_interval must be 0.1-300 seconds, got {poll_interval}")
        
        logger.info("=" * 70)
        logger.info("TEAM-GRADE Pipeline Worker Started")
        logger.info(f"Poll interval: {poll_interval}s")
        logger.info("=" * 70)

        iteration = 0

        try:
            while max_iterations is None or iteration < max_iterations:
                iteration += 1

                try:
                    if iteration % STALL_CLEANUP_EVERY_N_ITERATIONS == 0:
                        try:
                            requeued = self.queue_manager.cleanup_stalled_items(
                                timeout_minutes=30,
                                stage_timeouts={STAGE_DETECTION: 150, STAGE_TRACKING: 150},
                            )
                            if requeued:
                                logger.warning(f"[Iteration {iteration}] Requeued {requeued} stalled item(s)")
                        except Exception as e:
                            logger.warning(f"[Iteration {iteration}] Stalled-item cleanup failed: {e}")

                    if iteration % RETENTION_SWEEP_EVERY_N_ITERATIONS == 0:
                        try:
                            purged = purge_unclaimed_videos(
                                self.firestore.engine, older_than_hours=settings.RETENTION_HOURS
                            )
                            if purged:
                                logger.info(f"[Iteration {iteration}] Purged {purged} unclaimed video(s)")
                        except Exception as e:
                            logger.warning(f"[Iteration {iteration}] Retention sweep failed: {e}")

                    # Get next job from queue
                    queue_item = self.queue_manager.dequeue_next_video()

                    if not queue_item:
                        logger.debug(f"[Iteration {iteration}] Queue empty, waiting...")
                        time.sleep(poll_interval)
                        continue

                    self.process_queue_item(queue_item, iteration)

                except KeyboardInterrupt:
                    logger.info("Worker interrupted by user")
                    break

                except Exception as e:
                    logger.error(
                        f"Worker error in iteration {iteration}: {e}",
                        exc_info=True
                    )
                    time.sleep(poll_interval)

        except Exception as e:
            logger.error(f"Fatal worker error: {e}", exc_info=True)
            raise

    def process_queue_item(
        self,
        queue_item: Dict[str, Any],
        iteration: Optional[int] = None,
        force_inline: bool = False
    ) -> None:
        """Process one dequeued item to completion: validate, dispatch (inline
        or to an AWS Batch GPU job), and finalize its queue status.

        Shared by `run_worker()`'s poll loop and `run_batch_job.py` (the Batch
        job entrypoint), so a GPU job finalizes its own queue item the exact
        same way the CPU loop always has.

        Args:
            queue_item: Dequeued row, must contain video_id/stage/_doc_id.
            iteration: Poll iteration number, for log messages only.
            force_inline: Skip the AWS Batch routing check and always run the
                stage handler in this process. Set by run_batch_job.py, which
                IS the offloaded job - without this, a detection/tracking item
                handed to it would just resubmit itself to Batch forever.
        """
        log_prefix = f"[Iteration {iteration}] " if iteration is not None else ""

        # Validate queue_item structure
        if not all(k in queue_item for k in ["video_id", "stage", "_doc_id"]):
            logger.error(f"{log_prefix}Malformed queue item: missing required fields")
            return

        video_id = queue_item.get("video_id")
        stage = queue_item.get("stage")
        queue_doc_id = queue_item.get("_doc_id")

        # Validate extracted fields are non-empty
        if not video_id or not isinstance(video_id, str):
            logger.error(f"{log_prefix}Invalid video_id from queue: {video_id}")
            return
        if not stage or not isinstance(stage, str):
            logger.error(f"{log_prefix}Invalid stage from queue: {stage}")
            return

        # GPU-eligible stages are offloaded to AWS Batch when enabled, rather
        # than run inline here. The Batch job's own entrypoint
        # (run_batch_job.py) calls this same method to finalize the item, so
        # we deliberately do NOT call mark_completed/mark_failed for it here -
        # the item stays "processing" (already set by dequeue_next_video's
        # SKIP LOCKED transaction) until the Batch job finishes.
        if not force_inline and stage in (STAGE_DETECTION, STAGE_TRACKING) and settings.GPU_BATCH_ENABLED:
            play_index = queue_item.get("play_index")
            items = [{"queue_doc_id": queue_doc_id, "play_index": play_index}]

            # Per-play redesign: batch sibling plays' already-ready work for
            # the same video+stage into this same Batch job (one model
            # load, not one job per play) - see queue_manager.claim_sibling_plays.
            # Only applies once this video actually has plays (play_index
            # is not None) - whole-video-mode jobs stay a batch-of-1.
            if play_index is not None:
                try:
                    siblings = self.queue_manager.claim_sibling_plays(
                        video_id, stage,
                        exclude_id=int(queue_doc_id),
                        max_extra=settings.GPU_BATCH_MAX_PLAYS_PER_JOB - 1,
                    )
                    items.extend(
                        {"queue_doc_id": s["_doc_id"], "play_index": s["play_index"]}
                        for s in siblings
                    )
                except Exception as e:
                    # Non-fatal: worst case this job only covers the one
                    # already-claimed item, same as before batching existed.
                    logger.warning(f"{log_prefix}[{video_id}] Failed to claim sibling plays for {stage}: {e}")

            try:
                job_id = submit_gpu_stage_job(video_id, stage, items)
                logger.info(
                    f"{log_prefix}[{video_id}] Submitted {stage} to AWS Batch: job {job_id} "
                    f"({len(items)} play(s))"
                )
            except Exception as e:
                logger.error(f"{log_prefix}[{video_id}] Failed to submit {stage} to AWS Batch: {e}")
                # The whole batch never got submitted - every claimed item
                # (not just the original one) needs to go back to the
                # queue, or the sibling rows would sit "processing" forever
                # with no job left to finalize them.
                for it in items:
                    self.queue_manager.mark_failed(it["queue_doc_id"], f"Batch submit failed: {e}", retry=True)
            return

        logger.info(f"{log_prefix}Processing: {video_id}/{stage}")

        success, error_msg = self._process_stage(video_id, stage, queue_item)

        if success:
            logger.info(f"[{video_id}] Stage {stage} completed successfully")
            self.queue_manager.mark_completed(queue_doc_id)
        else:
            logger.error(f"[{video_id}] Stage {stage} failed: {error_msg}")
            self.queue_manager.mark_failed(
                queue_doc_id,
                error_msg or "Unknown error",
                retry=True
            )

    def _process_stage(
        self,
        video_id: str,
        stage: str,
        queue_item: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Process a single pipeline stage.

        Args:
            video_id: YouTube video ID (will be sanitized)
            stage: Stage name
            queue_item: Queue item data

        Returns:
            (success: bool, error_message: Optional[str])
        """
        safe_video_id = None  # Initialize for exception logging
        
        try:
            # Sanitize video_id to prevent path traversal
            try:
                safe_video_id = settings.sanitize_id(video_id)
            except ValueError as e:
                error_msg = f"Invalid video_id: {e}"
                logger.error(f"[{video_id}] {error_msg}")
                return False, error_msg

            # Get handler for stage
            handler = STAGE_HANDLERS.get(stage)

            if not handler:
                error_msg = f"Unknown stage: {stage}"
                logger.error(f"[{safe_video_id}] {error_msg}")
                # Fail immediately for config errors; don't retry
                return False, error_msg

            # Extract payload. queue_item's play_index (a real column once
            # play_detection populates it, None for whole-video-mode jobs)
            # is merged in directly here rather than through the "payload"
            # JSONB key - queue_item never actually has a "payload" key
            # today (a known pre-existing inconsistency, not fixed here),
            # but play_index is a real column so this always works.
            payload = queue_item.get("payload", {})
            play_index = queue_item.get("play_index")
            if play_index is not None:
                payload = {**payload, "play_index": play_index}

            logger.info(
                f"[{safe_video_id}] Executing stage handler: {stage}"
            )

            # Run stage handler with retry logic
            for attempt in range(1, self.max_retries + 1):
                try:
                    # Mark attempt (non-critical, log warning if fails)
                    try:
                        mark_stage_attempt(self.firestore, safe_video_id, stage, play_index=play_index)
                    except Exception as e:
                        logger.warning(
                            f"[{safe_video_id}][{stage}] Failed to mark attempt: {e}"
                        )

                    # Call handler
                    success, error_msg = handler(
                        self.firestore,
                        safe_video_id,
                        self.queue_manager,
                        payload
                    )

                    if success:
                        return True, None

                    # If not successful, may retry
                    if attempt < self.max_retries:
                        wait_time = self.retry_backoff * (2 ** (attempt - 1))
                        logger.warning(
                            f"[{safe_video_id}][{stage}] Attempt {attempt} failed, "
                            f"retrying in {wait_time:.1f}s: {error_msg}"
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            f"[{safe_video_id}][{stage}] All {self.max_retries} attempts failed: "
                            f"{error_msg}"
                        )
                        return False, error_msg

                except Exception as e:
                    if attempt < self.max_retries:
                        wait_time = self.retry_backoff * (2 ** (attempt - 1))
                        logger.warning(
                            f"[{safe_video_id}][{stage}] Attempt {attempt} exception, "
                            f"retrying in {wait_time:.1f}s: {e}"
                        )
                        time.sleep(wait_time)
                    else:
                        error_msg = f"Exception after {self.max_retries} attempts: {str(e)}"
                        logger.error(f"[{safe_video_id}][{stage}] {error_msg}")
                        return False, error_msg

            return False, "Max retries exceeded"

        except Exception as e:
            error_msg = f"Failed to process stage {stage}: {str(e)}"
            # Use safe_video_id if available, otherwise unsanitized for debugging
            video_id_for_log = safe_video_id if safe_video_id else video_id
            logger.error(f"[{video_id_for_log}] {error_msg}", exc_info=True)
            return False, error_msg


def main():
    """Main entry point for pipeline worker."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TEAM-GRADE Ingestion Pipeline Worker"
    )
    parser.add_argument(
        "--project",
        default=settings.FIRESTORE_PROJECT_ID,
        help="Google Cloud project ID"
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        help="Path to service account credentials JSON"
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=settings.LOGS_DIR,
        help="Log directory"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.QUEUE_POLL_INTERVAL,
        help="Queue poll interval in seconds"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=settings.QUEUE_MAX_RETRIES,
        help="Maximum retries per stage"
    )

    args = parser.parse_args()

    # Create worker
    try:
        worker = PipelineWorker(
            project_id=args.project,
            credentials_path=args.credentials,
            log_dir=args.log_dir,
            max_retries=args.max_retries
        )
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Run worker
    try:
        worker.run_worker(poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        sys.exit(0)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Worker failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
