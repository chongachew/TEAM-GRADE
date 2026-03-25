"""
Motion analyzer — frame differencing at 480p / 4 fps to detect play bursts.

Strategy:
  1. Extract low-res (480p) frames at SAMPLE_FPS via FFmpeg into memory.
  2. Convert each frame to grayscale and compute absolute pixel diff vs
     previous frame.
  3. Mask the diff to ignore the scoreboard region (top 10 % of frame).
  4. A "burst" is a run of consecutive frames where the mean diff exceeds
     MOTION_DIFF_THRESHOLD.
  5. Small gaps (< MOTION_BURST_GAP_FRAMES) between bursts are bridged.

Output: list of dicts with keys {start_time, end_time, motion_score}.

References:
  - mudassarkhn/Motion-detection-using-Frame-Differencing
  - OpenCV absdiff + threshold pattern
"""

from __future__ import annotations

import logging
import subprocess
from typing import List, Dict, Any

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise ImportError("opencv-python is required: pip install opencv-python") from exc

from preprocessor.config import (
    SAMPLE_WIDTH,
    SAMPLE_HEIGHT,
    SAMPLE_FPS,
    MOTION_DIFF_THRESHOLD,
    MOTION_BURST_MIN_FRAMES,
    MOTION_BURST_GAP_FRAMES,
    FFMPEG_BIN,
)

logger = logging.getLogger(__name__)


def _frames_from_video(video_path: str) -> List[np.ndarray]:
    """Decode *video_path* at low-res/low-fps into a list of BGR frames."""
    vf = f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT},fps={SAMPLE_FPS}"
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
        logger.error("FFmpeg frame extract failed: %s", result.stderr.decode()[-500:])
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


class MotionAnalyzer:
    """Detect motion bursts (play activity) via frame differencing."""

    def __init__(
        self,
        diff_threshold: int = MOTION_DIFF_THRESHOLD,
        burst_min_frames: int = MOTION_BURST_MIN_FRAMES,
        burst_gap_frames: int = MOTION_BURST_GAP_FRAMES,
        sample_fps: int = SAMPLE_FPS,
    ) -> None:
        self.diff_threshold = diff_threshold
        self.burst_min_frames = burst_min_frames
        self.burst_gap_frames = burst_gap_frames
        self.sample_fps = sample_fps

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, video_path: str) -> List[Dict[str, Any]]:
        """Return motion bursts detected in *video_path*.

        Each burst:
            {start_time, end_time, motion_score}
        """
        frames = _frames_from_video(video_path)
        if len(frames) < 2:
            logger.warning("Not enough frames to analyze motion in %s", video_path)
            return []
        return self._detect_bursts(frames)

    def analyze_frames(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """Analyze a pre-loaded list of BGR frames (useful for testing)."""
        if len(frames) < 2:
            return []
        return self._detect_bursts(frames)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_diff_scores(self, frames: List[np.ndarray]) -> np.ndarray:
        """Return per-frame mean absolute diff (length = len(frames) - 1)."""
        # Ignore top 10 % of frame — scoreboard area
        scoreboard_rows = max(1, int(SAMPLE_HEIGHT * 0.10))
        scores = []
        prev_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
        prev_gray = prev_gray[scoreboard_rows:, :]
        for frame in frames[1:]:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = gray[scoreboard_rows:, :]
            diff = cv2.absdiff(prev_gray, gray)
            scores.append(float(diff.mean()))
            prev_gray = gray
        return np.array(scores, dtype=np.float32)

    def _detect_bursts(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        scores = self._compute_diff_scores(frames)
        is_active = scores >= self.diff_threshold

        # Bridge small gaps
        i = 0
        while i < len(is_active) - self.burst_gap_frames:
            if is_active[i] and is_active[i + self.burst_gap_frames]:
                is_active[i:i + self.burst_gap_frames] = True
            i += 1

        # Extract bursts
        bursts: List[Dict[str, Any]] = []
        in_burst = False
        burst_start = 0
        burst_scores: List[float] = []

        for i, active in enumerate(is_active):
            t = i / self.sample_fps  # time of inter-frame diff = frame i+1
            if active and not in_burst:
                burst_start = t
                burst_scores = [float(scores[i])]
                in_burst = True
            elif active and in_burst:
                burst_scores.append(float(scores[i]))
            elif not active and in_burst:
                n_frames = len(burst_scores)
                if n_frames >= self.burst_min_frames:
                    motion_score = float(np.mean(burst_scores))
                    bursts.append({
                        "start_time": round(burst_start, 3),
                        "end_time": round(t, 3),
                        "motion_score": round(motion_score, 4),
                    })
                in_burst = False
                burst_scores = []

        # Close open burst at end of video
        if in_burst and len(burst_scores) >= self.burst_min_frames:
            t_end = (len(scores)) / self.sample_fps
            bursts.append({
                "start_time": round(burst_start, 3),
                "end_time": round(t_end, 3),
                "motion_score": round(float(np.mean(burst_scores)), 4),
            })

        logger.info("MotionAnalyzer: %d bursts detected", len(bursts))
        return bursts
