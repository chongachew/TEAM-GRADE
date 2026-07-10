"""
Download Stage (Stage 2/9) - Video Download
Downloads video from YouTube using yt-dlp and stores locally.
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, update_stage_status
from ingest.utils.bandwidth_utils import BandwidthMeasurer

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "download"
STAGE_NEXT = "whistle_detection" if getattr(settings, "WHISTLE_DETECTION_ENABLED", False) else "frame_extraction"


def run_download_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute YouTube video download stage.
    
    Stage 2/9: Download video from YouTube using yt-dlp.
    Video is stored locally at: team-grade-processing/videos/{video_id}.mp4
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client with .db attribute
    - queue_manager: Queue manager with .enqueue_video method
    - payload: Optional dict with settings
    
    **Output Data:**
    - File: videos/{video_id}.mp4
    - Firestore updates:
        - stages.download.status = "completed"
        - stages.download.path = file path
        - stages.download.file_size_bytes = size
        - download_path = file path (quick reference)
    - Enqueues frame_extraction stage
    
    **Possible Return Values:**
    - (True, None): Download succeeded
    - (False, "VIDEO_ID_INVALID"): video_id failed validation
    - (False, "VALIDATION_ERROR"): no video_url stored for this video_id
    - (False, "DOWNLOAD_FAILED"): yt-dlp failed to download
    - (False, "FIRESTORE_ERROR"): Failed to update metadata
    - (False, "QUEUE_ERROR"): Failed to enqueue next stage
    - (False, "UNEXPECTED_ERROR"): Unexpected error
    
    Args:
        firestore_client: Google Cloud Firestore client
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional configuration (not used)
    
    Returns:
        (success: bool, error_code: Optional[str])
        
    Example:
        >>> from ingest.firestore_client import FirestoreClient
        >>> from ingest.queue_manager import QueueManager
        >>> firestore = FirestoreClient()
        >>> queue = QueueManager(firestore)
        >>> success, error = run_download_stage(firestore, "dQw4w9WgXcQ", queue)
        >>> assert success is True
    
    Notes:
        - Uses yt-dlp for robust YouTube downloading
        - Retries configured downloads on transient failure
        - Falls back to cached video if available
        - Logs file size in MB for monitoring
    """
    
    # ========================================================================
    # INPUT VALIDATION
    # ========================================================================
    
    try:
        # Validate firestore_client
        if not firestore_client or not hasattr(firestore_client, 'db'):
            raise ValidationError(
                "firestore_client must be initialized with .db attribute"
            )
        
        # Validate queue_manager
        if not queue_manager or not hasattr(queue_manager, 'enqueue_video'):
            raise ValidationError(
                "queue_manager must have .enqueue_video method"
            )
        
        # Validate and sanitize video_id
        video_id_safe = VideoIdValidator.validate(video_id)

        # The original submitted URL (YouTube, Vimeo, TikTok, Google Drive
        # share, or a direct video-file link) - stored on the video doc at
        # ingest time by ingest_worker.py / api/server.py. This is what
        # actually gets downloaded; the 11-char video_id is only TEAM-GRADE's
        # internal identifier and, for non-YouTube sources, carries no
        # relationship to the source at all.
        video_doc = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).get()
        video_url = (video_doc.to_dict() or {}).get("video_url") if video_doc.exists else None
        if not video_url:
            raise ValidationError(f"No video_url stored for {video_id_safe}", video_id=video_id_safe)

    except ValidationError as e:
        error_code = e.error_code
        logger.warning(f"[{STAGE_NAME}] Validation failed: {e}", extra={
            "video_id": e.video_id or video_id,
            "stage": STAGE_NAME,
            "error_code": error_code
        })
        return False, error_code
    
    # ========================================================================
    # STAGE EXECUTION
    # ========================================================================
    
    try:
        logger.info(f"[{STAGE_NAME}] Starting YouTube download", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })
        
        # -------- Initialize downloader --------
        
        try:
            from ingest.youtube_downloader import YouTubeDownloader
            
            videos_dir = settings.get_videos_dir()
            downloader = YouTubeDownloader(output_dir=videos_dir)
            
            logger.info(f"[{STAGE_NAME}] Downloader initialized", extra={
                "video_id": video_id_safe,
                "output_dir": str(videos_dir),
            })
        
        except Exception as e:
            error_code = "DOWNLOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Failed to initialize downloader: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Download video --------
        
        video_path = None
        try:
            output_path = videos_dir / f"{video_id_safe}.mp4"
            max_retries = getattr(settings, 'DOWNLOAD_MAX_RETRIES', 3)
            
            # ✅ OPTIMIZATION: Measure bandwidth and adapt quality (Phase 2 #5)
            logger.info(f"[{STAGE_NAME}] Measuring download bandwidth...", extra={
                "video_id": video_id_safe,
            })
            bandwidth_mbs = BandwidthMeasurer.measure_bandwidth()
            format_spec = BandwidthMeasurer.select_format_for_video(bandwidth_mbs, video_id_safe)
            
            logger.info(f"[{STAGE_NAME}] Starting download with adaptive quality", extra={
                "video_id": video_id_safe,
                "output_path": str(output_path),
                "max_retries": max_retries,
                "bandwidth_mbs": round(bandwidth_mbs, 2),
                "format": format_spec.split('[')[0] if '[' in format_spec else format_spec,
            })
            
            # Download with retry logic - uses the real source URL (video_url),
            # not the bare internal video_id. yt-dlp's YouTube extractor
            # happens to special-case a bare 11-char ID as if it were a full
            # URL, but no other extractor (Vimeo/TikTok/Drive) does, so the
            # real URL is required for any non-YouTube source to work at all.
            video_path = downloader.download_video(
                url=video_url,
                output_path=output_path,
                max_retries=max_retries,
                format_spec=format_spec  # ✅ OPTIMIZATION: Pass adaptive quality format
            )
            
            # Verify download succeeded
            if not video_path:
                # yt-dlp returned None - check for any video file that was created
                # (yt-dlp might create .webm, .mkv, etc. instead of .mp4)
                found_videos = list(videos_dir.glob(f"{video_id_safe}.*"))
                found_videos = [f for f in found_videos 
                               if f.suffix in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv']]
                
                if found_videos:
                    # Use the most recently created video file
                    video_path = sorted(found_videos, key=lambda f: f.stat().st_mtime)[-1]
                    logger.info(f"[{STAGE_NAME}] Found video file with different format: {video_path.name}")
                else:
                    raise StageExecutionError(
                        STAGE_NAME,
                        f"Downloaded file not found. Expected format variations in {videos_dir}",
                        video_id=video_id_safe
                    )
            elif not Path(video_path).exists():
                # Path returned by downloader doesn't exist - try to find an actual file
                found_videos = list(videos_dir.glob(f"{video_id_safe}.*"))
                found_videos = [f for f in found_videos 
                               if f.suffix in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv']]
                
                if found_videos:
                    video_path = sorted(found_videos, key=lambda f: f.stat().st_mtime)[-1]
                    logger.info(f"[{STAGE_NAME}] Using alternative video file: {video_path.name}")
                else:
                    raise StageExecutionError(
                        STAGE_NAME,
                        f"Downloaded file not found at {video_path}",
                        video_id=video_id_safe
                    )
            
            video_path = Path(video_path)
            file_size_bytes = video_path.stat().st_size
            file_size_mb = file_size_bytes / (1024 * 1024)
            
            logger.info(f"[{STAGE_NAME}] Download completed", extra={
                "video_id": video_id_safe,
                "file_size_bytes": file_size_bytes,
                "file_size_mb": round(file_size_mb, 1),
            })
        
        except StageExecutionError as e:
            error_code = "DOWNLOAD_FAILED"
            logger.warning(f"[{STAGE_NAME}] Download failed: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            
            # Fallback: check for cached video
            cached_path = videos_dir / f"{video_id_safe}.mp4"
            if cached_path.exists():
                logger.info(f"[{STAGE_NAME}] Using cached video from previous attempt", extra={
                    "video_id": video_id_safe,
                    "cached_path": str(cached_path),
                })
                video_path = cached_path
            else:
                return False, error_code
        
        except Exception as e:
            error_code = "DOWNLOAD_FAILED"
            logger.error(f"[{STAGE_NAME}] Unexpected download error: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        # -------- Update Firestore metadata --------
        
        try:
            file_size_bytes = video_path.stat().st_size
            
            db = firestore_client.db
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.download.status": "completed",
                "stages.download.path": str(video_path),
                "stages.download.file_size_bytes": file_size_bytes,
                "stages.download.completed_at": get_utc_timestamp(),
                "download_path": str(video_path)
            })
            
            logger.info(f"[{STAGE_NAME}] Updated Firestore metadata", extra={
                "video_id": video_id_safe,
                "file_size_bytes": file_size_bytes,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to update Firestore: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Enqueue next stage --------
        
        try:
            priority = getattr(settings, 'QUEUE_DEFAULT_PRIORITY', 5)
            
            success = queue_manager.enqueue_video(
                video_id_safe,
                stage=STAGE_NEXT,
                priority=priority,
                metadata={
                    "previous_stage": STAGE_NAME,
                    "video_path": str(video_path)
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
            
            logger.info(f"[{STAGE_NAME}] Enqueued next stage", extra={
                "video_id": video_id_safe,
                "next_stage": STAGE_NEXT,
            })
        
        except Exception as e:
            error_code = "QUEUE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to enqueue next stage: {e}", extra={
                "video_id": video_id_safe,
                "next_stage": STAGE_NEXT,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- SUCCESS --------
        
        logger.info(f"[{STAGE_NAME}] Stage completed successfully", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "next_stage": STAGE_NEXT,
        })
        
        return True, None
    
    # ========================================================================
    # UNEXPECTED ERRORS
    # ========================================================================
    
    except Exception as e:
        error_code = "UNEXPECTED_ERROR"
        logger.error(f"[{STAGE_NAME}] Unexpected error: {e}", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
            "error_code": error_code
        }, exc_info=True)
        return False, error_code
