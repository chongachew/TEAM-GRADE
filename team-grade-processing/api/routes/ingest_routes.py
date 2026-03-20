"""
Ingestion Routes
API endpoints for video ingestion and status tracking.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["Ingestion"])


class IngestRequest(BaseModel):
    """Request model for ingestion."""
    url: str  # YouTube URL or video ID


class IngestResponse(BaseModel):
    """Response model for successful ingestion."""
    status: str
    video_id: str
    message: str
    stages_remaining: int
    timestamp: str


class StageStatus(BaseModel):
    """Status of a single stage."""
    status: str  # "pending", "in-progress", "completed", "failed", "skipped"
    attempt: Optional[int] = None
    error: Optional[str] = None
    completed_at: Optional[str] = None


class VideoStatusResponse(BaseModel):
    """Response model for video status."""
    video_id: str
    status: str  # "pending", "processing", "completed", "failed"
    current_stage: Optional[str] = None
    progress: Dict[str, Any]
    stages: Dict[str, Any]


class ProgressInfo(BaseModel):
    """Progress information."""
    stages_completed: int
    stages_total: int
    percentage: int


@router.post("/", response_model=IngestResponse)
async def ingest_video(request: IngestRequest):
    """
    Trigger ingestion for a YouTube video.

    Args:
        request: Ingestion request with URL or video ID

    Returns:
        Ingestion result with video_id and status
    """
    try:
        from ingest.youtube_metadata import YouTubeMetadataExtractor
        from ingest.firestore_client import FirestoreClient
        from ingest.queue_manager import QueueManager
        from pathlib import Path

        logger.info(f"[API] Ingest request: {request.url}")

        # Extract video ID
        metadata_extractor = YouTubeMetadataExtractor()
        video_id = metadata_extractor.get_video_id(request.url)

        if not video_id:
            logger.error(f"[API] Invalid URL: {request.url}")
            raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")

        # Validate and sanitize video_id
        try:
            from config import settings
            safe_video_id = settings.sanitize_id(video_id)
        except (ValueError, ImportError) as e:
            logger.warning(f"[API] Invalid video_id format: {e}")
            raise HTTPException(status_code=400, detail="Invalid video_id format")

        logger.info(f"[API] Extracted video_id: {safe_video_id}")

        # Check credentials
        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            logger.error(f"[API] Credentials not found: {creds_path}")
            raise HTTPException(
                status_code=500,
                detail="Firestore credentials not configured"
            )

        # Initialize Firestore
        try:
            firestore = FirestoreClient(credentials_path=creds_path)
            queue_manager = QueueManager(firestore)
        except Exception as e:
            logger.error(f"[API] Firestore initialization failed: {e}")
            raise HTTPException(status_code=500, detail="Firestore connection failed")

        # Check if already ingested
        try:
            video_doc = firestore.db.collection("videos").document(safe_video_id).get()
            if video_doc.exists:
                existing_data = video_doc.to_dict()
                if existing_data.get("status") != "pending":
                    logger.warning(f"[API] Video already processing: {safe_video_id}")
                    raise HTTPException(
                        status_code=409,
                        detail=f"Video already ingested (status: {existing_data.get('status')})"
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[API] Failed to check existing video: {e}")
            raise HTTPException(status_code=500, detail="Failed to check video status")

        # Enqueue first stage (metadata)
        try:
            success = queue_manager.enqueue_video(
                safe_video_id,
                stage="metadata",
                priority=5,
                metadata={"source": "api", "ingestion_time": datetime.utcnow().isoformat()}
            )

            if not success:
                logger.error(f"[API] Failed to enqueue video: {safe_video_id}")
                raise HTTPException(status_code=500, detail="Failed to queue video")

            logger.info(f"[API] Enqueued video: {safe_video_id}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[API] Enqueue failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to queue video")

        return IngestResponse(
            status="queued",
            video_id=safe_video_id,
            message="Video queued for ingestion",
            stages_remaining=9,
            timestamp=datetime.utcnow().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Ingest endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{video_id}/status", response_model=VideoStatusResponse)
async def get_video_status(video_id: str):
    """
    Get processing status for a video.

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        Video status and stage information
    """
    # Validate and sanitize video_id early
    try:
        from config import settings
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"[API] Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    try:
        from ingest.firestore_client import FirestoreClient
        from pathlib import Path

        logger.info(f"[API] Status request: {safe_video_id}")

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore credentials not configured")

        # Initialize Firestore
        try:
            firestore = FirestoreClient(credentials_path=creds_path)
        except Exception as e:
            logger.error(f"[API] Firestore connection failed: {e}")
            raise HTTPException(status_code=500, detail="Firestore connection failed")

        # Get video document
        try:
            video_doc = firestore.db.collection("videos").document(safe_video_id).get()

            if not video_doc.exists:
                logger.warning(f"[API] Video not found: {safe_video_id}")
                raise HTTPException(status_code=404, detail="Video not found")

            video_data = video_doc.to_dict()

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[API] Failed to fetch video: {e}")
            raise HTTPException(status_code=500, detail="Failed to fetch video status")

        # Extract status information
        status = video_data.get("status", "unknown")
        stages_data = video_data.get("stages", {})

        # Find current stage
        current_stage = None
        for stage_name, stage_info in stages_data.items():
            if stage_info.get("status") == "in-progress":
                current_stage = stage_name
                break

        # Calculate progress
        stages_completed = sum(
            1 for s in stages_data.values()
            if s.get("status") in ["completed", "skipped"]
        )
        total_stages = len(stages_data)
        percentage = int((stages_completed / total_stages * 100) if total_stages > 0 else 0)

        logger.info(
            f"[API] Status: {safe_video_id} = {status}, "
            f"progress={stages_completed}/{total_stages}"
        )

        return VideoStatusResponse(
            video_id=safe_video_id,
            status=status,
            current_stage=current_stage,
            progress={
                "stages_completed": stages_completed,
                "stages_total": total_stages,
                "percentage": percentage
            },
            stages=stages_data
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Status endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{video_id}/info")
async def get_video_info(video_id: str):
    """
    Get detailed info about a video.

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        Video metadata and statistics
    """
    # Validate and sanitize video_id early
    try:
        from config import settings
        safe_video_id = settings.sanitize_id(video_id)
    except (ValueError, ImportError) as e:
        logger.warning(f"[API] Invalid video_id format: {e}")
        raise HTTPException(status_code=400, detail="Invalid video_id format")

    try:
        from ingest.firestore_client import FirestoreClient
        from pathlib import Path

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore not configured")

        firestore = FirestoreClient(credentials_path=creds_path)

        # Get video document
        video_doc = firestore.db.collection("videos").document(safe_video_id).get()

        if not video_doc.exists:
            raise HTTPException(status_code=404, detail="Video not found")

        video_data = video_doc.to_dict()

        # Extract key info
        return {
            "video_id": safe_video_id,
            "title": video_data.get("title"),
            "status": video_data.get("status"),
            "jersey_number": video_data.get("jersey_number"),
            "frame_count": video_data.get("frame_count"),
            "download_path": video_data.get("download_path"),
            "file_size_mb": video_data.get("file_size_mb"),
            "created_at": video_data.get("created_at"),
            "completed_at": video_data.get("completed_at"),
            "stages": video_data.get("stages", {})
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Info endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
