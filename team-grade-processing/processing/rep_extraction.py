"""
Rep Extraction Module
Groups frames into discrete reps and maps players using trajectory analysis.
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict
from collections import defaultdict
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Represents a single frame in video."""
    frame_id: int
    timestamp: float
    track_id: Optional[int]
    jersey_number: Optional[str]
    jersey_confidence: float
    position: str  # Position code (QB, RB, etc.)
    keypoints: Dict[str, Dict[str, float]]


@dataclass
class Rep:
    """Represents a single rep (one play execution)."""
    rep_id: int
    start_frame: int
    end_frame: int
    duration_frames: int
    player_id: Optional[str]
    track_id: Optional[int]
    jersey_number: Optional[str]
    position: str
    position_confidence: float
    keypoint_sequence: List[Dict[str, Dict[str, float]]]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            "keypoint_sequence": self.keypoint_sequence
        }


class RepExtractor:
    """
    Extracts discrete reps from continuous video stream.
    Uses motion detection and trajectory analysis.
    """

    def __init__(
        self,
        max_static_frames: int = 30,
        max_gap_frames: int = 10,
        min_rep_duration: int = 15,
        max_rep_duration: int = 300
    ):
        """
        Initialize rep extractor.

        Args:
            max_static_frames: Frames without motion before rep ends
            max_gap_frames: Max frames to bridge trajectory gaps
            min_rep_duration: Minimum frames for valid rep
            max_rep_duration: Maximum frames for single rep
        """
        self.max_static_frames = max_static_frames
        self.max_gap_frames = max_gap_frames
        self.min_rep_duration = min_rep_duration
        self.max_rep_duration = max_rep_duration

    def extract_reps(
        self,
        frames: List[Frame],
        motion_threshold: float = 0.02
    ) -> List[Rep]:
        """
        Extract reps from frame sequence.

        Args:
            frames: Ordered list of Frame objects
            motion_threshold: Keypoint variance threshold for motion detection

        Returns:
            List of extracted Rep objects

        Raises:
            ValueError: If frames list is empty
        """
        if not frames:
            raise ValueError("Frames list cannot be empty")

        logger.info(f"Extracting reps from {len(frames)} frames")

        # Detect motion frames
        motion_mask = self._detect_motion(frames, motion_threshold)

        # Segment by motion pauses
        reps = self._segment_by_motion(frames, motion_mask)

        # Filter by duration
        reps = self._filter_by_duration(reps)

        logger.info(f"Extracted {len(reps)} reps")
        return reps

    def _detect_motion(
        self,
        frames: List[Frame],
        threshold: float
    ) -> np.ndarray:
        """
        Detect frames with significant motion.

        Args:
            frames: Frame list
            threshold: Motion variance threshold

        Returns:
            Boolean array where True indicates motion
        """
        motion = np.zeros(len(frames), dtype=bool)

        for i in range(1, len(frames)):
            if not frames[i].keypoints or not frames[i-1].keypoints:
                continue

            # Calculate keypoint displacement
            displacement = self._calculate_displacement(
                frames[i-1].keypoints,
                frames[i].keypoints
            )

            if displacement > threshold:
                motion[i] = True

        logger.debug(f"Motion detected in {motion.sum()} frames")
        return motion

    def _calculate_displacement(
        self,
        kp1: Dict[str, Dict[str, float]],
        kp2: Dict[str, Dict[str, float]]
    ) -> float:
        """
        Calculate total keypoint displacement between frames.

        Args:
            kp1: First frame keypoints
            kp2: Second frame keypoints

        Returns:
            Total displacement distance
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

            distance = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            total_displacement += distance
            count += 1

        return total_displacement / count if count > 0 else 0.0

    def _segment_by_motion(
        self,
        frames: List[Frame],
        motion_mask: np.ndarray
    ) -> List[Rep]:
        """
        Segment frames into reps based on motion pauses.

        Args:
            frames: Frame list
            motion_mask: Boolean motion mask

        Returns:
            List of Rep objects
        """
        reps = []
        rep_start = 0
        static_count = 0

        for i in range(len(frames)):
            if motion_mask[i]:
                static_count = 0
            else:
                static_count += 1

            # End rep on sustained static period
            if static_count >= self.max_static_frames:
                if i - rep_start >= self.min_rep_duration:
                    rep = self._create_rep(frames, rep_start, i - self.max_static_frames)
                    if rep:
                        reps.append(rep)
                
                rep_start = i
                static_count = 0

        # Handle final rep
        if len(frames) - rep_start >= self.min_rep_duration:
            rep = self._create_rep(frames, rep_start, len(frames) - 1)
            if rep:
                reps.append(rep)

        return reps

    def _create_rep(
        self,
        frames: List[Frame],
        start_idx: int,
        end_idx: int
    ) -> Optional[Rep]:
        """
        Create Rep object from frame range.

        Args:
            frames: Full frame list
            start_idx: Start frame index
            end_idx: End frame index

        Returns:
            Rep object or None if invalid
        """
        if start_idx >= end_idx:
            return None

        rep_frames = frames[start_idx:end_idx + 1]

        # Select dominant jersey number
        jersey_numbers = [
            (f.jersey_number, f.jersey_confidence)
            for f in rep_frames
            if f.jersey_number
        ]
        
        jersey_num = None
        jersey_conf = 0.0
        if jersey_numbers:
            jersey_num, jersey_conf = max(jersey_numbers, key=lambda x: x[1])

        # Collect keypoint sequence
        keypoint_seq = [f.keypoints for f in rep_frames]

        # Infer position from frames
        positions = [f.position for f in rep_frames if f.position]
        position = max(set(positions), key=positions.count) if positions else "UNKNOWN"

        rep = Rep(
            rep_id=len(frames),  # Simplified ID
            start_frame=rep_frames[0].frame_id,
            end_frame=rep_frames[-1].frame_id,
            duration_frames=len(rep_frames),
            player_id=jersey_num,
            track_id=rep_frames[0].track_id,
            jersey_number=jersey_num,
            position=position,
            position_confidence=0.5,  # Placeholder
            keypoint_sequence=keypoint_seq,
            metadata={
                "source_frames": len(rep_frames),
                "jersey_detection_frames": len(jersey_numbers)
            }
        )

        return rep

    def _filter_by_duration(self, reps: List[Rep]) -> List[Rep]:
        """
        Filter reps by valid duration range.

        Args:
            reps: List of Rep objects

        Returns:
            Filtered Rep list
        """
        filtered = []
        for rep in reps:
            if self.min_rep_duration <= rep.duration_frames <= self.max_rep_duration:
                filtered.append(rep)
            else:
                logger.debug(
                    f"Filtered rep {rep.rep_id}: "
                    f"{rep.duration_frames} frames (invalid range)"
                )

        return filtered

    def map_track_id_to_player(
        self,
        reps: List[Rep],
        player_roster: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[int, str]:
        """
        Map track_ids to player identities using jersey numbers.

        Args:
            reps: List of extracted reps
            player_roster: Optional {jersey_number: {name, position, team}}

        Returns:
            Mapping of track_id -> player_id (jersey or name)
        """
        track_map = {}

        for rep in reps:
            if rep.track_id is None:
                continue

            # Use jersey number as primary identifier
            if rep.jersey_number:
                player_id = rep.jersey_number
                
                # Optionally cross-reference with roster
                if player_roster and rep.jersey_number in player_roster:
                    player_info = player_roster[rep.jersey_number]
                    player_id = player_info.get("name", rep.jersey_number)

                track_map[rep.track_id] = player_id
                logger.info(
                    f"Track {rep.track_id} -> Player {player_id} "
                    f"(jersey {rep.jersey_number})"
                )

        return track_map


def smooth_trajectory(
    keypoints_sequence: List[Dict[str, Dict[str, float]]],
    window_size: int = 3
) -> List[Dict[str, Dict[str, float]]]:
    """
    Smooth keypoint trajectories across rep using moving average.

    Args:
        keypoints_sequence: Sequence of keypoint dicts
        window_size: Moving average window

    Returns:
        Smoothed keypoint sequence
    """
    if len(keypoints_sequence) <= window_size:
        return keypoints_sequence

    smoothed = []

    for i in range(len(keypoints_sequence)):
        start = max(0, i - window_size // 2)
        end = min(len(keypoints_sequence), i + window_size // 2 + 1)

        smoothed_kp = {}
        for kp_name in keypoints_sequence[i].keys():
            x_vals = []
            y_vals = []
            confs = []

            for j in range(start, end):
                kp = keypoints_sequence[j].get(kp_name, {})
                if kp:
                    x_vals.append(kp.get("x", 0))
                    y_vals.append(kp.get("y", 0))
                    confs.append(kp.get("confidence", 0))

            smoothed_kp[kp_name] = {
                "x": np.mean(x_vals),
                "y": np.mean(y_vals),
                "z": keypoints_sequence[i][kp_name].get("z", 0),
                "confidence": np.mean(confs)
            }

        smoothed.append(smoothed_kp)

    return smoothed
