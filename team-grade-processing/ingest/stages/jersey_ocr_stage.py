"""
Jersey OCR Stage (Stage 6/9)
Detects jersey numbers from torso crops using EasyOCR.
Keeps best detection (highest confidence); stops after threshold exceeded.
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path
import cv2

from config import settings
from ingest.utils.firestore_utils import get_utc_timestamp
from ingest.stages import STAGE_JERSEY_OCR, STAGE_REP_EXTRACTION, STAGE_SEQUENCE

logger = logging.getLogger(__name__)


def run_jersey_ocr_stage(
    firestore_client,
    video_id: str,
    queue_manager,
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute jersey OCR stage.

    Stage 6/9: Run OCR on torso crops to detect jersey numbers.
    Stops after first confident detection (threshold: 0.8 by default).

    Args:
        firestore_client: Firestore client instance (must have .db attribute)
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance (must have .enqueue_video method)
        payload: Stage payload (optional, not used in Stage 6)

    Returns:
        (success: bool, error_message: Optional[str])
    """
    try:
        # Validate inputs
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValueError("firestore_client is None or missing .db attribute")
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValueError("queue_manager is None or missing .enqueue_video method")
        
        # Sanitize and validate video_id
        video_id_safe = settings.sanitize_id(video_id)
        
        logger.info(f"[OCR][video_id={video_id_safe}] Starting jersey OCR stage")

        from ..processing.jersey_ocr import JerseyOCR
        from ..utils.firestore_utils import write_jersey_number

        # Get torso crop metadata from Firestore
        db = firestore_client.db
        video_ref = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe)

        torso_docs = list(video_ref.collection(settings.COLLECTION_TORSO).stream())

        if not torso_docs:
            logger.warning(f"[OCR][video_id={video_id_safe}] No torso crops found")
            # Mark stage as completed (skipped since no crops) and enqueue next
            _update_stage_status(video_ref)
            
            priority = getattr(settings, 'QUEUE_DEFAULT_PRIORITY', 5)
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_REP_EXTRACTION,
                priority=priority,
                metadata={"previous_stage": STAGE_JERSEY_OCR, "jersey_found": False}
            )
            return success, None

        logger.info(
            f"[OCR][video_id={video_id_safe}] Processing {len(torso_docs)} torso crops"
        )

        # Initialize OCR engine with retry: GPU=True first, then GPU=False
        ocr_engine = getattr(settings, 'OCR_ENGINE', 'easyocr')
        ocr_language = getattr(settings, 'OCR_LANGUAGE', 'en')
        try:
            ocr = JerseyOCR(ocr_engine=ocr_engine, language=ocr_language, gpu=True)
        except Exception as e:
            logger.warning(
                f"[OCR][video_id={video_id_safe}] Failed to initialize OCR with GPU=True: {e}; "
                f"retrying with GPU=False"
            )
            try:
                ocr = JerseyOCR(ocr_engine=ocr_engine, language=ocr_language, gpu=False)
            except Exception as e2:
                logger.error(f"[OCR][video_id={video_id_safe}] OCR initialization failed: {e2}")
                return False, f"Failed to initialize OCR: {e2}"

        # Get crops directory using centralizedpath helper
        crops_dir = settings.get_torso_crops_dir(video_id_safe)
        if not crops_dir.exists():
            logger.warning(f"[OCR][video_id={video_id_safe}] Crops directory does not exist: {crops_dir}")

        best_detection = None
        best_confidence = 0.0
        ocr_stop_threshold = getattr(settings, 'OCR_STOP_ON_CONFIDENCE', 0.8)

        # Process each torso crop
        for torso_doc in torso_docs:
            try:
                torso_data = torso_doc.to_dict()
                frame_id = torso_doc.id

                # Build full path: use sanitized directory + consistent naming
                crop_file = crops_dir / f"torso_{frame_id}.jpg"

                if not crop_file.exists():
                    logger.warning(
                        f"[OCR][video_id={video_id_safe}][frame={frame_id}] Crop file not found: {crop_file}"
                    )
                    continue

                # Load crop image
                crop_image = cv2.imread(str(crop_file))
                if crop_image is None:
                    logger.warning(
                        f"[OCR][video_id={video_id_safe}][frame={frame_id}] Failed to load crop"
                    )
                    continue

                # Run OCR
                ocr_result = ocr.read_jersey(crop_image)

                if ocr_result and ocr_result.get("jersey_number"):
                    jersey_num = ocr_result.get("jersey_number")
                    confidence = ocr_result.get("confidence", 0.0)

                    logger.info(
                        f"[OCR][video_id={video_id_safe}][frame={frame_id}] "
                        f"Jersey detected: {jersey_num} (conf={confidence:.2f})"
                    )

                    # Keep best detection
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_detection = {
                            "jersey_number": jersey_num,
                            "confidence": confidence,
                            "frame_id": int(frame_id),
                            "source_frame_index": torso_data.get("frame_index", 0)
                        }

                        # Stop if confident enough (configurable threshold)
                        if confidence > ocr_stop_threshold:
                            logger.info(
                                f"[OCR][video_id={video_id_safe}] High-confidence detection (>{ocr_stop_threshold}), stopping"
                            )
                            break

            except Exception as e:
                logger.warning(
                    f"[OCR][video_id={video_id_safe}][frame={frame_id}] Error running OCR: {e}"
                )
                continue

        # Write jersey number if detected
        if best_detection:
            logger.info(
                f"[OCR][video_id={video_id_safe}] Best detection: "
                f"{best_detection['jersey_number']} (conf={best_detection['confidence']:.2f})"
            )

            write_jersey_number(
                firestore_client,
                video_id_safe,
                best_detection["jersey_number"],
                best_detection["confidence"],
                best_detection["source_frame_index"]
            )
        else:
            logger.warning(
                f"[OCR][video_id={video_id_safe}] No jersey number detected"
            )

        # Update stage status (consolidated, DRY)
        _update_stage_status(video_ref)

        logger.info(f"[OCR][video_id={video_id_safe}] Jersey OCR stage completed")

        # Enqueue next stage: rep_extraction
        priority = getattr(settings, 'QUEUE_DEFAULT_PRIORITY', 5)
        success = queue_manager.enqueue_video(
            video_id_safe,
            stage=STAGE_REP_EXTRACTION,
            priority=priority,
            metadata={
                "previous_stage": STAGE_JERSEY_OCR,
                "jersey_found": bool(best_detection),
                "jersey_number": best_detection["jersey_number"] if best_detection else None
            }
        )

        if not success:
            logger.error(
                f"[OCR][video_id={video_id_safe}] Failed to enqueue rep_extraction stage"
            )
            return False, "Failed to enqueue next stage"

        return True, None

    except ValueError as ve:
        # Validation error: input invalid, don't retry
        error_msg = f"Jersey OCR stage validation failed: {str(ve)}"
        logger.error(f"[OCR][video_id={video_id_safe}] {error_msg}")
        return False, error_msg
    except Exception as e:
        # Transient error: might succeed on retry
        error_msg = f"Jersey OCR stage failed: {str(e)}"
        logger.error(f"[OCR][video_id={video_id_safe}] {error_msg}")
        return False, error_msg


def _update_stage_status(video_ref):
    """Helper: Mark jersey_ocr stage as completed in Firestore (DRY)."""
    video_ref.update({
        f"stages.{STAGE_JERSEY_OCR}.status": "completed",
        f"stages.{STAGE_JERSEY_OCR}.completed_at": get_utc_timestamp()
    })
