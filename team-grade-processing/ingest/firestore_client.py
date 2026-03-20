"""
Firestore Client Module
Manages video metadata and ingestion queue in Firestore.
Uses explicit service account credentials for authentication on Windows.
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)

# Configuration: Path to Google Cloud service account JSON key
# Place your downloaded service account JSON file at this location
FIRESTORE_CREDENTIALS_PATH = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"


class VideoStatus(str, Enum):
    """Video processing status."""
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    INGESTED = "ingested"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestStage(str, Enum):
    """Ingestion pipeline stages."""
    METADATA = "metadata"
    DOWNLOAD = "download"
    FRAME_EXTRACTION = "frame_extraction"
    POSE_ESTIMATION = "pose_estimation"
    TORSO_CROPPING = "torso_cropping"
    JERSEY_OCR = "jersey_ocr"
    REP_EXTRACTION = "rep_extraction"
    COMPLETED = "completed"


class FirestoreClient:
    """Manage video metadata and ingestion queue in Firestore."""

    VIDEOS_COLLECTION = "videos"
    QUEUE_COLLECTION = "ingestion_queue"
    BATCH_SIZE = 100

    def __init__(
        self,
        project_id: str = "the-bridge-athletics1",
        database: str = "(default)",
        credentials_path: Optional[Path] = None
    ):
        """
        Initialize Firestore client with explicit service account credentials.

        Args:
            project_id: Google Cloud project ID
            database: Firestore database ID
            credentials_path: Path to service account JSON key file
                             Defaults to FIRESTORE_CREDENTIALS_PATH if not provided

        Raises:
            RuntimeError: If Firestore initialization fails
            FileNotFoundError: If credentials file not found
        """
        try:
            from google.cloud import firestore
            from google.oauth2 import service_account

            # Determine credentials file path
            creds_path = credentials_path or FIRESTORE_CREDENTIALS_PATH

            logger.info(f"Initializing Firestore client for project: {project_id}")
            logger.info(f"Credentials path: {creds_path}")

            # Verify credentials file exists
            if not creds_path.exists():
                error_msg = (
                    f"Firestore credentials file not found: {creds_path}\n"
                    f"Please place your Google Cloud service account JSON key at: {creds_path}"
                )
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            logger.info(f"[OK] Found credentials file: {creds_path}")

            # Load credentials from service account JSON
            try:
                with open(creds_path, 'r') as f:
                    creds_dict = json.load(f)
                logger.info(f"[OK] Loaded service account from JSON")
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in credentials file: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            except Exception as e:
                error_msg = f"Failed to read credentials file: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Create credentials object
            try:
                credentials = service_account.Credentials.from_service_account_info(creds_dict)
                logger.info(f"[OK] Created service account credentials")
            except Exception as e:
                error_msg = f"Failed to create credentials from service account: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Initialize Firestore client with explicit credentials
            try:
                self.db = firestore.Client(
                    project=project_id,
                    credentials=credentials
                )
                self.project_id = project_id
                self.database = database
                logger.info(f"[OK] Firestore client initialized for project: {project_id}")
            except Exception as e:
                error_msg = f"Failed to initialize Firestore client: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Test connection
            self._test_connection()
            logger.info("[OK] Firestore client initialized successfully")

        except ImportError as e:
            error_msg = "google-cloud-firestore or google-auth not installed.\nInstall with: pip install google-cloud-firestore google-auth"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Firestore: {e}")
            raise RuntimeError(f"Firestore initialization failed: {e}")

    def _test_connection(self) -> bool:
        """
        Test Firestore connection.

        Returns:
            True if connection successful

        Raises:
            RuntimeError: If connection test fails
        """
        try:
            # Try to read a simple document
            test_doc = self.db.collection("_system").document("test").get()
            logger.debug("[OK] Firestore connection test successful")
            return True
        except Exception as e:
            logger.error(f"Firestore connection test failed: {e}")
            raise RuntimeError(f"Cannot connect to Firestore: {e}")

    def save_video_metadata(
        self,
        video_id: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Save or update video metadata in Firestore.

        Args:
            video_id: YouTube video ID
            metadata: Video metadata dictionary

        Returns:
            True if successful
        """
        try:
            logger.info(f"Saving metadata for video: {video_id}")

            doc_data = {
                "video_id": video_id,
                "status": VideoStatus.PENDING.value,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                **metadata
            }

            self.db.collection(self.VIDEOS_COLLECTION).document(video_id).set(
                doc_data,
                merge=True
            )

            logger.info(f"[OK] Saved metadata for {video_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save metadata for {video_id}: {e}")
            return False

    def get_video_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve video metadata from Firestore.

        Args:
            video_id: YouTube video ID

        Returns:
            Metadata dictionary or None if not found
        """
        try:
            doc = self.db.collection(self.VIDEOS_COLLECTION).document(video_id).get()

            if doc.exists:
                logger.debug(f"[OK] Retrieved metadata for {video_id}")
                return doc.to_dict()
            else:
                logger.warning(f"Video metadata not found: {video_id}")
                return None

        except Exception as e:
            logger.error(f"Failed to retrieve metadata for {video_id}: {e}")
            return None

    def get_video_status(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve video status and metadata from Firestore.

        Args:
            video_id: YouTube video ID

        Returns:
            Video document as dictionary or None if not found
        """
        try:
            doc = self.db.collection(self.VIDEOS_COLLECTION).document(video_id).get()

            if doc.exists:
                logger.debug(f"[OK] Retrieved status for {video_id}")
                return doc.to_dict()
            else:
                logger.warning(f"Video not found: {video_id}")
                return None

        except Exception as e:
            logger.error(f"Failed to retrieve status for {video_id}: {e}")
            return None

    def update_video_status(
        self,
        video_id: str,
        status: VideoStatus,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update video processing status.

        Args:
            video_id: YouTube video ID
            status: New status
            error_message: Optional error message if status is FAILED

        Returns:
            True if successful
        """
        try:
            update_data = {
                "status": status.value,
                "updated_at": datetime.utcnow().isoformat(),
            }

            if error_message:
                update_data["error"] = error_message

            self.db.collection(self.VIDEOS_COLLECTION).document(video_id).update(update_data)

            logger.info(f"[OK] Updated {video_id} status to {status.value}")
            return True

        except Exception as e:
            logger.error(f"Failed to update status for {video_id}: {e}")
            return False

    def update_video_field(
        self,
        video_id: str,
        field: str,
        value: Any
    ) -> bool:
        """
        Update a specific field in video metadata.

        Args:
            video_id: YouTube video ID
            field: Field name
            value: New value

        Returns:
            True if successful
        """
        try:
            update_data = {
                field: value,
                "updated_at": datetime.utcnow().isoformat(),
            }

            self.db.collection(self.VIDEOS_COLLECTION).document(video_id).update(update_data)

            logger.debug(f"[OK] Updated {video_id}.{field}")
            return True

        except Exception as e:
            logger.error(f"Failed to update {video_id}.{field}: {e}")
            return False

    def add_to_queue(
        self,
        video_id: str,
        stage: IngestStage,
        priority: int = 5,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add video to ingestion queue.

        Args:
            video_id: YouTube video ID
            stage: Current processing stage
            priority: Priority (1-10, lower=higher priority)
            metadata: Optional metadata for queue item

        Returns:
            True if successful
        """
        try:
            logger.info(f"Adding {video_id} to queue at stage: {stage.value}")

            queue_item = {
                "video_id": video_id,
                "stage": stage.value,
                "priority": priority,
                "status": "queued",
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "retry_count": 0,
                "max_retries": 3,
            }

            if metadata:
                queue_item.update(metadata)

            # Generate document ID based on priority and timestamp
            queue_doc_id = f"{priority:02d}_{datetime.utcnow().timestamp()}_{video_id}"

            self.db.collection(self.QUEUE_COLLECTION).document(queue_doc_id).set(queue_item)

            logger.info(f"[OK] Added {video_id} to queue")
            return True

        except Exception as e:
            logger.error(f"Failed to add {video_id} to queue: {e}")
            return False

    def dequeue_next_video(self) -> Optional[Dict[str, Any]]:
        """
        Get next video from queue (FIFO by priority, then creation time).

        Returns:
            Queue item dictionary or None if queue empty
        """
        try:
            # Query for oldest queued item with highest priority
            query = (
                self.db.collection(self.QUEUE_COLLECTION)
                .where("status", "==", "queued")
                .order_by("priority")
                .order_by("created_at")
                .limit(1)
            )

            docs = query.stream()
            doc = next(docs, None)

            if doc:
                queue_item = doc.to_dict()
                queue_item["_doc_id"] = doc.id

                logger.info(f"[OK] Dequeued video: {queue_item['video_id']} (stage: {queue_item['stage']})")
                return queue_item
            else:
                logger.debug("Queue is empty")
                return None

        except Exception as e:
            logger.error(f"Failed to dequeue video: {e}")
            return None

    def update_queue_item(
        self,
        queue_doc_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update queue item status/metadata.

        Args:
            queue_doc_id: Queue document ID
            updates: Dictionary of fields to update

        Returns:
            True if successful
        """
        try:
            updates["updated_at"] = datetime.utcnow().isoformat()

            self.db.collection(self.QUEUE_COLLECTION).document(queue_doc_id).update(updates)

            logger.debug(f"[OK] Updated queue item: {queue_doc_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update queue item {queue_doc_id}: {e}")
            return False

    def remove_from_queue(self, queue_doc_id: str) -> bool:
        """
        Remove completed item from queue.

        Args:
            queue_doc_id: Queue document ID

        Returns:
            True if successful
        """
        try:
            self.db.collection(self.QUEUE_COLLECTION).document(queue_doc_id).delete()

            logger.info(f"[OK] Removed {queue_doc_id} from queue")
            return True

        except Exception as e:
            logger.error(f"Failed to remove {queue_doc_id} from queue: {e}")
            return False

    def get_queue_status(self) -> Dict[str, int]:
        """
        Get current queue statistics.

        Returns:
            Dictionary with queue status
        """
        try:
            queued = len(list(
                self.db.collection(self.QUEUE_COLLECTION)
                .where("status", "==", "queued")
                .stream()
            ))

            processing = len(list(
                self.db.collection(self.QUEUE_COLLECTION)
                .where("status", "==", "processing")
                .stream()
            ))

            logger.info(f"Queue status: {queued} queued, {processing} processing")

            return {
                "queued": queued,
                "processing": processing,
                "total": queued + processing,
            }

        except Exception as e:
            logger.error(f"Failed to get queue status: {e}")
            return {"queued": 0, "processing": 0, "total": 0}

    def list_videos_by_status(self, status: VideoStatus, limit: int = 50) -> List[Dict[str, Any]]:
        """
        List videos by status.

        Args:
            status: Video status to filter by
            limit: Maximum number of results

        Returns:
            List of video documents
        """
        try:
            docs = (
                self.db.collection(self.VIDEOS_COLLECTION)
                .where("status", "==", status.value)
                .limit(limit)
                .stream()
            )

            videos = [doc.to_dict() for doc in docs]
            logger.debug(f"Found {len(videos)} videos with status {status.value}")
            return videos

        except Exception as e:
            logger.error(f"Failed to list videos: {e}")
            return []

    def archive_old_videos(self, days: int = 30) -> int:
        """
        Archive completed videos older than specified days.

        Args:
            days: Archive videos older than this many days

        Returns:
            Number of videos archived
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            cutoff_iso = cutoff_date.isoformat()

            docs = (
                self.db.collection(self.VIDEOS_COLLECTION)
                .where("status", "==", VideoStatus.COMPLETED.value)
                .where("updated_at", "<", cutoff_iso)
                .stream()
            )

            count = 0
            for doc in docs:
                self.db.collection(self.VIDEOS_COLLECTION).document(doc.id).update({
                    "archived": True,
                    "archived_at": datetime.utcnow().isoformat(),
                })
                count += 1

            logger.info(f"Archived {count} videos")
            return count

        except Exception as e:
            logger.error(f"Failed to archive videos: {e}")
            return 0

    def queue_entry_exists(self, video_id: str) -> bool:
        """
        Check if a queue entry exists for the given video_id.

        Args:
            video_id: YouTube video ID

        Returns:
            True if queue entry exists, False otherwise
        """
        try:
            # Query for any queue entry matching this video_id
            docs = list(
                self.db.collection(self.QUEUE_COLLECTION)
                .where("video_id", "==", video_id)
                .limit(1)
                .stream()
            )
            return len(docs) > 0
        except Exception as e:
            logger.error(f"Failed to check queue entry for {video_id}: {e}")
            return False

    def check_queue_integrity(self, video_id: str) -> Dict[str, Any]:
        """
        Check integrity of video and queue state.

        Args:
            video_id: YouTube video ID

        Returns:
            Dictionary with video_exists, queue_exists, status, stage
        """
        try:
            # Check video document
            video_doc = self.db.collection(self.VIDEOS_COLLECTION).document(video_id).get()
            video_exists = video_doc.exists
            video_data = video_doc.to_dict() if video_exists else {}

            # Check queue entry
            queue_exists = self.queue_entry_exists(video_id)

            return {
                "video_exists": video_exists,
                "queue_exists": queue_exists,
                "status": video_data.get("status", "unknown") if video_exists else None,
                "stage": video_data.get("stage", "metadata") if video_exists else None,
                "error": video_data.get("error", None) if video_exists else None,
            }
        except Exception as e:
            logger.error(f"Failed to check queue integrity for {video_id}: {e}")
            return {
                "video_exists": False,
                "queue_exists": False,
                "status": None,
                "stage": None,
                "error": str(e),
            }

    def get_pending_videos(self, max_age_minutes: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all videos stuck in pending status.

        Args:
            max_age_minutes: Only return videos older than this many minutes (optional)

        Returns:
            List of video documents with status=pending
        """
        try:
            query = (
                self.db.collection(self.VIDEOS_COLLECTION)
                .where("status", "==", VideoStatus.PENDING.value)
            )

            if max_age_minutes:
                cutoff_time = datetime.utcnow() - timedelta(minutes=max_age_minutes)
                cutoff_iso = cutoff_time.isoformat()
                query = query.where("created_at", "<", cutoff_iso)

            docs = query.stream()
            videos = []
            for doc in docs:
                video_data = doc.to_dict()
                video_data["_doc_id"] = doc.id
                videos.append(video_data)

            logger.debug(f"Found {len(videos)} pending videos")
            return videos

        except Exception as e:
            logger.error(f"Failed to get pending videos: {e}")
            return []

    def get_stale_queue_entries(self, max_age_minutes: int = 60) -> List[Dict[str, Any]]:
        """
        Get queue entries older than specified minutes.

        Args:
            max_age_minutes: Queue entries older than this are considered stale

        Returns:
            List of stale queue entries
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=max_age_minutes)
            cutoff_iso = cutoff_time.isoformat()

            query = (
                self.db.collection(self.QUEUE_COLLECTION)
                .where("created_at", "<", cutoff_iso)
                .where("status", "==", "queued")
            )

            docs = query.stream()
            entries = []
            for doc in docs:
                entry_data = doc.to_dict()
                entry_data["_doc_id"] = doc.id
                entries.append(entry_data)

            logger.debug(f"Found {len(entries)} stale queue entries")
            return entries

        except Exception as e:
            logger.error(f"Failed to get stale queue entries: {e}")
            return []

    def delete_queue_entry_by_video_id(self, video_id: str) -> bool:
        """
        Delete queue entry for a given video_id.

        Args:
            video_id: YouTube video ID

        Returns:
            True if entry deleted, False otherwise
        """
        try:
            docs = list(
                self.db.collection(self.QUEUE_COLLECTION)
                .where("video_id", "==", video_id)
                .stream()
            )

            if not docs:
                logger.debug(f"No queue entry found for {video_id}")
                return False

            for doc in docs:
                self.db.collection(self.QUEUE_COLLECTION).document(doc.id).delete()

            logger.info(f"[OK] Deleted queue entry for {video_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete queue entry for {video_id}: {e}")
            return False


# Standalone functions
def save_video_metadata(video_id: str, metadata: Dict[str, Any]) -> bool:
    """Standalone function to save video metadata."""
    try:
        client = FirestoreClient()
        return client.save_video_metadata(video_id, metadata)
    except Exception as e:
        logger.error(f"Failed in standalone save: {e}")
        return False


def update_video_status(video_id: str, status: str, error: Optional[str] = None) -> bool:
    """Standalone function to update video status."""
    try:
        client = FirestoreClient()
        return client.update_video_status(video_id, VideoStatus(status), error)
    except Exception as e:
        logger.error(f"Failed in standalone update: {e}")
        return False


def add_to_queue(video_id: str, stage: str, priority: int = 5) -> bool:
    """Standalone function to add video to queue."""
    try:
        client = FirestoreClient()
        return client.add_to_queue(video_id, IngestStage(stage), priority)
    except Exception as e:
        logger.error(f"Failed in standalone add_to_queue: {e}")
        return False
