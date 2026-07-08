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
        Falls back to YOLO if primary model fails.
        
        Raises:
            ValueError: If all models fail to load
        """
        try:
            if self.model_name == "mediapipe":
                try:
                    self._load_mediapipe()
                except Exception as mp_e:
                    logger.warning(f"MediaPipe loading failed: {mp_e}. Falling back to YOLO...")
                    self._load_yolo_pose()
                    logger.info("Switched to YOLO Pose due to MediaPipe unavailability")
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
        """Load MediaPipe pose model using tasks API (0.10.x)."""
        try:
            import os
            import urllib.request
            from pathlib import Path
            
            # Try new tasks API first
            try:
                from mediapipe.tasks.python import vision
                from mediapipe.tasks.python.core.base_options import BaseOptions
                import mediapipe as mp
                
                # Download model if not already cached
                model_dir = Path.home() / ".mediapipe" / "models"
                model_dir.mkdir(parents=True, exist_ok=True)
                model_path = model_dir / "pose_landmarker.task"
                
                if not model_path.exists():
                    logger.info("Downloading MediaPipe pose model...")
                    model_url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker/float16/1/pose_landmarker.task"
                    try:
                        urllib.request.urlretrieve(model_url, str(model_path))
                        logger.info(f"Model downloaded to {model_path}")
                    except Exception as download_e:
                        logger.warning(f"Failed to download model: {download_e}. This is OK for CPU inference.")
                        # Model download is optional - tasks API can work without it in some cases
                
                # Create pose landmarker
                if model_path.exists():
                    base_options = BaseOptions(model_asset_path=str(model_path))
                    options = vision.PoseLandmarkerOptions(
                        base_options=base_options,
                        running_mode=vision.RunningMode.IMAGE,
                        num_poses=1
                    )
                    self.pose_landmarker = vision.PoseLandmarker.create_from_options(options)
                else:
                    # Try creating without model path as fallback
                    base_options = BaseOptions()
                    options = vision.PoseLandmarkerOptions(
                        base_options=base_options,
                        running_mode=vision.RunningMode.IMAGE,
                        num_poses=1
                    )
                    self.pose_landmarker = vision.PoseLandmarker.create_from_options(options)
                
                self.use_tasks_api = True
                self.vision_module = vision
                logger.info("Loaded MediaPipe PoseLandmarker (tasks API 0.10.x)")
                return
                
            except ImportError as import_e:
                logger.warning(f"Tasks API import failed: {import_e}. Trying legacy API...")
            except Exception as tasks_e:
                logger.warning(f"Tasks API loading failed: {tasks_e}. Trying legacy API...")
            
            # Fallback to legacy solutions API
            import mediapipe as mp
            if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'pose'):
                self.model = mp.solutions.pose.Pose(
                    static_image_mode=False,
                    model_complexity=1,
                    smooth_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
                self.use_tasks_api = False
                logger.info("Loaded MediaPipe Pose (legacy solutions API)")
            else:
                raise ValueError("MediaPipe pose not available - all APIs failed")
                
        except Exception as e:
            logger.error(f"Failed to load MediaPipe: {e}")
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
        Run MediaPipe pose estimation using either tasks API or legacy API.
        
        Args:
            frame: Input frame (BGR)
            
        Returns:
            Pose keypoints dictionary
        """
        import cv2
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
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
        
        keypoints = {}
        
        # Use tasks API if available
        if hasattr(self, 'use_tasks_api') and self.use_tasks_api and hasattr(self, 'pose_landmarker'):
            try:
                # Convert frame to tasks API format
                # MediaPipe tasks API expects RGB uint8 or TensorImage
                import mediapipe as mp
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                
                # Run pose detection
                results = self.pose_landmarker.detect(image)
                
                # Extract keypoints from first person
                if results.pose_landmarks and len(results.pose_landmarks) > 0:
                    for name, landmark in zip(landmark_names, results.pose_landmarks[0]):
                        keypoints[name] = {
                            "x": float(landmark.x),
                            "y": float(landmark.y),
                            "z": float(landmark.z),
                            "confidence": float(landmark.visibility) if hasattr(landmark, 'visibility') else 1.0
                        }
                logger.debug(f"Tasks API: detected {len(keypoints)} keypoints")
            except Exception as e:
                logger.warning(f"Tasks API inference failed: {e}. Trying legacy API...")
                keypoints = {}
        
        # Fallback to legacy API if tasks failed or not available
        if not keypoints and hasattr(self, 'model') and self.model is not None:
            try:
                results = self.model.process(frame_rgb)
                
                if results.pose_landmarks:
                    for name, landmark in zip(landmark_names, results.pose_landmarks.landmark):
                        keypoints[name] = {
                            "x": float(landmark.x),
                            "y": float(landmark.y),
                            "z": float(landmark.z),
                            "confidence": float(landmark.visibility)
                        }
                logger.debug(f"Legacy API: detected {len(keypoints)} keypoints")
            except Exception as e:
                logger.warning(f"Legacy API inference failed: {e}")
                keypoints = {}
        
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
            # Convert GPU tensor to CPU numpy array immediately
            kpts_tensor = results[0].keypoints.data[0]
            try:
                # Handle GPU tensor: convert to CPU, then to numpy
                if hasattr(kpts_tensor, 'cpu'):
                    kpts = kpts_tensor.cpu().numpy()
                else:
                    kpts = np.asarray(kpts_tensor)
            except Exception as e:
                logger.warning(f"Tensor conversion failed: {e}. Skipping frame.")
                return self._empty_pose_result()
            
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
        # Clean up tasks API landmarker
        if hasattr(self, 'pose_landmarker'):
            try:
                self.pose_landmarker.close()
            except Exception:
                pass
        
        # Clean up legacy model
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
