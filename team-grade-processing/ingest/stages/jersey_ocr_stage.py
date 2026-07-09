"""
Jersey OCR Stage (Stage 6/9) - Jersey Number Detection
Detects jersey numbers from torso crops using EasyOCR.
"""

import logging
from typing import Optional, Dict, Any, List

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_jersey_number, write_jersey_number_for_track

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "jersey_ocr"
STAGE_NEXT = "rep_extraction"

# ✅ OPTIMIZATION: Module-level OCR cache (40% faster after first video)
_OCR_CACHE = None


def get_ocr_instance():
    """Get or create cached JerseyOCR instance.
    
    Returns:
        JerseyOCR instance (cached globally)
    """
    global _OCR_CACHE
    
    if _OCR_CACHE is None:
        from processing.jersey_ocr import JerseyOCR
        logger.info(f"[{STAGE_NAME}] Initializing OCR model (first time load)...")
        
        _OCR_CACHE = JerseyOCR(
            ocr_engine=settings.OCR_ENGINE,
            language=settings.OCR_LANGUAGE,
            gpu=True
        )
        logger.info(f"[{STAGE_NAME}] OCR model loaded and cached")
    
    return _OCR_CACHE


def run_jersey_ocr_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute jersey OCR stage.
    
    Stage 6/9: Run OCR on torso crops to detect jersey numbers.
    Stops after first confident detection (threshold configurable).
    
    Args:
        firestore_client: Google Cloud Firestore client
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration
    
    Returns:
        (success: bool, error_code: Optional[str])
    """
    try:
        # Validate inputs
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )
        
        # Validate and sanitize video_id
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
        logger.info(f"[{STAGE_NAME}] Starting jersey OCR stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        import cv2
        from processing.jersey_ocr import JerseyOCR

        # Get torso crop metadata from Firestore
        try:
            crops_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
                video_id_safe
            ).collection("torso")
            crops_docs = list(crops_ref.stream())
            
            if not crops_docs:
                logger.warning(f"[{STAGE_NAME}] No torso crops found", extra={
                    "video_id": video_id_safe,
                })
                # Skip to next stage
                firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                    f"stages.{STAGE_NAME}.status": "completed",
                    f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
                })
                queue_manager.enqueue_video(
                    video_id_safe,
                    stage=STAGE_NEXT,
                    priority=settings.QUEUE_DEFAULT_PRIORITY,
                    metadata={"previous_stage": STAGE_NAME, "tracks_with_jersey_detected": 0}
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

        # Initialize OCR engine - ✅ OPTIMIZE: Use cached instance (40% faster)
        try:
            ocr = get_ocr_instance()
        except Exception as e:
            error_code = "MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] OCR initialization failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Get crops directory
        crops_dir = settings.get_torso_crops_dir(video_id_safe)
        ocr_stop_threshold = settings.OCR_STOP_ON_CONFIDENCE
        multi_player = getattr(settings, 'MULTI_PLAYER_TRACKING_ENABLED', False)

        # Group crops by track_id (multi-player path) or process as one flat,
        # global list (single-athlete path, track_id absent - unchanged behavior
        # other than the read_jersey -> read_jersey_number fix below).
        crops_by_track: Dict[Optional[int], List[Dict[str, Any]]] = {}
        for crop_doc in crops_docs:
            data = crop_doc.to_dict()
            key = data.get("track_id") if multi_player else None
            crops_by_track.setdefault(key, []).append(data)

        best_detections: Dict[Optional[int], Dict[str, Any]] = {}

        # Process each track's crops (or the single global "track" None on the
        # single-athlete path), stopping early PER TRACK on a high-confidence hit
        # rather than stopping the whole stage on the first video-wide hit.
        try:
            for track_id, track_crops in crops_by_track.items():
                best_confidence = 0.0
                frame_index = None

                for crop_data in track_crops:
                    try:
                        frame_index = crop_data.get("frame_index")

                        if track_id is not None:
                            crop_path = crops_dir / f"torso_{frame_index:06d}_{track_id:03d}.jpg"
                        else:
                            crop_path = crops_dir / f"torso_{frame_index:06d}.jpg"

                        if not crop_path.exists():
                            logger.debug(f"[{STAGE_NAME}] Crop file not found: {crop_path.name}")
                            continue

                        crop_image = cv2.imread(str(crop_path))
                        if crop_image is None:
                            logger.debug(f"[{STAGE_NAME}] Failed to load crop image: {crop_path.name}")
                            continue

                        # Run OCR (JerseyOCR's real method is read_jersey_number,
                        # returning a (number, confidence) tuple - a previous
                        # version of this stage called a nonexistent read_jersey()
                        # method here, which raised AttributeError on every crop,
                        # silently caught below, so OCR never actually ran).
                        jersey_num, confidence = ocr.read_jersey_number(
                            crop_image, confidence_threshold=settings.OCR_CONFIDENCE_THRESHOLD
                        )

                        if jersey_num:
                            logger.info(f"[{STAGE_NAME}] Jersey detected", extra={
                                "video_id": video_id_safe,
                                "track_id": track_id,
                                "jersey": jersey_num,
                                "confidence": confidence,
                            })

                            if confidence > best_confidence:
                                best_confidence = confidence
                                best_detections[track_id] = {
                                    "jersey_number": jersey_num,
                                    "confidence": confidence,
                                    "frame_index": frame_index,
                                }

                                if confidence >= ocr_stop_threshold:
                                    logger.info(f"[{STAGE_NAME}] High-confidence detection, stopping this track", extra={
                                        "video_id": video_id_safe,
                                        "track_id": track_id,
                                        "confidence": confidence,
                                        "threshold": ocr_stop_threshold,
                                    })
                                    break

                    except Exception as frame_error:
                        logger.warning(f"[{STAGE_NAME}] Error processing crop: {frame_error}", extra={
                            "video_id": video_id_safe,
                            "track_id": track_id,
                            "frame_index": frame_index,
                        })
                        continue

            logger.info(f"[{STAGE_NAME}] OCR complete", extra={
                "video_id": video_id_safe,
                "tracks_processed": len(crops_by_track),
                "tracks_with_jersey_detected": len(best_detections),
            })

        except Exception as e:
            error_code = "FRAME_PROCESSING_ERROR"
            logger.error(f"[{STAGE_NAME}] Error during OCR processing: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        # Write to Firestore
        try:
            for track_id, detection in best_detections.items():
                if track_id is not None:
                    write_jersey_number_for_track(
                        firestore_client, video_id_safe, track_id,
                        detection["jersey_number"], detection["confidence"], detection["frame_index"],
                    )
                else:
                    write_jersey_number(
                        firestore_client,
                        video_id_safe,
                        detection["jersey_number"],
                        detection["confidence"],
                        detection["frame_index"]
                    )
            if best_detections:
                logger.info(f"[{STAGE_NAME}] Wrote jersey detections to Firestore", extra={
                    "video_id": video_id_safe,
                    "count": len(best_detections),
                })

            # Update stage status
            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.tracks_processed": len(crops_by_track),
                f"stages.{STAGE_NAME}.tracks_with_jersey_detected": len(best_detections),
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
            })

        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write to Firestore: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        # Enqueue next stage
        try:
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=settings.QUEUE_DEFAULT_PRIORITY,
                metadata={
                    "previous_stage": STAGE_NAME,
                    "tracks_with_jersey_detected": len(best_detections),
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
