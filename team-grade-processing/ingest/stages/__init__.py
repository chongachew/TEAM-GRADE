"""
Ingestion Pipeline Stages - Multi-stage Queue-Based Processing Pipeline

This module implements the central dispatcher for the 9-stage video processing pipeline:
1. metadata - Initialize video document and stage tracking
2. download - Download video from YouTube
3. frame_extraction - Extract frames at 15 FPS
4. pose - Detect pose keypoints using MediaPipe
5. torso_crop - Crop torso regions from high-confidence frames
6. jersey_ocr - Detect jersey number from torso crops
7. rep_extraction - Segment plays/repetitions from pose trajectory
8. biomechanics - Calculate trait scores per repetition
9. complete - Finalize processing and cleanup

Stages execute sequentially; each queue item progresses through all stages until completion.
If a stage fails, exponential backoff retry is triggered (3 max retries, 0.1s → 0.3s → 0.9s).

Public API exports both handler functions and stage name constants to prevent hardcoding.
"""

import logging
from typing import Optional
from .metadata_stage import run_metadata_stage
from .download_stage import run_download_stage
# Authenticity check: whole-file manipulation-heuristic gate, ahead of whistle_detection/frame_extraction
from .authenticity_check_stage import run_authenticity_check_stage
# Whistle detection: audio-based play-boundary cue, ahead of frame_extraction
from .whistle_detection_stage import run_whistle_detection_stage
# Phase 3 Week 2: GPU-accelerated frame extraction
from .frame_extraction_stage_gpu import run_frame_extraction_stage
# Multi-player pivot: motion compensation / detection / tracking, ahead of pose
from .motion_compensation_stage import run_motion_compensation_stage
from .detection_stage import run_detection_stage
from .tracking_stage import run_tracking_stage
# Phase 3 Week 3: Lightweight pose model (2MB, 2x faster)
from .pose_stage_lite import run_pose_stage_lite as run_pose_stage
from .torso_crop_stage import run_torso_crop_stage
from .jersey_ocr_stage import run_jersey_ocr_stage
from .rep_extraction_stage import run_rep_extraction_stage
# Phase 3 Week 4: Vectorized biomechanics (NumPy matrix operations)
from .biomechanics_stage_vectorized import run_biomechanics_stage
from .complete_stage import run_complete_stage

__version__ = "1.0.0"

# Setup logging for dispatcher operations
logger = logging.getLogger(__name__)

# Import centralized constants with validation
_import_from_config = False
try:
    from config.constants import (
        STAGE_METADATA, STAGE_DOWNLOAD, STAGE_AUTHENTICITY_CHECK, STAGE_WHISTLE_DETECTION, STAGE_FRAME_EXTRACTION,
        STAGE_MOTION_COMPENSATION, STAGE_DETECTION, STAGE_TRACKING,
        STAGE_POSE, STAGE_TORSO_CROP, STAGE_JERSEY_OCR,
        STAGE_REP_EXTRACTION, STAGE_BIOMECHANICS, STAGE_COMPLETE
    )
    _import_from_config = True
    logger.debug("Stage constants loaded from config.constants")
except ImportError as e:
    # Fallback to hardcoded values with explicit logging
    logger.warning(f"config.constants not found ({e}); using fallback stage constants")
    STAGE_METADATA = "metadata"
    STAGE_DOWNLOAD = "download"
    STAGE_AUTHENTICITY_CHECK = "authenticity_check"
    STAGE_WHISTLE_DETECTION = "whistle_detection"
    STAGE_FRAME_EXTRACTION = "frame_extraction"
    STAGE_MOTION_COMPENSATION = "motion_compensation"
    STAGE_DETECTION = "detection"
    STAGE_TRACKING = "tracking"
    STAGE_POSE = "pose"
    STAGE_TORSO_CROP = "torso_crop"
    STAGE_JERSEY_OCR = "jersey_ocr"
    STAGE_REP_EXTRACTION = "rep_extraction"
    STAGE_BIOMECHANICS = "biomechanics"
    STAGE_COMPLETE = "complete"

# Validate stage constants at module load
def _validate_stage_constants():
    """Validate all stage name constants are non-empty strings."""
    stages = [
        STAGE_METADATA, STAGE_DOWNLOAD, STAGE_AUTHENTICITY_CHECK, STAGE_WHISTLE_DETECTION, STAGE_FRAME_EXTRACTION,
        STAGE_MOTION_COMPENSATION, STAGE_DETECTION, STAGE_TRACKING,
        STAGE_POSE, STAGE_TORSO_CROP, STAGE_JERSEY_OCR,
        STAGE_REP_EXTRACTION, STAGE_BIOMECHANICS, STAGE_COMPLETE
    ]
    for stage in stages:
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError(f"Invalid stage constant: {stage!r} (must be non-empty string)")
    logger.debug("All stage constants validated successfully")

try:
    _validate_stage_constants()
except ValueError as e:
    logger.error(f"Stage constant validation failed: {e}")
    raise

