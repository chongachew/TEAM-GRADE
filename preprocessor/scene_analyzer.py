"""
Scene analyzer — PySceneDetect integration for broadcast cut detection.

Uses ContentDetector (fast mode) to find hard cuts between scenes.  Scene
boundaries often correspond to replay/broadcast transitions that bookend plays.

Output: list of dicts {start_time, end_time} representing detected scenes.

References:
  - Breakthrough/PySceneDetect ContentDetector
  - scenedetect.video_manager + detect_scenes pattern
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any

from preprocessor.config import (
    SCENE_THRESHOLD,
    SCENE_MIN_SCENE_LEN_S,
    FFMPEG_THREADS,
)

logger = logging.getLogger(__name__)


def _import_scenedetect():
    """Lazy import so the rest of the codebase works without PySceneDetect."""
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        return open_video, SceneManager, ContentDetector
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "scenedetect is required: pip install scenedetect"
        ) from exc


class SceneAnalyzer:
    """Detect broadcast scene boundaries using PySceneDetect ContentDetector."""

    def __init__(
        self,
        threshold: float = SCENE_THRESHOLD,
        min_scene_len_s: float = SCENE_MIN_SCENE_LEN_S,
    ) -> None:
        self.threshold = threshold
        self.min_scene_len_s = min_scene_len_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, video_path: str) -> List[Dict[str, Any]]:
        """Return list of detected scenes in *video_path*.

        Each scene:
            {start_time, end_time}
        """
        open_video, SceneManager, ContentDetector = _import_scenedetect()

        try:
            video = open_video(video_path)
        except Exception as exc:
            logger.error("SceneAnalyzer: could not open %s — %s", video_path, exc)
            return []

        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=self.threshold))

        try:
            scene_manager.detect_scenes(video, show_progress=False)
        except Exception as exc:
            logger.error("SceneAnalyzer: detection failed — %s", exc)
            return []

        scene_list = scene_manager.get_scene_list()
        results: List[Dict[str, Any]] = []
        fps = video.frame_rate if hasattr(video, "frame_rate") else 30.0

        for start_tc, end_tc in scene_list:
            start_s = start_tc.get_seconds()
            end_s = end_tc.get_seconds()
            if (end_s - start_s) < self.min_scene_len_s:
                continue
            results.append({
                "start_time": round(start_s, 3),
                "end_time": round(end_s, 3),
            })

        logger.info("SceneAnalyzer: %d scenes detected in %s",
                    len(results), video_path)
        return results
