"""
Shared ffmpeg clip-cutting helper - stream-copies a frame range out of a
source video. Extracted from api/server.py's get_rep_clip (the original,
rep-scoped "cut a clip" endpoint) so play_clip_export_stage.py can reuse the
exact same cutting behavior for whole-play clips instead of duplicating the
ffmpeg invocation.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ClipCutError(Exception):
    """ffmpeg failed, or produced no output file."""


def cut_clip(source_path: Path, start_frame: int, end_frame: int, output_path: Path, fps: int) -> None:
    """Stream-copy (-c copy) the [start_frame, end_frame] range of
    source_path into output_path. No re-encode - fast, but the actual cut
    point snaps to the nearest keyframe at/before the requested start (may
    include a moment of footage before the range technically started, the
    same accepted tradeoff get_rep_clip's callers already live with).

    -ss placed BEFORE -i: fast input seeking (seeks near the preceding
    keyframe) rather than forcing ffmpeg to decode the entire file from true
    frame 0 on every cut - a real cost against full game film.

    Raises:
        ClipCutError: if ffmpeg exits non-zero, times out, or produces no
            output file.
    """
    start_seconds = start_frame / fps
    duration_seconds = (end_frame - start_frame) / fps

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_seconds),
        "-i", str(source_path),
        "-t", str(duration_seconds),
        "-c", "copy",
        str(output_path),
    ]

    try:
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise ClipCutError(f"ffmpeg timed out cutting {source_path} -> {output_path}") from e

    if result.returncode != 0 or not output_path.exists():
        raise ClipCutError(result.stderr.decode(errors="replace"))
