"""
Detection Stage (multi-player pipeline) - Per-Frame Player Detection
Runs RF-DETR (Roboflow's real-time DINOv2-backbone detection/segmentation
transformer) on every extracted frame to find all players. Phase 1 uses
pretrained COCO weights' generic "person" class, zero-shot (no Roboflow
annotation/fine-tuning yet - see plan Phase 2). Only enqueued when
settings.MULTI_PLAYER_TRACKING_ENABLED is true.
"""

import logging
from typing import Optional, Dict, Any

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import (
    get_utc_timestamp,
    write_detections_batch,
    clear_detections_for_frame_range,
    get_play,
    update_stage_status,
)
from ingest.s3_client import ensure_frames_local
from ingest.frame_range import list_frame_paths_by_index
from processing.field_boundary import (
    point_in_field,
    refine_boundary_with_lines,
    segment_field_polygon,
)

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "detection"
STAGE_NEXT = "tracking"

# Module-level model cache (mirrors jersey_ocr_stage.py's _OCR_CACHE pattern) -
# RF-DETR weight download + load takes tens of seconds; reuse across videos.
_DETECTION_MODEL = None

_MODEL_SIZE_CLASSES = {
    "nano": "RFDETRSegNano",
    "small": "RFDETRSegSmall",
    "medium": "RFDETRSegMedium",
    "large": "RFDETRSegLarge",
    "xlarge": "RFDETRSegXLarge",
    "2xlarge": "RFDETRSeg2XLarge",
}


def get_detection_model():
    """Get or create the cached RF-DETR segmentation model instance."""
    global _DETECTION_MODEL

    if _DETECTION_MODEL is None:
        import rfdetr
        from ingest.gpu_utils import resolve_device

        class_name = _MODEL_SIZE_CLASSES.get(settings.DETECTION_MODEL_SIZE, "RFDETRSegMedium")
        model_cls = getattr(rfdetr, class_name)
        device = resolve_device(settings.DETECTION_USE_GPU)

        logger.info(f"[{STAGE_NAME}] Loading {class_name} (device={device})...")
        _DETECTION_MODEL = model_cls(device=device)
        logger.info(f"[{STAGE_NAME}] Detection model loaded")

    return _DETECTION_MODEL


