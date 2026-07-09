"""
Tracking Stage (multi-player pipeline) - Multi-Object Identity Tracking
Runs the hand-rolled DensityAwareMemoryTracker (SAM2 per-frame segmentation +
IoU/appearance association) sequentially over all frames, assigning a persistent
track_id to each detection. Fires an opportunistic jersey-OCR re-ID check when a
track reappears after a long gap. Only enqueued when
settings.MULTI_PLAYER_TRACKING_ENABLED is true.
"""

import logging
from collections import defaultdict
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
from ingest.utils.firestore_utils import (
    get_utc_timestamp,
    write_tracks_batch,
    upsert_track_meta,
    write_jersey_number_for_track,
)

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "tracking"
STAGE_NEXT = "pose"

# Module-level caches (mirrors jersey_ocr_stage.py's _OCR_CACHE / detection_stage's
# _DETECTION_MODEL pattern) - both SAM2 and the OCR reader are expensive to load.
_TRACKER = None
_REID_OCR = None


def get_tracker():
    """Get or create the cached DensityAwareMemoryTracker instance.

    Note: unlike the other cached models, the tracker holds per-video state
    (self.tracks) - it must be reset (a fresh instance) per video. Callers should
    use reset_tracker(), not this getter, to obtain a clean tracker for a new run.
    """
    global _TRACKER
    if _TRACKER is None:
        from ingest.tracking import DensityAwareMemoryTracker
        from ingest.gpu_utils import resolve_device

        device = resolve_device(settings.TRACKING_USE_GPU)
        logger.info(f"[{STAGE_NAME}] Loading SAM2 tracker (device={device})...")
        _TRACKER = DensityAwareMemoryTracker(device=device)
        logger.info(f"[{STAGE_NAME}] Tracker loaded")
    return _TRACKER


def reset_tracker():
    """Reset per-video track state while reusing the already-loaded SAM2 model.

    DensityAwareMemoryTracker doesn't expose a reset() (its constructor is the
    expensive part, loading SAM2); simplest correct approach is to swap in fresh
    track bookkeeping on the existing instance.
    """
    tracker = get_tracker()
    tracker.tracks = {}
    tracker._next_track_id = 0
    return tracker


def get_reid_ocr():
    """Get or create the cached JerseyOCR instance used by the re-ID hook."""
    global _REID_OCR
    if _REID_OCR is None:
        from processing.jersey_ocr import JerseyOCR
        _REID_OCR = JerseyOCR(ocr_engine=settings.OCR_ENGINE, language=settings.OCR_LANGUAGE)
    return _REID_OCR


