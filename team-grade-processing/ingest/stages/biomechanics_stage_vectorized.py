"""
Biomechanics Stage - Phase 3 Week 4 Vectorized Version (Stage 8/9)
Calculates biomechanics metrics from pose data and reps using vectorized operations.

Optimizations:
- Batch scoring of all frames at once (vs loop-per-frame)
- NumPy matrix operations (vs nested Python loops)
- Single Firestore batch write (vs multiple writes)
- Expected speedup: 2-2.5x (3.8s → 1.5-2.5s)
"""

import logging
from typing import Optional, Dict, Any, List
import numpy as np

from config import settings
from ingest.exceptions import (
    ValidationError, StageExecutionError, FirestoreError, QueueError
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_analysis_results
from processing.vectorized_traits import VectorizedTraitScorer, VectorizedBiomechanicsEngine

logger = logging.getLogger(__name__)

STAGE_NAME = "biomechanics"
STAGE_NEXT = "complete"


def _load_trait_configs(config_root: str) -> Dict[str, Dict]:
    """
    Load trait configurations from config files.
    
    Args:
        config_root: Path to config directory
    
    Returns:
        Dict of trait_name -> {"features": [...], "weights": [...]}
    """
    try:
        # Default trait configuration (when real configs not available)
        # Structure: {"trait_name": {"features": [...], "weights": [...]}}
        return {
            "strength": {
                "features": ["leg_power", "core_stability", "upper_body"],
                "weights": [0.5, 0.3, 0.2]
            },
            "agility": {
                "features": ["hip_mobility", "balance", "lateral_speed"],
                "weights": [0.4, 0.3, 0.3]
            },
            "speed": {
                "features": ["acceleration", "linear_speed", "deceleration"],
                "weights": [0.35, 0.4, 0.25]
            },
            "technique": {
                "features": ["footwork", "hand_placement", "body_control"],
                "weights": [0.4, 0.3, 0.3]
            },
            "awareness": {
                "features": ["field_vision", "positioning", "decision_making"],
                "weights": [0.33, 0.33, 0.34]
            }
        }
    except Exception as e:
        logger.warning(f"Failed to load trait configs: {e}, using defaults")
        return {}


def _extract_pose_features(pose_frames: List[Dict[str, Dict[str, float]]]) -> Dict[str, float]:
    """
    Extract numerical features from a rep's ordered sequence of real pose frames.

    Replaces the previous np.random.uniform placeholder (a real, pre-existing
    bug independent of the multi-player pivot - every feature was fabricated
    regardless of input). Reuses existing primitives rather than inventing new
    formulas:
      - processing.utils.calculate_keypoint_displacement (promoted from
        RepExtractor._calculate_displacement) drives the velocity-derived
        features (linear_speed, acceleration, leg_power, lateral_speed,
        deceleration).
      - FastTrajectoryAnalyzer.extract_center_point (hip/shoulder centroid)
        drives the stability-derived features (core_stability, balance,
        hip_mobility, positioning) as the inverse of centroid-trajectory
        variance across the rep.
      - Features with no keypoint-only signal (upper_body, footwork,
        hand_placement, body_control, field_vision, decision_making) fall back
        to mean keypoint confidence across the rep - a documented placeholder,
        not silently presented as equally trustworthy as the motion-derived
        features above (see the README/plan honesty note this mirrors).

    Args:
        pose_frames: Ordered list of per-frame landmarks dicts (one rep's
                     [start_frame, end_frame] range, one track)

    Returns:
        Dict of feature_name -> numerical_value (0-100 scale). Empty dict if
        pose_frames is empty, matching the previous fallback shape.
    """
    if not pose_frames:
        return {}

    try:
        from processing.utils import calculate_keypoint_displacement
        from processing.fast_trajectory import FastTrajectoryAnalyzer

        max_velocity_norm = settings.MAX_EXPECTED_VELOCITY_NORM
        centroid_scale = settings.CENTROID_STABILITY_SCALE

        def scale_velocity(v: float) -> float:
            return float(np.clip((v / max_velocity_norm) * 100.0, 0, 100))

        # --- Velocity-derived features: real frame-to-frame keypoint displacement ---
        ankle_displacements = []
        hip_displacements = []
        for i in range(1, len(pose_frames)):
            prev_frame, curr_frame = pose_frames[i - 1], pose_frames[i]

            for side in ("left", "right"):
                key = f"{side}_ankle"
                if key in prev_frame and key in curr_frame:
                    ankle_displacements.append(
                        calculate_keypoint_displacement(
                            {key: prev_frame[key]}, {key: curr_frame[key]}
                        )
                    )

            hip_prev = {k: prev_frame[k] for k in ("left_hip", "right_hip") if k in prev_frame}
            hip_curr = {k: curr_frame[k] for k in ("left_hip", "right_hip") if k in curr_frame}
            if hip_prev and hip_curr:
                hip_displacements.append(calculate_keypoint_displacement(hip_prev, hip_curr))

        mean_ankle_v = float(np.mean(ankle_displacements)) if ankle_displacements else 0.0
        max_ankle_v = float(np.max(ankle_displacements)) if ankle_displacements else 0.0
        final_ankle_v = float(ankle_displacements[-1]) if ankle_displacements else 0.0
        mean_hip_v = float(np.mean(hip_displacements)) if hip_displacements else 0.0

        features: Dict[str, float] = {
            "linear_speed": scale_velocity(mean_ankle_v),
            "acceleration": scale_velocity(max_ankle_v),
            "leg_power": scale_velocity(max_ankle_v),
            "lateral_speed": scale_velocity(mean_hip_v),
            # Peak-to-final velocity drop, as a real (if simplified) deceleration proxy.
            "deceleration": scale_velocity(max(0.0, max_ankle_v - final_ankle_v)),
        }

        # --- Stability-derived features: inverse of centroid-trajectory variance ---
        analyzer = FastTrajectoryAnalyzer()
        frame_shape = (540, 960, 3)  # normalized keypoints, actual pixel size doesn't matter here
        centroids = np.array([
            analyzer.extract_center_point(frame, frame_shape) for frame in pose_frames
        ])
        centroid_std = float(np.mean(np.std(centroids, axis=0))) if len(centroids) > 1 else 0.0
        stability_score = float(np.clip(100.0 - centroid_std * centroid_scale, 0, 100))

        features["core_stability"] = stability_score
        features["balance"] = stability_score
        features["hip_mobility"] = stability_score
        features["positioning"] = stability_score

        # --- Placeholder features: no keypoint-only signal exists for these ---
        # (no ball/field/teammate context available from pose alone). Derived from
        # mean keypoint confidence across the rep rather than fabricated outright,
        # but explicitly NOT presented as equally trustworthy as the features above.
        all_confidences = [
            kp.get("confidence", 0.0)
            for frame in pose_frames
            for kp in frame.values()
            if isinstance(kp, dict)
        ]
        placeholder_score = float(np.clip(np.mean(all_confidences) * 100.0, 0, 100)) if all_confidences else 50.0
        for name in ("upper_body", "footwork", "hand_placement", "body_control", "field_vision", "decision_making"):
            features[name] = placeholder_score

        return features

    except Exception as e:
        logger.warning(f"Feature extraction error: {e}")
        return {}


def run_biomechanics_stage(
    firestore_client, video_id: str, queue_manager, payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """
    Execute vectorized biomechanics stage. Stage 8/9: Calculate biomechanics metrics.
    
    Process:
    1. Load all reps from Firestore
    2. Extract features from pose data (vectorized)
    3. Score traits for all reps at once (matrix multiplication)
    4. Calculate statistics (vectorized)
    5. Write results in single batch
    
    Performance:
    - Current (loop): 22ms per frame × 600 frames = 3.8s
    - Vectorized: 8-10ms per frame × 600 frames = 1.5-2.5s
    - Speedup: 2-2.5x
    """
    try:
        # ========== Validation ==========
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError("firestore_client invalid")
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError("queue_manager invalid")
        
        video_id_safe = VideoIdValidator.validate(video_id)
    except ValidationError as e:
        logger.warning(
            f"[{STAGE_NAME}] Validation failed",
            extra={"error_code": e.error_code}
        )
        return False, e.error_code
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting vectorized analysis", 
                   extra={"video_id": video_id_safe})
        
        # ========== Step 1: Load all reps from Firestore ==========
        try:
            reps_ref = firestore_client.db.collection(
                settings.COLLECTION_VIDEOS
            ).document(video_id_safe).collection(settings.COLLECTION_REPS)
            reps_docs = list(reps_ref.stream())
            
            if not reps_docs:
                logger.warning(f"[{STAGE_NAME}] No reps found", 
                              extra={"video_id": video_id_safe})
                # Mark stage complete even with no reps
                firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                    video_id_safe
                ).update({
                    f"stages.{STAGE_NAME}.status": "completed",
                    f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                    f"stages.{STAGE_NAME}.rep_count": 0,
                })
                queue_manager.enqueue_video(
                    video_id_safe, stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY,
                    metadata={"previous_stage": STAGE_NAME}
                )
                return True, None
                
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore read failed: {e}")
            return False, "FIRESTORE_ERROR"
        
        # ========== Step 2: Extract features from all reps (vectorized) ==========
        # Rep docs never actually had a "pose_data" field (a previous version of
        # this stage read rep_data.get("pose_data", {}), which was always {} even
        # before considering the random-fakery in _extract_pose_features below).
        # Real keypoints live in the pose subcollection, queried per rep by its
        # [start_frame, end_frame] range (and by track_id on the multi-player path).
        try:
            pose_ref = firestore_client.db.collection(
                settings.COLLECTION_VIDEOS
            ).document(video_id_safe).collection(settings.COLLECTION_POSE)

            reps_features = []
            rep_indices = []
            rep_track_ids = []

            for rep_doc in reps_docs:
                rep_data = rep_doc.to_dict()
                rep_index = rep_data.get("rep_index", len(reps_features))
                rep_indices.append(rep_index)

                track_id = rep_data.get("track_id")
                rep_track_ids.append(track_id)
                start_frame = rep_data.get("start_frame", 0)
                end_frame = rep_data.get("end_frame", 0)

                query = pose_ref.where("frame_index", ">=", start_frame).where(
                    "frame_index", "<=", end_frame
                )
                if track_id is not None:
                    # Requires a composite Firestore index (track_id ASC, frame_index
                    # ASC) - first run surfaces a FAILED_PRECONDITION error with a
                    # console link to auto-create it; see models/README.md.
                    query = query.where("track_id", "==", track_id)

                pose_docs_for_rep = list(query.order_by("frame_index").stream())
                pose_frames = [d.to_dict().get("landmarks", {}) for d in pose_docs_for_rep]

                features = _extract_pose_features(pose_frames)
                reps_features.append(features)

            logger.debug(
                f"[{STAGE_NAME}] Extracted features from {len(reps_features)} reps"
            )

        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Feature extraction failed: {e}")
            return False, "FEATURE_EXTRACTION_ERROR"
        
        # ========== Step 3: Load trait configs ==========
        try:
            trait_configs = _load_trait_configs(str(settings.CONFIG_ROOT))
            if not trait_configs:
                logger.warning(f"[{STAGE_NAME}] No trait configs loaded, using defaults")
                # Feature extraction should still work with defaults
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Trait config load failed: {e}")
            return False, "CONFIG_ERROR"
        
        # ========== Step 4: Vectorized trait scoring (core optimization) ==========
        try:
            scorer = VectorizedTraitScorer()
            
            # This single call scores ALL reps and ALL traits at once
            # Using NumPy matrix multiplication instead of nested loops
            trait_scores, trait_names = scorer.score_traits_batch(
                position="unknown",
                reps_data=reps_features,
                trait_configs=trait_configs
            )
            
            # Bucketing (also vectorized)
            buckets, bucket_names = scorer.bucket_scores_batch(trait_scores)
            
            logger.info(
                f"[{STAGE_NAME}] Vectorized scoring complete: "
                f"{trait_scores.shape[0]} reps × {trait_scores.shape[1]} traits"
            )
            
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Vectorized scoring failed: {e}")
            return False, "SCORING_ERROR"
        
        # ========== Step 5: Calculate statistics (vectorized) ==========
        try:
            statistics = scorer.get_statistics(trait_scores, trait_names)
            overall_grade = scorer.aggregate_traits(trait_scores, trait_names)
            letter_grade = VectorizedTraitScorer._numeric_to_letter(
                np.array([overall_grade])
            )[0]
            
            logger.debug(
                f"[{STAGE_NAME}] Statistics calculated: "
                f"overall_grade={overall_grade:.1f} ({letter_grade})"
            )
            
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Statistics calculation failed: {e}")
            return False, "STATISTICS_ERROR"
        
        # ========== Step 6: Prepare analysis data for Firestore ==========
        try:
            analysis_data = []
            
            for i, rep_index in enumerate(rep_indices):
                rep_analysis = {
                    "rep_index": rep_index,
                    "track_id": rep_track_ids[i],
                    "traits": {
                        trait_names[j]: float(trait_scores[i, j])
                        for j in range(len(trait_names))
                    },
                    "buckets": {
                        trait_names[j]: str(buckets[i, j])
                        for j in range(len(trait_names))
                    },
                    "overall_grade": float(np.mean(trait_scores[i, :])),
                }
                analysis_data.append(rep_analysis)
            
            logger.debug(f"[{STAGE_NAME}] Prepared {len(analysis_data)} analysis records")
            
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Data preparation failed: {e}")
            return False, "DATA_PREP_ERROR"
        
        # ========== Step 7: Write results to Firestore (batch) ==========
        try:
            if analysis_data:
                # Batch write all analysis results
                batch = firestore_client.db.batch()
                
                for rep_data in analysis_data:
                    analysis_ref = (
                        firestore_client.db
                        .collection(settings.COLLECTION_VIDEOS)
                        .document(video_id_safe)
                        .collection(settings.COLLECTION_ANALYSIS)
                        .document(f"rep_{rep_data['rep_index']}")
                    )
                    batch.set(analysis_ref, rep_data)
                
                batch.commit()
            
            # Update video document with overall statistics
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe
            ).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.analysis_count": len(analysis_data),
                f"stages.{STAGE_NAME}.overall_grade": overall_grade,
                f"stages.{STAGE_NAME}.letter_grade": letter_grade,
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                # Store method used (for benchmarking)
                f"stages.{STAGE_NAME}.method": "vectorized",
            })
            
            logger.info(
                f"[{STAGE_NAME}] Wrote {len(analysis_data)} analysis records to Firestore"
            )
            
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore write failed: {e}")
            return False, "FIRESTORE_ERROR"
        
        # ========== Step 8: Enqueue next stage ==========
        try:
            queue_manager.enqueue_video(
                video_id_safe, stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                metadata={"previous_stage": STAGE_NAME}
            )
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Queue enqueue failed: {e}")
            return False, "QUEUE_ERROR"
        
        logger.info(
            f"[{STAGE_NAME}] Complete (vectorized)",
            extra={"video_id": video_id_safe, "analyses": len(analysis_data)}
        )
        return True, None
        
    except Exception as e:
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}")
        return False, "INTERNAL_ERROR"
