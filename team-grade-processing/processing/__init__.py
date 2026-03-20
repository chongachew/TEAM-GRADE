"""
TEAM-GRADE Local Processing Pipeline
Production-ready video processing for YouTube → frames → pose → reps
"""

from .pose_estimation import PoseEstimator, resize_frame
from .torso_cropper import TorsoCropper, visualize_torso_box
from .jersey_ocr import JerseyOCR, preprocess_jersey_crop, filter_jersey_candidates
from .rep_extraction import (
    RepExtractor, Frame, Rep, smooth_trajectory
)
from .utils import (
    setup_logging, get_project_structure, get_video_files,
    load_frame, load_frames_batch, extract_frames_from_video,
    load_json, save_json, load_pose_json, save_pose_json,
    load_pose_sequence, normalize_keypoints, denormalize_keypoints,
    filter_keypoints_by_confidence, ensure_directory
)

__version__ = "1.0.0"
__all__ = [
    "PoseEstimator", "resize_frame",
    "TorsoCropper", "visualize_torso_box",
    "JerseyOCR", "preprocess_jersey_crop", "filter_jersey_candidates",
    "RepExtractor", "Frame", "Rep", "smooth_trajectory",
    "setup_logging", "get_project_structure", "get_video_files",
    "load_frame", "load_frames_batch", "extract_frames_from_video",
    "load_json", "save_json", "load_pose_json", "save_pose_json",
    "load_pose_sequence", "normalize_keypoints", "denormalize_keypoints",
    "filter_keypoints_by_confidence", "ensure_directory"
]
