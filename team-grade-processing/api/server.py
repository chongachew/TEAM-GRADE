"""
TEAM-GRADE Ingestion API Server
Provides REST API layer for the YouTube ingestion worker system.
"""

import logging
import os
import re
import secrets
import sys
import subprocess
import tempfile
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, HttpUrl
from pathlib import Path
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Add parent directory to path to import ingest modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================================
# DEPENDENCY CHECK & INSTALLATION
# ============================================================================

def ensure_dependencies():
    """Ensure all required dependencies are installed."""
    # Maps pip package name -> the actual importable module name (these differ
    # for several packages, e.g. "opencv-python" installs as "cv2").
    required_packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "pydantic": "pydantic",
        "google-cloud-firestore": "google.cloud.firestore",
        "yt-dlp": "yt_dlp",
        "opencv-python": "cv2",
        "numpy": "numpy",
        "pillow": "PIL",
    }

    missing = []
    for package, module_name in required_packages.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"\n[WARN] Missing dependencies: {', '.join(missing)}")
        print("[INFO] Installing missing dependencies...")
        for package in missing:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", package, "-q"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60
                )
                print(f"  [OK] {package}")
            except Exception as e:
                print(f"  [FAIL] {package}: {e}")
        print("[OK] Dependency check complete\n")

# Run dependency check at import time
ensure_dependencies()

from ingest.ingest_worker import IngestWorker
# VideoStatus/IngestStage are plain str-Enums with no Firestore SDK
# dependency at import time (google-cloud-firestore is imported lazily,
# inside FirestoreClient.__init__) - safe to keep importing them from here
# even though FirestoreClient itself is no longer constructed anywhere
# (Pass 2a data-layer migration: see ingest/postgres_client.py).
from ingest.firestore_client import FirestoreClient, VideoStatus, IngestStage
from ingest.postgres_client import PostgresClient
from ingest.youtube_metadata import YouTubeMetadataExtractor
from ingest.utils.firestore_utils import COLLECTION_TRACKS, COLLECTION_TRACKS_META, COLLECTION_PLAYS
from ingest.stages.biomechanics_stage_vectorized import _extract_pose_features, _load_trait_configs
from processing.vectorized_traits import VectorizedTraitScorer
from processing.clip_cutter import cut_clip, ClipCutError
import numpy as np

# Import centralized constants
try:
    from config.constants import ALL_STAGES, STAGE_STATUS_SKIPPED, TOTAL_STAGES
except ImportError:
    # Fallback if constants not available
    ALL_STAGES = [
        "metadata", "download", "frame_extraction", "pose",
        "torso_crop", "jersey_ocr", "rep_extraction", "biomechanics", "complete"
    ]
    STAGE_STATUS_SKIPPED = "skipped"
    TOTAL_STAGES = len(ALL_STAGES)

from config import settings
# Reuses the exact same conditional this stage already computes for a normal
# download, rather than duplicating the WHISTLE_DETECTION_ENABLED check here.
from ingest.stages.download_stage import STAGE_NEXT as UPLOAD_FIRST_STAGE
from ingest.utils.firestore_utils import get_utc_timestamp
from ingest.s3_client import (
    upload_file,
    download_file,
    video_key,
    torso_key,
    play_clip_key,
    file_exists_in_s3,
    get_presigned_url,
    ensure_video_local,
    S3ObjectNotFoundError,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('api.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="TEAM-GRADE Ingestion API",
    description="API for YouTube video ingestion into TEAM-GRADE system",
    version="1.0.0"
)

# Per-IP rate limiting - only applied to /api/ingest and /api/ingest/upload
# (see their decorators below), the two endpoints a completely anonymous,
# no-login visitor can call. Everything else stays unlimited.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add CORS middleware - MUST be added before routes
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
    allow_headers=["*"],
)

# ============================================================================
# STATIC FILES & UI
# ============================================================================

# Mount UI static files
ui_path = Path(__file__).parent.parent / "ui"
if ui_path.exists():
    app.mount("/ui/static", StaticFiles(directory=str(ui_path)), name="static")
else:
    logger.warning(f"UI directory not found at {ui_path}")

# Serve source video files for playback. This used to be a StaticFiles mount
# straight over settings.VIDEOS_DIR (Starlette's FileResponse under the hood
# already supports HTTP range requests, so the <video> element's scrubber/
# seek worked with no extra range-handling code) - but that only ever worked
# when this API container happened to already have the file on its own local
# disk, which the worker's download stage never puts there (separate ECS
# service, separate disk). Redirecting to a presigned S3 GET URL instead
# keeps the no-extra-range-handling property (S3 natively supports Range
# requests) while actually working across containers. Falls back to serving
# a local file directly (old behavior) if the video isn't in S3 yet for any
# reason, so nothing regresses for a video mid-migration.
settings.get_videos_dir()  # ensure the directory exists (upload endpoint writes directly into it)


@app.get("/media/{video_id}.mp4")
async def get_media_video(video_id: str):
    """Serve (via presigned-URL redirect) the source video for playback -
    see the module-level comment above for why this replaced a plain
    StaticFiles mount over the local videos/ directory.

    Raises:
        HTTPException: 400 for an invalid video_id, 404 if the video isn't
            available either in S3 or on this container's local disk.
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    try:
        s3_key = video_key(safe_video_id)
        if file_exists_in_s3(s3_key):
            url = get_presigned_url(s3_key)
            return RedirectResponse(url=url, status_code=302)

        # Not in S3 (upload hasn't happened/completed yet, or this predates
        # the migration) - fall back to serving straight off local disk if
        # this particular container happens to have it, same as the old
        # StaticFiles-mount behavior.
        local_path = settings.get_video_path(safe_video_id)
        if local_path.exists():
            return FileResponse(str(local_path), media_type="video/mp4")

        raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Media retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve video: {str(e)}")


@app.get("/media/{video_id}/plays/{play_index}.mp4")
async def get_media_play_clip(video_id: str, play_index: int):
    """Serve (via presigned-URL redirect) one play's standalone clip -
    same pattern as get_media_video above, but for the per-play clips
    play_detection_stage.py cuts+uploads inline at ingest.s3_client
    .play_clip_key(). Unlike the full video, there's no local-disk
    fallback here - these clips only ever exist in S3 (the API container
    never writes them locally, play_detection_stage.py's worker uploads
    and deletes its local temp copy immediately).

    Raises:
        HTTPException: 400 for an invalid video_id, 404 if the clip isn't
            in S3 (export never ran, or failed - see GET /api/plays'
            clip_url, which is None in exactly this case, so a well-behaved
            client shouldn't normally reach this route for such a play).
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    try:
        s3_key = play_clip_key(safe_video_id, play_index)
        if file_exists_in_s3(s3_key):
            url = get_presigned_url(s3_key)
            return RedirectResponse(url=url, status_code=302)

        raise HTTPException(
            status_code=404, detail=f"No clip available for {safe_video_id} play {play_index}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Play clip retrieval error for {safe_video_id}/{play_index}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve play clip: {str(e)}")


# Initialize ingestion components
ingest_worker = None
firestore_client = None
metadata_extractor = None
queue_manager = None

try:
    # Step 1: Initialize Postgres (CRITICAL - all other components depend on it).
    # Pass 2a data-layer migration: firestore_client (the module-level name)
    # now holds a PostgresClient - every route below still calls
    # firestore_client.<method>(...) / firestore_client.db... unchanged,
    # serviced by PostgresClient's matching method surface + its .db
    # Firestore-compat shim (see ingest/postgres_client.py).
    firestore_client = PostgresClient()
    logger.info("[OK] Postgres client initialized")

    # Step 2: Initialize QueueManager (CRITICAL for enqueuing)
    from ingest.queue_manager import QueueManager
    queue_manager = QueueManager(firestore_client)
    logger.info("[OK] Queue manager initialized")

    # Step 3: Initialize IngestWorker (uses QueueManager internally)
    ingest_worker = IngestWorker()
    logger.info("[OK] Ingest worker initialized")

    # Step 4: Initialize metadata extractor
    metadata_extractor = YouTubeMetadataExtractor()
    logger.info("[OK] Ingestion components initialized successfully")

except Exception as e:
    logger.warning(f"[WARN] Failed to initialize IngestWorker: {str(e)}")
    logger.info("[INFO] Will use fallback direct queue enqueue mode")
    logger.info("[INFO] Postgres and queue manager must be available for fallback mode")

    # Try to at least initialize critical components for fallback
    try:
        if not firestore_client:
            firestore_client = PostgresClient()
            logger.info("[OK] Postgres client initialized (fallback)")

        if firestore_client and not queue_manager:
            from ingest.queue_manager import QueueManager
            queue_manager = QueueManager(firestore_client)
            logger.info("[OK] Queue manager initialized (fallback)")

        if not metadata_extractor:
            metadata_extractor = YouTubeMetadataExtractor()
            logger.info("[OK] Metadata extractor initialized (fallback)")
    except Exception as fallback_e:
        logger.error(f"[CRITICAL] Fallback initialization failed: {str(fallback_e)}")
        logger.error("[CRITICAL] API will operate in read-only mode")


# ============================================================================
# MODELS
# ============================================================================

class IngestRequest(BaseModel):
    """Request model for ingestion endpoint."""
    video_url: str
    team_id: Optional[str] = None
    player_number: Optional[int] = None
    position: Optional[str] = None


class IngestResponse(BaseModel):
    """Response model for successful ingestion request."""
    status: str
    video_id: str
    message: str
    timestamp: str


class StatusResponse(BaseModel):
    """Response model for status endpoint."""
    video_id: str
    status: str
    progress: int = 0
    stages: Dict[str, Any] = {}
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: str
    timestamp: str
    components: Dict[str, str]


class RepCorrectionRequest(BaseModel):
    """Request model for manually correcting a rep's start/end boundary."""
    track_id: int
    rep_index: int
    start_frame: int
    end_frame: int
    # None for videos processed before the per-play redesign / single-
    # athlete-mode videos, where play_detection never runs.
    play_index: Optional[int] = None


class NotifyEmailRequest(BaseModel):
    """Request model for POST /api/videos/{video_id}/notify-email."""
    email: str


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/ui/")
async def serve_ui():
    """Serve the UI HTML file."""
    ui_file = Path(__file__).parent.parent / "ui" / "index.html"
    if ui_file.exists():
        return FileResponse(str(ui_file), media_type="text/html")
    else:
        raise HTTPException(
            status_code=404,
            detail=f"UI file not found at {ui_file}"
        )

@app.get("/api/upload")
async def serve_upload():
    """Serve the upload.html file for video ingestion."""
    upload_file = Path(__file__).parent.parent / "upload.html"
    if upload_file.exists():
        return FileResponse(str(upload_file), media_type="text/html")
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Upload file not found at {upload_file}"
        )

