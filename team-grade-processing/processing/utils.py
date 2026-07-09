"""
Utility Functions Module
Common helpers for frame loading, JSON handling, and path management.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import cv2
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================================
# Path Helpers
# ============================================================================

def ensure_directory(directory: Path) -> Path:
    """
    Create directory if it doesn't exist.

    Args:
        directory: Path to directory

    Returns:
        Path object

    Raises:
        OSError: If directory creation fails
    """
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    except OSError as e:
        logger.error(f"Failed to create directory {directory}: {e}")
        raise


def get_project_structure() -> Dict[str, Path]:
    """
    Get standard project directory structure.
    Assumes running from project root or processing folder.

    Returns:
        Dictionary mapping purpose to Path
    """
    root = Path(__file__).parent.parent

    structure = {
        "root": root,
        "raw_videos": root / "raw_videos",
        "extracted_frames": root / "extracted_frames",
        "pose_outputs": root / "pose_outputs",
        "torso_crops": root / "torso_crops",
        "processing": root / "processing",
        "logs": root / "logs"
    }

    # Create all directories
    for name, path in structure.items():
        if name not in ["root", "processing"]:  # Don't create root/processing
            ensure_directory(path)

    return structure


def get_video_files(directory: Path, extensions: List[str] = None) -> List[Path]:
    """
    Find all video files in directory.

    Args:
        directory: Search directory
        extensions: Video file extensions (default: .mp4, .avi, .mov, .mkv)

    Returns:
        Sorted list of video file paths
    """
    if extensions is None:
        extensions = [".mp4", ".avi", ".mov", ".mkv"]

    directory = Path(directory)
    video_files = []

    for ext in extensions:
        video_files.extend(directory.glob(f"*{ext}"))
        video_files.extend(directory.glob(f"*{ext.upper()}"))

    return sorted(set(video_files))


# ============================================================================
# Frame Loading Helpers
# ============================================================================

def load_frame(frame_path: Path) -> Optional[np.ndarray]:
    """
    Load single frame from disk.

    Args:
        frame_path: Path to frame file

    Returns:
        Frame as numpy array (BGR), or None if load failed
    """
    try:
        frame = cv2.imread(str(frame_path))
        if frame is None:
            logger.warning(f"Failed to read frame: {frame_path}")
            return None
        return frame
    except Exception as e:
        logger.error(f"Error loading frame {frame_path}: {e}")
        return None


def load_frames_batch(
    frame_paths: List[Path],
    max_batch_size: int = 32
) -> List[np.ndarray]:
    """
    Load multiple frames efficiently.

    Args:
        frame_paths: List of frame file paths
        max_batch_size: Maximum frames to load at once

    Returns:
        List of loaded frames
    """
    frames = []
    
    for i, path in enumerate(frame_paths):
        frame = load_frame(path)
        if frame is not None:
            frames.append(frame)
        
        # Log progress
        if (i + 1) % max_batch_size == 0:
            logger.info(f"Loaded {i + 1}/{len(frame_paths)} frames")

    logger.info(f"Successfully loaded {len(frames)}/{len(frame_paths)} frames")
    return frames


def extract_frames_from_video(
    video_path: Path,
    output_dir: Path,
    stride: int = 1,
    max_frames: Optional[int] = None
) -> List[Path]:
    """
    Extract frames from video file.

    Args:
        video_path: Path to video file
        output_dir: Directory to save frames
        stride: Extract every nth frame
        max_frames: Maximum frames to extract

    Returns:
        List of saved frame paths

    Raises:
        FileNotFoundError: If video not found
        ValueError: If video cannot be opened
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    ensure_directory(output_dir)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    frame_paths = []
    frame_count = 0
    saved_count = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_count % stride == 0 and (max_frames is None or saved_count < max_frames):
                frame_path = output_dir / f"frame_{frame_count:06d}.jpg"
                cv2.imwrite(str(frame_path), frame)
                frame_paths.append(frame_path)
                saved_count += 1

            frame_count += 1

            if frame_count % 100 == 0:
                logger.info(f"Extracted {frame_count} frames")

        logger.info(f"Extracted {saved_count} frames from {video_path}")
        return frame_paths

    finally:
        cap.release()


# ============================================================================
# JSON Helpers
# ============================================================================

