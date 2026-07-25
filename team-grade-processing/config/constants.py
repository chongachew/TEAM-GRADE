"""
Centralized Constants for TEAM-GRADE Pipeline

This file contains all shared constants to prevent drift between modules.
All modules should import from here instead of hardcoding values.
"""

# ===========================================================================
# FIRESTORE CONFIGURATION
# ===========================================================================

FIRESTORE_PROJECT_ID = "the-bridge-athletics1"
FIRESTORE_DATABASE = "(default)"

# Firestore Collections
FIRESTORE_COLLECTION_VIDEOS = "videos"
FIRESTORE_COLLECTION_QUEUE = "ingestion_queue"

# Firestore Field Names - Videos Collection
VIDEO_FIELD_ID = "video_id"
VIDEO_FIELD_STATUS = "status"
VIDEO_FIELD_STAGES = "stages"
VIDEO_FIELD_CREATED_AT = "created_at"
VIDEO_FIELD_UPDATED_AT = "updated_at"
VIDEO_FIELD_DOWNLOAD_PATH = "download_path"
VIDEO_FIELD_ERROR = "error"

# Firestore Field Names - Queue Collection
QUEUE_FIELD_VIDEO_ID = "video_id"
QUEUE_FIELD_STAGE = "stage"
QUEUE_FIELD_STATUS = "status"
QUEUE_FIELD_PRIORITY = "priority"
QUEUE_FIELD_CREATED_AT = "created_at"
QUEUE_FIELD_UPDATED_AT = "updated_at"
QUEUE_FIELD_RETRY_COUNT = "retry_count"
QUEUE_FIELD_MAX_RETRIES = "max_retries"

# Status Values
VIDEO_STATUS_PENDING = "pending"
VIDEO_STATUS_PROCESSING = "processing"
VIDEO_STATUS_DOWNLOADED = "downloaded"
VIDEO_STATUS_INGESTED = "ingested"
VIDEO_STATUS_COMPLETED = "completed"
VIDEO_STATUS_FAILED = "failed"

QUEUE_STATUS_QUEUED = "queued"
QUEUE_STATUS_PROCESSING = "processing"
QUEUE_STATUS_COMPLETED = "completed"
QUEUE_STATUS_FAILED = "failed"

# ===========================================================================
# PIPELINE STAGES
# ===========================================================================

STAGE_METADATA = "metadata"
STAGE_DOWNLOAD = "download"
STAGE_AUTHENTICITY_CHECK = "authenticity_check"
STAGE_FRAME_EXTRACTION = "frame_extraction"
STAGE_WHISTLE_DETECTION = "whistle_detection"
STAGE_MOTION_COMPENSATION = "motion_compensation"
STAGE_PLAY_DETECTION = "play_detection"
STAGE_DETECTION = "detection"
STAGE_TRACKING = "tracking"
STAGE_POSE = "pose"
STAGE_TORSO_CROP = "torso_crop"
STAGE_JERSEY_OCR = "jersey_ocr"
STAGE_REP_EXTRACTION = "rep_extraction"
STAGE_BIOMECHANICS = "biomechanics"
STAGE_COMPLETE = "complete"

# All stages in order
ALL_STAGES = [
    STAGE_METADATA,
    STAGE_DOWNLOAD,
    STAGE_AUTHENTICITY_CHECK,
    STAGE_WHISTLE_DETECTION,
    STAGE_FRAME_EXTRACTION,
    STAGE_MOTION_COMPENSATION,
    STAGE_PLAY_DETECTION,
    STAGE_DETECTION,
    STAGE_TRACKING,
    STAGE_POSE,
    STAGE_TORSO_CROP,
    STAGE_JERSEY_OCR,
    STAGE_REP_EXTRACTION,
    STAGE_BIOMECHANICS,
    STAGE_COMPLETE,
]

TOTAL_STAGES = len(ALL_STAGES)

# Stage status values
STAGE_STATUS_PENDING = "pending"
STAGE_STATUS_IN_PROGRESS = "in-progress"
STAGE_STATUS_COMPLETED = "completed"
STAGE_STATUS_FAILED = "failed"
STAGE_STATUS_SKIPPED = "skipped"

# ===========================================================================
# API CONFIGURATION
# ===========================================================================

API_BASE_URL = "http://localhost:8000"
API_ENDPOINT_INGEST = "/api/ingest"
API_ENDPOINT_STATUS = "/api/ingest/{video_id}/status"
API_ENDPOINT_UPLOAD = "/api/upload"
API_ENDPOINT_HEALTH = "/api/health"

# ===========================================================================
# WORKER CONFIGURATION
# ===========================================================================

WORKER_POLL_INTERVAL_SECONDS = 5.0
WORKER_MAX_RETRIES = 3
WORKER_RETRY_BACKOFF_SECONDS = 1.0

# ===========================================================================
# QUEUE CONFIGURATION
# ===========================================================================

QUEUE_PRIORITY_HIGHEST = 1
QUEUE_PRIORITY_NORMAL = 5
QUEUE_PRIORITY_LOWEST = 10

QUEUE_BATCH_SIZE = 100
QUEUE_DEFAULT_PRIORITY = QUEUE_PRIORITY_NORMAL

# ===========================================================================
# FILE PATHS
# ===========================================================================

VIDEOS_DIRECTORY = "videos"
LOGS_DIRECTORY = "logs"