@app.get("/")
async def root():
    """Root endpoint - shows API information and navigation."""
    return {
        "name": "TEAM-GRADE Ingestion API",
        "version": "1.0.0",
        "status": "running",
        "documentation": "http://localhost:8000/docs",
        "endpoints": {
            "health": "/health",
            "ingest": "/ingest (POST)",
            "status": "/status/{video_id} (GET)",
            "queue": "/queue (GET)"
        }
    }


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.
    
    Returns:
        HealthResponse with status of all components
    """
    logger.info("GET /api/health")
    
    components = {
        "firestore_client": "ok" if firestore_client else "unavailable",
        "metadata_extractor": "ok" if metadata_extractor else "unavailable"
    }
    
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now().isoformat(),
        components=components
    )


# OPTIONS handlers for CORS preflight
@app.options("/api/ingest")
async def options_ingest():
    """Handle CORS preflight request for /api/ingest"""
    return {}


@app.options("/api/ingest/{video_id}/status")
async def options_status(video_id: str):
    """Handle CORS preflight request for /api/ingest/{video_id}/status"""
    return {}


@app.post("/api/ingest", response_model=IngestResponse)
@limiter.limit(settings.INGEST_RATE_LIMIT)
async def ingest_video(request: Request, body: IngestRequest) -> IngestResponse:
    """
    Ingest a YouTube video.

    Args:
        body: IngestRequest with YouTube URL

    Returns:
        IngestResponse with video_id and queued status

    Raises:
        HTTPException: If URL is invalid or ingestion fails
    """
    logger.info(f"POST /api/ingest - URL: {body.video_url}")

    if not metadata_extractor:
        raise HTTPException(
            status_code=503,
            detail="Metadata extractor not available"
        )
    
    try:
        # Validate URL format
        url = body.video_url.strip()
        if not metadata_extractor.validate_youtube_url(url):
            logger.warning(f"Invalid YouTube URL: {url}")
            raise HTTPException(
                status_code=400,
                detail="Invalid YouTube URL format"
            )
        
        # Extract video ID
        video_id = metadata_extractor.get_video_id(url)
        if not video_id:
            logger.warning(f"Could not extract video ID from: {url}")
            raise HTTPException(
                status_code=400,
                detail="Could not extract video ID from URL"
            )
        
        logger.info(f"Extracted video_id: {video_id}")
        
        # Check existing state (if firestore available)
        should_enqueue = True
        response_status = "queued"
        response_message = "Video queued for ingestion"
        
        if firestore_client:
            try:
                # Check queue integrity
                integrity = firestore_client.check_queue_integrity(video_id)
                video_exists = integrity["video_exists"]
                queue_exists = integrity["queue_exists"]
                current_status = integrity["status"]
                
                logger.info(f"Queue integrity check - video_exists: {video_exists}, queue_exists: {queue_exists}, status: {current_status}")
                
                # Decision logic
                if video_exists and current_status == VideoStatus.COMPLETED.value:
                    should_enqueue = False
                    response_status = "already_processed"
                    response_message = "Video already completely processed"
                    logger.info(f"[OK] Skipping enqueue: already complete")
                    
                elif video_exists and current_status == VideoStatus.PROCESSING.value:
                    should_enqueue = False
                    response_status = "already_queued"
                    response_message = "Video is currently being processed"
                    logger.info(f"[OK] Skipping enqueue: already processing")
                    
                elif video_exists and current_status == VideoStatus.PENDING.value and queue_exists:
                    should_enqueue = False
                    response_status = "already_queued"
                    response_message = "Video is already in queue"
                    logger.info(f"[OK] Skipping enqueue: already queued")
                    
                elif video_exists and current_status == VideoStatus.PENDING.value and not queue_exists:
                    should_enqueue = True
                    response_status = "recovery_enqueue"
                    response_message = "Video stuck in pending - recovery enqueue"
                    logger.warning(f"[WARN] Recovery enqueue: pending but no queue entry exists")

                elif video_exists and current_status == VideoStatus.FAILED.value:
                    # Previously had no branch at all for this - fell through to
                    # should_enqueue's True default, but ingest_worker's own
                    # existence check below bails out on ANY existing video row
                    # regardless of status, silently no-opping a "retry" with no
                    # error surfaced. Explicit branch here documents the intent
                    # (retry is allowed) so it's not accidentally lost again; the
                    # actual fix is at the ingest_worker call site not trusting a
                    # bare "no exception raised" as success.
                    should_enqueue = True
                    response_status = "retry_enqueue"
                    response_message = "Video previously failed - retrying"
                    logger.info(f"[OK] Retry enqueue: previous attempt failed")

                elif not video_exists:
                    should_enqueue = True
                    response_status = "queued"
                    response_message = "Video queued for ingestion"
                    logger.info(f"[OK] Fresh enqueue: video does not exist yet")
                    
            except Exception as e:
                logger.debug(f"Could not check existing status: {str(e)}")
                # On error, default to attempting enqueue (fail-open)
                should_enqueue = True
        
        # Perform ingestion if needed
        if should_enqueue:
            ingestion_success = False
            
            # PRIMARY PATH: Use IngestWorker if available
            if ingest_worker:
                logger.info(f"[PRIMARY] Using IngestWorker for {video_id}")
                try:
                    # ingest_youtube_url returns a result dict rather than
                    # raising on a "soft" failure (e.g. its own internal
                    # existence check bails out on ANY prior video row,
                    # completed or failed) - this used to be discarded
                    # entirely, so a previously-failed video's retry request
                    # would silently no-op here while still reporting success
                    # below, with nothing ever actually re-enqueued.
                    primary_result = ingest_worker.ingest_youtube_url(url)
                    if not primary_result or not primary_result.get("success"):
                        raise RuntimeError(
                            primary_result.get("message", "IngestWorker reported failure")
                            if primary_result else "IngestWorker returned no result"
                        )
                    logger.info(f"[OK] IngestWorker successfully handled {video_id}")
                    ingestion_success = True
                except Exception as worker_e:
                    logger.warning(f"[WARN] IngestWorker failed: {str(worker_e)}")
                    logger.info(f"[FALLBACK] Attempting direct queue enqueue for {video_id}")
                    ingestion_success = False
            
            # FALLBACK PATH: If IngestWorker not available or failed, use direct queue
            if not ingestion_success and queue_manager and firestore_client:
                logger.info(f"[FALLBACK] Direct queue enqueue for {video_id}")
                try:
                    # Save metadata first
                    basic_metadata = {
                        "video_url": url,
                        "video_id": video_id,
                        "status": VideoStatus.PENDING.value,
                        "created_at": datetime.now().isoformat(),
                        "stages": {stage: {"status": "pending"} for stage in ALL_STAGES},
                    }
                    if body.team_id:
                        basic_metadata["team_id"] = body.team_id
                    if body.player_number:
                        basic_metadata["player_number"] = body.player_number
                    if body.position:
                        basic_metadata["position"] = body.position
                    
                    # Save video record
                    firestore_client.save_video_metadata(video_id, basic_metadata)
                    logger.info(f"[OK] Saved video metadata for {video_id}")
                    
                    # Add to queue
                    queue_manager.enqueue_video(
                        video_id,
                        stage=IngestStage.METADATA.value,
                        priority=5,
                        metadata={"fallback_queued": True}
                    )
                    logger.info(f"[OK] Successfully enqueued {video_id} via fallback path")
                    ingestion_success = True
                    
                except Exception as fallback_e:
                    logger.error(f"[FAIL] Fallback queue enqueue failed: {str(fallback_e)}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Ingestion failed: {str(fallback_e)}"
                    )
            
            # Proceed with reliability checks if ingestion was successful
            if not ingestion_success:
                logger.error(f"[FAIL] No ingestion path succeeded for {video_id}")
                raise HTTPException(
                    status_code=500,
                    detail="No available ingestion path (worker and queue both unavailable)"
                )
            
            # BOTH PATHS CONVERGE HERE - Continue with post-ingest checks
            try:
                logger.info(f"[OK] Successfully enqueued {video_id}")
                # POST-INGEST RELIABILITY CHECKS
                if firestore_client:
                    try:
                        logger.info(f"Running post-ingest queue integrity check for {video_id}...")
                        
                        # Check if queue entry exists
                        import time
                        time.sleep(0.5)  # Brief delay for Firestore consistency
                        
                        queue_integrity = firestore_client.check_queue_integrity(video_id)
                        video_exists = queue_integrity["video_exists"]
                        queue_exists = queue_integrity["queue_exists"]
                        
                        logger.info(f"Post-ingest check: video_exists={video_exists}, queue_exists={queue_exists}")
                        
                        if video_exists and not queue_exists:
                            # Queue entry missing - attempt self-healing
                            logger.warning(f"[WARN] Queue entry missing after ingest for {video_id}")
                            healed = firestore_client.verify_and_heal_queue(video_id)
                            if healed:
                                logger.info(f"[OK] Queue entry restored through self-healing")
                            else:
                                logger.error(f"[FAIL] Queue entry still missing after self-healing attempt")
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"Queue entry creation failed for {video_id} (recovery failed)"
                                )
                        elif not queue_exists:
                            logger.error(f"[FAIL] Queue entry not created and video not found for {video_id}")
                            raise HTTPException(
                                status_code=500,
                                detail=f"Queue entry creation failed for {video_id}"
                            )
                        
                        logger.info(f"[OK] Queue integrity verified for {video_id}")
                        
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.error(f"Post-ingest check failed: {str(e)}")
                        # Don't fail hard here - the ingest may have succeeded
                        logger.warning(f"Skipping integrity check due to error (will retry on next status check)")
                        
            except Exception as e:
                logger.error(f"Post-ingest checks failed: {str(e)}")
                # Don't fail - the ingestion itself may have succeeded
        else:
            logger.info(f"Skipped enqueue for {video_id} (status: {response_status})")
        
        return IngestResponse(
            status=response_status,
            video_id=video_id,
            message=response_message,
            timestamp=datetime.now().isoformat()
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Ingestion error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {str(e)}"
        )


@app.post("/api/ingest/upload", response_model=IngestResponse)
@limiter.limit(settings.INGEST_RATE_LIMIT)
async def ingest_uploaded_file(
    request: Request,
    file: UploadFile = File(...),
    team_id: Optional[str] = Form(None),
    player_number: Optional[int] = Form(None),
    position: Optional[str] = Form(None),
) -> IngestResponse:
    """
    Ingest a directly-uploaded video file (no YouTube/yt-dlp source).

    The file is already local, so this skips the "download" stage (and
    "metadata", since there's no YouTube metadata to fetch) and enqueues
    straight at whatever stage a normal download would have enqueued next -
    the pipeline behaves identically to a normal video from that point on.

    Raises:
        HTTPException: If no file is provided or ingestion fails.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not firestore_client or not queue_manager:
        raise HTTPException(
            status_code=503,
            detail="Firestore client or queue manager not available"
        )

    try:
        # Random 11-char ID using VideoIdValidator's exact allowed alphabet
        # (A-Za-z0-9_-) - passes validation unchanged, no validator loosening
        # needed for uploads specifically.
        video_id = secrets.token_urlsafe(9)[:11]

        video_path = settings.get_video_path(video_id)
        contents = await file.read()
        video_path.write_bytes(contents)

        # Upload to S3 (best-effort; never fails the ingest request) - same
        # non-fatal idiom as download_stage.py's upload, for the same reason:
        # this file only exists on the API container's disk, but every
        # pipeline stage after this one (frame_extraction, etc.) runs in the
        # separate team-grade-worker ECS service, on its own separate disk.
        try:
            upload_file(video_path, video_key(video_id))
            logger.info(f"Uploaded directly-uploaded video to S3: {video_key(video_id)}")
        except Exception as e:
            logger.warning(f"Failed to upload directly-uploaded video to S3 (non-fatal): {e}")

        skipped_at = get_utc_timestamp()
        basic_metadata: Dict[str, Any] = {
            "video_url": f"uploaded:{file.filename}",
            "video_id": video_id,
            "status": VideoStatus.PENDING.value,
            "timestamp": datetime.now().isoformat(),
            "download_path": str(video_path),
            "stages": {
                stage: {"status": "pending"}
                for stage in ALL_STAGES
            },
        }
        # metadata/download don't apply to an upload - they didn't run, not
        # "pending" work still to be done.
        basic_metadata["stages"]["metadata"] = {
            "status": STAGE_STATUS_SKIPPED,
            "completed_at": skipped_at,
        }
        basic_metadata["stages"]["download"] = {
            "status": STAGE_STATUS_SKIPPED,
            "path": str(video_path),
            "file_size_bytes": len(contents),
            "completed_at": skipped_at,
        }
        if team_id:
            basic_metadata["team_id"] = team_id
        if player_number:
            basic_metadata["player_number"] = player_number
        if position:
            basic_metadata["position"] = position

        if not firestore_client.save_video_metadata(video_id, basic_metadata):
            raise HTTPException(status_code=500, detail="Failed to save video metadata")

        if not queue_manager.enqueue_video(
            video_id,
            stage=UPLOAD_FIRST_STAGE,
            priority=settings.QUEUE_DEFAULT_PRIORITY,
            metadata={"source": "upload", "previous_stage": "download"}
        ):
            raise HTTPException(status_code=500, detail="Failed to queue uploaded video")

        logger.info(f"[OK] Ingested uploaded file as {video_id}, queued at {UPLOAD_FIRST_STAGE}")

        return IngestResponse(
            status="queued",
            video_id=video_id,
            message="Uploaded video queued for processing",
            timestamp=datetime.now().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Upload ingestion error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Upload ingestion failed: {str(e)}"
        )


@app.get("/api/ingest/{video_id}/status", response_model=StatusResponse)
async def get_status(video_id: str) -> StatusResponse:
    """
    Get ingestion status for a video.
    
    Args:
        video_id: YouTube video ID (will be validated)
        
    Returns:
        StatusResponse with current status and stage
        
    Raises:
        HTTPException: If video not found or invalid video_id
    """
    # Validate and sanitize video_id
    try:
        from config import settings
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid video_id format"
        )
    
    logger.info(f"GET /api/ingest/{safe_video_id}/status")
    
    if not firestore_client:
        raise HTTPException(
            status_code=503,
            detail="Firestore client not available"
        )
    
    try:
        # Query Firestore for video status
        video_data = firestore_client.get_video_status(safe_video_id)
        
        if not video_data:
            logger.warning(f"Video not found: {safe_video_id}")
            raise HTTPException(
                status_code=404,
                detail=f"Video {safe_video_id} not found"
            )
        
        status = video_data.get("status", "unknown")
        error = video_data.get("error", None)
        stages_data = video_data.get("stages", {})

        # Progress: once play_detection has run, remaining stages apply per
        # play, not per video (a stage can be "completed" for one play and
        # still "pending" for another at the same moment) - the old
        # whole-video stage-count fraction is structurally wrong once plays
        # exist, so prefer play-completion fraction when available.
        plays_ref = firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(
            safe_video_id
        ).collection(COLLECTION_PLAYS)
        plays_docs = [d.to_dict() for d in plays_ref.stream()]

        if plays_docs:
            completed_plays = sum(1 for p in plays_docs if p.get("status") == "completed")
            progress = min(100, int((completed_plays / len(plays_docs)) * 100))
        else:
            completed_count = sum(1 for s in stages_data.values() if s.get("status") == "completed")
            progress = min(100, int((completed_count / TOTAL_STAGES) * 100))

        logger.info(f"Video {safe_video_id} status: {status}, progress: {progress}%")
        
        return StatusResponse(
            video_id=safe_video_id,
            status=status,
            progress=progress,
            stages=stages_data or {},
            error=error
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Status query error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve status: {str(e)}"
        )


