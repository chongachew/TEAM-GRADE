"""
Pose Estimation Pipeline Stage
================================
Implements hybrid pose detection using MediaPipe (primary) with YOLO fallback.
Logs stage status to Firebase Firestore using the existing db client.
"""

import json
import logging
import os
import urllib.request
from typing import Dict, List, Optional

import cv2
import numpy as np
from google.cloud import firestore

# ============ EXISTING FIREBASE CLIENT ============
# Import the shared db instance — DO NOT create a new one.
from Ingestion.firestore_utils import db

logger = logging.getLogger(__name__)

# ============ MODEL MANAGEMENT ============

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "mediapipe")

POSE_MODELS = {
    "lite": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
    ),
    "full": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task"
    ),
    "heavy": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
    ),
}


def ensure_pose_model(complexity: str = "lite") -> str:
    """Download and cache a MediaPipe pose landmarker model.

    Args:
        complexity: One of ``"lite"``, ``"full"``, or ``"heavy"``.

    Returns:
        Absolute path to the cached ``.task`` file.

    Raises:
        ValueError: If *complexity* is not recognised.
        RuntimeError: If the download fails.
    """
    if complexity not in POSE_MODELS:
        raise ValueError(
            f"Unknown model complexity: '{complexity}'. Choose from: {list(POSE_MODELS)}"
        )

    os.makedirs(MODELS_DIR, exist_ok=True)
    model_name = f"pose_landmarker_{complexity}.task"
    local_path = os.path.join(MODELS_DIR, model_name)

    if os.path.exists(local_path):
        logger.info(f"✅ Pose model cached: {local_path}")
        return local_path

    url = POSE_MODELS[complexity]
    logger.info(f"📥 Downloading pose model ({complexity}) from {url} …")
    try:
        urllib.request.urlretrieve(url, local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        logger.info(f"✅ Model saved: {model_name} ({size_mb:.1f} MB)")
        return local_path
    except Exception as exc:
        logger.error(f"❌ Download failed: {exc}")
        raise RuntimeError(
            f"Failed to download pose model.\n"
            f"  URL : {url}\n"
            f"  Save to: {local_path}\n"
            f"  Manual download: curl -o '{local_path}' '{url}'"
        ) from exc


# ============ MEDIAPIPE DETECTOR ============

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


class MediaPipePoseDetector:
    """Pose detector backed by MediaPipe PoseLandmarker (Tasks API)."""

    def __init__(self, model_complexity: str = "lite") -> None:
        """Initialise the PoseLandmarker.

        Args:
            model_complexity: Model variant — ``"lite"``, ``"full"``, or ``"heavy"``.

        Raises:
            ImportError: If mediapipe is not installed.
            RuntimeError: If the model cannot be loaded.
        """
        if not MEDIAPIPE_AVAILABLE:
            raise ImportError(
                "mediapipe is not installed. Run: pip install 'mediapipe>=0.10'"
            )

        model_path = ensure_pose_model(model_complexity)

        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = mp_vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            output_segmentation_masks=False,
        )
        self.detector = mp_vision.PoseLandmarker.create_from_options(options)
        logger.info(f"✅ MediaPipe PoseLandmarker initialised ({model_complexity})")

    def detect_pose(self, frame: np.ndarray) -> dict:
        """Detect pose landmarks in a BGR frame.

        Args:
            frame: BGR image as a NumPy array (H x W x 3).

        Returns:
            ``{"landmarks": list, "confidence": float}`` or ``{}`` when no
            pose is detected.
        """
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self.detector.detect(mp_image)

        if not result.pose_landmarks:
            return {}

        landmarks = result.pose_landmarks[0]
        serialised = [
            {"x": lm.x, "y": lm.y, "z": lm.z, "visibility": lm.visibility}
            for lm in landmarks
        ]
        confidence = float(landmarks[0].presence)
        return {"landmarks": serialised, "confidence": confidence}


# ============ YOLO DETECTOR ============

try:
    from ultralytics import YOLO

    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


class YOLOPoseDetector:
    """Pose detector backed by YOLOv8-pose (ultralytics)."""

    def __init__(self) -> None:
        """Initialise YOLO pose model.

        Raises:
            ImportError: If ultralytics is not installed.
        """
        if not YOLO_AVAILABLE:
            raise ImportError(
                "ultralytics is not installed. Run: pip install ultralytics"
            )
        self.model = YOLO("yolov8l-pose.pt")
        logger.info("✅ YOLO pose detector initialised")

    def detect_pose(self, frame: np.ndarray) -> dict:
        """Detect pose keypoints in a BGR frame.

        Args:
            frame: BGR image as a NumPy array (H x W x 3).

        Returns:
            ``{"landmarks": list, "confidence": float}`` or ``{}`` when no
            pose is detected.
        """
        results = self.model(frame, verbose=False)

        if not results or results[0].keypoints is None:
            return {}

        keypoints = results[0].keypoints
        if len(keypoints.xy) == 0:
            return {}

        xy = keypoints.xy[0].tolist()
        conf_mean = (
            float(keypoints.conf[0].mean())
            if keypoints.conf is not None
            else 0.0
        )
        serialised = [{"x": pt[0], "y": pt[1]} for pt in xy]
        return {"landmarks": serialised, "confidence": conf_mean}


# ============ SMART FACTORY ============


