"""
Per-play frame-range loading (Phase C of the per-play pipeline redesign).

Every frame-touching stage (detection/tracking/pose) used to glob the whole
frames directory and derive frame_index *positionally*
(`enumerate(frame_paths)` / `batch_start + offset`) - correct only because
frame_paths was always the full, contiguous, zero-based frame list for the
whole video. Once a stage is scoped to one play's frame range, positional
indexing silently produces the wrong frame_index (e.g. a play starting at
frame 500 would be mislabeled as frames 0..N). This module centralizes the
fix: always derive frame_index from the actual filename
(processing.play_boundary_detection.frame_index_from_name), never from list
position, and optionally filter to a play's [start_frame, end_frame] range.
"""

from pathlib import Path
from typing import Dict, Optional

from config import settings
from processing.play_boundary_detection import frame_index_from_name


def list_frame_paths_by_index(
    video_id_safe: str,
    start_frame: Optional[int] = None,
    end_frame: Optional[int] = None,
) -> Dict[int, Path]:
    """Return {real_frame_index: path} for this video's extracted frames,
    optionally restricted to [start_frame, end_frame] (inclusive).

    Args:
        video_id_safe: already-sanitized video_id.
        start_frame: first frame_index to include, or None for no lower bound.
        end_frame: last frame_index to include, or None for no upper bound.
    """
    frames_dir = settings.get_frames_dir(video_id_safe)
    result: Dict[int, Path] = {}
    for path in frames_dir.glob("frame_*.jpg"):
        frame_index = frame_index_from_name(path)
        if start_frame is not None and frame_index < start_frame:
            continue
        if end_frame is not None and frame_index > end_frame:
            continue
        result[frame_index] = path
    return result