def run_detection_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute player detection stage.

    Runs RF-DETR on every extracted frame, keeping only "person"-class detections
    above settings.DETECTION_CONFIDENCE_THRESHOLD, and writes them to the
    `detections` subcollection for the tracking stage to consume.

    Args:
        firestore_client: Google Cloud Firestore client
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
        logger.info(f"[{STAGE_NAME}] Starting detection stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        import cv2
        import torch

        play_index = (payload or {}).get("play_index")
        start_frame = end_frame = None
        if play_index is not None:
            play = get_play(firestore_client, video_id_safe, play_index)
            if play is None:
                error_code = "PLAY_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] No plays row found", extra={
                    "video_id": video_id_safe,
                    "play_index": play_index,
                    "error_code": error_code
                })
                return False, error_code
            start_frame, end_frame = play["start_frame"], play["end_frame"]

        # Defensive guard: normally already-local (same worker process as
        # frame_extraction), but a retry could be picked up by a different
        # worker instance - cheap check-then-fetch from S3.
        try:
            ensure_frames_local(video_id_safe)
        except Exception as e:
            logger.debug(f"[{STAGE_NAME}] S3 frames fallback unavailable (non-fatal): {e}", extra={
                "video_id": video_id_safe,
            })
        # Keyed by the real frame_index parsed from each filename, not list
        # position - critical once this is filtered to one play's range,
        # since the first frame in a play starting at frame 500 must still
        # be labeled frame_index=500, not 0.
        frame_paths_by_index = list_frame_paths_by_index(video_id_safe, start_frame, end_frame)
        ordered_frames = sorted(frame_paths_by_index.items())

        if not ordered_frames:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No frames found", extra={
                "video_id": video_id_safe,
                "play_index": play_index,
                "error_code": error_code
            })
            return False, error_code

        logger.info(f"[{STAGE_NAME}] Found frames", extra={
            "video_id": video_id_safe,
            "frame_count": len(ordered_frames),
            "play_index": play_index,
        })

        try:
            model = get_detection_model()
        except Exception as e:
            error_code = "DETECTION_MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to load detection model: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        detections_out = []
        batch_size = settings.DETECTION_BATCH_SIZE

        try:
            for batch_start in range(0, len(ordered_frames), batch_size):
                batch = ordered_frames[batch_start: batch_start + batch_size]
                images = []
                frames_bgr = []
                valid_indices = []
                for frame_idx, path in batch:
                    frame = cv2.imread(str(path))
                    if frame is None:
                        logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_idx}")
                        continue
                    # RF-DETR expects RGB channel order; field_boundary's
                    # HSV-based segmentation expects the original BGR frame -
                    # keep both rather than converting back and forth.
                    images.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    frames_bgr.append(frame)
                    valid_indices.append(frame_idx)

                if not images:
                    continue

                # no_grad: this is inference-only, but without it RF-DETR's forward
                # pass still builds an autograd graph for every batch - each one
                # holding its own set of GPU-resident intermediate tensors (worse
                # for the "-seg" variants, whose mask branch has much bigger
                # intermediates than plain box detection) that only gets freed once
                # nothing references that graph anymore.
                with torch.no_grad():
                    results = model.predict(images, threshold=settings.DETECTION_CONFIDENCE_THRESHOLD)
                if not isinstance(results, list):
                    results = [results]

                for frame_idx, detections, frame_bgr in zip(valid_indices, results, frames_bgr):
                    field_polygon = None
                    if settings.FIELD_BOUNDARY_FILTER_ENABLED:
                        field_polygon = segment_field_polygon(frame_bgr)
                        if field_polygon is not None and settings.FIELD_BOUNDARY_LINE_REFINEMENT_ENABLED:
                            field_polygon = refine_boundary_with_lines(frame_bgr, field_polygon)
                        elif field_polygon is None:
                            # Fail open: segmentation failed on this frame
                            # (e.g. corrupted/blank) - keep every detection
                            # rather than dropping a whole frame's real
                            # players over one bad mask.
                            logger.warning(f"[{STAGE_NAME}] Field segmentation failed, "
                                            f"skipping boundary filter for this frame", extra={
                                "video_id": video_id_safe,
                                "frame_index": frame_idx,
                            })

                    class_names = detections.data.get("class_name", [])
                    det_idx = 0
                    for i in range(len(detections.xyxy)):
                        name = class_names[i] if i < len(class_names) else None
                        if name not in settings.DETECTION_CLASS_NAMES:
                            continue
                        x1, y1, x2, y2 = [float(v) for v in detections.xyxy[i]]
                        if field_polygon is not None:
                            # Bottom-center of the bbox approximates where the
                            # person's feet touch the ground - the standard
                            # convention for testing "is this person on the
                            # field" rather than the whole (taller) bbox.
                            foot_x, foot_y = (x1 + x2) / 2.0, y2
                            if not point_in_field(foot_x, foot_y, field_polygon):
                                continue
                        detections_out.append({
                            "frame_index": frame_idx,
                            "detection_index": det_idx,
                            "bbox": [x1, y1, x2, y2],
                            "confidence": float(detections.confidence[i]),
                            "class_name": name,
                            "created_at": get_utc_timestamp(),
                            "play_index": play_index,
                        })
                        det_idx += 1

                # Every detection doc above was already pulled out as plain floats,
                # so nothing here still needs these GPU tensors - drop the reference
                # and hand the freed blocks back to the allocator now rather than
                # letting them sit until the next batch's reassignment gets around
                # to it. Across ~2,000 batches for a full-length video this is what
                # was slowly filling the GPU until it OOM'd partway through.
                del results
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                if (batch_start // batch_size) % 5 == 0:
                    logger.info(f"[{STAGE_NAME}] Progress", extra={
                        "video_id": video_id_safe,
                        "frames_processed": min(batch_start + batch_size, len(ordered_frames)),
                        "total_frames": len(ordered_frames),
                        "detections_so_far": len(detections_out),
                    })

        except Exception as e:
            error_code = "DETECTION_FAILED"
            logger.error(f"[{STAGE_NAME}] Detection failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        try:
            # Clear this run's frame range before writing - write_detections_batch
            # upserts by (video_id, frame_index, detection_index), which can't
            # by itself shrink a frame's detection count (e.g. the field-boundary
            # filter now correctly finding 0 people on a frame that used to have
            # several) - without this, the extra old rows never get overwritten
            # and leak into the UI as stale boxes. Confirmed live 2026-07-29.
            frame_index_lo = ordered_frames[0][0]
            frame_index_hi = ordered_frames[-1][0]
            cleared = clear_detections_for_frame_range(
                firestore_client, video_id_safe, frame_index_lo, frame_index_hi
            )
            logger.info(f"[{STAGE_NAME}] Cleared stale detections before write", extra={
                "video_id": video_id_safe,
                "frame_index_lo": frame_index_lo,
                "frame_index_hi": frame_index_hi,
                "rows_cleared": cleared,
            })

            written = write_detections_batch(firestore_client, video_id_safe, detections_out)
            logger.info(f"[{STAGE_NAME}] Wrote detections to Firestore", extra={
                "video_id": video_id_safe,
                "detections_written": written,
            })

            update_stage_status(
                firestore_client, video_id_safe, STAGE_NAME, "completed",
                metadata={
                    "total_detections": len(detections_out),
                    "total_frames": len(ordered_frames),
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
                metadata={"previous_stage": STAGE_NAME}
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
