"""
Density-Aware Memory Tracker
Hand-rolled multi-object tracking adaptation on top of Meta's official `sam2`
package, built for TEAM-GRADE's specific occlusion pattern: players bunching up
(e.g. a line of scrimmage) for a handful of frames, then separating.

Design note - why this wraps SAM2ImagePredictor, not SAM2VideoPredictor:
SAM2's stateful video predictor (init_state/propagate_in_video) owns its memory
bank internally and isn't exposed as a stable, public surface for the kind of
surgical "skip this frame" customization the LinkedIn-post stack described
("adapted with a better-behaved memory bank"). Rather than monkey-patching an
internal, version-coupled API, this tracker uses SAM2's per-frame image predictor
(encode once per frame via set_image(), decode a mask per detection box via
predict()) for high-quality segmentation, and owns the entire temporal
association/memory-bank layer itself in plain, inspectable Python. This is the
same shape of design used in published football-tracking work (e.g. SAM +
appearance-histogram re-identification for occlusion recovery), just with SAM2
masks instead of SAM(v1) masks feeding the appearance descriptor.

Association per frame: a single Hungarian (optimal bipartite) assignment over a
combined IoU + appearance-similarity score for every (detection, active track)
pair, not two independent passes. This matters even when nothing goes missing:
when two tracked players' paths visually cross, IoU-to-previous-position is
genuinely ambiguous right at the crossing frame (both candidates are plausible
by position alone) - appearance similarity is what breaks that tie correctly,
not just a fallback for tracks that dropped out of detection for a while. A
candidate pair is only eligible at all if its IoU clears TRACKING_MATCH_IOU_THRESHOLD
(continuous tracking) or, for a track that's been missing at least one frame, its
appearance similarity clears TRACKING_REID_SIMILARITY_THRESHOLD (long-gap
reappearance after separating from a pile-up, where position alone gives no
signal at all).

Selective memory update: a track's appearance memory bank only gets a new sample
when the current frame is clearly visible (not occluded) AND meaningfully
different from the last banked sample (IoU below TRACKING_MEMORY_STATIC_IOU_THRESHOLD
against its own last banked box) - this keeps the bank representative rather than
padded with near-duplicate or corrupted (partially-occluded) entries, which is
the concrete mechanism behind stock SAM2's occlusion degradation that this
adaptation targets.

Density-aware hold time: a track lost while flagged "occluded" (part of a
same-frame IoU cluster of >=3 overlapping boxes - the line-of-scrimmage
signature) gets TRACKING_MAX_LOST_FRAMES_DENSE frames before being terminated,
vs TRACKING_MAX_LOST_FRAMES_ISOLATED for a track lost in the open (matches
stock SAM2's ~5-10 frame default).
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from config import settings

logger = logging.getLogger(__name__)


def compute_iou(box_a: List[float], box_b: List[float]) -> float:
    """Standard IoU between two [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area

    return inter_area / union if union > 0 else 0.0


@dataclass
class TrackState:
    """Internal per-track bookkeeping (not the Firestore doc shape directly)."""
    track_id: int
    last_bbox: List[float]
    last_frame_seen: int
    frames_since_last_seen: int = 0
    occlusion_state: str = "visible"  # "visible" | "occluded" | "lost"
    status: str = "active"  # "active" | "terminated"
    last_lost_in_density: bool = False  # was it occluded/dense when last seen, for hold-time selection
    memory_bank: List[np.ndarray] = field(default_factory=list)  # HSV histograms
    last_banked_bbox: Optional[List[float]] = None


class DensityAwareMemoryTracker:
    """Multi-object tracker: SAM2 per-frame segmentation + hand-rolled
    IoU/appearance association and a selective, density-aware memory bank."""

    def __init__(
        self,
        checkpoint_path: Optional[str] = None,
        config_file: Optional[str] = None,
        device: str = "cpu",
        max_lost_frames_isolated: Optional[int] = None,
        max_lost_frames_dense: Optional[int] = None,
        density_iou_threshold: Optional[float] = None,
        memory_update_min_motion: Optional[float] = None,
        static_iou_threshold: Optional[float] = None,
        occluded_confidence_threshold: Optional[float] = None,
        match_iou_threshold: Optional[float] = None,
        reid_similarity_threshold: Optional[float] = None,
    ):
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        checkpoint_path = checkpoint_path or settings.SAM2_CHECKPOINT_PATH
        config_file = config_file or settings.SAM2_CONFIG_FILE

        sam2_model = build_sam2(config_file, checkpoint_path, device=device)
        self.predictor = SAM2ImagePredictor(sam2_model)

        self.max_lost_frames_isolated = (
            max_lost_frames_isolated
            if max_lost_frames_isolated is not None
            else settings.TRACKING_MAX_LOST_FRAMES_ISOLATED
        )
        self.max_lost_frames_dense = (
            max_lost_frames_dense
            if max_lost_frames_dense is not None
            else settings.TRACKING_MAX_LOST_FRAMES_DENSE
        )
        self.density_iou_threshold = (
            density_iou_threshold
            if density_iou_threshold is not None
            else settings.TRACKING_DENSITY_IOU_THRESHOLD
        )
        self.static_iou_threshold = (
            static_iou_threshold
            if static_iou_threshold is not None
            else settings.TRACKING_MEMORY_STATIC_IOU_THRESHOLD
        )
        self.occluded_confidence_threshold = (
            occluded_confidence_threshold
            if occluded_confidence_threshold is not None
            else settings.TRACKING_OCCLUDED_CONFIDENCE_THRESHOLD
        )
        self.match_iou_threshold = (
            match_iou_threshold if match_iou_threshold is not None else settings.TRACKING_MATCH_IOU_THRESHOLD
        )
        self.reid_similarity_threshold = (
            reid_similarity_threshold
            if reid_similarity_threshold is not None
            else settings.TRACKING_REID_SIMILARITY_THRESHOLD
        )
        # memory_update_min_motion reserved for a future pixel-displacement-based
        # variant of the static check; the current static check is IoU-based (see
        # module docstring) since bbox IoU is already available with no extra cost.
        self.memory_update_min_motion = (
            memory_update_min_motion
            if memory_update_min_motion is not None
            else settings.TRACKING_MEMORY_UPDATE_MIN_MOTION
        )

        self.tracks: Dict[int, TrackState] = {}
        self._next_track_id = 0

    def _new_track_id(self) -> int:
        track_id = self._next_track_id
        self._next_track_id += 1
        return track_id

    def _segment(self, bbox: List[float]) -> Tuple[np.ndarray, float]:
        """Predict a mask for one box in the currently-set image (set_image must
        already have been called for this frame). Returns (mask, mask_score)."""
        box = np.array(bbox)
        masks, scores, _ = self.predictor.predict(box=box, multimask_output=False)
        return masks[0].astype(bool), float(scores[0])

    @staticmethod
    def _appearance_descriptor(frame_rgb: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
        """Masked HSV color histogram - the appearance signal used for re-ID
        across long occlusion gaps, in the same spirit as jersey-color-based
        football re-identification approaches."""
        if mask.sum() < 10:
            return None
        hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
        mask_u8 = (mask.astype(np.uint8)) * 255
        hist = cv2.calcHist([hsv], [0, 1], mask_u8, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        return hist

    @staticmethod
    def _appearance_similarity(hist_a: np.ndarray, hist_b: np.ndarray) -> float:
        """Histogram correlation in [-1, 1]; higher is more similar."""
        return float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))

    def _compute_density_flags(self, boxes: List[List[float]]) -> List[bool]:
        """Per the plan: a box is "occluded" (part of a pile-up) if it has
        IoU > density_iou_threshold with >=2 other boxes simultaneously."""
        n = len(boxes)
        flags = [False] * n
        for i in range(n):
            overlap_count = 0
            for j in range(n):
                if i == j:
                    continue
                if compute_iou(boxes[i], boxes[j]) > self.density_iou_threshold:
                    overlap_count += 1
                    if overlap_count >= 2:
                        flags[i] = True
                        break
        return flags

    def process_frame(
        self,
        frame_idx: int,
        frame_rgb: np.ndarray,
        detections: List[Dict],
    ) -> List[Dict]:
        """Process one frame's detections, updating internal track state.

        Args:
            frame_idx: Frame index
            frame_rgb: RGB frame image (H, W, 3)
            detections: List of {"bbox": [x1,y1,x2,y2], "confidence": float} dicts
                        (as written by the detection stage for this frame)

        Returns:
            List of per-track result dicts matching the videos/{id}/tracks schema:
            frame_index, track_id, bbox, mask_area_px, confidence, matched_via,
            occlusion_state, frames_since_last_seen.
        """
        results: List[Dict] = []

        if not detections:
            self._age_unmatched_tracks(matched_ids=set(), frame_idx=frame_idx)
            return results

        self.predictor.set_image(frame_rgb)

        boxes = [d["bbox"] for d in detections]
        density_flags = self._compute_density_flags(boxes)

        # Segment every detection this frame (mask needed for both mask_area_px
        # and the appearance descriptor used in re-identification).
        seg_results = []
        for det, occluded in zip(detections, density_flags):
            mask, mask_score = self._segment(det["bbox"])
            appearance = self._appearance_descriptor(frame_rgb, mask)
            seg_results.append({
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "mask": mask,
                "mask_score": mask_score,
                "occluded": occluded or det["confidence"] < self.occluded_confidence_threshold,
                "appearance": appearance,
            })

        assignments = self._match_detections_to_tracks(frame_idx, seg_results)

        matched_track_ids = set()
        for det_idx, (track_id, matched_via) in assignments.items():
            seg = seg_results[det_idx]
            matched_track_ids.add(track_id)

            if track_id not in self.tracks:
                self.tracks[track_id] = TrackState(
                    track_id=track_id,
                    last_bbox=seg["bbox"],
                    last_frame_seen=frame_idx,
                )
            track = self.tracks[track_id]
            recovered_from_gap = track.frames_since_last_seen  # captured before this frame's update, below

            occlusion_state = "occluded" if seg["occluded"] else "visible"

            # Selective memory update (module docstring): skip banking a frame
            # that's occluded, or near-identical to the last banked frame.
            should_bank = (
                not seg["occluded"]
                and seg["appearance"] is not None
                and (
                    track.last_banked_bbox is None
                    or compute_iou(seg["bbox"], track.last_banked_bbox) < self.static_iou_threshold
                )
            )
            if should_bank:
                track.memory_bank.append(seg["appearance"])
                track.last_banked_bbox = seg["bbox"]

            track.last_bbox = seg["bbox"]
            track.last_frame_seen = frame_idx
            track.frames_since_last_seen = 0
            track.occlusion_state = occlusion_state
            track.last_lost_in_density = seg["occluded"]
            track.status = "active"

            results.append({
                "frame_index": frame_idx,
                "track_id": track_id,
                "bbox": seg["bbox"],
                "mask_area_px": int(seg["mask"].sum()),
                "confidence": seg["confidence"],
                "matched_via": matched_via,
                "occlusion_state": occlusion_state,
                "frames_since_last_seen": 0,
                # Frames the track was missing before this match recovered it (0
                # for routine continuous tracking) - lets callers (e.g. the
                # tracking stage's re-ID OCR hook) distinguish a trivial
                # crossing-paths appearance tie-break from a genuine long-gap
                # reappearance worth an extra verification check.
                "recovered_from_gap": recovered_from_gap,
            })

        self._age_unmatched_tracks(matched_ids=matched_track_ids, frame_idx=frame_idx)

        return results

    def _match_detections_to_tracks(
        self, frame_idx: int, seg_results: List[Dict]
    ) -> Dict[int, Tuple[int, str]]:
        """Single Hungarian assignment over combined IoU + appearance score.

        See module docstring: this is deliberately NOT "try IoU, then fall back to
        appearance for missing tracks" — appearance is folded into the same score
        so it can break ties even when a competing track was never actually lost
        (the crossing-paths case), not just after a detection gap.

        Returns {detection_index: (track_id, matched_via)}.
        """
        assignments: Dict[int, Tuple[int, str]] = {}

        active_tracks = {
            tid: t for tid, t in self.tracks.items() if t.status == "active"
        }

        if not active_tracks:
            for det_idx in range(len(seg_results)):
                assignments[det_idx] = (self._new_track_id(), "new_track")
            return assignments

        track_ids = list(active_tracks.keys())
        n_det = len(seg_results)
        n_track = len(track_ids)

        NO_MATCH = 1e6
        cost = np.full((n_det, n_track), NO_MATCH)
        matched_via_grid = [[None] * n_track for _ in range(n_det)]

        for i, seg in enumerate(seg_results):
            for j, tid in enumerate(track_ids):
                track = active_tracks[tid]
                iou = compute_iou(seg["bbox"], track.last_bbox)

                appearance_sim = 0.0
                has_appearance = seg["appearance"] is not None and bool(track.memory_bank)
                if has_appearance:
                    appearance_sim = max(
                        self._appearance_similarity(seg["appearance"], banked)
                        for banked in track.memory_bank
                    )

                iou_ok = iou > self.match_iou_threshold
                reid_ok = appearance_sim > self.reid_similarity_threshold
                if not (iou_ok or reid_ok):
                    continue

                # IoU dominates for continuous tracking; appearance is weighted in
                # to resolve ambiguity (crossing paths) and to drive matches once
                # IoU alone gives no signal (long-gap reappearance). Note: reid_ok
                # is NOT gated on frames_since_last_seen — that value reflects the
                # END of the previous frame and hasn't been aged for this frame
                # yet, so gating on it would make appearance-reid unavailable on
                # exactly the frame a track first fails its IoU match (it only
                # becomes "lost" from the *next* frame's perspective). Appearance
                # similarity alone is the eligibility signal; the threshold is
                # what keeps this from matching implausible pairs.
                score = iou + (0.5 * appearance_sim if has_appearance else 0.0)
                cost[i, j] = -score
                matched_via_grid[i][j] = "iou_match" if iou_ok else "appearance_reid"

        row_ind, col_ind = linear_sum_assignment(cost)

        used_dets = set()
        for i, j in zip(row_ind, col_ind):
            if cost[i, j] >= NO_MATCH:
                continue
            tid = track_ids[j]
            assignments[i] = (tid, matched_via_grid[i][j])
            used_dets.add(i)

        for i in range(n_det):
            if i not in used_dets:
                assignments[i] = (self._new_track_id(), "new_track")

        return assignments

    def _age_unmatched_tracks(self, matched_ids: set, frame_idx: int) -> None:
        """Increment frames_since_last_seen for active tracks not matched this
        frame, and terminate any that exceed their applicable hold time."""
        for tid, track in self.tracks.items():
            if track.status != "active" or tid in matched_ids:
                continue

            track.frames_since_last_seen = frame_idx - track.last_frame_seen
            track.occlusion_state = "lost"

            max_lost = (
                self.max_lost_frames_dense
                if track.last_lost_in_density
                else self.max_lost_frames_isolated
            )
            if track.frames_since_last_seen > max_lost:
                track.status = "terminated"
                logger.debug(
                    f"[tracking] Track {tid} terminated after "
                    f"{track.frames_since_last_seen} lost frames "
                    f"(dense={track.last_lost_in_density})"
                )

    def get_track_summaries(self) -> List[Dict]:
        """Summary rows for the tracks_meta collection (first/last frame, status)."""
        summaries = []
        for tid, track in self.tracks.items():
            summaries.append({
                "track_id": tid,
                "last_frame": track.last_frame_seen,
                "status": track.status,
            })
        return summaries
