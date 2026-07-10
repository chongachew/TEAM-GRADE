"""
Whistle Detection Stage - Referee/Coach Whistle Cues from Audio
Runs the ported preprocessor/audio_analyzer.py whistle detector against the
downloaded video's audio track, producing timestamped, confidence-scored
whistle events - a play-boundary signal independent of pose/motion, which
rep_extraction can use to cross-validate or refine its boundaries. Only
enqueued when settings.WHISTLE_DETECTION_ENABLED is true.
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
from ingest.utils.firestore_utils import get_utc_timestamp, write_whistle_events_batch

logger = logging.getLogger(__name__)

STAGE_NAME = "whistle_detection"
STAGE_NEXT = "frame_extraction"


def run_whistle_detection_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute whistle detection stage.

    Loads audio from the downloaded video file and runs the librosa
    onset + spectral-centroid + bandpass-STE fusion detector (see
    processing/audio_analyzer.py) to find whistle events.

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
        logger.info(f"[{STAGE_NAME}] Starting whistle detection stage", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })

        # -------- Load video path from Firestore (same convention as
        # frame_extraction_stage_gpu.py: download_path field, fallback to the
        # default videos dir) --------
        try:
            db = firestore_client.db
            video_doc = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).get()

            if not video_doc.exists:
                error_code = "VIDEO_DOC_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Video document not found", extra={
                    "video_id": video_id_safe,
                    "error_code": error_code
                })
                return False, error_code

            video_data = video_doc.to_dict()
            video_path_str = video_data.get("download_path")
            if not video_path_str:
                videos_dir = settings.get_videos_dir()
                video_path_str = str(videos_dir / f"{video_id_safe}.mp4")
                logger.info(f"[{STAGE_NAME}] Using fallback video path", extra={
                    "video_id": video_id_safe,
                    "fallback_path": video_path_str,
                })
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to load video document: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code

        from pathlib import Path
        video_path = Path(video_path_str)
        if not video_path.exists():
            error_code = "VIDEO_FILE_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] Video file not found", extra={
                "video_id": video_id_safe,
                "path": str(video_path),
                "error_code": error_code
            })
            return False, error_code

        try:
            from processing.audio_analyzer import AudioAnalyzer

            analyzer = AudioAnalyzer()
            result = analyzer.process_file(str(video_path))
            events = result["events"]
        except Exception as e:
            error_code = "WHISTLE_DETECTION_FAILED"
            logger.error(f"[{STAGE_NAME}] Whistle detection failed: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code

        try:
            written = write_whistle_events_batch(firestore_client, video_id_safe, events)
            logger.info(f"[{STAGE_NAME}] Wrote whistle events to Firestore", extra={
                "video_id": video_id_safe,
                "events_written": written,
            })

            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                f"stages.{STAGE_NAME}.status": "completed",
                f"stages.{STAGE_NAME}.event_count": len(events),
                f"stages.{STAGE_NAME}.processing_time_sec": result["metadata"]["processing_time_sec"],
                f"stages.{STAGE_NAME}.completed_at": get_utc_timestamp(),
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
        })

        return True, None

    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code
