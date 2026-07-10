"""
TEAM-GRADE Ingestion API Server
Provides REST API layer for the YouTube ingestion worker system.
"""

import logging
import os
import secrets
import sys
import subprocess
import tempfile
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, HttpUrl
from pathlib import Path

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
from ingest.firestore_client import FirestoreClient, VideoStatus, IngestStage
from ingest.youtube_metadata import YouTubeMetadataExtractor
from ingest.utils.firestore_utils import COLLECTION_TRACKS, COLLECTION_TRACKS_META
from ingest.stages.biomechanics_stage_vectorized import _extract_pose_features, _load_trait_configs
from processing.vectorized_traits import VectorizedTraitScorer
import numpy as np

# Import centralized constants
try:
    from config.constants import ALL_STAGES, STAGE_STATUS_SKIPPED
except ImportError:
    # Fallback if constants not available
    ALL_STAGES = [
        "metadata", "download", "frame_extraction", "pose",
        "torso_crop", "jersey_ocr", "rep_extraction", "biomechanics", "complete"
    ]
    STAGE_STATUS_SKIPPED = "skipped"

from config import settings
# Reuses the exact same conditional this stage already computes for a normal
# download, rather than duplicating the WHISTLE_DETECTION_ENABLED check here.
from ingest.stages.download_stage import STAGE_NEXT as UPLOAD_FIRST_STAGE
from ingest.utils.firestore_utils import get_utc_timestamp

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

# Serve source video files for playback. Starlette's StaticFiles (FileResponse
# under the hood) already supports HTTP range requests on this fastapi version,
# so the <video> element's scrubber/seek works with no extra range-handling code.
settings.get_videos_dir()  # ensure the directory exists before mounting it
app.mount("/media", StaticFiles(directory=str(settings.VIDEOS_DIR)), name="media")

# Initialize ingestion components
ingest_worker = None
firestore_client = None
metadata_extractor = None
queue_manager = None

