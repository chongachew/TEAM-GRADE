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

# Config directory (used by biomechanics trait-config loading)
CONFIG_ROOT = PROJECT_ROOT / "config"

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
    "authenticity_check",
    "whistle_detection",
    "frame_extraction",
    "motion_compensation",
    "detection",
    "tracking",
    "pose",
    "torso_crop",
    "jersey_ocr",
    "rep_extraction",
    "biomechanics",
    "complete"
]

# Master switch for the multi-player detection/tracking/motion-compensation stages.
# When false (default), frame_extraction enqueues straight to "pose" and the pipeline
# behaves exactly as it did before this pivot (single athlete, num_poses=1).
MULTI_PLAYER_TRACKING_ENABLED = os.getenv("MULTI_PLAYER_TRACKING_ENABLED", "false").lower() in ["true", "1", "yes"]

# ============================================================================
# ENVIRONMENT & DEBUG
# ============================================================================

# Environment detection
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() in ["true", "1", "yes"]
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # "development", "staging", "production"

# ============================================================================
# DATABASE CONFIGURATION (Postgres - Pass 2a data-layer migration)
# ============================================================================

# Postgres connection string (postgresql://user:pass@host:port/dbname).
# Same env-var convention as Bridge Athletics' own packages/db (Drizzle):
# read once from DATABASE_URL, no fallback default - ingest/postgres_client.py
# raises a clear RuntimeError at construction time if this is unset rather
# than silently connecting somewhere unintended. In production this comes
# from AWS Secrets Manager (bridge/team-grade/prod/database-url); locally,
# point it at a disposable Postgres (see team-grade-processing/README.md).
DATABASE_URL = os.getenv("DATABASE_URL")

# ============================================================================
# FIRESTORE CONFIGURATION (retained for rollback; no longer load-bearing -
# nothing in the runtime path constructs FirestoreClient anymore as of the
# Postgres migration above, but ingest/firestore_client.py itself is left in
# place and these constants aren't deleted, in case of rollback need)
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
COLLECTION_ANALYSIS = "analysis"

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

# Multi-player pose settings (used when MULTI_PLAYER_TRACKING_ENABLED)
POSE_MAX_PLAYERS = int(os.getenv("POSE_MAX_PLAYERS", 12))  # num_poses passed to MediaPipe
POSE_TRACK_IOU_THRESHOLD = float(os.getenv("POSE_TRACK_IOU_THRESHOLD", 0.3))  # min IoU to assign a pose to a track

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
# MOTION COMPENSATION SETTINGS
# ============================================================================

MOTION_COMPENSATION_MAX_CORNERS = 200  # goodFeaturesToTrack maxCorners
MOTION_COMPENSATION_RANSAC_REPROJ_THRESHOLD = 3.0  # findHomography RANSAC threshold (px)
MOTION_COMPENSATION_MIN_INLIER_RATIO = float(os.getenv("MOTION_COMPENSATION_MIN_INLIER_RATIO", 0.3))
# Below this inlier ratio, fall back to identity homography for that frame (likely a scene cut).

# ============================================================================
# DETECTION SETTINGS (RF-DETR)
# ============================================================================

DETECTION_USE_GPU = os.getenv("DETECTION_USE_GPU", "false").lower() in ["true", "1", "yes"]
# RF-DETR segmentation variant size: "nano", "small", "medium", "large", "xlarge", "2xlarge".
# "medium" is the Phase 1 default (accuracy/speed middle ground on pretrained COCO weights).
DETECTION_MODEL_SIZE = os.getenv("DETECTION_MODEL_SIZE", "medium")
DETECTION_CONFIDENCE_THRESHOLD = float(os.getenv("DETECTION_CONFIDENCE_THRESHOLD", 0.5))
DETECTION_CLASS_NAMES = ["person"]  # Phase 1: pretrained COCO "person" class, zero-shot
# Confirmed live 2026-07-22: 50 frames/batch OOM'd the Batch job's CPU
# allocator during mask postprocessing (RF-DETR Seg produces a per-pixel
# mask per detection) - a single batch tried to allocate ~395MB in one
# tensor. Also discovered alongside this: DETECTION_USE_GPU was never set
# on the job definition, so this ran on CPU despite the GPU-provisioned
# Batch instance - fixed there too, but this smaller batch size is kept
# regardless as a real memory-pressure fix, not just a GPU workaround.
DETECTION_BATCH_SIZE = int(os.getenv("DETECTION_BATCH_SIZE", 8))

# ============================================================================
# TRACKING SETTINGS (SAM2 + density-aware memory bank)
# ============================================================================

