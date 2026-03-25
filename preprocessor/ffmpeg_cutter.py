"""
FFmpeg cutter — lossless segment extraction using ``-c copy``.

All cuts use stream copy (no re-encode) which is:
  - Fast: I/O bound only, no GPU/CPU inference
  - Lossless: no quality loss
  - Compatible with H.264/AAC broadcast recordings

Caveat: ``-c copy`` seeks to the nearest keyframe before the requested start
time, so the segment may start a fraction of a second early.  The metadata
records the *requested* timestamps; the actual cut may differ by up to the
keyframe interval (~2 s for typical broadcast video).

References:
  - moviepy / FFmpeg subprocess pattern
  - FFmpeg docs: -ss before -i for fast keyframe seek
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from preprocessor.config import (
    FFMPEG_BIN,
    FFMPEG_CUT_CODEC,
    FFMPEG_EXTRA_ARGS,
    FFMPEG_THREADS,
)

logger = logging.getLogger(__name__)


def cut_segment(
    input_path: str,
    output_path: str,
    start_s: float,
    end_s: float,
    extra_args: Optional[List[str]] = None,
) -> bool:
    """Cut a single segment from *input_path* and write to *output_path*.

    Uses ``-ss`` before ``-i`` for fast keyframe seek and ``-c copy`` to
    avoid re-encoding.

    Returns True on success, False on failure.
    """
    duration = end_s - start_s
    if duration <= 0:
        logger.error("cut_segment: invalid range [%.3f, %.3f]", start_s, end_s)
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-ss", str(start_s),
        "-i", input_path,
        "-t", str(duration),
        "-c", FFMPEG_CUT_CODEC,
        *FFMPEG_EXTRA_ARGS,
        *(extra_args or []),
        output_path,
    ]

    logger.debug("FFmpegCutter: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=300,  # 5 min safety timeout per segment
    )

    if result.returncode != 0:
        logger.error(
            "FFmpegCutter: cut failed [%.3f–%.3f] → %s\n%s",
            start_s, end_s, output_path,
            result.stderr.decode()[-800:],
        )
        return False

    logger.info("FFmpegCutter: ✓ %s (%.1f s)", output_path, duration)
    return True


class FFmpegCutter:
    """Batch segment cutter with optional async subprocess management."""

    def __init__(self, ffmpeg_bin: str = FFMPEG_BIN,
                 codec: str = FFMPEG_CUT_CODEC) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.codec = codec

    def cut(
        self,
        input_path: str,
        output_path: str,
        start_s: float,
        end_s: float,
    ) -> bool:
        return cut_segment(input_path, output_path, start_s, end_s)

    def cut_all(
        self,
        input_path: str,
        output_dir: str,
        segments: List[dict],
        prefix: str = "play",
    ) -> List[str]:
        """Cut every segment in *segments* list into *output_dir*.

        *segments* items must have ``start_time`` and ``end_time`` keys.
        Returns list of successfully created output file paths.
        """
        os.makedirs(output_dir, exist_ok=True)
        created: List[str] = []
        for i, seg in enumerate(segments, start=1):
            out_file = os.path.join(output_dir, f"{prefix}_{i:03d}.mp4")
            ok = self.cut(
                input_path, out_file,
                seg["start_time"], seg["end_time"],
            )
            if ok:
                created.append(out_file)
        return created