def load_json(file_path: Path) -> Dict[str, Any]:
    """
    Load JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Parsed JSON as dictionary

    Raises:
        FileNotFoundError: If file not found
        json.JSONDecodeError: If JSON invalid
    """
    file_path = Path(file_path)
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        logger.debug(f"Loaded JSON: {file_path}")
        return data
    except FileNotFoundError:
        logger.error(f"JSON file not found: {file_path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        raise


def save_json(
    data: Dict[str, Any],
    file_path: Path,
    pretty: bool = True,
    indent: int = 2
) -> bool:
    """
    Save dictionary to JSON file.

    Args:
        data: Data to save
        file_path: Output path
        pretty: Enable pretty printing
        indent: JSON indentation level

    Returns:
        True if successful, False otherwise
    """
    file_path = Path(file_path)
    ensure_directory(file_path.parent)

    try:
        with open(file_path, 'w') as f:
            if pretty:
                json.dump(data, f, indent=indent)
            else:
                json.dump(data, f)
        logger.debug(f"Saved JSON: {file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save JSON {file_path}: {e}")
        return False


def load_pose_json(file_path: Path) -> Optional[Dict[str, Any]]:
    """
    Load pose output JSON file with error handling.

    Args:
        file_path: Path to pose JSON

    Returns:
        Pose data or None if load failed
    """
    try:
        return load_json(file_path)
    except Exception as e:
        logger.warning(f"Failed to load pose JSON: {e}")
        return None


def save_pose_json(
    pose_data: Dict[str, Any],
    file_path: Path
) -> bool:
    """
    Save pose data to JSON with metadata.

    Args:
        pose_data: Pose keypoints and metadata
        file_path: Output path

    Returns:
        True if successful
    """
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "data": pose_data
    }
    return save_json(output, file_path)


def load_pose_sequence(directory: Path) -> List[Dict[str, Any]]:
    """
    Load sequence of pose JSON files from directory.

    Args:
        directory: Directory containing pose JSONs

    Returns:
        List of pose data dictionaries

    Raises:
        FileNotFoundError: If directory not found
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    pose_files = sorted(directory.glob("*.json"))
    poses = []

    for pose_file in pose_files:
        pose_data = load_pose_json(pose_file)
        if pose_data:
            poses.append(pose_data)

    logger.info(f"Loaded {len(poses)} pose files")
    return poses


# ============================================================================
# Keypoint Helpers
# ============================================================================

def calculate_keypoint_displacement(
    kp1: Dict[str, Dict[str, float]],
    kp2: Dict[str, Dict[str, float]],
) -> float:
    """
    Mean displacement of matched, confidence-filtered keypoints between two
    frames (average Euclidean distance over keypoints present and confident in
    both frames). Promoted from RepExtractor._calculate_displacement so both
    rep_extraction.py and biomechanics_stage_vectorized.py's velocity-derived
    features share one implementation instead of two copies drifting apart.

    Args:
        kp1: First frame's keypoints
        kp2: Second frame's keypoints

    Returns:
        Mean displacement distance (0.0 if no common confident keypoints)
    """
    total_displacement = 0.0
    count = 0

    common_points = set(kp1.keys()) & set(kp2.keys())

    for point_name in common_points:
        if kp1[point_name].get("confidence", 0) < 0.3:
            continue
        if kp2[point_name].get("confidence", 0) < 0.3:
            continue

        x1, y1 = kp1[point_name]["x"], kp1[point_name]["y"]
        x2, y2 = kp2[point_name]["x"], kp2[point_name]["y"]

        distance = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        total_displacement += distance
        count += 1

    return total_displacement / count if count > 0 else 0.0



def normalize_keypoints(
    keypoints: Dict[str, Dict[str, float]],
    frame_shape: Tuple[int, int, int]
) -> Dict[str, Dict[str, float]]:
    """
    Normalize keypoint coordinates to [0, 1] range.

    Args:
        keypoints: Raw keypoint dictionary
        frame_shape: Frame dimensions (height, width, channels)

    Returns:
        Normalized keypoints
    """
    height, width = frame_shape[:2]
    normalized = {}

    for name, kp in keypoints.items():
        normalized[name] = {
            "x": kp["x"] / width,
            "y": kp["y"] / height,
            "z": kp.get("z", 0.0),
            "confidence": kp.get("confidence", 0.0)
        }

    return normalized


def denormalize_keypoints(
    keypoints: Dict[str, Dict[str, float]],
    frame_shape: Tuple[int, int, int]
) -> Dict[str, Dict[str, float]]:
    """
    Denormalize keypoints from [0, 1] to pixel coordinates.

    Args:
        keypoints: Normalized keypoints
        frame_shape: Frame dimensions

    Returns:
        Pixel-space keypoints
    """
    height, width = frame_shape[:2]
    denormalized = {}

    for name, kp in keypoints.items():
        denormalized[name] = {
            "x": int(kp["x"] * width),
            "y": int(kp["y"] * height),
            "z": kp.get("z", 0.0),
            "confidence": kp.get("confidence", 0.0)
        }

    return denormalized


def filter_keypoints_by_confidence(
    keypoints: Dict[str, Dict[str, float]],
    threshold: float = 0.3
) -> Dict[str, Dict[str, float]]:
    """
    Filter keypoints below confidence threshold.

    Args:
        keypoints: Input keypoints
        threshold: Minimum confidence

    Returns:
        Filtered keypoints
    """
    return {
        name: kp
        for name, kp in keypoints.items()
        if kp.get("confidence", 0.0) >= threshold
    }


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(
    log_dir: Path,
    log_level: str = "INFO"
) -> None:
    """
    Configure logging for processing pipeline.

    Args:
        log_dir: Directory for log files
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    log_dir = Path(log_dir)
    ensure_directory(log_dir)

    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(getattr(logging, log_level))
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level))
    console_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger.info(f"Logging initialized: {log_file}")