class PoseDetector:
    """Smart pose detector that tries MediaPipe and falls back to YOLO."""

    def __init__(
        self,
        prefer_mediapipe: bool = True,
        model_complexity: str = "lite",
    ) -> None:
        """Initialise the best available detector.

        Args:
            prefer_mediapipe: When ``True`` (default), MediaPipe is tried first.
            model_complexity: Complexity passed to :class:`MediaPipePoseDetector`.

        Raises:
            RuntimeError: If no detector can be initialised.
        """
        self._use_yolo = False
        self._detector = None

        if prefer_mediapipe and MEDIAPIPE_AVAILABLE:
            try:
                self._detector = MediaPipePoseDetector(model_complexity)
                logger.info("🎯 Using MediaPipe for pose detection")
                return
            except Exception as exc:
                logger.warning(f"⚠️ MediaPipe initialisation failed: {exc}")

        if YOLO_AVAILABLE:
            logger.info("🔄 Falling back to YOLO for pose detection")
            self._detector = YOLOPoseDetector()
            self._use_yolo = True
            return

        raise RuntimeError(
            "❌ No pose detector available. "
            "Install mediapipe (pip install 'mediapipe>=0.10') or "
            "ultralytics (pip install ultralytics)."
        )

    def detect(self, frame: np.ndarray) -> dict:
        """Run pose detection on *frame*.

        Args:
            frame: BGR image as a NumPy array.

        Returns:
            Detection result dict (may be empty if no pose found).
        """
        return self._detector.detect_pose(frame)

    def is_using_yolo(self) -> bool:
        """Return ``True`` if the YOLO fallback is active."""
        return self._use_yolo


# ============ FIREBASE LOGGING ============


def update_firebase_queue_status(
    video_id: str,
    stage: str,
    status: str,
    metadata: Optional[Dict] = None,
) -> None:
    """Update a queue document in Firestore using the existing ``db`` client.

    Document path: ``videos/{video_id}/queue/{stage}``

    Args:
        video_id: Firestore document ID for the video.
        stage: Pipeline stage name (e.g. ``"pose_stage"``).
        status: Status string (e.g. ``"processing"`` or ``"completed"``).
        metadata: Optional dict of additional fields to store.
    """
    try:
        queue_ref = (
            db.collection("videos")
            .document(video_id)
            .collection("queue")
            .document(stage)
        )
        update_data: Dict = {
            "status": status,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        if metadata:
            update_data["metadata"] = metadata
        queue_ref.set(update_data, merge=True)
        logger.info(f"🔥 Firebase [{stage}] → {status}")
    except Exception as exc:
        logger.error(f"Firebase update failed (continuing): {exc}")


# ============ PIPELINE STAGE ============


class PoseExtractionStage:
    """Pipeline stage that extracts pose landmarks from video frames."""

    def __init__(self) -> None:
        mode = os.environ.get("POSE_DETECTOR_MODE", "auto").lower()

        if mode == "mediapipe":
            # Only MediaPipe; raises RuntimeError if unavailable.
            self.detector = PoseDetector(prefer_mediapipe=True)
        elif mode == "yolo":
            # Only YOLO; raises RuntimeError if ultralytics is not installed.
            self.detector = PoseDetector(prefer_mediapipe=False)
        else:
            self.detector = PoseDetector(prefer_mediapipe=True)

        detector_name = "YOLO" if self.detector.is_using_yolo() else "MediaPipe"
        logger.info(f"🎯 PoseExtractionStage ready — detector: {detector_name}")

    def process(self, video_id: str, frames_dir: str) -> str:
        """Extract pose from every frame in *frames_dir*.

        Args:
            video_id: Identifier used for Firebase logging.
            frames_dir: Directory containing frame image files.

        Returns:
            Absolute path to the written ``poses.json`` file.
        """
        update_firebase_queue_status(video_id, "pose_stage", "processing")

        frame_files: List[str] = sorted(
            (f for f in os.listdir(frames_dir)
             if f.lower().endswith((".jpg", ".jpeg", ".png"))),
            key=lambda name: (
                int("".join(filter(str.isdigit, name)) or "0"),
                name,
            ),
        )
        total = len(frame_files)
        logger.info(f"🔄 Starting pose extraction — {total} frames")

        poses_data = []
        detected_count = 0

        for i, frame_file in enumerate(frame_files):
            frame_path = os.path.join(frames_dir, frame_file)
            frame = cv2.imread(frame_path)

            if frame is None:
                logger.warning(f"⚠️ Could not load frame: {frame_file}")
                poses_data.append(None)
                continue

            try:
                result = self.detector.detect(frame)
                poses_data.append(result if result else None)
                if result:
                    detected_count += 1
            except Exception as exc:
                logger.error(f"❌ Pose detection failed on {frame_file}: {exc}")
                poses_data.append(None)

            if (i + 1) % 100 == 0:
                logger.info(
                    f"🔄 Frame {i + 1}/{total} — "
                    f"{detected_count} poses detected so far"
                )

        output_path = os.path.join(frames_dir, "poses.json")
        with open(output_path, "w") as fh:
            json.dump(poses_data, fh)

        metadata = {
            "total_frames": total,
            "detected_frames": detected_count,
            "detector": "yolo" if self.detector.is_using_yolo() else "mediapipe",
            "output_path": output_path,
        }
        update_firebase_queue_status(video_id, "pose_stage", "completed", metadata)

        logger.info(
            f"✅ Pose extraction complete — "
            f"{detected_count}/{total} frames with poses → {output_path}"
        )
        return output_path
