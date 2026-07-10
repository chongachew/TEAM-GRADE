"""
TEAM-GRADE Ingestion API Server
Provides REST API layer for the YouTube ingestion worker system.
"""

import logging
import os
import sys
import subprocess
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
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

# Import centralized constants
try:
    from config.constants import ALL_STAGES
except ImportError:
    # Fallback if constants not available
    ALL_STAGES = [
        "metadata", "download", "frame_extraction", "pose",
        "torso_crop", "jersey_ocr", "rep_extraction", "biomechanics", "complete"
    ]

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

        # Fallback join for analysis docs written before track_id was denormalized
        # into them directly — look it up from the reps collection by rep_index.
        missing = [r for r in reps if r.get("track_id") is None]
        if missing:
            reps_ref = (
                firestore_client.db.collection(settings.COLLECTION_VIDEOS)
                .document(safe_video_id)
                .collection(settings.COLLECTION_REPS)
            )
            track_id_by_rep_index = {
                d.to_dict().get("rep_index"): d.to_dict().get("track_id")
                for d in reps_ref.stream()
            }
            for rep in missing:
                rep["track_id"] = track_id_by_rep_index.get(rep.get("rep_index"))

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