__all__ = [
    # Stage handler functions (exported to prevent external hardcoding)
    "run_metadata_stage",
    "run_download_stage",
    "run_authenticity_check_stage",
    "run_whistle_detection_stage",
    "run_frame_extraction_stage",
    "run_motion_compensation_stage",
    "run_detection_stage",
    "run_tracking_stage",
    "run_pose_stage",
    "run_torso_crop_stage",
    "run_jersey_ocr_stage",
    "run_rep_extraction_stage",
    "run_biomechanics_stage",
    "run_complete_stage",
    # Stage name constants (use these instead of hardcoding strings)
    "STAGE_METADATA",
    "STAGE_DOWNLOAD",
    "STAGE_AUTHENTICITY_CHECK",
    "STAGE_WHISTLE_DETECTION",
    "STAGE_FRAME_EXTRACTION",
    "STAGE_MOTION_COMPENSATION",
    "STAGE_DETECTION",
    "STAGE_TRACKING",
    "STAGE_POSE",
    "STAGE_TORSO_CROP",
    "STAGE_JERSEY_OCR",
    "STAGE_REP_EXTRACTION",
    "STAGE_BIOMECHANICS",
    "STAGE_COMPLETE",
    # Dispatcher and helpers (exported for orchestration)
    "STAGE_HANDLERS",
    "STAGE_SEQUENCE",
    "get_next_stage",
]

# Stage dispatcher mapping: stage_name → handler_function
# Each handler validates input and returns (success: bool, error_message: Optional[str])
STAGE_HANDLERS = {
    STAGE_METADATA: run_metadata_stage,
    STAGE_DOWNLOAD: run_download_stage,
    STAGE_AUTHENTICITY_CHECK: run_authenticity_check_stage,
    STAGE_WHISTLE_DETECTION: run_whistle_detection_stage,
    STAGE_FRAME_EXTRACTION: run_frame_extraction_stage,
    STAGE_MOTION_COMPENSATION: run_motion_compensation_stage,
    STAGE_DETECTION: run_detection_stage,
    STAGE_TRACKING: run_tracking_stage,
    STAGE_POSE: run_pose_stage,
    STAGE_TORSO_CROP: run_torso_crop_stage,
    STAGE_JERSEY_OCR: run_jersey_ocr_stage,
    STAGE_REP_EXTRACTION: run_rep_extraction_stage,
    STAGE_BIOMECHANICS: run_biomechanics_stage,
    STAGE_COMPLETE: run_complete_stage,
}

def _validate_handlers():
    """Validate all handlers are callable at module load."""
    for stage_name, handler in STAGE_HANDLERS.items():
        if not callable(handler):
            raise TypeError(f"Stage '{stage_name}' handler is not callable: {handler!r}")
    logger.debug(f"All {len(STAGE_HANDLERS)} stage handlers validated (callable)")

try:
    _validate_handlers()
except TypeError as e:
    logger.error(f"Handler validation failed: {e}")
    raise

# Stage sequence defines execution order: linear 9-stage pipeline
# Each video progresses through stages sequentially; stage N depends on successful completion of stage N-1
# Exception: stage "complete" has no successor; it marks the video as fully processed
STAGE_SEQUENCE = [
    STAGE_METADATA,
    STAGE_DOWNLOAD,
    STAGE_AUTHENTICITY_CHECK,
    STAGE_WHISTLE_DETECTION,
    STAGE_FRAME_EXTRACTION,
    STAGE_MOTION_COMPENSATION,
    STAGE_DETECTION,
    STAGE_TRACKING,
    STAGE_POSE,
    STAGE_TORSO_CROP,
    STAGE_JERSEY_OCR,
    STAGE_REP_EXTRACTION,
    STAGE_BIOMECHANICS,
    STAGE_COMPLETE,
]

def get_next_stage(current_stage: str) -> Optional[str]:
    """
    Get next stage in pipeline sequence.
    
    Args:
        current_stage: Current stage name (must be in STAGE_SEQUENCE)
    
    Returns:
        Next stage name, or None if current_stage is last stage (STAGE_COMPLETE)
    
    Raises:
        ValueError: If current_stage not in STAGE_SEQUENCE
    
    Example:
        >>> get_next_stage(STAGE_METADATA)
        'download'
        >>> get_next_stage(STAGE_COMPLETE)
        None
    """
    if current_stage not in STAGE_SEQUENCE:
        valid_stages = ", ".join(STAGE_SEQUENCE)
        raise ValueError(f"Unknown stage '{current_stage}'; valid stages: {valid_stages}")
    
    try:
        current_idx = STAGE_SEQUENCE.index(current_stage)
        if current_idx == len(STAGE_SEQUENCE) - 1:
            # Current stage is last in sequence; no successor
            return None
        return STAGE_SEQUENCE[current_idx + 1]
    except (ValueError, IndexError) as e:
        logger.error(f"Error getting next stage for '{current_stage}': {e}")
        raise ValueError(f"Internal error: could not determine next stage after '{current_stage}'")