@app.get("/api/analysis/{video_id}")
async def get_analysis(
    video_id: str, track_id: Optional[int] = Query(None), player_id: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """
    Get per-rep analysis results for a video.

    Args:
        video_id: YouTube video ID (will be validated)
        track_id: optional filter to scope the returned reps to one raw
            (track_id, whatever play it happens to match first) instance -
            prefer player_id, which is unambiguous across plays
        player_id: optional filter to scope the returned reps to one
            cross-play player identity (see _group_tracks_by_player) -
            every rep belonging to that real player, across every play they
            appear in. Takes precedence over track_id if both are given.

    Returns:
        {"reps": [{"rep_index", "track_id", "traits", "buckets", "overall_grade"}, ...],
         "provisional": bool}
        Empty "reps" list (not a 404) if the video exists but analysis hasn't been
        written yet — callers need to be able to tell "not found" apart from
        "not ready yet".

        provisional=true means the final rep_extraction+biomechanics pass
        hasn't run yet, but some pose data already exists - "reps" is a
        preview computed fresh from whatever's been captured so far, using
        the identical segmentation+scoring logic the final pass uses. It may
        still shift, merge, or split as more of the video finishes
        processing. The instant the real final pass writes its results, this
        endpoint returns those instead and provisional flips to false,
        permanently, for that video.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    # Validate and sanitize video_id
    try:
        from config import settings
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid video_id format"
        )

    logger.info(f"GET /api/analysis/{safe_video_id}")

    if not firestore_client:
        raise HTTPException(
            status_code=503,
            detail="Firestore client not available"
        )

    try:
        video_data = firestore_client.get_video_status(safe_video_id)

        if not video_data:
            logger.warning(f"Video not found: {safe_video_id}")
            raise HTTPException(
                status_code=404,
                detail=f"Video {safe_video_id} not found"
            )

        # Active biomechanics stage (biomechanics_stage_vectorized.py) writes each
        # rep's analysis to this subcollection, not a top-level "analysis" field.
        analysis_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(settings.COLLECTION_ANALYSIS)
        )
        reps = [doc.to_dict() for doc in analysis_ref.stream()]

        # Single read of the reps subcollection covers two joins at once: the
        # fallback for analysis docs written before track_id was denormalized
        # into them directly, and the start_frame/end_frame/duration_seconds
        # timing fields the frontend needs to seek the video player to a rep.
        reps_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(settings.COLLECTION_REPS)
        )
        # Keyed on the full (rep_index, track_id, play_index) identity tuple,
        # not bare rep_index - rep_index resets to 0 on every play (Phase C),
        # so a bare-rep_index key collapsed onto whichever rep happened to be
        # last in stream() order, stamping its start_frame/end_frame onto
        # every other rep sharing that rep_index regardless of play or
        # player. Confirmed live: three different tracks in the same play
        # all showed the same start_frame before this fix.
        reps_docs_data = [d.to_dict() for d in reps_ref.stream()]
        rep_docs_by_key = {
            (d.get("rep_index"), d.get("track_id"), d.get("play_index")): d
            for d in reps_docs_data
        }
        # Legacy fallback, bare rep_index only: analysis docs written before
        # track_id was denormalized into them directly have no track_id of
        # their own to build the precise key with. Only pre-per-play-redesign
        # docs ever hit this - every current analysis doc always carries its
        # own real track_id and play_index, so the precise key above matches.
        rep_docs_by_rep_index = {d.get("rep_index"): d for d in reps_docs_data}
        for rep in reps:
            rep_doc = rep_docs_by_key.get(
                (rep.get("rep_index"), rep.get("track_id"), rep.get("play_index"))
            )
            if rep_doc is None and rep.get("track_id") is None:
                rep_doc = rep_docs_by_rep_index.get(rep.get("rep_index"), {})
            rep_doc = rep_doc or {}
            if rep.get("track_id") is None:
                rep["track_id"] = rep_doc.get("track_id")
            # Single-athlete-mode videos (MULTI_PLAYER_TRACKING_ENABLED off, the
            # default) have no real track_id at all - use 0 as a sentinel so
            # every rep is concretely claimable without a schema change on the
            # already-shipped film-stats route (trackId is a required int there).
            if rep.get("track_id") is None:
                rep["track_id"] = 0
            rep["start_frame"] = rep_doc.get("start_frame")
            rep["end_frame"] = rep_doc.get("end_frame")
            rep["duration_seconds"] = rep_doc.get("duration_seconds")

        # Preview plays while the final pass hasn't run yet: if there's no
        # final analysis but some pose data already exists (pose_stage_lite
        # now writes incrementally - see its streaming_batch_size flush),
        # run the exact same segmentation + scoring logic the final pass
        # uses, just on however much pose data currently exists. Computed
        # fresh on every call, never cached or persisted - the next poll
        # naturally reflects whatever's been captured since. The moment the
        # real rep_extraction+biomechanics pass writes to `analysis`, this
        # branch stops being reached at all; the final answer always wins.
        provisional = False
        if not reps and video_data.get("status") != "completed":
            try:
                pose_ref = (
                    firestore_client.db.collection(settings.COLLECTION_VIDEOS)
                    .document(safe_video_id)
                    .collection(settings.COLLECTION_POSE)
                )
                poses_docs = list(pose_ref.stream())
                if poses_docs:
                    from processing.rep_extraction import segment_reps_from_pose_docs

                    multi_player = getattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)
                    provisional_reps_meta, _ = segment_reps_from_pose_docs(
                        firestore_client, safe_video_id, poses_docs, multi_player,
                        settings.FRAME_EXTRACTION_FPS, settings.DEFAULT_PLAYER_POSITION,
                    )
                    for rep_meta in provisional_reps_meta:
                        try:
                            analysis = _recompute_rep_analysis(
                                firestore_client, safe_video_id, rep_meta["rep_index"],
                                rep_meta.get("track_id"), rep_meta["start_frame"], rep_meta["end_frame"],
                            )
                        except ValueError:
                            # Shouldn't normally happen (the range was just
                            # derived from this same pose data) - skip rather
                            # than fail the whole preview over one rep.
                            continue
                        # Same 0-sentinel convention as the final-answer path below.
                        if analysis.get("track_id") is None:
                            analysis["track_id"] = 0
                        analysis["start_frame"] = rep_meta["start_frame"]
                        analysis["end_frame"] = rep_meta["end_frame"]
                        analysis["duration_seconds"] = rep_meta["duration_seconds"]
                        reps.append(analysis)
                    provisional = len(reps) > 0
            except Exception as e:
                # Soft-flag: a broken preview must never break the endpoint -
                # worst case it falls back to today's "no plays yet".
                logger.warning(f"[get_analysis] Provisional analysis failed for {safe_video_id} (non-fatal): {e}")

        # Attach each rep's cross-play player_id (see _group_tracks_by_player
        # and GET /api/tracks) so the frontend can tell which reps belong to
        # the same real player across different plays, instead of comparing
        # raw track_id - which resets to 0 on every play and collides
        # between unrelated players (Phase C). Covers both final-pass and
        # provisional-preview reps, since this runs after both are collected.
        tracks_meta_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_TRACKS_META)
        )
        player_groups = _group_tracks_by_player([doc.to_dict() for doc in tracks_meta_ref.stream()])
        instance_to_player = {
            (instance["track_id"], instance["play_index"]): group["player_id"]
            for group in player_groups
            for instance in group["instances"]
        }
        for rep in reps:
            rep["player_id"] = instance_to_player.get(
                (rep.get("track_id"), rep.get("play_index")), "track_0_x"
            )

        if player_id is not None:
            # Cross-play filter: every rep belonging to the same real player
            # (see _group_tracks_by_player), spanning every play they appear
            # in - not just one (track_id, play_index) instance.
            reps = [r for r in reps if r.get("player_id") == player_id]
        elif track_id is not None:
            reps = [r for r in reps if r.get("track_id") == track_id]

        # Combined authenticity flag - OR of whatever signals are present.
        # Each signal (file_integrity from authenticity_check_stage.py,
        # vision_motion from frame_extraction_stage_gpu.py) is written
        # independently, so this just reads whatever's already on video_data
        # with no extra Firestore call. Soft-flag here; Bridge Athletics'
        # claim-to-profile route is the one place that hard-blocks on this.
        # video_data comes straight from a Postgres row (every column always
        # present) - unlike a Firestore doc, a genuinely-NULL column value
        # means .get(key, {}) returns None (the real stored value), not the
        # default, since the key itself is never actually absent. Real bug
        # this crashed on (2026-07-28): a video whose authenticity_check
        # stage never ran/wrote anything left this column NULL.
        authenticity_signals = video_data.get("authenticity_signals") or {}
        authenticity_flagged = any(sig.get("flagged") for sig in authenticity_signals.values())

        logger.info(f"Retrieved {len(reps)} rep analysis record(s) for {safe_video_id} (provisional={provisional})")
        return {
            "reps": reps,
            "provisional": provisional,
            "authenticity_flagged": authenticity_flagged,
            "authenticity_signals": authenticity_signals,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Analysis retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve analysis: {str(e)}"
        )


@app.get("/api/plays/{video_id}")
async def get_plays(video_id: str) -> Dict[str, Any]:
    """
    Get per-play boundaries/status for a video (per-play redesign, Phase D),
    plus the video-level grade aggregate once it's been computed.

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        {"plays": [{"play_index", "start_frame", "end_frame", "status",
                    "detection_method", "clip_url"}, ...],
         "overall_grade": float | None, "letter_grade": str | None}
        Empty "plays" list (not a 404) if the video exists but
        play_detection hasn't run yet, or this is a single-athlete-mode
        video (play_detection never runs in that mode) - same "not found
        vs. not ready" distinction get_analysis already makes.
        overall_grade/letter_grade are None until complete_stage.py sets
        them (see ingest/stages/complete_stage.py::_compute_overall_grade).
        clip_url is None whenever clip_ready is falsy (export hasn't run,
        or failed - see play_detection_stage.py's inline, best-effort
        _export_play_clip) - the watch page falls back to seeking within
        the full video for that one play in that case.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    try:
        from config import settings
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid video_id format"
        )

    logger.info(f"GET /api/plays/{safe_video_id}")

    if not firestore_client:
        raise HTTPException(
            status_code=503,
            detail="Firestore client not available"
        )

    try:
        video_data = firestore_client.get_video_status(safe_video_id)

        if not video_data:
            logger.warning(f"Video not found: {safe_video_id}")
            raise HTTPException(
                status_code=404,
                detail=f"Video {safe_video_id} not found"
            )

        plays_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_PLAYS)
        )
        plays = sorted(
            (doc.to_dict() for doc in plays_ref.stream()),
            key=lambda p: p.get("play_index", 0),
        )
        for play in plays:
            play["clip_url"] = (
                f"/media/{safe_video_id}/plays/{play['play_index']}.mp4"
                if play.get("clip_ready") else None
            )

        logger.info(f"Retrieved {len(plays)} play(s) for {safe_video_id}")
        return {
            "plays": plays,
            "overall_grade": video_data.get("overall_grade"),
            "letter_grade": video_data.get("letter_grade"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Plays retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve plays: {str(e)}"
        )


def _group_tracks_by_player(tracks_meta_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group per-play tracked-player rows into stable cross-play "player"
    identities, so a claimed player's cards/plays/reps aren't scattered
    across one entry per (track_id, play_index) just because tracking
    resets track_id to 0 on every play (Phase C's per-play redesign) -
    confirmed live: a real 38-play video had 1081 raw tracks_meta rows.

    Rows sharing a jersey_number with jersey_confidence >=
    settings.OCR_CONFIDENCE_THRESHOLD merge into one player_id
    ("jersey_<number>"). Everything else - no jersey read, or one too
    unreliable to trust - stays its own singleton
    ("track_<track_id>_<play_index>") rather than guess-merging on weak
    OCR, which could silently combine two different real players.

    Computed fresh on every call, never persisted - mirrors get_analysis's
    existing "provisional" preview pattern, so this stays correct as
    jersey OCR data streams in during processing with no migration needed.

    Returns:
        List of {"player_id", "jersey_number", "total_frames_tracked",
        "instances": [{"track_id", "play_index"}, ...]}, one per group, in
        first-seen order.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for row in tracks_meta_rows:
        track_id = row.get("track_id")
        play_index = row.get("play_index")
        jersey_number = row.get("jersey_number")
        jersey_confidence = row.get("jersey_confidence") or 0.0
        confident_jersey = bool(jersey_number) and jersey_confidence >= settings.OCR_CONFIDENCE_THRESHOLD

        player_id = (
            f"jersey_{jersey_number}" if confident_jersey
            else f"track_{track_id}_{play_index if play_index is not None else 'x'}"
        )

        if player_id not in groups:
            groups[player_id] = {
                "player_id": player_id,
                "jersey_number": jersey_number if confident_jersey else None,
                "total_frames_tracked": 0,
                "instances": [],
            }
            order.append(player_id)

        group = groups[player_id]
        group["total_frames_tracked"] += row.get("total_frames_tracked") or 0
        group["instances"].append({"track_id": track_id, "play_index": play_index})

    return [groups[player_id] for player_id in order]


@app.get("/api/tracks/{video_id}")
async def get_tracks(video_id: str) -> Dict[str, Any]:
    """
    Get the player library for a video: one entry per cross-play player
    identity (see _group_tracks_by_player) - not one per raw tracks_meta
    row, which would be one per (track_id, play_index) instead. track_id
    resets to 0 on every play (Phase C), so a real multi-play video could
    have 1000+ tracks_meta rows for a couple dozen real players; this
    endpoint groups those rows by jersey number so a player claimed once
    shows up as one card whose "instances" span every play they appear in.

    Single-athlete-mode videos (the default) have no tracks_meta
    subcollection at all - in that case, return a single synthetic entry
    built from the root video doc's own jersey fields, with track_id=0 (the
    same sentinel /api/analysis uses), so the player library and claim flow
    always have exactly one correct card instead of rendering empty.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    logger.info(f"GET /api/tracks/{safe_video_id}")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            logger.warning(f"Video not found: {safe_video_id}")
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        tracks_meta_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_TRACKS_META)
        )
        raw_rows = [doc.to_dict() for doc in tracks_meta_ref.stream()]
        players = _group_tracks_by_player(raw_rows)

        if not players:
            players.append({
                "player_id": "track_0_x",
                "jersey_number": video_data.get("jersey_number"),
                "total_frames_tracked": 0,
                "instances": [{"track_id": 0, "play_index": None}],
            })

        logger.info(f"Retrieved {len(players)} player(s) from {len(raw_rows)} track row(s) for {safe_video_id}")
        return {"tracks": players}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Tracks retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tracks: {str(e)}")


@app.get("/api/tracks/{video_id}/thumbnail/{track_id}")
async def get_track_thumbnail(
    video_id: str, track_id: int, play_index: Optional[int] = Query(None)
) -> FileResponse:
    """
    Serve one representative torso-crop image for a tracked player.

    The crop is chosen server-side (largest crop_box area - the clearest/
    closest available shot; torso crops don't carry their own confidence
    score) and its file path is resolved from Firestore, never accepted
    directly from the client - crop_path is stored relative to PROJECT_ROOT
    with no path-traversal guarantee of its own.

    play_index is optional: a grouped player identity (see
    _group_tracks_by_player) can span several (track_id, play_index)
    instances, and the frontend picks one representative instance to fetch
    a thumbnail for. Omitted, this falls back to matching track_id alone
    across every play - fine for the common single-instance case, but could
    pick a crop from the wrong play if the same track_id number happens to
    recur across plays with no confident jersey to disambiguate.

    Raises:
        HTTPException: If the video or a thumbnail for that track doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    logger.info(f"GET /api/tracks/{safe_video_id}/thumbnail/{track_id}")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            logger.warning(f"Video not found: {safe_video_id}")
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        torso_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(settings.COLLECTION_TORSO)
        )
        crops = [doc.to_dict() for doc in torso_ref.stream()]

        if track_id:
            crops = [c for c in crops if c.get("track_id") == track_id]
        else:
            # Sentinel track_id=0: single-athlete-mode crops carry no track_id field at all.
            crops = [c for c in crops if c.get("track_id") is None]

        if play_index is not None:
            crops = [c for c in crops if c.get("play_index") == play_index]

        if not crops:
            raise HTTPException(
                status_code=404, detail=f"No thumbnail available for track {track_id}"
            )

        def crop_area(crop: Dict[str, Any]) -> float:
            box = crop.get("crop_box")
            if not box or len(box) != 4:
                return 0
            x_min, y_min, x_max, y_max = box
            return max(0, x_max - x_min) * max(0, y_max - y_min)

        best = max(crops, key=crop_area)
        crop_path = (settings.PROJECT_ROOT / best["crop_path"]).resolve()
        project_root = settings.PROJECT_ROOT.resolve()

        # Defense in depth: confirm the resolved path is actually inside
        # PROJECT_ROOT before serving it, even though crop_path always
        # originates server-side from our own pipeline's writes.
        if project_root not in crop_path.parents:
            raise HTTPException(status_code=404, detail="Thumbnail file not found")

        if not crop_path.exists():
            # This API container never ran torso_crop_stage on its own disk
            # (that runs in the separate team-grade-worker ECS service, on
            # its own disk) - fetch this one crop from the shared S3 bucket
            # instead of 404ing outright. Targeted single-file fetch (not the
            # whole video's torso/ prefix) since only this one crop is needed.
            if not download_file(torso_key(safe_video_id, crop_path.name), crop_path):
                raise HTTPException(status_code=404, detail="Thumbnail file not found")

        return FileResponse(str(crop_path), media_type="image/jpeg")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[FAIL] Thumbnail retrieval error for {safe_video_id}/{track_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to retrieve thumbnail: {str(e)}")


@app.get("/api/tracks/{video_id}/frame/{frame_index}")
async def get_frame_boxes(video_id: str, frame_index: int) -> Dict[str, Any]:
    """
    Get per-track bounding boxes for one exact frame - feeds the live box
    overlay during video playback, one small request per displayed frame
    rather than pulling the whole tracks subcollection up front.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        tracks_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_TRACKS)
            .where("frame_index", "==", frame_index)
        )
        # role (referee/player/uncertain, from role_classification_stage) is
        # a per-track attribute, not per-frame - one small query per request
        # to join it in, same cost class as the tracks stream above. NULL
        # for tracks from before that stage existed - the frontend treats
        # that the same as "player" (only a confident "referee" dims a box).
        tracks_meta_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_TRACKS_META)
        )
        role_by_track = {
            doc.to_dict().get("track_id"): doc.to_dict().get("role")
            for doc in tracks_meta_ref.stream()
        }

        boxes = [
            {
                "track_id": d.to_dict().get("track_id"),
                "bbox": d.to_dict().get("bbox"),
                "role": role_by_track.get(d.to_dict().get("track_id")),
            }
            for d in tracks_ref.stream()
        ]

        return {"frame_index": frame_index, "boxes": boxes}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[FAIL] Frame boxes retrieval error for {safe_video_id}/{frame_index}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to retrieve frame boxes: {str(e)}")


@app.get("/api/tracks/{video_id}/route/{track_id}")
async def get_track_route(
    video_id: str, track_id: int, play_index: Optional[int] = Query(None)
) -> Dict[str, Any]:
    """
    Get one track's movement path across a play - the sequence of bbox
    bottom-centers (feet position, a better on-field-location proxy than the
    box centroid) over the track's frame range, feeds the watch page's
    spotlight route overlay.

    play_index should be given whenever available: track_id resets to 0 on
    every play (Phase C's per-play redesign), so omitting it would blend
    together every play's identically-numbered track into one nonsensical
    path.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        tracks_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(COLLECTION_TRACKS)
            .where("track_id", "==", track_id)
        )
        if play_index is not None:
            tracks_ref = tracks_ref.where("play_index", "==", play_index)

        rows = sorted(
            (d.to_dict() for d in tracks_ref.stream()),
            key=lambda r: r.get("frame_index", 0),
        )

        points = []
        for row in rows:
            bbox = row.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = bbox
            points.append({
                "frame_index": row.get("frame_index"),
                "timestamp_seconds": row.get("frame_index", 0) / settings.FRAME_EXTRACTION_FPS,
                "x": (x1 + x2) / 2,
                "y": y2,
            })

        return {"track_id": track_id, "play_index": play_index, "points": points}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[FAIL] Track route retrieval error for {safe_video_id}/{track_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to retrieve track route: {str(e)}")


@app.get("/api/pose/{video_id}/frame/{frame_index}")
async def get_frame_pose(
    video_id: str, frame_index: int, play_index: Optional[int] = Query(None)
) -> Dict[str, Any]:
    """
    Get per-track pose keypoints for one exact frame - feeds the watch
    page's skeletal overlay, one small request per displayed frame, same
    shape/pattern as get_frame_boxes above but reading the `pose` collection
    instead of `tracks`.

    Raises:
        HTTPException: If the video itself doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        pose_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(settings.COLLECTION_POSE)
            .where("frame_index", "==", frame_index)
        )
        if play_index is not None:
            pose_ref = pose_ref.where("play_index", "==", play_index)

        poses = [
            {
                "track_id": d.to_dict().get("track_id"),
                "landmarks": d.to_dict().get("landmarks"),
                "confidence_mean": d.to_dict().get("confidence_mean"),
            }
            for d in pose_ref.stream()
        ]

        return {"frame_index": frame_index, "poses": poses}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[FAIL] Frame pose retrieval error for {safe_video_id}/{frame_index}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to retrieve frame pose: {str(e)}")


def _find_rep_doc(
    firestore_client: 'FirestoreClient',
    safe_video_id: str,
    rep_index: int,
    lookup_track_id: Optional[int],
    play_index: Optional[int] = None,
):
    """
    Find a rep doc by rep_index + track_id (None for the single-athlete-mode
    sentinel) + play_index - shared by the boundary-correction and clip-cut
    endpoints.

    play_index must be included: rep_index and track_id both reset to 0 on
    every play (Phase C's per-play redesign), so matching on just the first
    two returned whichever play happened to be first in stream() order -
    the boundary-correction endpoint could silently update a DIFFERENT
    play's rep, and the clip-cut endpoint could cut a different player's
    footage into a highlight reel.

    Returns:
        The matching Firestore doc snapshot, or None if not found.
    """
    reps_ref = (
        firestore_client.db.collection(settings.COLLECTION_VIDEOS)
        .document(safe_video_id)
        .collection(settings.COLLECTION_REPS)
    )
    for doc in reps_ref.stream():
        data = doc.to_dict()
        if data.get("rep_index") != rep_index:
            continue
        if data.get("track_id") != lookup_track_id:
            continue
        if data.get("play_index") != play_index:
            continue
        return doc
    return None


def _recompute_rep_analysis(
    firestore_client: 'FirestoreClient',
    safe_video_id: str,
    rep_index: int,
    track_id: Optional[int],
    start_frame: int,
    end_frame: int,
    play_index: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Recompute a single rep's biomechanics score over a corrected frame range.

    Reuses the exact same logic biomechanics_stage_vectorized.py already runs
    per-rep on the pipeline's first pass - pose data already covers the whole
    video (pose_stage_lite runs before rep_extraction ever decides a rep's
    boundaries), so a corrected boundary is just a different query range, not
    a re-run of any ML stage.

    Raises:
        ValueError: If there's no pose data in the new range (would otherwise
            silently score as all-zero rather than erroring - score_traits_batch
            doesn't itself fail on empty features).
    """
    pose_ref = (
        firestore_client.db.collection(settings.COLLECTION_VIDEOS)
        .document(safe_video_id)
        .collection(settings.COLLECTION_POSE)
    )
    query = pose_ref.where("frame_index", ">=", start_frame).where("frame_index", "<=", end_frame)
    if play_index is not None:
        query = query.where("play_index", "==", play_index)
    if track_id is not None:
        query = query.where("track_id", "==", track_id)
    pose_docs = list(query.order_by("frame_index").stream())
    pose_frames = [d.to_dict().get("landmarks", {}) for d in pose_docs]

    if not pose_frames:
        raise ValueError("No pose data found for the corrected frame range")

    features = _extract_pose_features(pose_frames)
    trait_configs = _load_trait_configs(str(settings.CONFIG_ROOT))

    scorer = VectorizedTraitScorer()
    trait_scores, trait_names = scorer.score_traits_batch(
        position="unknown", reps_data=[features], trait_configs=trait_configs
    )
    buckets, _ = scorer.bucket_scores_batch(trait_scores)

    return {
        "rep_index": rep_index,
        "track_id": track_id,
        # Must be included: _SubDocRef.set()'s "analysis" branch
        # (ingest/postgres_client.py) reads play_index straight out of this
        # dict to target rep_analysis's real (video_id, rep_index, track_id,
        # play_index) row via upsert_row_coalesce_keys. Omitting it meant
        # every boundary correction silently wrote to the play_index IS NULL
        # slot instead of the rep's real play, leaving the actual row
        # uncorrected.
        "play_index": play_index,
        "traits": {trait_names[j]: float(trait_scores[0, j]) for j in range(len(trait_names))},
        "buckets": {trait_names[j]: str(buckets[0, j]) for j in range(len(trait_names))},
        "overall_grade": float(np.mean(trait_scores[0, :])),
    }


@app.patch("/api/reps/{video_id}")
async def correct_rep_boundary(video_id: str, request: RepCorrectionRequest) -> Dict[str, Any]:
    """
    Manually correct a rep's start/end frame boundary and recompute its
    biomechanics score fresh over the new range - one unified boundary that
    fixes the plays grid, the score, and any highlight clip cut from this rep,
    all at once (per the blueprint's "Manual play-boundary correction").

    request.track_id uses the same 0-sentinel convention as /api/analysis and
    /api/tracks: 0 means "single-athlete-mode, no real track_id".

    Raises:
        HTTPException: 400 for an invalid/too-short range or no pose data in
            it, 404 if the video or the specific rep doesn't exist
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    logger.info(f"PATCH /api/reps/{safe_video_id} - rep_index={request.rep_index}")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        frame_count = video_data.get("frame_count")
        if not frame_count:
            raise HTTPException(
                status_code=409, detail="Video has no known frame count yet (still processing?)"
            )

        if not (0 <= request.start_frame < request.end_frame < frame_count):
            raise HTTPException(
                status_code=400,
                detail=f"start_frame/end_frame must satisfy 0 <= start < end < {frame_count}",
            )
        if request.end_frame - request.start_frame < settings.REP_MIN_DURATION:
            raise HTTPException(
                status_code=400,
                detail=f"Corrected range must be at least {settings.REP_MIN_DURATION} frames",
            )

        # track_id=0 is the single-athlete-mode sentinel (no real track_id on
        # the rep doc at all) - same idiom already used by the thumbnail and
        # analysis endpoints.
        lookup_track_id = request.track_id if request.track_id else None

        rep_doc = _find_rep_doc(
            firestore_client, safe_video_id, request.rep_index, lookup_track_id, request.play_index
        )
        if rep_doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"Rep {request.rep_index} (track_id={request.track_id}, play_index={request.play_index}) not found",
            )
        rep_doc_ref = rep_doc.reference

        try:
            analysis = _recompute_rep_analysis(
                firestore_client,
                safe_video_id,
                request.rep_index,
                lookup_track_id,
                request.start_frame,
                request.end_frame,
                request.play_index,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        duration_frames = request.end_frame - request.start_frame
        duration_seconds = duration_frames / settings.FRAME_EXTRACTION_FPS

        rep_doc_ref.update({
            "start_frame": request.start_frame,
            "end_frame": request.end_frame,
            "duration_frames": duration_frames,
            "duration_seconds": duration_seconds,
        })

        analysis_ref = (
            firestore_client.db.collection(settings.COLLECTION_VIDEOS)
            .document(safe_video_id)
            .collection(settings.COLLECTION_ANALYSIS)
            .document(f"rep_{request.rep_index}")
        )
        analysis_ref.set(analysis)

        logger.info(
            f"[OK] Corrected rep {request.rep_index} boundary for {safe_video_id}: "
            f"[{request.start_frame}, {request.end_frame}]"
        )

        return {
            **analysis,
            "start_frame": request.start_frame,
            "end_frame": request.end_frame,
            "duration_seconds": duration_seconds,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[FAIL] Rep boundary correction error for {safe_video_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to correct rep boundary: {str(e)}")


@app.post("/api/videos/{video_id}/mark-claimed")
async def mark_video_claimed(video_id: str) -> Dict[str, Any]:
    """
    Called by Bridge Athletics right after a successful claim (POST
    /video/film-stats or /video/reels/from-team-grade) so the retention
    sweep (ingest/retention.py) knows not to purge this video. Idempotent -
    calling it again just refreshes claimed_at.
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    video_data = firestore_client.get_video_status(safe_video_id)
    if not video_data:
        raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

    firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(safe_video_id).update({
        "claimed_at": get_utc_timestamp(),
    })
    logger.info(f"[OK] Marked video {safe_video_id} as claimed")
    return {"video_id": safe_video_id, "claimed": True}


# Deliberately simple (not the RFC-5322 grammar) - this only gates against
# obvious junk before storing the address; the real deliverability check
# happens for free when Resend actually tries to send to it.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/videos/{video_id}/notify-email")
async def set_notify_email(video_id: str, body: NotifyEmailRequest) -> Dict[str, Any]:
    """
    Called from the-bridge.app's /watch/{video_id} page while a video is
    still processing - lets a visitor leave an email instead of babysitting
    the progress bar. complete_stage.py reads this field once, when the
    pipeline actually finishes, and fires a one-off notification via Bridge
    Athletics (see settings.BRIDGE_API_BASE). Idempotent - calling again just
    overwrites the address.
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    email = body.email.strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    video_data = firestore_client.get_video_status(safe_video_id)
    if not video_data:
        raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

    firestore_client.db.collection(settings.COLLECTION_VIDEOS).document(safe_video_id).update({
        "notify_email": email,
    })
    logger.info(f"[OK] Set notify_email for video {safe_video_id}")
    return {"video_id": safe_video_id, "notify_email_set": True}


@app.get("/api/reps/{video_id}/clip")
async def get_rep_clip(
    video_id: str,
    track_id: int = Query(...),
    rep_index: int = Query(...),
    with_overlay: bool = Query(False),
    play_index: Optional[int] = Query(None),
) -> FileResponse:
    """
    Cut and return a short clip of one rep's frame range, for the highlight-
    tape creation flow (blueprint: "Highlight tape creation"). Cuts from the
    already-downloaded local source file - nothing purges it yet, so no
    re-fetch from the original source (YouTube/Vimeo/etc.) is needed for the
    common case. If that local file is missing (e.g. manually cleaned up),
    this returns 404 rather than attempting a same-source re-fetch - that
    fallback is real future work, not needed while nothing purges files today.

    Uses ffmpeg stream-copy (-c copy) by default: fast, no re-encode, but the
    actual cut point snaps to the nearest keyframe at/before the requested
    start - may include a moment of footage before the play technically
    started, an accepted tradeoff for a first version, not a bug to chase
    further here.

    with_overlay (blueprint: "Player-highlighting box overlay", off by
    default): draws the claimed track's stored per-frame box into the clip.
    This forces a re-encode (a filter can't run under -c copy) via
    processing/box_overlay.py. Soft-flag, not a hard requirement: if the
    track has no/too-sparse box data for this range (track_id=0's single-
    athlete sentinel, an old video with no multi-player tracking data, or a
    manually-corrected boundary extending past what tracking covered), this
    silently falls back to the exact plain -c copy clip - the response's
    X-Overlay-Applied header tells the caller which happened.

    Raises:
        HTTPException: 400 for an invalid video_id, 404 if the video/rep/
            local source file doesn't exist, 500 if ffmpeg fails
    """
    try:
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    logger.info(f"GET /api/reps/{safe_video_id}/clip - track_id={track_id}, rep_index={rep_index}")

    if not firestore_client:
        raise HTTPException(status_code=503, detail="Firestore client not available")

    output_path: Optional[Path] = None
    try:
        video_data = firestore_client.get_video_status(safe_video_id)
        if not video_data:
            raise HTTPException(status_code=404, detail=f"Video {safe_video_id} not found")

        # track_id=0 is the single-athlete-mode sentinel (no real track_id on
        # the rep doc at all) - same idiom used by /api/analysis, /api/tracks,
        # and PATCH /api/reps/{video_id}.
        lookup_track_id = track_id if track_id else None
        rep_doc = _find_rep_doc(firestore_client, safe_video_id, rep_index, lookup_track_id, play_index)
        if rep_doc is None:
            raise HTTPException(
                status_code=404, detail=f"Rep {rep_index} (track_id={track_id}, play_index={play_index}) not found"
            )

        rep_data = rep_doc.to_dict()
        start_frame = rep_data.get("start_frame")
        end_frame = rep_data.get("end_frame")
        if start_frame is None or end_frame is None:
            raise HTTPException(status_code=404, detail="Rep has no start_frame/end_frame recorded")

        source_path = settings.get_video_path(safe_video_id)
        if not source_path.exists():
            # This API container never ran download_stage on its own disk
            # (that runs in the separate team-grade-worker ECS service, on
            # its own disk) - fetch the source video from the shared S3
            # bucket before giving up on cutting a clip.
            try:
                source_path = ensure_video_local(safe_video_id)
            except S3ObjectNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail="Source video is no longer available locally - can't cut a clip",
                )

        start_seconds = start_frame / settings.FRAME_EXTRACTION_FPS
        duration_seconds = (end_frame - start_frame) / settings.FRAME_EXTRACTION_FPS

        tmp_dir = Path(tempfile.gettempdir())
        output_path = tmp_dir / f"{safe_video_id}_{track_id}_{rep_index}_{secrets.token_hex(4)}.mp4"

        # -ss placed BEFORE -i for both the plain and overlay paths - input
        # seeking is fast (seeks near the preceding keyframe) AND, once a
        # re-encode is already happening (as the overlay path requires),
        # frame-accurate too. Output seeking (-ss after -i) would instead
        # force ffmpeg to decode the entire file from true frame 0 on every
        # request - a real cost against full game film, not just this rep.
        overlay_applied = False
        drawbox_filter: Optional[str] = None
        if with_overlay and track_id:
            # track_id=0 is the single-athlete-mode sentinel - no real
            # per-track data exists to draw from, so don't even try.
            try:
                from processing.box_overlay import (
                    build_drawbox_filter,
                    fetch_track_boxes,
                    get_source_resolution_scale,
                )

                boxes = fetch_track_boxes(firestore_client, safe_video_id, track_id, start_frame, end_frame)
                scale = get_source_resolution_scale(firestore_client, safe_video_id, source_path, start_frame)
                if boxes and scale:
                    drawbox_filter = build_drawbox_filter(
                        boxes, start_frame, end_frame, settings.FRAME_EXTRACTION_FPS, scale[0], scale[1]
                    )
            except Exception as e:
                # Never let a box-overlay failure take down the plain clip -
                # same soft-flag philosophy as the authenticity check.
                logger.warning(
                    f"[get_rep_clip] Overlay build failed for {safe_video_id} rep {rep_index}, "
                    f"falling back to plain clip: {e}"
                )
                drawbox_filter = None

        if drawbox_filter:
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_seconds),
                "-i", str(source_path),
                "-t", str(duration_seconds),
                "-vf", drawbox_filter,
                "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "copy",
                str(output_path),
            ]
            overlay_applied = True
            result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)
            if result.returncode != 0 or not output_path.exists():
                logger.error(
                    f"[FAIL] ffmpeg clip cut failed for {safe_video_id} rep {rep_index}: "
                    f"{result.stderr.decode(errors='replace')}"
                )
                raise HTTPException(status_code=500, detail="Failed to cut clip")
        else:
            try:
                cut_clip(source_path, start_frame, end_frame, output_path, settings.FRAME_EXTRACTION_FPS)
            except ClipCutError as e:
                logger.error(f"[FAIL] ffmpeg clip cut failed for {safe_video_id} rep {rep_index}: {e}")
                raise HTTPException(status_code=500, detail="Failed to cut clip")

        logger.info(
            f"[OK] Cut clip for {safe_video_id} rep {rep_index} -> {output_path} "
            f"(overlay_applied={overlay_applied})"
        )

        return FileResponse(
            str(output_path),
            media_type="video/mp4",
            headers={"X-Overlay-Applied": "true" if overlay_applied else "false"},
            background=BackgroundTask(lambda: output_path.unlink(missing_ok=True)),
        )

    except HTTPException:
        if output_path is not None and output_path.exists():
            output_path.unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired:
        if output_path is not None and output_path.exists():
            output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Clip cutting timed out")
    except Exception as e:
        if output_path is not None and output_path.exists():
            output_path.unlink(missing_ok=True)
        logger.error(f"[FAIL] Clip cut error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cut clip: {str(e)}")


@app.get("/api/queue")
async def get_queue_status() -> Dict[str, Any]:
    """
    Get current ingestion queue status.
    
    Returns:
        Dictionary with queue statistics
    """
    logger.info("GET /api/queue")
    
    if not firestore_client:
        return {
            "queued": 0,
            "processing": 0,
            "total": 0,
            "timestamp": datetime.now().isoformat(),
            "note": "Firestore client not available"
        }
    
    try:
        queue_status = firestore_client.get_queue_status()
        
        return {
            "queued": queue_status.get("queued", 0),
            "processing": queue_status.get("processing", 0),
            "total": queue_status.get("queued", 0) + queue_status.get("processing", 0),
            "timestamp": datetime.now().isoformat()
        }
    
    except Exception as e:
        logger.error(f"[FAIL] Queue status error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve queue status: {str(e)}"
        )


# ============================================================================
# STARTUP/SHUTDOWN
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    logger.info("=" * 70)
    logger.info("TEAM-GRADE INGESTION API - STARTING")
    logger.info("=" * 70)
    logger.info("[OK] API initialized and ready")


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    logger.info("=" * 70)
    logger.info("TEAM-GRADE INGESTION API - SHUTTING DOWN")
    logger.info("=" * 70)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting FastAPI server on http://0.0.0.0:8000")
    logger.info("API documentation available at http://0.0.0.0:8000/docs")
    logger.info("UI available at http://0.0.0.0:8000/ui/")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
