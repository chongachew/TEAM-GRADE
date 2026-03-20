"""
TEAM-GRADE YouTube Ingestion Worker Package
YouTube → Metadata → Download → Firestore → Processing Queue
"""

from .ingest_worker import IngestWorker
from .youtube_metadata import (
    YouTubeMetadataExtractor,
    get_video_id,
    fetch_youtube_metadata,
    parse_football_metadata,
    validate_youtube_url,
)
from .youtube_downloader import (
    YouTubeDownloader,
    download_video,
    get_video_info,
)
from .firestore_client import (
    FirestoreClient,
    VideoStatus,
    IngestStage,
    save_video_metadata,
    update_video_status,
    add_to_queue,
)
from .queue_manager import (
    QueueManager,
    enqueue_video,
    dequeue_next_video,
    get_queue_stats,
)

__all__ = [
    # Main orchestrator
    "IngestWorker",
    # Metadata
    "YouTubeMetadataExtractor",
    "get_video_id",
    "fetch_youtube_metadata",
    "parse_football_metadata",
    "validate_youtube_url",
    # Downloader
    "YouTubeDownloader",
    "download_video",
    "get_video_info",
    # Firestore
    "FirestoreClient",
    "VideoStatus",
    "IngestStage",
    "save_video_metadata",
    "update_video_status",
    "add_to_queue",
    # Queue
    "QueueManager",
    "enqueue_video",
    "dequeue_next_video",
    "get_queue_stats",
]

__version__ = "1.0.0"
__author__ = "TEAM-GRADE"
