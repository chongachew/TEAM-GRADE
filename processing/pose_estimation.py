import os
import cv2
import json
import logging
import urllib.request
from datetime import datetime
from typing import Dict, Optional

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

# ============ FIREBASE SETUP ============
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-key.json"))
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    FIREBASE_AVAILABLE = True
except Exception as e:
    logger.warning(f"Firebase not available: {e}")
    db = None
    FIREBASE_AVAILABLE = False

# ============ MODEL MANAGEMENT ============
MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "mediapipe")

POSE_MODELS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
}


def ensure_pose_model(complexity: str = "lite") -> str:
    """Download and cache pose model. Returns path to .task file."""
    os.makedirs(MODELS_DIR, exist_ok=True)

    model_name = f"pose_landmarker_{complexity}.task"
    local_path = os.path.join(MODELS_DIR, model_name)

    if os.path.exists(local_path):
        logger.info(f"Pose model cached: {local_path}")
        return local_path

    url = POSE_MODELS.get(complexity)
    if not url:
        raise ValueError(f"Unknown complexity: {complexity}. Use: lite, full, heavy")

    logger.info(f"Downloading pose model ({complexity})...")
    try:
        urllib.request.urlretrieve(url, local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        logger.info(f"Model saved: {model_name} ({size_mb:.1f} MB)")
        return local_path
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise RuntimeError(
            f"Failed to download pose model. URL: {url}\n"
            f"Manual download: {url}\n"
            f"Save to: {local_path}"
        )


# ============ MEDIAPIPE DETECTOR ============
try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


class MediaPipePoseDetector:
    def __init__(self, model_complexity: str = "lite"):
        """Initialize MediaPipe PoseLandmarker."""
        if not MEDIAPIPE_AVAILABLE:
            raise ImportError("mediapipe not installed")

        model_path = ensure_pose_model(model_complexity)

        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = mp_vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            output_segmentation_masks=False,
        )

        self.detector = mp_vision.PoseLandmarker.create_from_options(options)
        logger.info(f"PoseLandmarker initialized ({model_complexity})")

    def detect_pose(self, frame) -> dict:
        """Detect pose in frame. Returns landmarks or empty dict if no pose."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        result = self.detector.detect(mp_image)

        if not result.pose_landmarks:
            return {}

        landmarks = result.pose_landmarks[0]
        return {
            "landmarks": [
                {
                    "x": lm.x,
                    "y": lm.y,
                    "z": lm.z,
                    "visibility": lm.visibility,
                    "name": f"landmark_{i}",
                }
                for i, lm in enumerate(landmarks)
            ],
            "confidence": float(landmarks[0].visibility) if landmarks else 0.0,
            "detector": "mediapipe",
        }


# ============ YOLO DETECTOR (FALLBACK) ============
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


class YOLOPoseDetector:
    def __init__(self):
        if not YOLO_AVAILABLE:
            raise ImportError("ultralytics not installed")

        # Load model with device optimization
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Loading YOLO on device: {device}")
            self.model = YOLO("yolov8l-pose.pt")
            self.model.to(device)
        except Exception as e:
            logger.warning(f"Device optimization failed: {e}, using default")
            self.model = YOLO("yolov8l-pose.pt")

        logger.info("YOLO Pose detector initialized")

    def detect_pose(self, frame) -> dict:
        """Detect pose using YOLO with proper tensor conversion."""
        try:
            results = self.model(frame, verbose=False)

            if not results or not results[0].keypoints:
                return {}

            keypoints = results[0].keypoints

            # CRITICAL FIX: Convert GPU tensors to CPU numpy arrays
            try:
                xy = (
                    keypoints.xy[0].cpu().numpy()
                    if hasattr(keypoints.xy[0], "cpu")
                    else keypoints.xy[0]
                )
                conf = (
                    keypoints.conf[0].cpu().numpy()
                    if hasattr(keypoints.conf[0], "cpu")
                    else keypoints.conf[0]
                )
            except Exception as e:
                logger.warning(f"Tensor conversion failed: {e}, trying fallback")
                xy = keypoints.xy[0]
                conf = keypoints.conf[0]
                if hasattr(xy, "tolist"):
                    xy = xy.tolist()
                if hasattr(conf, "tolist"):
                    conf = conf.tolist()

            # Build landmarks with proper error handling.
            # pt may have fewer than 2 elements on malformed detections, so
            # guard each index access defensively.
            landmarks = []
            for i in range(len(xy)):
                try:
                    pt = xy[i]
                    c = conf[i] if i < len(conf) else 0.0
                    landmarks.append(
                        {
                            "x": float(pt[0]) if len(pt) > 0 else 0.0,
                            "y": float(pt[1]) if len(pt) > 1 else 0.0,
                            "z": 0.0,
                            "visibility": float(c) if c is not None else 0.0,
                            "name": f"landmark_{i}",
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to process landmark {i}: {e}")
                    landmarks.append(
                        {"x": 0, "y": 0, "z": 0, "visibility": 0, "name": f"landmark_{i}"}
                    )

            # Calculate mean confidence safely
            try:
                mean_conf = float(conf.mean()) if hasattr(conf, "mean") and len(conf) > 0 else 0.0
            except Exception:
                mean_conf = 0.0

            return {
                "landmarks": landmarks,
                "confidence": mean_conf,
                "detector": "yolo",
            }
        except Exception as e:
            logger.error(f"YOLO detection failed: {e}")
            return {}


# ============ SMART DETECTOR (MediaPipe + YOLO Fallback) ============
class PoseDetector:
    def __init__(self, prefer_mediapipe: bool = True, model_complexity: str = "lite"):
        self.use_yolo_fallback = False
        self.detector = None

        if prefer_mediapipe and MEDIAPIPE_AVAILABLE:
            try:
                self.detector = MediaPipePoseDetector(model_complexity=model_complexity)
                logger.info("Using MediaPipe for pose detection")
            except Exception as e:
                logger.warning(f"MediaPipe initialization failed: {e}")

        if self.detector is None:
            if YOLO_AVAILABLE:
                logger.info("Falling back to YOLO for pose detection")
                self.detector = YOLOPoseDetector()
                self.use_yolo_fallback = True
            else:
                raise ImportError("No pose detector available (MediaPipe and YOLO both unavailable)")

    def detect(self, frame) -> dict:
        return self.detector.detect_pose(frame)

    def is_using_yolo(self) -> bool:
        return self.use_yolo_fallback


# ============ FIREBASE LOGGING ============
def update_firebase_queue_status(video_id: str, stage: str, status: str, metadata: Dict = None):
    """Update queue item status in Firebase."""
    if not FIREBASE_AVAILABLE or not db:
        logger.warning("Firebase unavailable, skipping status update")
        return

    try:
        queue_ref = (
            db.collection("videos")
            .document(video_id)
            .collection("queue")
            .document(stage)
        )

        update_data = {
            "status": status,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        if status == "processing":
            update_data["started_at"] = firestore.SERVER_TIMESTAMP
        elif status == "completed":
            update_data["completed_at"] = firestore.SERVER_TIMESTAMP

        if metadata:
            update_data["metadata"] = metadata

        queue_ref.set(update_data, merge=True)
        logger.info(f"Firebase updated: {video_id}/{stage} -> {status}")

    except Exception as e:
        logger.error(f"Firebase update failed: {e}")


# ============ PIPELINE STAGE ============
class PoseExtractionStage:
    def __init__(self, model_complexity: str = "lite"):
        self.detector = PoseDetector(prefer_mediapipe=True, model_complexity=model_complexity)

    def process(self, video_id: str, frames_dir: str) -> str:
        """Extract pose from all frames with memory-efficient streaming."""

        # Mark as PROCESSING
        update_firebase_queue_status(video_id, "pose_stage", "processing")

        frame_files = sorted(os.listdir(frames_dir))
        detected_count = 0
        failed_frames = []

        # Stream writes to file instead of keeping all in memory
        output_path = os.path.join(frames_dir, "poses.json")

        with open(output_path, "w") as f:
            f.write("[\n")

            for i, frame_file in enumerate(frame_files):
                # Write separator before every entry except the first
                if i > 0:
                    f.write(",\n")

                frame_path = os.path.join(frames_dir, frame_file)
                frame = cv2.imread(frame_path)

                if frame is None:
                    logger.warning(f"Failed to load frame: {frame_file}")
                    f.write("  null")
                    failed_frames.append(frame_file)
                    continue

                try:
                    result = self.detector.detect(frame)
                    if result and "landmarks" in result:
                        detected_count += 1

                    f.write("  " + json.dumps(result if result else None))

                except Exception as e:
                    logger.error(f"Pose detection failed on {frame_file}: {e}")
                    f.write("  null")
                    failed_frames.append(frame_file)
                finally:
                    del frame  # Release frame memory immediately

                # Progress logging every 100 frames
                if (i + 1) % 100 == 0:
                    logger.info(
                        f"Processed {i + 1}/{len(frame_files)} frames "
                        f"({detected_count} poses detected)"
                    )

            f.write("\n]")

        logger.info(
            f"Pose extraction complete: {detected_count}/{len(frame_files)} frames with detected poses"
        )

        detection_rate = round(detected_count / len(frame_files) * 100, 2) if frame_files else 0

        completion_metadata = {
            "poses_detected": detected_count,
            "total_frames": len(frame_files),
            "detection_rate": detection_rate,
            "failed_frames": len(failed_frames),
            "detector_used": "yolo" if self.detector.is_using_yolo() else "mediapipe",
            "poses_json_path": output_path,
            "completed_at_utc": datetime.utcnow().isoformat(),
        }

        update_firebase_queue_status(video_id, "pose_stage", "completed", completion_metadata)

        logger.info(f"Pose stage completed for {video_id}")
        logger.info(f"   Detection rate: {detection_rate}%")
        logger.info(f"   Detector: {'YOLO' if self.detector.is_using_yolo() else 'MediaPipe'}")

        return output_path


# ============ MAIN ENTRY POINT ============
if __name__ == "__main__":
    video_id = "YfcuT_rHwAI"
    frames_dir = f"./frames/{video_id}"

    stage = PoseExtractionStage(model_complexity="lite")
    output_path = stage.process(video_id, frames_dir)
    print(f"Poses saved to: {output_path}")
