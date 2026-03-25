"""
Segmenter fusion — combine audio, motion, scene, and field signals into
confidence-scored play segment candidates.

Fusion algorithm:
  1. Collect all event *anchors*: whistle timestamps + motion burst start/end
     + scene boundary timestamps.
  2. Cluster anchors within PAD_S of each other into a single candidate window.
  3. For each candidate window:
       - whistle_confidence  : max whistle score overlapping the window
       - motion_score        : mean motion score overlapping the window
       - scene_score         : 1 if a scene boundary is within the window, else 0
       - field_presence      : mean field coverage in the window
       - fusion_confidence   : weighted sum of the four signals
  4. Discard candidates below FUSION_MIN_CONFIDENCE.

Output: list of SegmentCandidate dicts sorted by start_time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

import numpy as np

from preprocessor.config import (
    WHISTLE_CONFIDENCE_WEIGHT,
    MOTION_SCORE_WEIGHT,
    SCENE_WEIGHT,
    FIELD_WEIGHT,
    FUSION_MIN_CONFIDENCE,
    SEGMENT_PAD_S,
    SEGMENT_MIN_DURATION_S,
    SEGMENT_MAX_DURATION_S,
)

logger = logging.getLogger(__name__)


@dataclass
class SegmentCandidate:
    start_time: float
    end_time: float
    duration: float
    whistle_confidence: float
    motion_score: float
    scene_score: float
    field_presence: float
    fusion_confidence: float
    # Normalised motion score (0–1) used internally for weighting
    _motion_norm: float = field(default=0.0, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_motion_norm", None)
        return d


def _overlaps(a_start: float, a_end: float,
              b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def _normalise_motion(motion_events: List[Dict[str, Any]],
                      max_score: float = 255.0) -> List[Dict[str, Any]]:
    """Scale raw mean-diff scores to [0, 1]."""
    if not motion_events:
        return []
    peak = max((e["motion_score"] for e in motion_events), default=1.0)
    peak = max(peak, 1.0)
    return [{**e, "_norm_score": e["motion_score"] / peak}
            for e in motion_events]


class SegmenterFusion:
    """Fuse audio/motion/scene/field signals into ranked play candidates."""

    def __init__(
        self,
        pad_s: float = SEGMENT_PAD_S,
        min_duration_s: float = SEGMENT_MIN_DURATION_S,
        max_duration_s: float = SEGMENT_MAX_DURATION_S,
        min_confidence: float = FUSION_MIN_CONFIDENCE,
        w_whistle: float = WHISTLE_CONFIDENCE_WEIGHT,
        w_motion: float = MOTION_SCORE_WEIGHT,
        w_scene: float = SCENE_WEIGHT,
        w_field: float = FIELD_WEIGHT,
    ) -> None:
        self.pad_s = pad_s
        self.min_duration_s = min_duration_s
        self.max_duration_s = max_duration_s
        self.min_confidence = min_confidence
        self.w_whistle = w_whistle
        self.w_motion = w_motion
        self.w_scene = w_scene
        self.w_field = w_field

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fuse(
        self,
        whistle_events: List[Dict[str, Any]],
        motion_bursts: List[Dict[str, Any]],
        scene_boundaries: List[Dict[str, Any]],
        field_scores: List[Dict[str, Any]],
        video_duration_s: Optional[float] = None,
    ) -> List[SegmentCandidate]:
        """Return sorted, filtered SegmentCandidate list."""

        # Build raw candidate windows from all signal anchors
        candidates = self._build_candidates(
            whistle_events, motion_bursts, scene_boundaries
        )

        # Score each candidate
        motion_norm = _normalise_motion(motion_bursts)
        scored: List[SegmentCandidate] = []
        for cand_start, cand_end in candidates:
            # Apply padding
            start = max(0.0, cand_start - self.pad_s)
            end = cand_end + self.pad_s
            if video_duration_s:
                end = min(end, video_duration_s)

            duration = end - start
            if duration < self.min_duration_s:
                continue
            if duration > self.max_duration_s:
                end = start + self.max_duration_s
                duration = self.max_duration_s

            w_conf = self._whistle_in_window(whistle_events, start, end)
            m_score = self._motion_in_window(motion_norm, start, end)
            s_score = self._scene_in_window(scene_boundaries, start, end)
            f_score = self._field_in_window(field_scores, start, end)

            fusion = (
                self.w_whistle * w_conf
                + self.w_motion * m_score
                + self.w_scene * s_score
                + self.w_field * f_score
            )

            if fusion < self.min_confidence:
                continue

            scored.append(SegmentCandidate(
                start_time=round(start, 3),
                end_time=round(end, 3),
                duration=round(duration, 3),
                whistle_confidence=round(w_conf, 4),
                motion_score=round(m_score, 4),
                scene_score=round(s_score, 4),
                field_presence=round(f_score, 4),
                fusion_confidence=round(fusion, 4),
            ))

        # Sort by time; remove duplicates / sub-windows
        scored.sort(key=lambda c: c.start_time)
        scored = self._dedup(scored)

        logger.info("SegmenterFusion: %d candidates after fusion & filtering",
                    len(scored))
        return scored

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_candidates(
        self,
        whistle_events: List[Dict[str, Any]],
        motion_bursts: List[Dict[str, Any]],
        scene_boundaries: List[Dict[str, Any]],
    ) -> List[tuple]:
        """Cluster all signal anchors into (start, end) candidate windows."""
        anchors: List[tuple] = []
        for e in whistle_events:
            anchors.append((e["start_time"], e["end_time"]))
        for b in motion_bursts:
            anchors.append((b["start_time"], b["end_time"]))
        for s in scene_boundaries:
            # Scene boundaries are points; give them a 1-second window
            anchors.append((s["start_time"], s["end_time"]))

        if not anchors:
            return []

        anchors.sort()
        merged: List[list] = [list(anchors[0])]
        for start, end in anchors[1:]:
            if start <= merged[-1][1] + self.pad_s:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(s, e) for s, e in merged]

    @staticmethod
    def _whistle_in_window(events: List[Dict[str, Any]],
                           start: float, end: float) -> float:
        scores = [e["whistle_confidence"] for e in events
                  if _overlaps(e["start_time"], e["end_time"], start, end)]
        return max(scores) if scores else 0.0

    @staticmethod
    def _motion_in_window(events: List[Dict[str, Any]],
                          start: float, end: float) -> float:
        scores = [e["_norm_score"] for e in events
                  if _overlaps(e["start_time"], e["end_time"], start, end)]
        return float(np.mean(scores)) if scores else 0.0

    @staticmethod
    def _scene_in_window(boundaries: List[Dict[str, Any]],
                         start: float, end: float) -> float:
        for s in boundaries:
            if _overlaps(s["start_time"], s["end_time"], start, end):
                return 1.0
        return 0.0

    @staticmethod
    def _field_in_window(scores: List[Dict[str, Any]],
                         start: float, end: float) -> float:
        window = [s["field_presence"] for s in scores
                  if start <= s["time"] <= end]
        return float(np.mean(window)) if window else 0.0

    @staticmethod
    def _dedup(candidates: List[SegmentCandidate]) -> List[SegmentCandidate]:
        """Remove candidates that are completely contained in a higher-scored
        neighbour."""
        if len(candidates) < 2:
            return candidates
        kept: List[SegmentCandidate] = [candidates[0]]
        for cand in candidates[1:]:
            prev = kept[-1]
            # If this candidate starts before previous ends (overlap), keep
            # only the one with higher fusion score
            if cand.start_time < prev.end_time:
                if cand.fusion_confidence > prev.fusion_confidence:
                    kept[-1] = cand
            else:
                kept.append(cand)
        return kept
