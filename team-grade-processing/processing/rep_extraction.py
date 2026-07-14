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
        Calculate mean keypoint displacement between frames.

        Args:
            kp1: First frame keypoints
            kp2: Second frame keypoints

        Returns:
            Mean displacement distance

        Note: delegates to processing.utils.calculate_keypoint_displacement, the
        shared implementation also used by biomechanics_stage_vectorized.py.
        """
        from processing.utils import calculate_keypoint_displacement
        return calculate_keypoint_displacement(kp1, kp2)

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


def segment_reps_from_pose_docs(
    firestore_client: Any,
    video_id_safe: str,
    poses_docs: List[Any],
    multi_player: bool,
    fps: float,
    default_position: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Turn a video's pose documents into rep (play) segments: the fast
    trajectory analyzer first, falling back to RepExtractor's causal
    motion-based segmentation if the fast analyzer errors or finds nothing -
    the exact same logic ingest/stages/rep_extraction_stage.py has always
    run for the final pass.

    Shared by rep_extraction_stage.py (the real, final pass over the whole
    video's pose data - result gets written to Firestore) and
    api/server.py's get_analysis (an on-demand "preview" pass over whatever
    partial pose data exists while a video is still processing - result is
    only ever returned, never written). Using the identical function for
    both means a preview never runs different logic than the final answer,
    it's only ever working from less data.

    Args:
        firestore_client: Firestore client (used for the per-track jersey
            number lookup in multi-player mode)
        video_id_safe: already-sanitized video ID
        poses_docs: Firestore doc snapshots from the video's `pose`
            subcollection (caller fetches these - full set for a final pass,
            whatever currently exists for a preview pass)
        multi_player: settings.MULTI_PLAYER_TRACKING_ENABLED
        fps: settings.FRAME_EXTRACTION_FPS
        default_position: settings.DEFAULT_PLAYER_POSITION

    Returns:
        (reps_data, tracks_processed) - reps_data has the exact shape
        already written to the `reps` Firestore subcollection:
        {rep_index, start_frame, end_frame, duration_frames, duration_seconds,
         track_id?, jersey_number?}
    """
    from processing.fast_trajectory import FastTrajectoryAnalyzer
    from config import settings

    frames_by_track: Dict[Optional[int], List[Frame]] = defaultdict(list)
    for idx, pose_doc in enumerate(poses_docs):
        pose_data = pose_doc.to_dict()
        keypoints_dict = {k: v for k, v in pose_data.get("landmarks", {}).items()}
        track_id = pose_data.get("track_id") if multi_player else None
        frame = Frame(
            frame_id=pose_data.get("frame_index", idx),
            timestamp=pose_data.get("frame_index", idx) / fps,
            track_id=track_id, jersey_number=None, jersey_confidence=0.0,
            position=default_position, keypoints=keypoints_dict
        )
        frames_by_track[track_id].append(frame)
    # Each track's frames are only the frames that track was actually
    # detected in (temporally sparse relative to the whole video) - sort by
    # frame_id since Firestore stream() order isn't guaranteed.
    for track_id in frames_by_track:
        frames_by_track[track_id].sort(key=lambda f: f.frame_id)

    # Pull each track's jersey number once (not per rep) for convenience.
    jersey_by_track: Dict[Optional[int], Optional[str]] = {}
    if multi_player:
        for track_id in frames_by_track:
            if track_id is None:
                continue
            try:
                meta_doc = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                    video_id_safe).collection("tracks_meta").document(f"{track_id:03d}").get()
                jersey_by_track[track_id] = meta_doc.to_dict().get("jersey_number") if meta_doc.exists else None
            except Exception:
                jersey_by_track[track_id] = None

    reps_data: List[Dict[str, Any]] = []

    for track_id, frames in frames_by_track.items():
        try:
            rep_extractor = RepExtractor(
                max_static_frames=settings.REP_MAX_STATIC_FRAMES,
                max_gap_frames=settings.REP_MAX_GAP_FRAMES,
                min_rep_duration=settings.REP_MIN_DURATION,
                max_rep_duration=settings.REP_MAX_DURATION
            )

            # Try fast trajectory analyzer first (O(n log n) instead of O(n^2)).
            try:
                keypoints_sequence = [frame.keypoints for frame in frames]

                fast_analyzer = FastTrajectoryAnalyzer(
                    position_threshold=50.0,
                    movement_threshold=10.0,
                    rest_threshold=30.0
                )

                rep_segments = fast_analyzer.segment_into_reps_fast(
                    keypoints_sequence,
                    frame_shape=(540, 960, 3),  # Standard football video size
                    min_rep_length=settings.REP_MIN_DURATION
                )

                if rep_segments:
                    reps = []
                    for rep_idx, (start, end) in enumerate(rep_segments):
                        rep = type('Rep', (), {
                            'start_frame': start,
                            'end_frame': end,
                            'duration_frames': end - start + 1
                        })()
                        reps.append(rep)
                else:
                    reps = rep_extractor.extract_reps(frames)
            except Exception:
                reps = rep_extractor.extract_reps(frames)

        except Exception as e:
            logger.warning(f"Rep extraction failed for track {track_id}: {e}")
            reps = []

        for rep_idx, rep in enumerate(reps):
            rep_meta = {
                "rep_index": rep_idx,
                "start_frame": rep.start_frame,
                "end_frame": rep.end_frame,
                "duration_frames": rep.duration_frames,
                "duration_seconds": rep.duration_frames / fps,
            }
            if track_id is not None:
                rep_meta["track_id"] = track_id
                rep_meta["jersey_number"] = jersey_by_track.get(track_id)
            reps_data.append(rep_meta)

    return reps_data, len(frames_by_track)

    return smoothed
