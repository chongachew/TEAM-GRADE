"""
Pipeline Configuration Settings
Centralized configuration for the TEAM-GRADE ingestion pipeline.

Environment Variables:
    GCP_PROJECT_ID: Google Cloud Project ID (default: "the-bridge-athletics1")
    LOG_LEVEL: Logging level - DEBUG, INFO, WARNING, ERROR (default: "INFO")
    QUEUE_POLL_INTERVAL: Seconds between queue polls (default: 5.0)
    QUEUE_MAX_RETRIES: Max retries per stage (default: 3)
    DEBUG_MODE: Enable debug logging (default: false)
"""

import json
import os
from pathlib import Path
from typing import Optional

# ============================================================================
# PATHS
# ============================================================================

# Project root (team-grade-processing directory)
PROJECT_ROOT = Path(__file__).parent.parent

# Firestore credentials
FIRESTORE_CREDENTIALS = PROJECT_ROOT / "credentials" / "service-account.json"

# Data directories
VIDEOS_DIR = PROJECT_ROOT / "videos"
EXTRACTED_FRAMES_DIR = PROJECT_ROOT / "extracted_frames"
TORSO_CROPS_DIR = PROJECT_ROOT / "torso_crops"
LOGS_DIR = PROJECT_ROOT / "logs"

# ============================================================================
# METADATA
# ============================================================================

CONFIG_VERSION = "1.0.0"
PIPELINE_VERSION = "2.0"
PIPELINE_STAGES = [
    "metadata",
    "download",
    "frame_extraction",
    "pose",
    "torso_crop",
    "jersey_ocr",
    "rep_extraction",
    "biomechanics",
    "complete"
]

# ============================================================================
# ENVIRONMENT & DEBUG
# ============================================================================

# Environment detection
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() in ["true", "1", "yes"]
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # "development", "staging", "production"

# ============================================================================
# FIRESTORE CONFIGURATION
# ============================================================================

# Google Cloud project (with environment variable override for multi-environment deployments)
FIRESTORE_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "the-bridge-athletics1")
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE_ID", "(default)")

# Collections (primary names used throughout pipeline)
COLLECTION_VIDEOS = "videos"
COLLECTION_INGESTION_QUEUE = "ingestion_queue"
COLLECTION_POSE = "pose"
COLLECTION_FRAMES = "frames"
COLLECTION_REPS = "reps"
COLLECTION_TORSO = "torso"
COLLECTION_TORSO_CROPS = "torso_crops"

# Backward compatibility aliases
VIDEOS_COLLECTION = COLLECTION_VIDEOS
QUEUE_COLLECTION = COLLECTION_INGESTION_QUEUE

# Document field names
VIDEO_STATUS_FIELD = "status"
VIDEO_STAGES_FIELD = "stages"
VIDEO_JERSEY_FIELD = "jersey_number"
VIDEO_CREATED_AT_FIELD = "created_at"
VIDEO_COMPLETED_AT_FIELD = "completed_at"

# Status values
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in-progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# ============================================================================
# FRAME EXTRACTION SETTINGS
# ============================================================================

FRAME_EXTRACTION_FPS = int(os.getenv("FRAME_EXTRACTION_FPS", 15))  # Frames per second
FRAME_FORMAT = "jpg"  # "jpg" or "png"
FRAME_EXTRACTION_CONSECUTIVE_FAILURES = 10  # Max consecutive frame read failures
FRAME_EXTRACTION_PROGRESS_LOG_INTERVAL = 50  # Log progress every N frames
FRAME_VALIDATION_ENABLED = True  # Validate frame size and format

# ============================================================================
# POSE ESTIMATION SETTINGS
# ============================================================================

POSE_MODEL = "blazepose"  # Model to use for pose estimation
POSE_DEVICE = "cpu"  # "cpu" or "cuda"
POSE_CONFIDENCE_THRESHOLD = float(os.getenv("POSE_CONFIDENCE_THRESHOLD", 0.7))
POSE_PROGRESS_LOG_INTERVAL = 50  # Log progress every N frames
POSE_KEYPOINT_COUNT = 33  # BlazePose returns 33 keypoints
POSE_BATCH_FRAME_COUNT = 10  # Process N frames per batch

# Min/max thresholds for keypoint filtering
POSE_KEYPOINT_MIN_CONFIDENCE = 0.7
POSE_KEYPOINT_MAX_CONFIDENCE = 1.0

