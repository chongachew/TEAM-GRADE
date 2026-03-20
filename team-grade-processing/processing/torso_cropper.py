"""
Torso Cropper Module
Extracts torso region from frames using pose keypoints for jersey OCR.
"""

import logging
from pathlib import Path
from typing import Dict, Tuple, Any, Optional
import numpy as np
import cv2

logger = logging.getLogger(__name__)


class TorsoCropper:
    """
    Crops torso region from frames using pose keypoints.
    Designed for jersey number detection and player identification.
    """

    # Torso keypoint indices (MediaPipe standard)
    TORSO_KEYPOINTS = [
        "nose", "left_shoulder", "right_shoulder",
        "left_hip", "right_hip", "left_elbow", "right_elbow"
    ]

    def __init__(self, torso_height_ratio: float = 0.6, torso_width_ratio: float = 0.5):
        """
        Initialize torso cropper with size ratios.

        Args:
            torso_height_ratio: Height of torso crop as fraction of bbox height
            torso_width_ratio: Width of torso crop as fraction of bbox width
        """
        self.torso_height_ratio = torso_height_ratio
        self.torso_width_ratio = torso_width_ratio

    def get_torso_box(
        self,
        keypoints: Dict[str, Dict[str, float]],
        frame_shape: Tuple[int, int, int]
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Calculate bounding box for torso region from pose keypoints.

        Args:
            keypoints: Dictionary of pose keypoints with x, y, z, confidence
            frame_shape: Frame dimensions (height, width, channels)

        Returns:
            Bounding box (x_min, y_min, x_max, y_max) in pixels, or None if detection failed

        Raises:
            ValueError: If keypoints dict is invalid
        """
        if not keypoints or not isinstance(keypoints, dict):
            logger.warning("Invalid keypoints format")
            return None

        height, width = frame_shape[:2]

        # Extract torso keypoints with confidence filtering
        valid_points = []
        for kp_name in self.TORSO_KEYPOINTS:
            if kp_name in keypoints:
                kp = keypoints[kp_name]
                confidence = kp.get("confidence", 0.0)
                
                if confidence > 0.3:  # Minimum confidence threshold
                    x = int(kp["x"] * width)
                    y = int(kp["y"] * height)
                    valid_points.append((x, y))

        if len(valid_points) < 3:  # Need at least 3 points for reasonable torso
            logger.warning(f"Insufficient valid keypoints: {len(valid_points)}")
            return None

        # Calculate bounding box from valid keypoints
        x_coords = [p[0] for p in valid_points]
        y_coords = [p[1] for p in valid_points]

        x_min = max(0, int(min(x_coords) - width * 0.05))  # 5% padding
        x_max = min(width, int(max(x_coords) + width * 0.05))
        y_min = max(0, int(min(y_coords) - height * 0.05))
        y_max = min(height, int(max(y_coords) + height * 0.15))  # More padding below

        # Enforce minimum size
        min_width, min_height = 64, 128
        if (x_max - x_min) < min_width or (y_max - y_min) < min_height:
            logger.debug("Torso box too small, applying defaults")
            x_center = (x_min + x_max) // 2
            y_center = (y_min + y_max) // 2
            x_min = max(0, x_center - min_width // 2)
            x_max = min(width, x_center + min_width // 2)
            y_min = max(0, y_center - min_height // 2)
            y_max = min(height, y_center + min_height // 2)

        logger.debug(f"Torso box: ({x_min}, {y_min}, {x_max}, {y_max})")
        return (x_min, y_min, x_max, y_max)

    def crop_torso(
        self,
        frame: np.ndarray,
        keypoints: Dict[str, Dict[str, float]]
    ) -> Optional[np.ndarray]:
        """
        Extract torso crop from frame using pose keypoints.

        Args:
            frame: Input video frame (BGR)
            keypoints: Dictionary of pose keypoints

        Returns:
            Cropped torso region, or None if crop failed

        Raises:
            ValueError: If frame is invalid
        """
        if frame is None or frame.size == 0:
            logger.error("Invalid frame")
            raise ValueError("Frame cannot be None or empty")

        # Get torso bounding box
        bbox = self.get_torso_box(keypoints, frame.shape)
        if bbox is None:
            logger.warning("Could not determine torso box")
            return None

        x_min, y_min, x_max, y_max = bbox

        # Extract crop
        try:
            crop = frame[y_min:y_max, x_min:x_max].copy()
            
            if crop.size == 0:
                logger.error("Crop resulted in empty image")
                return None

            logger.debug(f"Extracted torso crop shape: {crop.shape}")
            return crop
        except Exception as e:
            logger.error(f"Failed to crop torso: {e}")
            return None

    def save_crop(
        self,
        crop: np.ndarray,
        output_path: Path,
        frame_id: int,
        player_id: Optional[str] = None,
        quality: int = 95
    ) -> bool:
        """
        Save torso crop to disk with organized naming.

        Args:
            crop: Cropped torso image
            output_path: Directory to save crop
            frame_id: Frame number for unique naming
            player_id: Optional player identifier
            quality: JPEG quality (1-100)

        Returns:
            True if save successful, False otherwise

        Raises:
            ValueError: If crop is invalid
        """
        if crop is None or crop.size == 0:
            logger.error("Cannot save empty crop")
            raise ValueError("Crop cannot be None or empty")

        # Create output directory
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if player_id:
            filename = f"player_{player_id}_frame_{frame_id:06d}.jpg"
        else:
            filename = f"torso_frame_{frame_id:06d}.jpg"

        filepath = output_path / filename

        # Save crop
        try:
            _, buffer = cv2.imencode(
                ".jpg",
                crop,
                [cv2.IMWRITE_JPEG_QUALITY, quality]
            )
            with open(filepath, "wb") as f:
                f.write(buffer)
            
            logger.info(f"Saved torso crop: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save crop: {e}")
            return False

    def crop_and_save(
        self,
        frame: np.ndarray,
        keypoints: Dict[str, Dict[str, float]],
        output_path: Path,
        frame_id: int,
        player_id: Optional[str] = None
    ) -> Optional[Path]:
        """
        Convenience function to crop and save in one call.

        Args:
            frame: Input frame
            keypoints: Pose keypoints
            output_path: Directory to save
            frame_id: Frame number
            player_id: Optional player ID

        Returns:
            Path to saved file if successful, None otherwise
        """
        crop = self.crop_torso(frame, keypoints)
        if crop is not None:
            if self.save_crop(crop, output_path, frame_id, player_id):
                output_path = Path(output_path)
                if player_id:
                    filename = f"player_{player_id}_frame_{frame_id:06d}.jpg"
                else:
                    filename = f"torso_frame_{frame_id:06d}.jpg"
                return output_path / filename
        
        return None


def visualize_torso_box(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    color: Tuple[int, int, int] = (0, 255, 0)
) -> np.ndarray:
    """
    Draw torso bounding box on frame for visualization.

    Args:
        frame: Input frame
        bbox: Bounding box (x_min, y_min, x_max, y_max)
        color: Box color in BGR

    Returns:
        Frame with drawn box
    """
    x_min, y_min, x_max, y_max = bbox
    output = frame.copy()
    cv2.rectangle(output, (x_min, y_min), (x_max, y_max), color, 2)
    return output
