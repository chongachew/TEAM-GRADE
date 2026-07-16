"""
Ingest Utils Package
Utilities for the ingestion pipeline.

Main components:
- **Logging**: Structured, ASCII-safe logging with consistent formatting
- **FFmpeg**: Video metadata retrieval and frame extraction
- **Firestore**: Persistence layer for video metadata, frames, poses, reps, and analysis results

All utilities handle input validation, error handling, and settings integration.
Exceptions: ValueError for invalid inputs, subprocess errors for FFmpeg operations.

Usage:
    from ingest.utils import StructuredLogger, extract_frames_from_video
    from ingest.utils import write_frames_batch, update_stage_status
"""

__version__ = "1.0.0"

from .logging_utils import StructuredLogger, setup_logging
from .ffmpeg_utils import FFmpegFrameExtractor, extract_frames_from_video
from .firestore_utils import (
    write_video_metadata,
    write_frames_batch,
    write_pose_batch,
    write_torso_crops_batch,
    write_reps_batch,
    update_stage_status,
    write_jersey_number,
    mark_stage_attempt,
    get_utc_timestamp,
)

# Logging utilities (structured, ASCII-safe logging with video_id context)
__all__ = [
    "StructuredLogger",
    "setup_logging",
    # FFmpeg utilities (video metadata and frame extraction)
    "FFmpegFrameExtractor",
    "extract_frames_from_video",
    # Postgres utilities (persistence layer for all pipeline data - see
    # firestore_utils.py's module docstring for why this file/module name
    # still says "firestore")
    "write_video_metadata",
    "write_frames_batch",
    "write_pose_batch",
    "write_torso_crops_batch",
    "write_reps_batch",
    "update_stage_status",
    "write_jersey_number",
    "mark_stage_attempt",
    "get_utc_timestamp",
]
