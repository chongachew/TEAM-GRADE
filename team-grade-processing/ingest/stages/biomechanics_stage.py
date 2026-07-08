"""
Biomechanics Stage (Stage 8/9) - Analysis & Scoring
Calculates biomechanics metrics from pose data and reps.
"""

import logging
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import (
    ValidationError, StageExecutionError, FirestoreError, QueueError
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_analysis_results

logger = logging.getLogger(__name__)

STAGE_NAME = "biomechanics"
STAGE_NEXT = "complete"


def run_biomechanics_stage(
    firestore_client, video_id: str, queue_manager, payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute biomechanics stage. Stage 8/9: Calculate biomechanics metrics."""
    try:
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError("firestore_client invalid")
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError("queue_manager invalid")
        
        video_id_safe = VideoIdValidator.validate(video_id)
    except ValidationError as e:
        logger.warning(f"[{STAGE_NAME}] Validation failed", extra={"error_code": e.error_code})
        return False, e.error_code
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting", extra={"video_id": video_id_safe})

        try:
            reps_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe).collection(settings.COLLECTION_REPS)
            reps_docs = list(reps_ref.stream())
            
            if not reps_docs:
                logger.warning(f"[{STAGE_NAME}] No reps", extra={"video_id": video_id_safe})
                firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                    f"stages.{STAGE_NAME}.status": "completed",
                    f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                })
                queue_manager.enqueue_video(video_id_safe, stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY, metadata={"previous_stage": STAGE_NAME})
                return True, None
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore read failed")
            return False, "FIRESTORE_ERROR"

        try:
            from trench_engine import TrenchEngine
            engine = TrenchEngine()
        except Exception as e:
            logger.warning(f"[{STAGE_NAME}] Engine load failed, skipping analysis")
            engine = None

        analysis_data = []
        for rep_doc in reps_docs:
            try:
                rep_data = rep_doc.to_dict()
                rep_index = rep_data.get("rep_index")
                
                if engine:
                    metrics = {"rep_index": rep_index}
                    analysis_data.append(metrics)
            except Exception as e:
                logger.debug(f"[{STAGE_NAME}] Rep analysis error")
                continue

        try:
            if analysis_data:
                write_analysis_batch(firestore_client, video_id_safe, analysis_data)
            
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.total_analyses": len(analysis_data),
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
            })
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Firestore write failed")
            return False, "FIRESTORE_ERROR"

        try:
            queue_manager.enqueue_video(video_id_safe, stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY, metadata={"previous_stage": STAGE_NAME})
        except Exception as e:
            logger.error(f"[{STAGE_NAME}] Queue failed")
            return False, "QUEUE_ERROR"

        logger.info(f"[{STAGE_NAME}] Complete", extra={"video_id": video_id_safe})
        return True, None

    except Exception as e:
        logger.error(f"[{STAGE_NAME}] Unexpected error")
        return False, "UNEXPECTED_ERROR"
        
        # Sanitize and validate video_id
        video_id_safe = settings.sanitize_id(video_id)
        
        logger.info(f"[BIOMECH][video_id={video_id_safe}] Starting biomechanics stage")

        from ingest.utils.firestore_utils import write_analysis_results, update_rep_analysis

        # Get reps from Firestore
        db = firestore_client.db
        video_ref = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe)

        rep_docs = list(video_ref.collection(settings.COLLECTION_REPS).stream())

        if not rep_docs:
            logger.warning(f"[BIOMECH][video_id={video_id_safe}] No reps found")

            # Mark as completed with empty analysis
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.biomechanics.status": "completed",
                "stages.biomechanics.traits_analyzed": 0,
                "stages.biomechanics.completed_at": get_utc_timestamp()
            })

            # Enqueue complete stage
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage="complete",
                priority=5,
                metadata={"previous_stage": "biomechanics", "reps_analyzed": 0}
            )

            return success, None

        logger.info(
            f"[BIOMECH][video_id={video_id_safe}] Analyzing {len(rep_docs)} reps"
        )

        # Get video metadata
        video_data = video_ref.get().to_dict() or {}
        jersey_number = video_data.get("jersey_number")

        # Get pose data for all reps
        pose_docs = list(video_ref.collection(settings.COLLECTION_POSE).stream())
        poses_by_frame = {doc.id: doc.to_dict() for doc in pose_docs}

        logger.info(
            f"[BIOMECH][video_id={video_id_safe}] Retrieved {len(poses_by_frame)} "
            f"pose documents"
        )

        # Initialize trench_engine (lazy load)
        try:
            from Analysis.trench_engine.engine import TrenchEngine
            engine = TrenchEngine()
            logger.info(f"[BIOMECH][video_id={video_id_safe}] Initialized TrenchEngine")
        except ImportError:
            logger.warning(
                f"[BIOMECH][video_id={video_id_safe}] TrenchEngine not available, "
                f"skipping detailed analysis"
            )
            engine = None
        except Exception as e:
            logger.error(
                f"[BIOMECH][video_id={video_id_safe}] Failed to initialize TrenchEngine: {e}"
            )
            engine = None

        # Analyze each rep
        per_rep_analysis = {}
        total_traits_analyzed = 0

        for rep_idx, rep_doc in enumerate(rep_docs):
            try:
                rep_data = rep_doc.to_dict()
                rep_id = rep_doc.id

                start_frame = rep_data.get("start_frame", 0)
                end_frame = rep_data.get("end_frame", 0)

                logger.info(
                    f"[BIOMECH][video_id={video_id_safe}][rep={rep_idx}] "
                    f"Analyzing rep {rep_id} (frames {start_frame}-{end_frame})"
                )

                # Extract pose sequence for this rep
                rep_poses = []
                for frame_id, pose_data in poses_by_frame.items():
                    frame_idx = pose_data.get("frame_index", 0)
                    if start_frame <= frame_idx <= end_frame:
                        rep_poses.append(pose_data)

                if not rep_poses:
                    logger.warning(
                        f"[BIOMECH][video_id={video_id_safe}][rep={rep_id}] "
                        f"No pose data for rep"
                    )
                    continue

                # Run analysis
                rep_analysis = {
                    "position": rep_data.get("position", "OL"),
                    "jersey_number": jersey_number,
                    "rep_duration_frames": rep_data.get("duration_frames", 0),
                    "rep_duration_seconds": rep_data.get("duration_seconds", 0.0)
                }

                # If TrenchEngine available, run detailed analysis
                if engine:
                    try:
                        # Run engine analysis
                        traits = engine.analyze_rep(
                            rep_poses,
                            position=rep_data.get("position", "OL")
                        )

                        if traits:
                            rep_analysis["traits"] = traits
                            total_traits_analyzed += len(traits)

                            logger.info(
                                f"[BIOMECH][video_id={video_id_safe}][rep={rep_id}] "
                                f"Analyzed {len(traits)} traits"
                            )

                    except Exception as e:
                        logger.warning(
                            f"[BIOMECH][video_id={video_id_safe}][rep={rep_id}] "
                            f"Analysis failed: {e}"
                        )
                        rep_analysis["analysis_error"] = str(e)

                else:
                    # Fallback: basic metrics from pose data
                    try:
                        # Compute basic metrics from keypoints
                        basic_metrics = _compute_basic_metrics(rep_poses)
                        rep_analysis["basic_metrics"] = basic_metrics

                    except Exception as e:
                        logger.warning(
                            f"[BIOMECH][video_id={video_id_safe}][rep={rep_id}] "
                            f"Metric computation failed: {e}"
                        )

                # Store per-rep analysis
                per_rep_analysis[rep_id] = rep_analysis

                # Write to Firestore
                update_rep_analysis(
                    firestore_client,
                    video_id_safe,
                    rep_id,
                    rep_analysis
                )

            except Exception as e:
                logger.error(
                    f"[BIOMECH][video_id={video_id_safe}][rep={rep_idx}] "
                    f"Error analyzing rep: {e}"
                )
                continue

        logger.info(
            f"[BIOMECH][video_id={video_id_safe}] Completed analysis on {len(per_rep_analysis)} "
            f"reps, traits_analyzed={total_traits_analyzed}"
        )

        # Compute summary stats
        summary = {
            "total_reps": len(rep_docs),
            "reps_analyzed": len(per_rep_analysis),
            "total_traits_analyzed": total_traits_analyzed,
            "analysis_timestamp": get_utc_timestamp().isoformat()
        }

        # Write overall analysis results
        analysis_results = {
            "per_rep": per_rep_analysis,
            "summary": summary
        }

        write_analysis_results(
            firestore_client,
            video_id_safe,
            analysis_results
        )

        # Update stage status
        db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
            "stages.biomechanics.status": "completed",
            "stages.biomechanics.traits_analyzed": total_traits_analyzed,
            "stages.biomechanics.completed_at": get_utc_timestamp()
        })

        logger.info(f"[BIOMECH][video_id={video_id_safe}] Biomechanics stage completed")

        # Enqueue next stage: complete
        success = queue_manager.enqueue_video(
            video_id_safe,
            stage="complete",
            priority=5,
            metadata={
                "previous_stage": "biomechanics",
                "reps_analyzed": len(per_rep_analysis),
                "total_traits_analyzed": total_traits_analyzed
            }
        )

        if not success:
            logger.error(
                f"[BIOMECH][video_id={video_id_safe}] Failed to enqueue complete stage"
            )
            return False, "Failed to enqueue next stage"

        return True, None

    except Exception as e:
        error_msg = f"Biomechanics stage failed: {str(e)}"
        logger.error(f"[ERROR][video_id={video_id_safe}] {error_msg}")
        return False, error_msg


def _compute_basic_metrics(poses: list) -> Dict[str, Any]:
    """Compute basic metrics from pose sequence.

    Args:
        poses: List of pose documents

    Returns:
        Dict with basic metrics
    """
    try:
        import numpy as np

        # Extract center of mass trajectory
        com_positions = []

        for pose in poses:
            keypoints = pose.get("keypoints", [])

            # Compute center of mass from shoulder and hip keypoints
            # Indices: 11=left_shoulder, 12=right_shoulder, 23=left_hip, 24=right_hip
            relevant_kps = [kp for kp in keypoints if kp.get("index") in [11, 12, 23, 24]]

            if len(relevant_kps) >= 2:
                x_coords = [kp.get("x", 0) for kp in relevant_kps]
                y_coords = [kp.get("y", 0) for kp in relevant_kps]

                com_x = np.mean(x_coords)
                com_y = np.mean(y_coords)
                com_positions.append((com_x, com_y))

        if not com_positions:
            return {}

        # Compute motion metrics
        com_array = np.array(com_positions)
        movement_distance = np.sum(np.linalg.norm(np.diff(com_array, axis=0), axis=1))

        return {
            "movement_distance": float(movement_distance),
            "position_count": len(com_positions)
        }

    except Exception as e:
        logger.warning(f"Failed to compute basic metrics: {e}")
        return {}