# High-confidence frame selection (for torso cropping)
HIGH_CONFIDENCE_THRESHOLD = 0.85  # Frames above this confidence get cropped

# ============================================================================
# TORSO CROPPING SETTINGS
# ============================================================================

TORSO_CROP_ENABLED = True
TORSO_CONFIDENCE_THRESHOLD = float(os.getenv("TORSO_CONFIDENCE_THRESHOLD", 0.7))
TORSO_CROP_PROGRESS_LOG_INTERVAL = 50
TORSO_CROPPER_CLASS_NAME = "TorsoCropper"  # Used by import system

# Crop region configuration
TORSO_CROP_EXPAND_RATIO = 1.2  # Expand bounding box by this ratio
TORSO_CROP_WIDTH = 256  # Standard crop width
TORSO_CROP_HEIGHT = 256  # Standard crop height

# ============================================================================
# JERSEY OCR SETTINGS
# ============================================================================

OCR_ENGINE = "easyocr"  # "easyocr", "paddle", "tesseract"
OCR_LANGUAGE = "en"
OCR_CONFIDENCE_THRESHOLD = float(os.getenv("OCR_CONFIDENCE_THRESHOLD", 0.8))
OCR_STOP_ON_CONFIDENCE = float(os.getenv("OCR_STOP_ON_CONFIDENCE", 0.8))
OCR_USE_GPU = os.getenv("JERSEY_OCR_USE_GPU", "false").lower() in ["true", "1", "yes"]
OCR_MODEL_SELECTION = "standard"  # "standard" or "custom"
OCR_BATCH_SIZE = 10  # Process N crops per batch

# Jersey number validation
JERSEY_NUMBER_MIN = 0
JERSEY_NUMBER_MAX = 99
JERSEY_VALID_REGEX = r"^\d{1,2}$"  # 0-99

# ============================================================================
# REP EXTRACTION SETTINGS
# ============================================================================

REP_EXTRACTOR_CONFIG = {
    "max_static_frames": int(os.getenv("REP_MAX_STATIC_FRAMES", 30)),
    "max_gap_frames": int(os.getenv("REP_MAX_GAP_FRAMES", 10)),
    "min_rep_duration": int(os.getenv("REP_MIN_DURATION", 15)),
    "max_rep_duration": int(os.getenv("REP_MAX_DURATION", 300))
}

REP_EXTRACTION_BATCH_SIZE = 10
REP_PROGRESS_LOG_INTERVAL = 10  # Log progress every N reps

# ============================================================================
# BIOMECHANICS ANALYSIS SETTINGS
# ============================================================================

BIOMECHANICS_ENABLED = True  # Run biomechanics analysis
USE_TRENCH_ENGINE = True  # Use trench_engine if available
TRENCH_ENGINE_MODULE = "biomechanics_engine.trench_engine"

# Trait scoring configuration
TRAIT_SCORE_MIN = 0.0
TRAIT_SCORE_MAX = 100.0
TRAIT_SCORE_DECIMAL_PLACES = 2

# ============================================================================
# WORKER CONFIGURATION
# ============================================================================

# Queue polling
QUEUE_POLL_INTERVAL = float(os.getenv("QUEUE_POLL_INTERVAL", 5.0))
QUEUE_MAX_RETRIES = int(os.getenv("QUEUE_MAX_RETRIES", 3))
QUEUE_RETRY_BACKOFF = float(os.getenv("QUEUE_RETRY_BACKOFF", 0.1))  # Initial backoff (exponential)
QUEUE_RETRY_BACKOFF_MAX = 10.0  # Maximum backoff time (seconds)
QUEUE_DEFAULT_PRIORITY = 5  # Default priority (1=highest, 10=lowest)
QUEUE_MAX_WAIT_TIME = 300  # Max seconds to wait for next stage

# Download configuration
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_TIMEOUT_SECONDS = 600
DOWNLOAD_FALLBACK_TO_CACHE = True  # Use cached video if download fails

# ============================================================================
# BATCH WRITING CONFIGURATION
# ============================================================================

# Firestore batch limits (max 500 operations per batch)
BATCH_SIZE = 100  # Default batch size
FRAME_EXTRACTION_BATCH_SIZE = 100
POSE_BATCH_SIZE = 20
TORSO_CROP_BATCH_SIZE = 50
REP_EXTRACTION_BATCH_SIZE = 10