TRACKING_USE_GPU = os.getenv("TRACKING_USE_GPU", "false").lower() in ["true", "1", "yes"]
TRACKING_MAX_LOST_FRAMES_ISOLATED = int(os.getenv("TRACKING_MAX_LOST_FRAMES_ISOLATED", 10))
TRACKING_MAX_LOST_FRAMES_DENSE = int(os.getenv("TRACKING_MAX_LOST_FRAMES_DENSE", 45))
TRACKING_DENSITY_IOU_THRESHOLD = float(os.getenv("TRACKING_DENSITY_IOU_THRESHOLD", 0.35))
TRACKING_MEMORY_UPDATE_MIN_MOTION = float(os.getenv("TRACKING_MEMORY_UPDATE_MIN_MOTION", 2.0))
TRACKING_MEMORY_STATIC_IOU_THRESHOLD = 0.9  # skip memory-bank update if IoU vs last banked frame exceeds this
TRACKING_OCCLUDED_CONFIDENCE_THRESHOLD = 0.4  # below this confidence, treat detection as occluded
TRACKING_MATCH_IOU_THRESHOLD = float(os.getenv("TRACKING_MATCH_IOU_THRESHOLD", 0.3))  # min IoU for frame-to-frame continuity match
TRACKING_REID_SIMILARITY_THRESHOLD = float(os.getenv("TRACKING_REID_SIMILARITY_THRESHOLD", 0.7))  # min appearance similarity for long-gap re-association
TRACKING_BATCH_SIZE = int(os.getenv("TRACKING_BATCH_SIZE", 20))
REID_OCR_TRIGGER_GAP_FRAMES = int(os.getenv("REID_OCR_TRIGGER_GAP_FRAMES", 45))

# ============================================================================
# GPU BATCH OFFLOAD (Phase 3 - AWS Batch)
# ============================================================================
# When enabled, the always-on CPU worker submits detection/tracking stages to
# AWS Batch instead of running them inline (see ingest/batch_dispatch.py,
# ingest_pipeline_worker.py's process_queue_item()). Independent of
# DETECTION_USE_GPU/TRACKING_USE_GPU above - those control whether the stage
# itself requests "cuda" once it's actually running; this controls WHERE it
# runs. Default false so local dev/tests never depend on AWS Batch.
GPU_BATCH_ENABLED = os.getenv("GPU_BATCH_ENABLED", "false").lower() in ["true", "1", "yes"]
AWS_BATCH_JOB_QUEUE = os.getenv("AWS_BATCH_JOB_QUEUE", "team-grade-gpu-queue")
AWS_BATCH_JOB_DEFINITION = os.getenv("AWS_BATCH_JOB_DEFINITION", "team-grade-gpu-stage")

# ============================================================================
# YOUTUBE COOKIES (bot-check bypass)
# ============================================================================
# YouTube's server-side bot-detection ("Sign in to confirm you're not a bot")
# blocks yt-dlp downloads from datacenter IPs (this worker's AWS IP included)
# even after yt-dlp exhausts its own automatic player-client fallbacks.
# docker-entrypoint.sh writes YOUTUBE_COOKIES_CONTENT (a Secrets Manager
# value, same pattern as GOOGLE_APPLICATION_CREDENTIALS_JSON below) out to
# this path at container startup - None (no cookies file) is a normal,
# supported state for local dev.
YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE") or None

# ============================================================================
# RETENTION (unclaimed free-tool videos)
# ============================================================================
# Videos submitted through the-bridge.app's free, no-login tool sit around
# forever unless claimed into a Bridge Athletics profile (see
# ingest/retention.py, POST /api/videos/{video_id}/mark-claimed). This is how
# long an unclaimed video's data (S3 objects + every DB row) is kept before
# the periodic purge sweep removes it.
RETENTION_HOURS = int(os.getenv("RETENTION_HOURS", "48"))

# ============================================================================
# RATE LIMITING (public, no-login ingest endpoints)
# ============================================================================
# slowapi rate-string format ("N/hour", "N/minute", etc.) - applies per
# client IP, scoped to just POST /api/ingest and /api/ingest/upload (the two
# routes a completely anonymous visitor can call), not the whole API.
INGEST_RATE_LIMIT = os.getenv("INGEST_RATE_LIMIT", "20/hour")

# ============================================================================
# EMAIL REMINDER (notify a visitor when their free-tool video finishes)
# ============================================================================
# Fire-and-forget call from complete_stage.py into Bridge Athletics' own
# email-sending capability (Resend is already wired up there, this project
# has no email infra of its own) - the reverse direction of
# markTeamGradeVideoClaimed (apps/api/src/routes/video.ts), same isolation
# principle: a struggling Bridge Athletics must never fail or delay this
# stage's own completion. None (unset) disables the notification entirely.
BRIDGE_API_BASE = os.getenv("BRIDGE_API_BASE") or None

