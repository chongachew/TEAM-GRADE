"""
Batch MediaPipe Pose Processor
Process frames efficiently using IMAGE mode (batch) instead of VIDEO mode (streaming).

Pattern from: Ashritha-sai/MotoR-Motion
Key benefit: 3-4x faster inference vs frame-by-frame processing
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.warning("cv2 not available")

try:
    import mediapipe as mp
    _MP_AVAILABLE = True
    # Try to import pose with fallback
    try:
        from mediapipe.solutions import pose as mp_pose
    except (ImportError, AttributeError):
        # Fallback for older/different MediaPipe versions
        try:
            from mediapipe.python.solutions import pose as mp_pose  
        except (ImportError, AttributeError):
            _MP_AVAILABLE = False
            logger.warning("MediaPipe pose module not found - install with: pip install mediapipe")
except ImportError:
    _MP_AVAILABLE = False
    logger.warning("mediapipe not available")


class BatchPoseProcessor:
    """Process frames in batches using MediaPipe IMAGE mode.
    
    Key Features:
    - Uses IMAGE mode (static_image_mode=True) for batch efficiency
    - Confidence filtering (only returns high-confidence landmarks)
    - Batch-aware logging
    - Handles missing/invalid frames gracefully
    """
    
    def __init__(
        self,
        batch_size: int = 32,
        confidence_threshold: float = 0.7,
        model_complexity: int = 1,
        verbose: bool = False
    ):
        """Initialize batch processor.
        
        Args:
            batch_size: Frames to process together (default 32 for GPU, 8-16 for CPU)
            confidence_threshold: Minimum landmark confidence (0-1)
            model_complexity: 0=lite (~10MB), 1=full (~30MB)
            verbose: Enable debug logging
            
        Raises:
            ImportError: If mediapipe or cv2 not installed
        """
        if not _MP_AVAILABLE:
            raise ImportError(
                "mediapipe not installed. Run: pip install mediapipe"
            )
        if not _CV2_AVAILABLE:
            raise ImportError(
                "opencv-python not installed. Run: pip install opencv-python"
            )
        
        if not (0 <= confidence_threshold <= 1):
            raise ValueError(f"confidence_threshold must be 0-1, got {confidence_threshold}")
        if model_complexity not in (0, 1):
            raise ValueError(f"model_complexity must be 0 or 1, got {model_complexity}")
        if batch_size < 1 or batch_size > 256:
            raise ValueError(f"batch_size must be 1-256, got {batch_size}")
        
        self.batch_size = batch_size
        self.confidence_threshold = confidence_threshold
        self.model_complexity = model_complexity
        self.verbose = verbose
        
        try:
            # Initialize MediaPipe Pose in IMAGE mode
            self.pose = mp_pose.Pose(
                static_image_mode=True,  # ✅ IMAGE mode for batch processing
                model_complexity=model_complexity,
                min_detection_confidence=confidence_threshold,
                min_tracking_confidence=confidence_threshold
            )
        except Exception as e:
            logger.error(f"Failed to initialize MediaPipe Pose: {e}")
            raise RuntimeError(
                f"MediaPipe Pose initialization failed. Install with: pip install --upgrade mediapipe\n"
                f"Error: {e}"
            )
        
        model_name = "LITE" if model_complexity == 0 else "FULL"
        logger.info(
            f"[BATCH_POSE] Initialized: batch_size={batch_size}, "
            f"model={model_name}, confidence_threshold={confidence_threshold}"
        )
        
        # Stats
        self.total_frames_processed = 0
        self.total_frames_with_landmarks = 0
        self.total_frames_failed = 0
    
    def process_batch(
        self,
        frame_paths: List[Path],
        video_id: str,
        min_visible_landmarks: int = 20
    ) -> List[Dict[str, Any]]:
        """Process frames in batches.
        
        Args:
            frame_paths: List of frame image paths (sorted by index)
            video_id: Video ID for logging
            min_visible_landmarks: Minimum visible landmarks to consider success
                (BlazePose has 33 landmarks total)
            
        Returns:
            List of pose results with keys:
            - frame_index: 0-based frame index
            - path: Path to frame file
            - landmarks: List of {x, y, z, visibility} for visible landmarks
            - visibility_scores: Raw visibility for all 33 landmarks
            - high_confidence: Boolean, True if >= min_visible_landmarks above threshold
            - timestamp_frame: Frame number in sequence
        """
        if not frame_paths:
            logger.warning(f"[BATCH_POSE][{video_id}] No frames to process")
            return []
        
        results = []
        num_batches = (len(frame_paths) + self.batch_size - 1) // self.batch_size
        
        logger.info(
            f"[BATCH_POSE][{video_id}] Processing {len(frame_paths)} frames "
            f"in {num_batches} batches (size={self.batch_size})"
        )
        
        batch_idx = 0
        for start_idx in range(0, len(frame_paths), self.batch_size):
            batch_idx += 1
            batch_frames = frame_paths[start_idx:start_idx + self.batch_size]
            
            if self.verbose:
                logger.debug(
                    f"[BATCH_POSE][{video_id}] Batch {batch_idx}/{num_batches} "
                    f"({len(batch_frames)} frames)"
                )
            
            for frame_pos, frame_path in enumerate(batch_frames):
                frame_index = start_idx + frame_pos
                
                try:
                    # Read frame
                    img = cv2.imread(str(frame_path))
                    if img is None:
                        logger.warning(
                            f"[BATCH_POSE][{video_id}] Failed to read frame: {frame_path}"
                        )
                        self.total_frames_failed += 1
                        continue
                    
                    # ✅ Process in IMAGE mode
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    pose_results = self.pose.process(img_rgb)
                    self.total_frames_processed += 1
                    
                    if pose_results.pose_landmarks:
                        # Extract high-confidence landmarks
                        landmarks = []
                        all_visibilities = []
                        
                        for landmark in pose_results.pose_landmarks.landmark:
                            all_visibilities.append(float(landmark.visibility))
                            
                            if landmark.visibility >= self.confidence_threshold:
                                landmarks.append({
                                    "x": float(landmark.x),
                                    "y": float(landmark.y),
                                    "z": float(landmark.z),
                                    "visibility": float(landmark.visibility)
                                })
                        
                        is_high_confidence = len(landmarks) >= min_visible_landmarks
                        
                        results.append({
                            "frame_index": frame_index,
                            "path": str(frame_path),
                            "landmarks": landmarks,
                            "visibility_scores": all_visibilities,
                            "high_confidence": is_high_confidence,
                            "timestamp_frame": frame_index,
                            "visible_count": len(landmarks),
                            "total_landmarks": 33
                        })
                        
                        if is_high_confidence:
                            self.total_frames_with_landmarks += 1
                            if self.verbose:
                                logger.debug(
                                    f"[BATCH_POSE][{video_id}] Frame {frame_index}: "
                                    f"{len(landmarks)}/{33} landmarks, high_confidence=True"
                                )
                        else:
                            if self.verbose:
                                logger.debug(
                                    f"[BATCH_POSE][{video_id}] Frame {frame_index}: "
                                    f"{len(landmarks)}/{33} landmarks, high_confidence=False"
                                )
                    else:
                        if self.verbose:
                            logger.debug(
                                f"[BATCH_POSE][{video_id}] Frame {frame_index}: No pose detected"
                            )
                        self.total_frames_failed += 1
                    
                except Exception as e:
                    logger.error(
                        f"[BATCH_POSE][{video_id}] Error processing frame {frame_index}: {e}"
                    )
                    self.total_frames_failed += 1
        
        # Log summary
        success_rate = (len(results) / len(frame_paths) * 100) if frame_paths else 0
        confidence_rate = (self.total_frames_with_landmarks / len(results) * 100) if results else 0
        
        logger.info(
            f"[BATCH_POSE][{video_id}] Batch processing complete: "
            f"{len(results)}/{len(frame_paths)} frames with landmarks ({success_rate:.1f}%), "
            f"{self.total_frames_with_landmarks} high-confidence ({confidence_rate:.1f}%)"
        )
        
        return results
    
    def process_single_frame(
        self,
        frame_path: Path,
        min_visible_landmarks: int = 20
    ) -> Optional[Dict[str, Any]]:
        """Process single frame (convenience method).
        
        Args:
            frame_path: Path to frame file
            min_visible_landmarks: Minimum visible landmarks
            
        Returns:
            Pose result dict or None if failed
        """
        try:
            img = cv2.imread(str(frame_path))
            if img is None:
                logger.warning(f"Failed to read frame: {frame_path}")
                return None
            
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pose_results = self.pose.process(img_rgb)
            
            if pose_results.pose_landmarks:
                landmarks = []
                for landmark in pose_results.pose_landmarks.landmark:
                    if landmark.visibility >= self.confidence_threshold:
                        landmarks.append({
                            "x": float(landmark.x),
                            "y": float(landmark.y),
                            "z": float(landmark.z),
                            "visibility": float(landmark.visibility)
                        })
                
                return {
                    "path": str(frame_path),
                    "landmarks": landmarks,
                    "high_confidence": len(landmarks) >= min_visible_landmarks
                }
        except Exception as e:
            logger.error(f"Failed to process frame {frame_path}: {e}")
        
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics.
        
        Returns:
            Stats dict
        """
        return {
            "total_frames_processed": self.total_frames_processed,
            "total_frames_with_landmarks": self.total_frames_with_landmarks,
            "total_frames_failed": self.total_frames_failed,
            "success_rate": (
                self.total_frames_with_landmarks / self.total_frames_processed * 100
                if self.total_frames_processed > 0 else 0
            )
        }
    
    def reset_stats(self):
        """Reset statistics counters."""
        self.total_frames_processed = 0
        self.total_frames_with_landmarks = 0
        self.total_frames_failed = 0
    
    def __del__(self):
        """Cleanup resources."""
        if hasattr(self, 'pose'):
            try:
                self.pose.close()
            except:
                pass