# Backward compatibility
FIRESTORE_BATCH_SIZE_FRAMES = FRAME_EXTRACTION_BATCH_SIZE
FIRESTORE_BATCH_SIZE_POSES = POSE_BATCH_SIZE
FIRESTORE_BATCH_SIZE_TORSO = TORSO_CROP_BATCH_SIZE
FIRESTORE_BATCH_SIZE_REPS = REP_EXTRACTION_BATCH_SIZE

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_JSON_FORMAT = os.getenv("LOG_FORMAT", "text") == "json"  # Use JSON logging if set

# Structured logging
STRUCTURED_LOG_ENABLED = True  # Log with extra={video_id, stage, error_code, ...}
LOG_EXCLUDE_FIELDS = ["password", "token", "secret", "api_key"]  # Fields to mask in logs

# ============================================================================
# API CONFIGURATION
# ============================================================================

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
API_WORKERS = int(os.getenv("API_WORKERS", 1))
API_RELOAD = DEBUG_MODE  # Auto-reload on code changes in debug mode
API_LOG_LEVEL = LOG_LEVEL
API_CORS_ORIGINS = ["http://localhost:8080", "http://127.0.0.1:8080"]  # CORS origins

# ============================================================================
# ERROR CODE CONFIGURATION
# ============================================================================

