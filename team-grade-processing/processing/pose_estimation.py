"""
Pose Estimation Module
Handles loading pose models and running inference on video frames.
Outputs pose keypoints in standardized JSON format.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)


class PoseEstimator:
    """
    Wrapper for pose estimation models.
    Supports various backends (OpenPose, MediaPipe, YOLO-Pose, etc.)
    """

    def __init__(self, model_name: str = "mediapipe", device: str = "cpu"):
        """
        Initialize pose estimator with specified backend.

        Args:
            model_name: Name of pose model ('mediapipe', 'yolo', 'openpose')
            device: Compute device ('cpu', 'cuda')
        """
        self.model_name = model_name
        self.device = device
        self.model = None
        self._load_model()

    def _load_model(self) -> None:
        """
        Load pose estimation model based on configuration.
        
        Raises:
            ValueError: If model_name is not supported
        """
        try:
            if self.model_name == "mediapipe":
                self._load_mediapipe()
            elif self.model_name == "yolo":
                self._load_yolo_pose()
            elif self.model_name == "openpose":
                self._load_openpose()
            else:
                raise ValueError(f"Unsupported model: {self.model_name}")
            
            logger.info(f"Loaded {self.model_name} model on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load pose model: {e}")
            raise

    def _load_mediapipe(self) -> None:
        """Load MediaPipe pose model."""
        try:
            import mediapipe as mp
            self.model = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                smooth_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
        except ImportError:
            logger.error("MediaPipe not installed. Install with: pip install mediapipe")
            raise

    def _load_yolo_pose(self) -> None:
        """Load YOLO Pose model."""
        try:
            from ultralytics import YOLO
            self.model = YOLO("yolov8n-pose.pt")
        except ImportError:
            logger.error("YOLO not installed. Install with: pip install ultralytics")
            raise

    def _load_openpose(self) -> None:
        """Load OpenPose model (placeholder)."""
        logger.warning("OpenPose support not implemented. Using MediaPipe as fallback.")
        self._load_mediapipe()

    def estimate_pose(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Estimate pose keypoints from a single frame.

        Args:
            frame: Video frame (BGR, numpy array)

        Returns:
            Dictionary containing:
            {
                "keypoints": {
                    "nose": {"x": float, "y": float, "z": float, "confidence": float},
                    "left_shoulder": {...},
                    ...
                },
                "num_persons": int,
                "timestamp": float
            }
        """
        try:
            if self.model_name == "mediapipe":
                return self._estimate_mediapipe(frame)
            elif self.model_name == "yolo":
                return self._estimate_yolo(frame)
            else:
                raise ValueError(f"Unsupported model: {self.model_name}")
        except Exception as e:
            logger.error(f"Pose estimation failed: {e}")
            return self._empty_pose_result()

    def _estimate_mediapipe(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Run MediaPipe pose estimation.
        
        Args:
            frame: Input frame (BGR)
            
        Returns:
            Pose keypoints dictionary
        """
        import cv2
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.model.process(frame_rgb)
        
        keypoints = {}
        if results.pose_landmarks:
            # Standard 33-point MediaPipe format
            landmark_names = [
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
                "left_foot_index", "right_foot_index"
            ]
            
            for name, landmark in zip(landmark_names, results.pose_landmarks.landmark):
                keypoints[name] = {
                    "x": float(landmark.x),
                    "y": float(landmark.y),
                    "z": float(landmark.z),
                    "confidence": float(landmark.visibility)
                }
        
        return {
            "keypoints": keypoints,
            "num_persons": 1 if keypoints else 0,
            "timestamp": 0.0
        }

    def _estimate_yolo(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Run YOLO Pose estimation.
        
        Args:
            frame: Input frame (BGR)
            
        Returns:
            Pose keypoints dictionary
        """
        results = self.model(frame, verbose=False)
        keypoints = {}
        
        if results and results[0].keypoints:
            kpts = results[0].keypoints.data[0]  # First person
            # YOLO 17-point format
            yolo_names = [
                "nose", "left_eye", "right_eye", "left_ear", "right_ear",
                "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
                "left_wrist", "right_wrist", "left_hip", "right_hip",
                "left_knee", "right_knee", "left_ankle", "right_ankle"
            ]
            
            for idx, name in enumerate(yolo_names):
                if idx < len(kpts):
                    keypoints[name] = {
                        "x": float(kpts[idx, 0] / frame.shape[1]),  # Normalize
                        "y": float(kpts[idx, 1] / frame.shape[0]),
                        "z": 0.0,  # YOLO doesn't provide z
                        "confidence": float(kpts[idx, 2])
                    }
        
        return {
            "keypoints": keypoints,
            "num_persons": 1 if keypoints else 0,
            "timestamp": 0.0
        }

    def _empty_pose_result(self) -> Dict[str, Any]:
        """Return empty pose result."""
        return {
            "keypoints": {},
            "num_persons": 0,
            "timestamp": 0.0
        }

    def batch_estimate(self, frames: List[np.ndarray]) -> List[Dict[str, Any]]:
        """
        Estimate poses for multiple frames.

        Args:
            frames: List of video frames

        Returns:
            List of pose dictionaries
        """
        results = []
        for frame in frames:
            result = self.estimate_pose(frame)
            results.append(result)
        
        logger.info(f"Processed {len(frames)} frames")
        return results

    def close(self) -> None:
        """Clean up model resources."""
        if self.model and hasattr(self.model, 'close'):
            self.model.close()
        logger.info("Pose estimator closed")


def resize_frame(frame: np.ndarray, max_width: int = 1280) -> np.ndarray:
    """
    Resize frame to maximum width while maintaining aspect ratio.
    
    Args:
        frame: Input frame
        max_width: Maximum width in pixels
        
    Returns:
        Resized frame
    """
    import cv2
    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / width
        new_height = int(height * scale)
        frame = cv2.resize(frame, (max_width, new_height))
    return frame
