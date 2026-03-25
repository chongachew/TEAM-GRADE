"""
Hybrid Pose Detection: MediaPipe PoseLandmarker (primary) + YOLO Pose (fallback).

Environment variable ``POSE_DETECTOR_MODE`` controls detector selection:
  - ``auto`` (default): Try MediaPipe first; fall back to YOLO on failure.
  - ``mediapipe``: Use MediaPipe only; raise if unavailable.
  - ``yolo``: Use YOLO only; raise if unavailable.

Output landmark dict format (unified across both detectors)::

    {
        "landmarks": [
            {"x": float, "y": float, "z": float, "visibility": float, "name": str},
            ...
        ],
        "confidence": float,
        "detector": "mediapipe" | "yolo"
    }

An empty dict ``{}`` is returned when no person is detected in a frame.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MEDIAPIPE_MODEL_URLS: Dict[str, str] = {
    "lite": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_lite/float16/latest/"
        "pose_landmarker_lite.task"
    ),
    "full": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_full/float16/latest/"
        "pose_landmarker_full.task"
    ),
    "heavy": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_heavy/float16/latest/"
        "pose_landmarker_heavy.task"
    ),
}

# Canonical landmark names for MediaPipe's 33-point model.
_MEDIAPIPE_LANDMARK_NAMES: List[str] = [
    "nose", "left_eye_inner", "left_eye", "left_eye_outer",
    "right_eye_inner", "right_eye", "right_eye_outer",
    "left_ear", "right_ear",
    "mouth_left", "mouth_right",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_pinky", "right_pinky",
    "left_index", "right_index",
    "left_thumb", "right_thumb",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
    "left_heel", "right_heel",
    "left_foot_index", "right_foot_index",
]

# COCO 17-point keypoint names used by YOLO Pose.
_YOLO_LANDMARK_NAMES: List[str] = [
    "nose",
    "left_eye", "right_eye",
    "left_ear", "right_ear",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
]

# Directory where .task model files are cached.
_MODELS_DIR = Path(__file__).parent.parent / "models" / "mediapipe"


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

def ensure_pose_model(complexity: str = "lite") -> str:
    """Download the MediaPipe PoseLandmarker model if not already cached.

    Args:
        complexity: One of ``"lite"``, ``"full"``, or ``"heavy"``.

    Returns:
        Absolute path to the cached ``.task`` file.

    Raises:
        ValueError: If ``complexity`` is not a recognised value.
        OSError: If the download fails and no cached copy exists.
    """
    if complexity not in _MEDIAPIPE_MODEL_URLS:
        raise ValueError(
            f"Unknown model complexity '{complexity}'. "
            f"Choose from: {list(_MEDIAPIPE_MODEL_URLS)}"
        )

    url = _MEDIAPIPE_MODEL_URLS[complexity]
    filename = f"pose_landmarker_{complexity}.task"
    dest = _MODELS_DIR / filename

    if dest.exists():
        logger.debug("MediaPipe model already cached at %s", dest)
        return str(dest)

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("📥 Downloading MediaPipe pose model (%s)…", complexity)
    logger.info("    URL: %s", url)

    try:
        urllib.request.urlretrieve(url, dest)  # noqa: S310 — URL is a trusted constant
        if dest.stat().st_size == 0:
            dest.unlink()
            raise OSError("Downloaded file is empty (0 bytes).")
        logger.info("✅ Download complete — model cached at %s", dest)
    except Exception as exc:
        # Remove partially downloaded file if present.
        if dest.exists():
            dest.unlink()
        raise OSError(
            f"Failed to download MediaPipe model from {url}: {exc}\n"
            f"You can manually download it and place it at: {dest}"
        ) from exc

    return str(dest)


# ---------------------------------------------------------------------------
# MediaPipe detector
# ---------------------------------------------------------------------------

class MediaPipePoseDetector:
    """Pose detector backed by MediaPipe PoseLandmarker (Tasks API, ≥0.10)."""

    def __init__(self, model_complexity: str = "lite") -> None:
        """Initialise the detector, downloading the model if necessary.

        Args:
            model_complexity: ``"lite"``, ``"full"``, or ``"heavy"``.

        Raises:
            RuntimeError: If MediaPipe is unavailable or initialisation fails.
        """
        try:
            import mediapipe as mp  # noqa: PLC0415
            from mediapipe.tasks.python import vision as mp_vision  # noqa: PLC0415
            from mediapipe.tasks.python.core import base_options as mp_base  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "mediapipe is not installed. Add 'mediapipe>=0.10' to requirements.txt."
            ) from exc

        model_path = ensure_pose_model(model_complexity)

        options = mp_vision.PoseLandmarkerOptions(
            base_options=mp_base.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_score=0.5,
            min_tracking_confidence=0.5,
        )

        self._detector = mp_vision.PoseLandmarker.create_from_options(options)
        self._mp_image_cls = mp.Image
        self._mp_image_format = mp.ImageFormat.SRGB
        logger.info("🎯 Using MediaPipe for pose detection (complexity=%s)", model_complexity)

    def detect_pose(self, frame: np.ndarray) -> dict:
        """Detect pose landmarks in a single BGR frame.

        Args:
            frame: BGR image as a NumPy array (H×W×3).

        Returns:
            Landmark dict or empty dict if no person detected.
        """
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp_image_cls(
            image_format=self._mp_image_format, data=rgb
        )
        result = self._detector.detect(mp_image)

        if not result.pose_landmarks:
            return {}

        pose = result.pose_landmarks[0]
        landmarks = [
            {
                "x": float(lm.x),
                "y": float(lm.y),
                "z": float(lm.z),
                "visibility": float(getattr(lm, "visibility", 1.0)),
                "name": name,
            }
            for lm, name in zip(pose, _MEDIAPIPE_LANDMARK_NAMES)
        ]

        confidence = float(
            np.mean([lm["visibility"] for lm in landmarks])
        )

        return {
            "landmarks": landmarks,
            "confidence": confidence,
            "detector": "mediapipe",
        }

    def close(self) -> None:
        """Release detector resources."""
        self._detector.close()


# ---------------------------------------------------------------------------
# YOLO detector
# ---------------------------------------------------------------------------

class YOLOPoseDetector:
    """Pose detector backed by YOLO v8 Pose (ultralytics)."""

    def __init__(self, model_name: str = "yolov8n-pose.pt") -> None:
        """Initialise the YOLO pose detector.

        Args:
            model_name: YOLO model weights filename (auto-downloaded by
                ultralytics on first use).

        Raises:
            RuntimeError: If ultralytics is not installed.
        """
        try:
            from ultralytics import YOLO  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed. Add 'ultralytics' to requirements.txt."
            ) from exc

        self._model = YOLO(model_name)
        logger.info("🔄 Using YOLO Pose for detection (model=%s)", model_name)

    def detect_pose(self, frame: np.ndarray) -> dict:
        """Detect pose keypoints in a single BGR frame.

        Args:
            frame: BGR image as a NumPy array (H×W×3).

        Returns:
            Landmark dict or empty dict if no person detected.
        """
        results = self._model(frame, verbose=False)

        if not results or results[0].keypoints is None:
            return {}

        kp_data = results[0].keypoints
        # kp_data.xy  shape: (num_persons, 17, 2)
        # kp_data.conf shape: (num_persons, 17)  (may be None for older models)
        if kp_data.xy is None or len(kp_data.xy) == 0:
            return {}

        xy = kp_data.xy[0].cpu().numpy()  # (17, 2)
        conf = (
            kp_data.conf[0].cpu().numpy()
            if kp_data.conf is not None
            else np.ones(len(xy), dtype=float)
        )

        h, w = frame.shape[:2]
        landmarks = [
            {
                "x": float(xy[i, 0]) / w,
                "y": float(xy[i, 1]) / h,
                "z": 0.0,
                "visibility": float(conf[i]),
                "name": name,
            }
            for i, name in enumerate(_YOLO_LANDMARK_NAMES)
            if i < len(xy)
        ]

        if not landmarks:
            return {}

        confidence = float(np.mean([lm["visibility"] for lm in landmarks]))

        return {
            "landmarks": landmarks,
            "confidence": confidence,
            "detector": "yolo",
        }


# ---------------------------------------------------------------------------
# Smart factory
# ---------------------------------------------------------------------------

class PoseDetector:
    """Unified pose detector with automatic MediaPipe → YOLO fallback.

    The detector to use can be overridden at runtime via the environment
    variable ``POSE_DETECTOR_MODE``:

    - ``auto`` (default): Try MediaPipe; fall back to YOLO.
    - ``mediapipe``: MediaPipe only.
    - ``yolo``: YOLO only.
    """

    def __init__(
        self,
        prefer_mediapipe: bool = True,
        model_complexity: str = "lite",
    ) -> None:
        mode = os.environ.get("POSE_DETECTOR_MODE", "auto").lower()

        self._detector: MediaPipePoseDetector | YOLOPoseDetector
        self._using_yolo = False

        if mode == "yolo":
            self._detector = YOLOPoseDetector()
            self._using_yolo = True
            return

        if mode == "mediapipe":
            self._detector = MediaPipePoseDetector(model_complexity)
            return

        # mode == "auto" (or unrecognised value treated as auto)
        if prefer_mediapipe:
            try:
                self._detector = MediaPipePoseDetector(model_complexity)
            except Exception as exc:
                logger.warning(
                    "⚠️  MediaPipe failed (%s) — falling back to YOLO.", exc
                )
                try:
                    self._detector = YOLOPoseDetector()
                    self._using_yolo = True
                except Exception as yolo_exc:
                    raise RuntimeError(
                        "❌ Both MediaPipe and YOLO detectors are unavailable. "
                        "Ensure at least one of 'mediapipe' or 'ultralytics' is installed."
                    ) from yolo_exc
        else:
            try:
                self._detector = YOLOPoseDetector()
                self._using_yolo = True
            except Exception as exc:
                logger.warning("⚠️  YOLO failed (%s) — trying MediaPipe.", exc)
                self._detector = MediaPipePoseDetector(model_complexity)

    def detect(self, frame: np.ndarray) -> dict:
        """Run pose detection on a single BGR frame.

        Args:
            frame: BGR image as a NumPy array (H×W×3).

        Returns:
            Unified landmark dict or empty dict if no person detected.
        """
        return self._detector.detect_pose(frame)

    def is_using_yolo(self) -> bool:
        """Return True when the YOLO fallback detector is active."""
        return self._using_yolo

    def close(self) -> None:
        """Release underlying detector resources (MediaPipe only)."""
        if hasattr(self._detector, "close"):
            self._detector.close()


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

class PoseExtractionStage:
    """Pipeline stage that runs pose detection across all extracted frames.

    Reads frames from ``frames_dir``, detects poses, and writes results to
    ``<frames_dir>/poses.json``.

    poses.json schema::

        {
            "video_id": str,
            "detector": "mediapipe" | "yolo",
            "frame_count": int,
            "poses": {
                "<frame_filename>": {
                    "landmarks": [...],
                    "confidence": float,
                    "detector": str
                },
                ...
            }
        }
    """

    _SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

    def __init__(
        self,
        prefer_mediapipe: bool = True,
        model_complexity: str = "lite",
    ) -> None:
        self._prefer_mediapipe = prefer_mediapipe
        self._model_complexity = model_complexity

    def process(self, video_id: str, frames_dir: str) -> str:
        """Detect poses in all frames under ``frames_dir``.

        Args:
            video_id: Identifier for the video (used in output metadata).
            frames_dir: Directory containing extracted frame image files.

        Returns:
            Path to the written ``poses.json`` file.

        Raises:
            FileNotFoundError: If ``frames_dir`` does not exist.
            RuntimeError: If no pose detector is available.
        """
        frames_path = Path(frames_dir)
        if not frames_path.exists():
            raise FileNotFoundError(f"frames_dir not found: {frames_dir}")

        frame_files = sorted(
            p for p in frames_path.iterdir()
            if p.suffix.lower() in self._SUPPORTED_EXTENSIONS
        )

        if not frame_files:
            logger.warning("No frame images found in %s", frames_dir)

        detector = PoseDetector(
            prefer_mediapipe=self._prefer_mediapipe,
            model_complexity=self._model_complexity,
        )
        active_detector = "yolo" if detector.is_using_yolo() else "mediapipe"
        logger.info(
            "Processing %d frames with %s detector…",
            len(frame_files),
            active_detector,
        )

        poses: Dict[str, dict] = {}
        start_time = time.time()

        for idx, frame_file in enumerate(frame_files):
            frame = cv2.imread(str(frame_file))
            if frame is None:
                logger.warning(
                    "Could not read frame %s — file may be corrupted or in an "
                    "unsupported format — skipping.",
                    frame_file.name,
                )
                continue

            try:
                result = detector.detect(frame)
            except Exception as exc:
                logger.warning(
                    "Error processing frame %s (index %d): %s — skipping.",
                    frame_file.name,
                    idx,
                    exc,
                )
                continue

            if result:
                poses[frame_file.name] = result

            if (idx + 1) % 1000 == 0:
                elapsed = time.time() - start_time
                fps = (idx + 1) / elapsed if elapsed > 0 else 0
                logger.info(
                    "  %d / %d frames processed (%.1f fps)",
                    idx + 1,
                    len(frame_files),
                    fps,
                )

        detector.close()

        output = {
            "video_id": video_id,
            "detector": active_detector,
            "frame_count": len(frame_files),
            "poses": poses,
        }

        output_path = frames_path / "poses.json"
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2)

        elapsed_total = time.time() - start_time
        logger.info(
            "✅ Pose extraction complete — %d / %d frames with poses, "
            "written to %s (%.1f s total).",
            len(poses),
            len(frame_files),
            output_path,
            elapsed_total,
        )

        return str(output_path)