# Machine-readable error codes for logging and monitoring
ERROR_CODES = {
    # Validation errors
    "VIDEO_ID_INVALID": "Invalid video ID format",
    "INVALID_URL": "Invalid YouTube URL",
    "INVALID_CREDENTIALS": "Invalid or missing credentials",
    
    # File errors
    "VIDEO_FILE_NOT_FOUND": "Video file not found",
    "FRAMES_NOT_FOUND": "Frame extraction directory not found",
    "POSE_DATA_NOT_FOUND": "Pose data not found",
    
    # Processing errors
    "DOWNLOAD_FAILED": "Video download failed",
    "NO_VIDEO_FRAMES": "No frames extracted from video",
    "MODEL_LOAD_FAILED": "Failed to load ML model",
    "FRAME_PROCESSING_ERROR": "Error processing frame batch",
    "REP_FORMATTING_ERROR": "Error formatting rep data",
    "CROPPER_INIT_FAILED": "Failed to initialize cropper",
    
    # Firestore errors
    "FIRESTORE_ERROR": "Firestore operation failed",
    "VIDEO_DOC_NOT_FOUND": "Video document not found in Firestore",
    
    # Queue errors
    "QUEUE_ERROR": "Queue operation failed",
    
    # Generic
    "UNEXPECTED_ERROR": "Unexpected error"
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def sanitize_id(video_id: str) -> str:
    """Sanitize video ID to prevent directory traversal attacks.

    Args:
        video_id: YouTube video ID or identifier

    Returns:
        Sanitized video ID with special characters removed

    Raises:
        ValueError: If video_id is empty after sanitization
    """
    # Remove directory traversal patterns and invalid filesystem characters
    sanitized = video_id.replace("..", "").replace("/", "").replace("\\", "")
    sanitized = sanitized.replace(":", "").replace("*", "").replace("?", "")
    sanitized = sanitized.replace('"', "").replace("<", "").replace(">", "")
    
    if not sanitized:
        raise ValueError(f"Video ID '{video_id}' is invalid after sanitization")
    
    return sanitized


def get_videos_dir() -> Path:
    """Get videos directory path.

    Returns:
        Path to videos directory

    Raises:
        OSError: If directory cannot be created
    """
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEOS_DIR


def get_video_path(video_id: str) -> Path:
    """Get path for a downloaded video.

    Args:
        video_id: YouTube video ID

    Returns:
        Path to video file

    Raises:
        ValueError: If video_id is invalid
    """
    safe_id = sanitize_id(video_id)
    return VIDEOS_DIR / f"{safe_id}.mp4"


def get_frames_dir(video_id: str) -> Path:
    """Get directory for extracted frames.

    Args:
        video_id: YouTube video ID

    Returns:
        Path to frames directory

    Raises:
        ValueError: If video_id is invalid
    """
    safe_id = sanitize_id(video_id)
    return EXTRACTED_FRAMES_DIR / safe_id


def get_torso_crops_dir(video_id: str) -> Path:
    """Get directory for torso crops.

    Args:
        video_id: YouTube video ID

    Returns:
        Path to torso crops directory

    Raises:
        ValueError: If video_id is invalid
    """
    safe_id = sanitize_id(video_id)
    return TORSO_CROPS_DIR / safe_id


def get_error_message(error_code: str, context: Optional[str] = None) -> str:
    """Get human-readable error message for error code.
    
    Args:
        error_code: Machine-readable error code
        context: Optional additional context
    
    Returns:
        Human-readable error message
    """
    message = ERROR_CODES.get(error_code, "Unknown error")
    if context:
        message = f"{message} ({context})"
    return message


def ensure_directories() -> None:
    """Create all required directories if they don't exist.

    Raises:
        OSError: If directories cannot be created
    """
    for dir_path in [VIDEOS_DIR, EXTRACTED_FRAMES_DIR, TORSO_CROPS_DIR, LOGS_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OSError(f"Cannot create directory {dir_path}: {e}") from e


def validate_environment() -> tuple[bool, str]:
    """Validate that all required directories and credentials exist.
    
    Raises exceptions for critical failures (invalid credentials, unwritable directories).

    Returns:
        (is_valid: bool, error_message: str) - Always (True, "Environment valid") on success
    
    Raises:
        ValueError: If credentials missing or invalid JSON
        OSError: If directories cannot be created or are not writable
    """
    # CRITICAL: Check credentials exist (fail fast)
    if not FIRESTORE_CREDENTIALS.exists():
        raise ValueError(
            f"CRITICAL: Firestore credentials not found at {FIRESTORE_CREDENTIALS}\n"
            f"Place your service account JSON here or set GOOGLE_APPLICATION_CREDENTIALS env var."
        )

    # CRITICAL: Validate credentials JSON format and required fields
    try:
        with open(FIRESTORE_CREDENTIALS, "r") as f:
            creds = json.load(f)
            # Validate required fields for Firestore service account
            required_fields = ["type", "project_id", "private_key_id", "private_key"]
            missing = [f for f in required_fields if f not in creds]
            if missing:
                raise ValueError(f"Credentials missing required fields: {missing}")
    except json.JSONDecodeError as e:
        raise ValueError(f"CRITICAL: Credentials file is not valid JSON: {e}")
    except IOError as e:
        raise ValueError(f"CRITICAL: Cannot read credentials file: {e}")

    # CRITICAL: Ensure directories can be created
    try:
        ensure_directories()
    except OSError as e:
        raise OSError(f"CRITICAL: Cannot create required directories: {e}")

    # CRITICAL: Check data directories are writable
    for dir_path in [VIDEOS_DIR, EXTRACTED_FRAMES_DIR, TORSO_CROPS_DIR, LOGS_DIR]:
        try:
            test_file = dir_path / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
        except Exception as e:
            raise OSError(f"CRITICAL: Cannot write to {dir_path}: {e}")

    return True, "Environment valid"


def validate_configuration() -> tuple[bool, list[str]]:
    """Validate that configuration parameters are valid.
    
    Performs semantic checks on all configuration values. Warnings are collected
    but do not prevent operation.
    
    Returns:
        (is_valid: bool, warnings: list[str]) - Always True unless critical issues found
    """
    warnings = []
    
    # Check FPS
    if FRAME_EXTRACTION_FPS < 1:
        warnings.append(f"FRAME_EXTRACTION_FPS={FRAME_EXTRACTION_FPS} is too low (minimum 1)")
    if FRAME_EXTRACTION_FPS > 60:
        warnings.append(f"FRAME_EXTRACTION_FPS={FRAME_EXTRACTION_FPS} is very high (typical max 30)")
    
    # Check confidence thresholds
    if not (0.0 <= POSE_CONFIDENCE_THRESHOLD <= 1.0):
        warnings.append(f"POSE_CONFIDENCE_THRESHOLD not in range [0, 1]")
    if not (0.0 <= OCR_CONFIDENCE_THRESHOLD <= 1.0):
        warnings.append(f"OCR_CONFIDENCE_THRESHOLD not in range [0, 1]")
    
    # Check timeouts
    if QUEUE_POLL_INTERVAL < 0.5:
        warnings.append(f"QUEUE_POLL_INTERVAL={QUEUE_POLL_INTERVAL} is very fast (minimum 0.5 recommended)")
    if DOWNLOAD_TIMEOUT_SECONDS < 60:
        warnings.append(f"DOWNLOAD_TIMEOUT_SECONDS={DOWNLOAD_TIMEOUT_SECONDS} may be too short")
    
    # Check batch sizes
    if BATCH_SIZE > 500:
        warnings.append(f"BATCH_SIZE={BATCH_SIZE} exceeds Firestore limit of 500 (will fail)")
    
    return True, warnings

