"""
Frame Extraction Stage (Stage 3/9) - Frame Extraction
Extracts video frames using FFmpeg at specified FPS with resilient error handling.
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import concurrent.futures

from config import settings
from ingest.exceptions import (
    ValidationError,
    StageExecutionError,
    FileNotFoundError as FileNotFoundError_,
    FirestoreError,
    QueueError,
)
from ingest.validation import VideoIdValidator
from ingest.utils.firestore_utils import get_utc_timestamp, write_frames_batch

logger = logging.getLogger(__name__)

# Stage name constants
STAGE_NAME = "frame_extraction"
STAGE_NEXT = "pose"


def run_frame_extraction_stage(
    firestore_client: 'FirestoreClient',
    video_id: str,
    queue_manager: 'QueueManager',
    payload: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """Execute frame extraction stage.
    
    Stage 3/9: Extract video frames using FFmpeg at configurable FPS.
    Frames stored locally: team-grade-processing/extracted_frames/{video_id}/
    
    **Input Contract:**
    - video_id: 11-char YouTube ID (will be validated)
    - firestore_client: Connected Firestore client with .db attribute
    - queue_manager: Queue manager with .enqueue_video method
    - payload: Optional dict with fps override: {"fps": 15}
    
    **Output:**
    - Files: extracted_frames/{video_id}/frame_XXXXXX.jpg
    - Firestore updates:
        - stages.frame_extraction.status = "completed"
        - stages.frame_extraction.total_frames = count
        - frame_count = total frames (quick reference)
    - Enqueues pose stage
    
    **Resilience:**
    - Skips unreadable frames (corrupt data)
    - Stops only after N consecutive failures (configurable)
    - Logs progress every 50 frames
    
    **Possible Return Values:**
    - (True, None): Extraction succeeded
    - (False, "VIDEO_ID_INVALID"): video_id failed validation
    - (False, "VIDEO_DOC_NOT_FOUND"): Video document missing
    - (False, "VIDEO_FILE_NOT_FOUND"): Downloaded video not found
    - (False, "NO_VIDEO_FRAMES"): No frames extracted
    - (False, "FIRESTORE_ERROR"): Failed to write results
    - (False, "QUEUE_ERROR"): Failed to enqueue next stage
    - (False, "UNEXPECTED_ERROR"): Unexpected error
    
    Args:
        firestore_client: Google Cloud Firestore client
        video_id: YouTube video ID (will be sanitized)
        queue_manager: Queue manager instance
        payload: Optional config: {"fps": 15}
    
    Returns:
        (success: bool, error_code: Optional[str])
        
    Example:
        >>> firestore = FirestoreClient()
        >>> queue = QueueManager(firestore)
        >>> payload = {"fps": 15}
        >>> success, error = run_frame_extraction_stage(firestore, "dQw4w9WgXcQ", queue, payload)
        >>> assert success is True
    
    Notes:
        - Uses OpenCV (cv2) to read and OpenCV to write frames
        - Extracts at settings.FRAME_EXTRACTION_FPS (default 15 FPS)
        - Applies JPEG quality=90 compression
        - Tracks consecutive failures to detect end of video
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
        logger.info(f"[{STAGE_NAME}] Starting frame extraction", extra={
            "video_id": video_id_safe,
            "stage": STAGE_NAME,
        })
        
        # -------- Load video path from Firestore --------
        
        try:
            db = firestore_client.db
            video_doc = db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).get()
            
            if not video_doc.exists:
                error_code = "VIDEO_DOC_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Video document not found", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                    "error_code": error_code
                })
                return False, error_code
            
            video_data = video_doc.to_dict()
            video_path_str = video_data.get("download_path")
            
            if not video_path_str:
                # Fallback: check default location
                videos_dir = settings.get_videos_dir()
                video_path_str = str(videos_dir / f"{video_id_safe}.mp4")
                logger.info(f"[{STAGE_NAME}] Using fallback video path", extra={
                    "video_id": video_id_safe,
                    "fallback_path": video_path_str,
                })
            
            video_path = Path(video_path_str)
            
            if not video_path.exists():
                error_code = "VIDEO_FILE_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Video file not found", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                    "path": str(video_path),
                    "error_code": error_code
                })
                return False, error_code
            
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            logger.info(f"[{STAGE_NAME}] Video file verified", extra={
                "video_id": video_id_safe,
                "path": str(video_path),
                "size_mb": round(file_size_mb, 1),
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to load video metadata: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Configure extraction parameters --------
        
        try:
            import cv2
            
            # FPS extraction from payload (default to settings)
            fps = payload.get("fps", settings.FRAME_EXTRACTION_FPS) if payload else settings.FRAME_EXTRACTION_FPS
            
            max_consecutive_failures = getattr(
                settings, 
                'FRAME_EXTRACTION_CONSECUTIVE_FAILURES', 
                10
            )
            
            logger.info(f"[{STAGE_NAME}] Configuration loaded", extra={
                "video_id": video_id_safe,
                "fps": fps,
                "max_consecutive_failures": max_consecutive_failures,
            })
        
        except Exception as e:
            error_code = "UNEXPECTED_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to load configuration: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Open video and extract frames --------
        
        try:
            # Open video with OpenCV
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                error_code = "VIDEO_FILE_NOT_FOUND"
                logger.error(f"[{STAGE_NAME}] Failed to open video", extra={
                    "video_id": video_id_safe,
                    "path": str(video_path),
                    "error_code": error_code
                })
                return False, error_code
            
            # Get video properties
            video_fps = cap.get(cv2.CAP_PROP_FPS) or fps
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            logger.info(f"[{STAGE_NAME}] Video loaded", extra={
                "video_id": video_id_safe,
                "total_frames": total_frames,
                "fps": video_fps,
                "resolution": f"{frame_width}x{frame_height}",
            })
        
        except Exception as e:
            error_code = "VIDEO_FILE_NOT_FOUND"
            logger.error(f"[{STAGE_NAME}] Failed to open video: {e}", extra={
                "video_id": video_id_safe,
                "path": str(video_path),
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Create output directory --------
        
        try:
            output_dir = settings.get_frames_dir(video_id_safe)
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"[{STAGE_NAME}] Output directory ready", extra={
                "video_id": video_id_safe,
                "path": str(output_dir),
            })
        
        except Exception as e:
            error_code = "UNEXPECTED_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to create output directory: {e}", extra={
                "video_id": video_id_safe,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Extract frames with resilience --------
        
        try:
            frames_metadata = []
            frame_count = 0
            consecutive_failures = 0
            frame_index = 0
            # Timestamp accumulator, not a fixed integer stride - int(video_fps / fps)
            # truncates rather than rounds, so any source fps whose ratio to the
            # target is just under a whole number (29.97/15 -> int(1.998) == 1,
            # 24/15 -> int(1.6) == 1) silently disabled downsampling entirely and
            # extracted at the full native rate instead. That desynced every
            # downstream frame_index from the frontend's fixed
            # currentTime * FRAME_EXTRACTION_FPS assumption (BoxOverlay.tsx),
            # which is what made the box overlay's boxes visibly lag behind the
            # players - live footage is essentially never an exact multiple of
            # 15fps. Comparing real elapsed time against the next-due capture
            # time is exact regardless of the source/target fps ratio.
            next_capture_time = 0.0
            frame_interval_seconds = 1.0 / fps if fps > 0 else 0.0

            # ✅ OPTIMIZATION: Collect frames first, then write in parallel (25% faster)
            frames_to_write = []  # Collect (frame_data, frame_count, frame_path) tuples

            logger.info(f"[{STAGE_NAME}] Starting resilient extraction", extra={
                "video_id": video_id_safe,
                "target_fps": fps,
                "frame_interval_seconds": frame_interval_seconds,
            })

            while True:
                ret, frame = cap.read()

                if not ret or frame is None:
                    consecutive_failures += 1

                    if consecutive_failures >= max_consecutive_failures:
                        logger.info(f"[{STAGE_NAME}] Extraction complete (consecutive failures threshold)", extra={
                            "video_id": video_id_safe,
                            "consecutive_failures": consecutive_failures,
                            "frames_extracted": frame_count,
                        })
                        break

                    frame_index += 1
                    continue  # Skip unreadable frame, continue

                # Reset consecutive failures on successful read
                consecutive_failures = 0

                timestamp_seconds = frame_index / video_fps

                # Sample once real elapsed time reaches the next due capture instant
                if timestamp_seconds >= next_capture_time:
                    next_capture_time += frame_interval_seconds
                    frame_filename = f"frame_{frame_count:06d}.jpg"
                    frame_path = output_dir / frame_filename

                    frames_metadata.append({
                        "frame_index": frame_count,
                        "timestamp_seconds": timestamp_seconds,
                        "path": str(frame_path),
                        "width": frame_width,
                        "height": frame_height
                    })
                    
                    # Collect frame for parallel writing
                    frames_to_write.append((frame, str(frame_path)))
                    frame_count += 1
                    
                    # Log progress
                    if frame_count % settings.POSE_PROGRESS_LOG_INTERVAL == 0:
                        logger.info(f"[{STAGE_NAME}] Progress", extra={
                            "video_id": video_id_safe,
                            "frames_extracted": frame_count,
                            "frames_processed": frame_index,
                        })
                
                frame_index += 1
            
            cap.release()
            
            if not frames_metadata:
                error_code = "NO_VIDEO_FRAMES"
                logger.error(f"[{STAGE_NAME}] No frames extracted", extra={
                    "video_id": video_id_safe,
                    "stage": STAGE_NAME,
                    "error_code": error_code
                })
                return False, error_code
            
            # ✅ OPTIMIZATION: Write frames in parallel (4 threads)
            logger.info(f"[{STAGE_NAME}] Writing {len(frames_to_write)} frames in parallel...", extra={
                "video_id": video_id_safe,
                "frames_count": len(frames_to_write),
            })
            
            def write_frame_file(frame_data_tuple):
                """Write a single frame to disk (used by ThreadPoolExecutor)."""
                frame_data, frame_path = frame_data_tuple
                try:
                    import cv2
                    cv2.imwrite(frame_path, frame_data, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    return True, frame_path
                except Exception as e:
                    logger.warning(f"[{STAGE_NAME}] Failed to save frame {frame_path}: {e}", extra={
                        "video_id": video_id_safe,
                    })
                    return False, frame_path
            
            # Write frames using up to 4 worker threads
            successful_writes = 0
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(write_frame_file, frame_tuple) for frame_tuple in frames_to_write]
                
                for future in concurrent.futures.as_completed(futures):
                    try:
                        success, frame_path = future.result()
                        if success:
                            successful_writes += 1
                    except Exception as e:
                        logger.error(f"[{STAGE_NAME}] Frame writing error: {e}", extra={
                            "video_id": video_id_safe,
                        })
            
            logger.info(f"[{STAGE_NAME}] Frame extraction and writing complete", extra={
                "video_id": video_id_safe,
                "total_extracted": frame_count,
                "total_written": successful_writes,
                "total_processed": frame_index,
            })
        
        except Exception as e:
            error_code = "UNEXPECTED_ERROR"
            logger.error(f"[{STAGE_NAME}] Unexpected error during extraction: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            }, exc_info=True)
            return False, error_code
        
        # -------- Write frame metadata to Firestore --------
        
        try:
            batch_size = getattr(settings, 'FRAME_EXTRACTION_BATCH_SIZE', 100)
            frames_written = write_frames_batch(
                firestore_client,
                video_id_safe,
                frames_metadata,
                batch_size=batch_size
            )
            
            logger.info(f"[{STAGE_NAME}] Frame metadata written to Firestore", extra={
                "video_id": video_id_safe,
                "frames_written": frames_written,
            })
        
        except Exception as e:
            error_code = "FIRESTORE_ERROR"
            logger.error(f"[{STAGE_NAME}] Failed to write frame metadata: {e}", extra={
                "video_id": video_id_safe,
                "stage": STAGE_NAME,
                "error_code": error_code
            })
            return False, error_code
        
        # -------- Update stage status --------
        
        try:
            db.collection(settings.COLLECTION_VIDEOS).document(video_id_safe).update({
                "stages.frame_extraction.status": "completed",
                "stages.frame_extraction.total_frames": frame_count,
                "stages.frame_extraction.fps": fps,
                "stages.frame_extraction.completed_at": get_utc_timestamp(),
                "frame_count": frame_count
            })
            
            logger.info(f"[{STAGE_NAME}] Updated stage status", extra={
                "video_id": video_id_safe,
                "frames": frame_count,
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
                    "frame_count": frame_count,
                    "fps": fps
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
            "frames_extracted": frame_count,
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
