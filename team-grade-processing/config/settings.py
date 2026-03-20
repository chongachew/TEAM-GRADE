"""
Pipeline Configuration Settings
Centralized configuration for the TEAM-GRADE ingestion pipeline.
"""

import json
import os
from pathlib import Path

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

# ============================================================================
# FIRESTORE CONFIGURATION
# ============================================================================

# Google Cloud project (with environment variable override for multi-environment deployments)
FIRESTORE_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "the-bridge-athletics1")
FIRESTORE_DATABASE = "(default)"

# Collections (primary names used throughout pipeline)
COLLECTION_VIDEOS = "videos"
COLLECTION_INGESTION_QUEUE = "ingestion_queue"
COLLECTION_POSE = "pose"
COLLECTION_FRAMES = "frames"
COLLECTION_REPS = "reps"
COLLECTION_TORSO_CROPS = "torso_crops"

# Backward compatibility aliases
VIDEOS_COLLECTION = COLLECTION_VIDEOS
QUEUE_COLLECTION = COLLECTION_INGESTION_QUEUE

# ============================================================================
# PIPELINE CONFIGURATION
# ============================================================================

# Frame extraction settings
FRAME_EXTRACTION_FPS = 15  # Frames per second
FRAME_FORMAT = "jpg"  # "jpg" or "png"

# Pose estimation settings
POSE_MODEL = "mediapipe"  # "mediapipe", "yolo", "openpose"
POSE_DEVICE = "cpu"  # "cpu" or "cuda"
POSE_CONFIDENCE_THRESHOLD = 0.7  # Confidence threshold for high-quality frames

# Torso cropping settings
TORSO_CONFIDENCE_THRESHOLD = 0.7  # Only crop frames above this confidence

# Jersey OCR settings
OCR_ENGINE = "easyocr"  # "easyocr", "paddle", "tesseract"
OCR_LANGUAGE = "en"
OCR_STOP_ON_CONFIDENCE = 0.8  # Stop after finding jersey with this confidence

# Rep extraction settings
REP_EXTRACTOR_CONFIG = {
    "max_static_frames": 30,  # Frames without motion before rep ends
    "max_gap_frames": 10,  # Max frames to bridge trajectory gaps
    "min_rep_duration": 15,  # Minimum frames for valid rep
    "max_rep_duration": 300  # Maximum frames for single rep
}

# Biomechanics analysis settings
BIOMECHANICS_ENABLED = True  # Run biomechanics analysis
USE_TRENCH_ENGINE = True  # Use trench_engine if available

# ============================================================================
# WORKER CONFIGURATION
# ============================================================================

# Queue polling
QUEUE_POLL_INTERVAL = 5.0  # Seconds between queue polls
QUEUE_MAX_RETRIES = 3  # Max retries per stage
QUEUE_RETRY_BACKOFF = 0.1  # Initial backoff for retries (exponential)

# Batch writing (Firestore limit is 500 ops/batch; these values stay well below limit)
BATCH_SIZE = 100  # Default batch size for most operations
FRAME_EXTRACTION_BATCH_SIZE = 100      # ~50 frames × 2 ops (metadata + features) = 100 ops
POSE_BATCH_SIZE = 20                   # Pose data per frame; smaller due to doc size (~2KB each, ~40KB/batch)
TORSO_CROP_BATCH_SIZE = 50             # Crop references; moderate size (bbox + confidence)
REP_EXTRACTION_BATCH_SIZE = 10         # Rep analysis; small batch for fine-grained tracking
# Backward compatibility
FIRESTORE_BATCH_SIZE_FRAMES = FRAME_EXTRACTION_BATCH_SIZE
FIRESTORE_BATCH_SIZE_POSES = POSE_BATCH_SIZE
FIRESTORE_BATCH_SIZE_TORSO = TORSO_CROP_BATCH_SIZE
FIRESTORE_BATCH_SIZE_REPS = REP_EXTRACTION_BATCH_SIZE

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# ============================================================================
# API CONFIGURATION
# ============================================================================

API_HOST = "0.0.0.0"
API_PORT = 8000
API_WORKERS = 1

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