def run_tracking_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute multi-object tracking stage.

    Reads per-frame detections, runs them sequentially through the tracker,
    writes per-frame track assignments and per-track summaries, and fires an
    opportunistic jersey-OCR check when a track reappears after a long gap.

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
        logger.info(f"[{STAGE_NAME}] Starting tracking stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        import cv2

        frames_dir = settings.get_frames_dir(video_id_safe)
        frame_paths = sorted(frames_dir.glob("frame_*.jpg"))

        if not frame_paths:
            error_code = "FRAMES_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] No frames found", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        try:
            detections_ref = firestore_client.db.collection(
                settings.COLLECTION_VIDEOS
            ).document(video_id_safe).collection("detections")
            detection_docs = list(detections_ref.stream())

            if not detection_docs:
                error_code = "DETECTIONS_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] No detections found", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code

        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to read detections: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        detections_by_frame: Dict[int, List[Dict]] = defaultdict(list)
        for doc in detection_docs:
            data = doc.to_dict()
            detections_by_frame[data["frame_index"]].append(data)

        try:
            tracker = reset_tracker()
        except Exception as e:
            error_code = "TRACKING_MODEL_LOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to load tracking model: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        track_docs: List[Dict] = []

        try:
            for frame_idx, frame_path in enumerate(frame_paths):
                frame_dets = detections_by_frame.get(frame_idx, [])
                if not frame_dets:
                    continue

                frame_bgr = cv2.imread(str(frame_path))
                if frame_bgr is None:
                    logger.debug(f"[{STAGE_NAME}] Skipped unreadable frame: {frame_idx}")
                    continue
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

                results = tracker.process_frame(frame_idx, frame_rgb, frame_dets)

                for result in results:
                    result["created_at"] = get_utc_timestamp()
                    track_docs.append(result)

                    # Opportunistic re-ID hook: only fires once a track has been
                    # missing long enough to clear REID_OCR_TRIGGER_GAP_FRAMES -
                    # a same-frame appearance tie-break during a brief crossing
                    # (recovered_from_gap == 0) isn't worth an extra OCR call,
                    # but a genuine long-gap reappearance is (see plan section 2c).
                    if (
                        result["matched_via"] == "appearance_reid"
                        and result["recovered_from_gap"] >= settings.REID_OCR_TRIGGER_GAP_FRAMES
                    ):
                        _maybe_run_reid_ocr(firestore_client, video_id_safe, frame_bgr, result)

                if frame_idx % 50 == 0:
                    logger.info(f"[{STAGE_NAME}] Progress", extra={
                        "video_id": video_id_safe,
                        "frames_processed": frame_idx,
                        "total_frames": len(frame_paths),
                        "active_tracks": sum(1 for t in tracker.tracks.values() if t.status == "active"),
                    })

        except Exception as e:
            error_code = "TRACKING_FAILED"
            logger.error(f"[{STAGE_NAME}] Tracking failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        try:
            written = write_tracks_batch(firestore_client, video_id_safe, track_docs)
            logger.info(f"[{STAGE_NAME}] Wrote track docs to Firestore", extra={
                "video_id": video_id_safe,
                "docs_written": written,
            })

            first_frame_by_track: Dict[int, int] = {}
            for doc in track_docs:
                tid = doc["track_id"]
                if tid not in first_frame_by_track or doc["frame_index"] < first_frame_by_track[tid]:
                    first_frame_by_track[tid] = doc["frame_index"]

            for tid, track in tracker.tracks.items():
                upsert_track_meta(firestore_client, video_id_safe, tid, {
                    "first_frame": first_frame_by_track.get(tid, track.last_frame_seen),
                    "last_frame": track.last_frame_seen,
                    "total_frames_tracked": sum(1 for d in track_docs if d["track_id"] == tid),
                    "status": track.status,
                })

            firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.tracking.status": "completed",
                "stages.tracking.total_tracks": len(tracker.tracks),
                "stages.tracking.total_track_docs": len(track_docs),
                "stages.tracking.completed_at": get_utc_timestamp(),
            })

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
            "total_tracks": len(tracker.tracks),
        })

        return True, None

    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code


def _maybe_run_reid_ocr(
    firestore_client,
    video_id_safe: str,
    frame_bgr,
    track_result: Dict,
) -> None:
    """Fire an opportunistic jersey-OCR read when a track was just re-identified
    via appearance (a long-gap reappearance signal), and cross-check it against
    that track's already-known jersey number (if any). Errors here are logged and
    swallowed - the re-ID hook is a best-effort cross-check, not a required part
    of a successful tracking-stage run.
    """
    try:
        from processing.torso_cropper import crop_torso_region_from_bbox

        crop = crop_torso_region_from_bbox(frame_bgr, track_result["bbox"])
        if crop is None:
            return

        ocr = get_reid_ocr()
        jersey_number, confidence = ocr.read_jersey_number(crop, confidence_threshold=settings.OCR_CONFIDENCE_THRESHOLD)
        if jersey_number is None:
            return

        track_id = track_result["track_id"]
        meta_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(video_id_safe)
            .collection("tracks_meta")
            .document(f"{track_id:03d}")
        )
        existing = meta_ref.get()
        existing_data = existing.to_dict() if existing.exists else {}
        existing_number = existing_data.get("jersey_number")
        existing_confidence = existing_data.get("jersey_confidence", 0.0)

        if existing_number is None:
            write_jersey_number_for_track(
                firestore_client, video_id_safe, track_id,
                jersey_number, confidence, track_result["frame_index"],
            )
        elif (
            existing_number != jersey_number
            and confidence >= settings.OCR_STOP_ON_CONFIDENCE
            and existing_confidence >= settings.OCR_STOP_ON_CONFIDENCE
        ):
            # Two high-confidence reads disagree after a re-ID event - flag for
            # manual review rather than silently overwriting or auto-splitting
            # the track (Phase 1 scope, per plan).
            logger.warning(
                f"[{STAGE_NAME}] Track {track_id} jersey mismatch after re-ID: "
                f"existing={existing_number} new={jersey_number}"
            )
            meta_ref.set({"track_id_conflict": True}, merge=True)

    except Exception as e:
        logger.debug(f"[{STAGE_NAME}] Re-ID OCR check failed (non-fatal): {e}")