# Shared secret sent as the x-webhook-secret header on the notify-film-ready
# call above - Bridge Athletics' route has no other auth on it (there's no
# user account yet at this point in the funnel), so this is what stops the
# endpoint being an open "send an email to any address" spam vector.
TEAM_GRADE_WEBHOOK_SECRET = os.getenv("TEAM_GRADE_WEBHOOK_SECRET") or None

# SAM2 checkpoint (downloaded separately - not bundled with the sam2 pip package;
# see models/README.md). Config file path is relative to the sam2 package's bundled
# configs/ directory (hydra-resolved), not a filesystem path.
SAM2_CHECKPOINT_PATH = os.getenv(
    "SAM2_CHECKPOINT_PATH", str(PROJECT_ROOT / "models" / "sam2.1_hiera_tiny.pt")
)
SAM2_CONFIG_FILE = os.getenv("SAM2_CONFIG_FILE", "configs/sam2.1/sam2.1_hiera_t.yaml")

# ============================================================================
# WHISTLE DETECTION SETTINGS
# ============================================================================
# Ported from a standalone preprocessor/ PR that was never wired into the real
# pipeline; ported as-is into processing/audio_analyzer.py. Tuned per academic
# research: "Automated Referee Whistle Sound Detection for Extraction of
# Highlights from Sports Video".

# Master switch for the whistle_detection stage. When false (default), download
# enqueues straight to "frame_extraction" and the pipeline behaves exactly as
# it did before this addition.
WHISTLE_DETECTION_ENABLED = os.getenv("WHISTLE_DETECTION_ENABLED", "false").lower() in ["true", "1", "yes"]

AUDIO_SAMPLE_RATE = 22050       # Hz - balance between speed and accuracy
AUDIO_CHUNK_DURATION = 30       # seconds - process in chunks for memory efficiency
AUDIO_HOP_LENGTH = 512          # samples - frame stride (~23 ms at 22050 Hz)
AUDIO_N_FFT = 2048              # samples - FFT window for frequency resolution

# Bandpass filter (academic research: whistle sits in 2-4 kHz)
WHISTLE_BANDPASS_LOW = 2000     # Hz - lower edge of whistle band
WHISTLE_BANDPASS_HIGH = 4000    # Hz - upper edge of whistle band
WHISTLE_BANDPASS_ORDER = 5      # FIR/IIR filter order

# Short-Time Energy (STE) thresholding
WHISTLE_ENERGY_PERCENTILE = 75  # Upper-quartile threshold (academic finding)
WHISTLE_ENERGY_FLOOR = 1e-6     # Absolute floor - avoids false positives on silence

# Spectral centroid target range for a referee whistle
WHISTLE_SPECTRAL_BAND = (2500, 3500)  # Hz - centroid range for a standard whistle

# Duration constraints
WHISTLE_MIN_DURATION = 0.05     # 50 ms minimum (academic finding)
WHISTLE_MAX_DURATION = 2.0      # 2 s maximum (outlier rejection)

# Librosa onset detection
ONSET_THRESHOLD = 0.1           # Peak threshold for librosa.onset.onset_detect
ONSET_DISTANCE = max(1, int(0.3 * AUDIO_SAMPLE_RATE / AUDIO_HOP_LENGTH))
# Minimum 0.3 s between onset peaks (in frames) - a referee cannot physically
# blow two distinct whistles within 300 ms.

# Post-processing
EVENT_MERGE_GAP = 0.1           # Merge events closer than 100 ms
CONFIDENCE_THRESHOLD = 0.5      # Minimum confidence to include in output

# Confidence fusion weights inside audio_analyzer (onset + STE + spectral)
AUDIO_FUSION_WEIGHTS = {
    "onset": 0.40,
    "ste": 0.35,
    "spectral": 0.25,
}

# ============================================================================
# AUTHENTICITY CHECK SETTINGS
# ============================================================================
# Port of VerifAI's (a separate project) heuristic detection concepts into this
# server-side pipeline - see docs/team-grade-integration-blueprint.md in the
# the-bridge.app repo, "AI verification" section. Two touchpoints: a whole-file
# gate right after download (processing/film_authenticity.py's
# analyze_file_integrity, run by ingest/stages/authenticity_check_stage.py),
# and a chunk-based vision/motion pass riding along with frame_extraction
# (analyze_frame_signals, called inline from frame_extraction_stage_gpu.py).
# Both are soft-flag only inside this pipeline - never fail a stage, just write
# a flag. Hard-blocking only happens on Bridge Athletics' claim-to-profile gate.

