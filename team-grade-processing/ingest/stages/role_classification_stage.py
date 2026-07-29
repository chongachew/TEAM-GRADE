"""
Role Classification Stage - Referee/Staff vs. Player Classification
Classifies each track's torso crop as "referee" (black/white striped
uniform) vs "player" via processing/uniform_classifier.py (classical CV, no
neural net) - lets the frontend stop drawing/tracking boxes over officials
without needing roster fine-tuning. Runs between torso_crop and jersey_ocr,
reading the SAME torso crops jersey_ocr_stage.py reads. Only enqueued when
settings.MULTI_PLAYER_TRACKING_ENABLED is true - a single-athlete video has
no role ambiguity to resolve (the one tracked person IS the athlete).
"""

import logging
from typing import Any, Dict, List, Optional

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_play, update_stage_status, upsert_track_meta
from ingest.s3_client import ensure_torso_crops_local

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "role_classification"
STAGE_NEXT = "jersey_ocr"


def run_role_classification_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute role classification stage.

    Classifies each multi-player track's torso crops as referee/player,
    sampling up to ROLE_CLASSIFICATION_MAX_CROPS_PER_TRACK crops spread
    across the track's lifetime (mirrors jersey_ocr_stage's own per-track
    convention) so one bad-angle/motion-blurred crop can't flip the whole
    track's classification. Writes the majority result to
    tracks_meta.role/role_confidence.

    Args:
        firestore_client: Postgres-backed client (Firestore-compat shim)
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration

    Returns:
        (success: bool, error_code: Optional[str])
    """
    try:
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )

        video_id_safe = VideoIdValidator.validate(video_id)

    except ValidationError as e:
        error_code = e.error_code
        logger.warning(f"[{STAGE_NAME}] Validation failed: {e}", extra={
            "video_id": e.video_id or video_id,
            "stage": STAGE_NAME,
            "error_code": error_code
        })
        return False, error_code

    try:
        logger.info(f"[{STAGE_NAME}] Starting role classification stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        import cv2
        from processing.uniform_classifier import classify_uniform, ROLE_REFEREE, ROLE_PLAYER

        play_index = (payload or {}).get("play_index")
        if play_index is not None and get_play(firestore_client, video_id_safe, play_index) is None:
            error_code = "PLAY_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No plays row found", extra={
                "video_id": video_id_safe,
                "play_index": play_index,
                "error_code": error_code
            })
            return False, error_code

        multi_player = getattr(settings, 'MULTI_PLAYER_TRACKING_ENABLED', False)
        if not multi_player:
            # No role ambiguity in single-athlete mode - nothing to classify.
            update_stage_status(
                firestore_client, video_id_safe, STAGE_NAME, "completed",
                metadata={"tracks_classified": 0},
                play_index=play_index,
            )
            queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                play_index=play_index,
                metadata={"previous_stage": STAGE_NAME},
            )
            return True, None

        try:
            crops_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe
            ).collection("torso")
            if play_index is not None:
                crops_ref = crops_ref.where("play_index", "==", play_index)
            crops_docs = list(crops_ref.stream())

            if not crops_docs:
                logger.warning(f"[{STAGE_NAME}] No torso crops found", extra={
                    "video_id": video_id_safe,
                })
                update_stage_status(
                    firestore_client, video_id_safe, STAGE_NAME, "completed",
                    play_index=play_index,
                )
                queue_manager.enqueue_video(
                    video_id_safe,
                    stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY,
                    play_index=play_index,
                    metadata={"previous_stage": STAGE_NAME, "tracks_classified": 0}
                )
                return True, None

            logger.info(f"[{STAGE_NAME}] Retrieved crop data", extra={
                "video_id": video_id_safe,
                "crop_count": len(crops_docs),
            })

        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to read crops from Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        crops_dir = settings.get_torso_crops_dir(video_id_safe)
        try:
            ensure_torso_crops_local(video_id_safe)
        except Exception as e:
            logger.debug(f"[{STAGE_NAME}] S3 torso-crops fallback unavailable (non-fatal): {e}", extra={
                "video_id": video_id_safe,
            })

        # Track_id is always present here (multi_player path only reaches
        # this point) - group and sort each track's crops by frame_index so
        # the sampled subset below is spread across the track's lifetime,
        # not just its earliest frames.
        crops_by_track: Dict[int, List[Dict[str, Any]]] = {}
        for crop_doc in crops_docs:
            data = crop_doc.to_dict()
            track_id = data.get("track_id")
            if track_id is None:
                continue
            crops_by_track.setdefault(track_id, []).append(data)
        for track_id in crops_by_track:
            crops_by_track[track_id].sort(key=lambda d: d.get("frame_index", 0))

        results: Dict[int, Dict[str, Any]] = {}

        try:
            for track_id, track_crops in crops_by_track.items():
                sample_n = settings.ROLE_CLASSIFICATION_MAX_CROPS_PER_TRACK
                if len(track_crops) > sample_n:
                    # Evenly spaced indices across the track's lifetime.
                    step = len(track_crops) / sample_n
                    sampled = [track_crops[int(i * step)] for i in range(sample_n)]
                else:
                    sampled = track_crops

                votes: List[str] = []
                confidences: List[float] = []

                for crop_data in sampled:
                    frame_index = crop_data.get("frame_index")
                    crop_path = crops_dir / f"torso_{frame_index:06d}_{track_id:03d}.jpg"
                    if not crop_path.exists():
                        logger.debug(f"[{STAGE_NAME}] Crop file not found: {crop_path.name}")
                        continue

                    crop_image = cv2.imread(str(crop_path))
                    if crop_image is None:
                        logger.debug(f"[{STAGE_NAME}] Failed to load crop image: {crop_path.name}")
                        continue

                    role, confidence = classify_uniform(crop_image)
                    votes.append(role)
                    confidences.append(confidence)

                if not votes:
                    continue

                referee_votes = [c for r, c in zip(votes, confidences) if r == ROLE_REFEREE]
                player_votes = [c for r, c in zip(votes, confidences) if r == ROLE_PLAYER]

                if len(referee_votes) > len(player_votes):
                    final_role = ROLE_REFEREE
                    final_confidence = sum(referee_votes) / len(referee_votes)
                elif player_votes:
                    final_role = ROLE_PLAYER
                    final_confidence = sum(player_votes) / len(player_votes)
                else:
                    # Every sampled crop was "uncertain" - nothing confident
                    # to report for this track.
                    continue

                results[track_id] = {"role": final_role, "role_confidence": final_confidence}

            logger.info(f"[{STAGE_NAME}] Classification complete", extra={
                "video_id": video_id_safe,
                "tracks_processed": len(crops_by_track),
                "tracks_classified": len(results),
            })

        except Exception as e:
            error_code = "FRAME_PROCESSING_ERROR"
            logger.error(f"[{STAGE_NAME}] Error during classification: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        try:
            for track_id, result in results.items():
                upsert_track_meta(
                    firestore_client, video_id_safe, track_id, result, play_index=play_index,
                )

            update_stage_status(
                firestore_client, video_id_safe, STAGE_NAME, "completed",
                metadata={
                    "tracks_processed": len(crops_by_track),
                    "tracks_classified": len(results),
                },
                play_index=play_index,
            )

        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write to Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        try:
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                play_index=play_index,
                metadata={
                    "previous_stage": STAGE_NAME,
                    "tracks_classified": len(results),
                }
            )
            if not success:
                error_code = "QUEUE_ERROR"
                logger.error(f"[{STAGE_NAME}] enqueue_video returned False", extra={
                    "video_id": video_id_safe,
                    "next_stage": STAGE_NEXT,
                    "error_code": error_code
                })
                return False, error_code
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "next_stage": STAGE_NEXT,
        })

        return True, None

    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code