try:
    # Step 1: Initialize Firestore (CRITICAL - all other components depend on it)
    firestore_client = FirestoreClient()
    logger.info("[OK] Firestore client initialized")
    
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
    logger.info("[INFO] Firestore and queue manager must be available for fallback mode")
    
    # Try to at least initialize critical components for fallback
    try:
        if not firestore_client:
            firestore_client = FirestoreClient()
            logger.info("[OK] Firestore client initialized (fallback)")
        
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
async def ingest_video(request: IngestRequest) -> IngestResponse:
    """
    Ingest a YouTube video.
    
    Args:
        request: IngestRequest with YouTube URL
        
    Returns:
        IngestResponse with video_id and queued status
        
    Raises:
        HTTPException: If URL is invalid or ingestion fails
    """
    logger.info(f"POST /api/ingest - URL: {request.video_url}")
    
    if not metadata_extractor:
        raise HTTPException(
            status_code=503,
            detail="Metadata extractor not available"
        )
    
    try:
        # Validate URL format
        url = request.video_url.strip()
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
                    ingest_worker.ingest_youtube_url(url)
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
                    if request.team_id:
                        basic_metadata["team_id"] = request.team_id
                    if request.player_number:
                        basic_metadata["player_number"] = request.player_number
                    if request.position:
                        basic_metadata["position"] = request.position
                    
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
async def ingest_uploaded_file(
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
        
        # Calculate progress percentage based on completed stages
        stages_data = video_data.get("stages", {})
        completed_count = sum(1 for s in stages_data.values() if s.get("status") == "completed")
        progress = min(100, int((completed_count / 9) * 100))
        
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
    video_id: str, track_id: Optional[int] = Query(None)
) -> Dict[str, Any]:
    """
    Get per-rep analysis results for a video.

    Args:
        video_id: YouTube video ID (will be validated)
        track_id: optional filter to scope the returned reps to one tracked player

    Returns:
        {"reps": [{"rep_index", "track_id", "traits", "buckets", "overall_grade"}, ...]}
        Empty "reps" list (not a 404) if the video exists but analysis hasn't been
        written yet — callers need to be able to tell "not found" apart from
        "not ready yet".

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
        rep_docs_by_index = {
            d.to_dict().get("rep_index"): d.to_dict() for d in reps_ref.stream()
        }
        for rep in reps:
            rep_doc = rep_docs_by_index.get(rep.get("rep_index"), {})
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

        if track_id is not None:
            reps = [r for r in reps if r.get("track_id") == track_id]

        logger.info(f"Retrieved {len(reps)} rep analysis record(s) for {safe_video_id}")
        return {"reps": reps}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Analysis retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve analysis: {str(e)}"
        )


@app.get("/api/tracks/{video_id}")
async def get_tracks(video_id: str) -> Dict[str, Any]:
    """
    Get the player library for a video: one entry per tracked player.

    Multi-player-mode videos (MULTI_PLAYER_TRACKING_ENABLED) return one entry
    per tracks_meta doc. Single-athlete-mode videos (the default) have no
    tracks_meta subcollection at all - in that case, return a single synthetic
    entry built from the root video doc's own jersey fields, with track_id=0
    (the same sentinel /api/analysis uses), so the player library and claim
    flow always have exactly one correct card instead of rendering empty.

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
        tracks = []
        for doc in tracks_meta_ref.stream():
            data = doc.to_dict()
            tracks.append({
                "track_id": int(doc.id),
                "jersey_number": data.get("jersey_number"),
                "jersey_confidence": data.get("jersey_confidence"),
                "first_frame": data.get("first_frame"),
                "last_frame": data.get("last_frame"),
                "total_frames_tracked": data.get("total_frames_tracked"),
                "status": data.get("status"),
            })

        if not tracks:
            tracks.append({
                "track_id": 0,
                "jersey_number": video_data.get("jersey_number"),
                "jersey_confidence": video_data.get("jersey_confidence"),
                "first_frame": None,
                "last_frame": None,
                "total_frames_tracked": None,
                "status": None,
            })

        logger.info(f"Retrieved {len(tracks)} track(s) for {safe_video_id}")
        return {"tracks": tracks}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FAIL] Tracks retrieval error for {safe_video_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tracks: {str(e)}")


@app.get("/api/tracks/{video_id}/thumbnail/{track_id}")
async def get_track_thumbnail(video_id: str, track_id: int) -> FileResponse:
    """
    Serve one representative torso-crop image for a tracked player.

    The crop is chosen server-side (largest crop_box area - the clearest/
    closest available shot; torso crops don't carry their own confidence
    score) and its file path is resolved from Firestore, never accepted
    directly from the client - crop_path is stored relative to PROJECT_ROOT
    with no path-traversal guarantee of its own.

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
        if project_root not in crop_path.parents or not crop_path.exists():
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
        boxes = [
            {"track_id": d.to_dict().get("track_id"), "bbox": d.to_dict().get("bbox")}
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


def _find_rep_doc(
    firestore_client: 'FirestoreClient',
    safe_video_id: str,
    rep_index: int,
    lookup_track_id: Optional[int],
):
    """
    Find a rep doc by rep_index + track_id (None for the single-athlete-mode
    sentinel) - shared by the boundary-correction and clip-cut endpoints.

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
        return doc
    return None


def _recompute_rep_analysis(
    firestore_client: 'FirestoreClient',
    safe_video_id: str,
    rep_index: int,
    track_id: Optional[int],
    start_frame: int,
    end_frame: int,
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

        rep_doc = _find_rep_doc(firestore_client, safe_video_id, request.rep_index, lookup_track_id)
        if rep_doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"Rep {request.rep_index} (track_id={request.track_id}) not found",
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


@app.get("/api/reps/{video_id}/clip")
async def get_rep_clip(video_id: str, track_id: int = Query(...), rep_index: int = Query(...)) -> FileResponse:
    """
    Cut and return a short clip of one rep's frame range, for the highlight-
    tape creation flow (blueprint: "Highlight tape creation"). Cuts from the
    already-downloaded local source file - nothing purges it yet, so no
    re-fetch from the original source (YouTube/Vimeo/etc.) is needed for the
    common case. If that local file is missing (e.g. manually cleaned up),
    this returns 404 rather than attempting a same-source re-fetch - that
    fallback is real future work, not needed while nothing purges files today.

    Uses ffmpeg stream-copy (-c copy): fast, no re-encode, but the actual cut
    point snaps to the nearest keyframe at/before the requested start - may
    include a moment of footage before the play technically started, an
    accepted tradeoff for a first version, not a bug to chase further here.

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
        rep_doc = _find_rep_doc(firestore_client, safe_video_id, rep_index, lookup_track_id)
        if rep_doc is None:
            raise HTTPException(
                status_code=404, detail=f"Rep {rep_index} (track_id={track_id}) not found"
            )

        rep_data = rep_doc.to_dict()
        start_frame = rep_data.get("start_frame")
        end_frame = rep_data.get("end_frame")
        if start_frame is None or end_frame is None:
            raise HTTPException(status_code=404, detail="Rep has no start_frame/end_frame recorded")

        source_path = settings.get_video_path(safe_video_id)
        if not source_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Source video is no longer available locally - can't cut a clip",
            )

        start_seconds = start_frame / settings.FRAME_EXTRACTION_FPS
        duration_seconds = (end_frame - start_frame) / settings.FRAME_EXTRACTION_FPS

        tmp_dir = Path(tempfile.gettempdir())
        output_path = tmp_dir / f"{safe_video_id}_{track_id}_{rep_index}_{secrets.token_hex(4)}.mp4"

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", str(source_path),
            "-t", str(duration_seconds),
            "-c", "copy",
            str(output_path),
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)

        if result.returncode != 0 or not output_path.exists():
            logger.error(
                f"[FAIL] ffmpeg clip cut failed for {safe_video_id} rep {rep_index}: "
                f"{result.stderr.decode(errors='replace')}"
            )
            raise HTTPException(status_code=500, detail="Failed to cut clip")

        logger.info(f"[OK] Cut clip for {safe_video_id} rep {rep_index} -> {output_path}")

        return FileResponse(
            str(output_path),
            media_type="video/mp4",
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
