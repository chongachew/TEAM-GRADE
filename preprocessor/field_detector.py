"""
Field detector — HSV green-mask validation to confirm field presence in a frame.

Strategy:
  1. Sample one frame per second at low resolution (480p).
  2. Convert to HSV color space.
  3. Apply a green-channel mask (tuned for artificial + natural grass).
  4. Compute the fraction of pixels that fall within the green band.
  5. Return a per-second field presence score (0.0 – 1.0).

Output: list of dicts {time, field_presence} and a helper method to
aggregate over a time range.

References:
  - OpenCV inRange HSV mask (standard technique)
  - Football field detection: green HSV band (35–85 H, 40–255 S, 40–255 V)
"""

from __future__ import annotations

import logging
import subprocess
from typing import List, Dict, Any, Tuple

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise ImportError("opencv-python is required: pip install opencv-python") from exc

from preprocessor.config import (
    SAMPLE_WIDTH,
    SAMPLE_HEIGHT,
    FIELD_HSV_LOWER,
    FIELD_HSV_UPPER,
    FIELD_MIN_COVERAGE,
    FFMPEG_BIN,
)

logger = logging.getLogger(__name__)


def _extract_frames_at_1fps(video_path: str) -> List[np.ndarray]:
    """Extract one frame per second at low resolution."""
    vf = f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT},fps=1"
    cmd = [
        FFMPEG_BIN,
        "-i", video_path,
        "-vf", vf,
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-an",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.error("FFmpeg field-frame extract failed: %s",
                     result.stderr.decode()[-500:])
        return []

    raw = result.stdout
    frame_size = SAMPLE_WIDTH * SAMPLE_HEIGHT * 3
    n_frames = len(raw) // frame_size
    frames = []
    for i in range(n_frames):
        chunk = raw[i * frame_size:(i + 1) * frame_size]
        frame = np.frombuffer(chunk, dtype=np.uint8).reshape(
            (SAMPLE_HEIGHT, SAMPLE_WIDTH, 3)
        )
        frames.append(frame)
    return frames


def green_coverage(frame_bgr: np.ndarray,
                   lower: Tuple[int, int, int] = FIELD_HSV_LOWER,
                   upper: Tuple[int, int, int] = FIELD_HSV_UPPER) -> float:
    """Return fraction of *frame_bgr* pixels that are 'grass green'."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(lower, dtype=np.uint8),
        np.array(upper, dtype=np.uint8),
    )
    return float(mask.sum()) / (mask.size * 255)


class FieldDetector:
    """Per-second field presence detector using an HSV green mask."""

    def __init__(
        self,
        hsv_lower: Tuple[int, int, int] = FIELD_HSV_LOWER,
        hsv_upper: Tuple[int, int, int] = FIELD_HSV_UPPER,
        min_coverage: float = FIELD_MIN_COVERAGE,
    ) -> None:
        self.hsv_lower = hsv_lower
        self.hsv_upper = hsv_upper
        self.min_coverage = min_coverage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, video_path: str) -> List[Dict[str, Any]]:
        """Return per-second field presence scores for *video_path*.

        Each entry:
            {time, field_presence}
        where *field_presence* is the green-pixel coverage ratio (0.0–1.0).
        """
        frames = _extract_frames_at_1fps(video_path)
        return self._score_frames(frames)

    def analyze_frames(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """Score a pre-loaded list of frames (one per second)."""
        return self._score_frames(frames)

    def mean_presence(self, scores: List[Dict[str, Any]],
                      start_s: float, end_s: float) -> float:
        """Return mean field presence in [start_s, end_s]."""
        window = [s["field_presence"] for s in scores
                  if start_s <= s["time"] <= end_s]
        return float(np.mean(window)) if window else 0.0

    def is_field_present(self, scores: List[Dict[str, Any]],
                         start_s: float, end_s: float) -> bool:
        """Return True if mean coverage in window exceeds threshold."""
        return self.mean_presence(scores, start_s, end_s) >= self.min_coverage

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_frames(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for t, frame in enumerate(frames):
            coverage = green_coverage(frame, self.hsv_lower, self.hsv_upper)
            results.append({
                "time": float(t),
                "field_presence": round(coverage, 4),
            })
        logger.debug("FieldDetector: scored %d frames", len(results))
        return results
