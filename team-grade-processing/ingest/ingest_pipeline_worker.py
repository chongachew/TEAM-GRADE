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
from .firestore_client import FirestoreClient
from .queue_manager import QueueManager
from .stages import STAGE_HANDLERS
from .utils import setup_logging, mark_stage_attempt

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

        log_file = self.log_dir / "pipeline_worker.log"
        setup_logging(__name__, str(log_file))

        # Initialize Firestore
        try:
            self.firestore = FirestoreClient(
                project_id=self.project_id,
                credentials_path=self.credentials_path
            )
            self.queue_manager = QueueManager(self.firestore)
            logger.info("[OK] Firestore and queue initialized")
        except Exception as e:
            logger.error(f"[FAIL] Failed to initialize Firestore: {e}")
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
                    # Get next job from queue
                    queue_item = self.queue_manager.dequeue_next_video()

                    if not queue_item:
                        logger.debug(f"[Iteration {iteration}] Queue empty, waiting...")
                        time.sleep(poll_interval)
                        continue

                    # Validate queue_item structure
                    if not all(k in queue_item for k in ["video_id", "stage", "_doc_id"]):
                        logger.error(f"[Iteration {iteration}] Malformed queue item: missing required fields")
                        time.sleep(poll_interval)
                        continue

                    video_id = queue_item.get("video_id")
                    stage = queue_item.get("stage")
                    queue_doc_id = queue_item.get("_doc_id")

                    # Validate extracted fields are non-empty
                    if not video_id or not isinstance(video_id, str):
                        logger.error(f"[Iteration {iteration}] Invalid video_id from queue: {video_id}")
                        time.sleep(poll_interval)
                        continue
                    if not stage or not isinstance(stage, str):
                        logger.error(f"[Iteration {iteration}] Invalid stage from queue: {stage}")
                        time.sleep(poll_interval)
                        continue

                    logger.info(f"[Iteration {iteration}] Processing: {video_id}/{stage}")

                    # Process stage
                    success, error_msg = self._process_stage(
                        video_id,
                        stage,
                        queue_item
                    )

                    # Handle result
                    if success:
                        logger.info(
                            f"[{video_id}] Stage {stage} completed successfully"
                        )
                        self.queue_manager.mark_completed(queue_doc_id)

                    else:
                        logger.error(
                            f"[{video_id}] Stage {stage} failed: {error_msg}"
                        )
                        self.queue_manager.mark_failed(
                            queue_doc_id,
                            error_msg or "Unknown error",
                            retry=True
                        )

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

            # Extract payload
            payload = queue_item.get("payload", {})

            logger.info(
                f"[{safe_video_id}] Executing stage handler: {stage}"
            )

            # Run stage handler with retry logic
            for attempt in range(1, self.max_retries + 1):
                try:
                    # Mark attempt (non-critical, log warning if fails)
                    try:
                        mark_stage_attempt(self.firestore, safe_video_id, stage)
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