# Master switch, same idiom as WHISTLE_DETECTION_ENABLED. Off by default so this
# ships fully inert until proven, same as every other optional stage here.
AUTHENTICITY_CHECK_ENABLED = os.getenv("AUTHENTICITY_CHECK_ENABLED", "false").lower() in ["true", "1", "yes"]

# How many frames to sample (evenly spaced) out of frame_extraction's already-
# decoded output for the vision/motion pass - not every frame, keeps it cheap.
AUTHENTICITY_SAMPLE_FRAME_COUNT = int(os.getenv("AUTHENTICITY_SAMPLE_FRAME_COUNT", 12))

# File-integrity gate (Part 1) thresholds
AUTHENTICITY_MAX_KEYFRAME_INTERVAL_SEC = float(os.getenv("AUTHENTICITY_MAX_KEYFRAME_INTERVAL_SEC", 12.0))
# ffprobe -read_intervals window used to sample pict_type without reading the
# whole file's frame index (cheap, bounded cost).
AUTHENTICITY_GOP_PROBE_WINDOW_SEC = float(os.getenv("AUTHENTICITY_GOP_PROBE_WINDOW_SEC", 30.0))

# Vision/motion pass (Part 2) thresholds - all hand-tuned starting guesses, not
# validated against real footage yet (same honesty as VerifAI's own docs).
AUTHENTICITY_EDGE_DENSITY_VARIANCE_MIN = float(os.getenv("AUTHENTICITY_EDGE_DENSITY_VARIANCE_MIN", 0.0005))
AUTHENTICITY_HISTOGRAM_CHISQR_SPIKE_THRESHOLD = float(os.getenv("AUTHENTICITY_HISTOGRAM_CHISQR_SPIKE_THRESHOLD", 5000.0))
AUTHENTICITY_MOTION_DIFF_STATIC_THRESHOLD = float(os.getenv("AUTHENTICITY_MOTION_DIFF_STATIC_THRESHOLD", 1.0))

# ============================================================================
# REP EXTRACTION SETTINGS
# ============================================================================

REP_EXTRACTOR_CONFIG = {
    "max_static_frames": int(os.getenv("REP_MAX_STATIC_FRAMES", 30)),
    "max_gap_frames": int(os.getenv("REP_MAX_GAP_FRAMES", 10)),
    "min_rep_duration": int(os.getenv("REP_MIN_DURATION", 15)),
    "max_rep_duration": int(os.getenv("REP_MAX_DURATION", 300))
}

# Bare aliases of REP_EXTRACTOR_CONFIG's values — rep_extraction_stage.py constructs
# RepExtractor/FastTrajectoryAnalyzer with these as keyword args, not the dict itself.
REP_MAX_STATIC_FRAMES = REP_EXTRACTOR_CONFIG["max_static_frames"]
REP_MAX_GAP_FRAMES = REP_EXTRACTOR_CONFIG["max_gap_frames"]
REP_MIN_DURATION = REP_EXTRACTOR_CONFIG["min_rep_duration"]
REP_MAX_DURATION = REP_EXTRACTOR_CONFIG["max_rep_duration"]

REP_EXTRACTION_BATCH_SIZE = 10
REP_PROGRESS_LOG_INTERVAL = 10  # Log progress every N reps

# Default position label for a tracked player when no team/roster context is available.
DEFAULT_PLAYER_POSITION = "UNKNOWN"

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

# Normalized-coordinate velocity (units/frame) treated as "max expected" when scaling
# real keypoint-displacement-derived features (linear_speed, acceleration, etc.) to the
# 0-100 trait band. A reasoned Phase-1 estimate, not yet validated against real footage
# with known field dimensions (see plan Phase 2).
MAX_EXPECTED_VELOCITY_NORM = float(os.getenv("MAX_EXPECTED_VELOCITY_NORM", 0.15))

# Scales normalized-coordinate centroid-trajectory std-dev into a 0-100 point
# deduction for stability-derived features (core_stability, balance, hip_mobility,
# positioning). Same "reasoned Phase-1 estimate" caveat as MAX_EXPECTED_VELOCITY_NORM.
CENTROID_STABILITY_SCALE = float(os.getenv("CENTROID_STABILITY_SCALE", 300.0))

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
    "MOTION_ESTIMATION_FAILED": "Camera motion estimation failed",
    "DETECTION_MODEL_LOAD_FAILED": "Failed to load player detection model",
    "DETECTION_FAILED": "Player detection failed",
    "TRACKING_MODEL_LOAD_FAILED": "Failed to load tracking model",
    "TRACKING_FAILED": "Player tracking failed",
    "DETECTIONS_NOT_FOUND": "No detections found for video",
    "TRACKS_NOT_FOUND": "No tracks found for video",
    "WHISTLE_DETECTION_FAILED": "Audio whistle detection failed",
    
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

