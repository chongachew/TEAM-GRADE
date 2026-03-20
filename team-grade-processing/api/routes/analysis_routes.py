"""
Analysis Routes
API endpoints for fetching biomechanics analysis results.
"""

import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["Analysis"])


@router.get("/{video_id}")
async def get_analysis(video_id: str):
    """
    Get biomechanics analysis results for a video.

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        Biomechanics analysis data (per-rep and summary)
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

        logger.info(f"[API] Analysis request: {safe_video_id}")

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore not configured")

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
            raise HTTPException(status_code=500, detail="Failed to fetch video")

        # Check if analysis exists
        analysis = video_data.get("analysis")

        if not analysis:
            logger.warning(f"[API] No analysis for: {safe_video_id}")
            raise HTTPException(
                status_code=404,
                detail="Analysis not available (pipeline may not be complete)"
            )

        # Build response
        return {
            "video_id": safe_video_id,
            "jersey_number": video_data.get("jersey_number"),
            "status": video_data.get("status"),
            "analysis": analysis,
            "metadata": {
                "frame_count": video_data.get("frame_count"),
                "completed_at": video_data.get("completed_at")
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Analysis endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{video_id}/reps")
async def get_reps(
    video_id: str,
    rep_id: Optional[str] = Query(None, description="Specific rep ID")
):
    """
    Get rep data for a video.

    Args:
        video_id: YouTube video ID (will be validated)
        rep_id: Optional specific rep ID

    Returns:
        List of reps with analysis data
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

        logger.info(f"[API] Reps request: {safe_video_id}")

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore not configured")

        firestore = FirestoreClient(credentials_path=creds_path)

        # Get video document (to verify it exists)
        video_doc = firestore.db.collection("videos").document(safe_video_id).get()

        if not video_doc.exists:
            raise HTTPException(status_code=404, detail="Video not found")

        # Get reps subcollection
        reps_collection = (
            firestore.db.collection("videos")
            .document(safe_video_id)
            .collection("reps")
        )

        if rep_id:
            # Get specific rep
            rep_doc = reps_collection.document(rep_id).get()

            if not rep_doc.exists:
                raise HTTPException(status_code=404, detail=f"Rep {rep_id} not found")

            return {
                "video_id": safe_video_id,
                "rep_id": rep_id,
                "data": rep_doc.to_dict()
            }

        else:
            # Get all reps
            rep_docs = list(reps_collection.stream())

            reps = []
            for doc in sorted(rep_docs, key=lambda d: d.id):
                rep_data = doc.to_dict()
                rep_data["rep_id"] = doc.id
                reps.append(rep_data)

            logger.info(f"[API] Found {len(reps)} reps for {safe_video_id}")

            return {
                "video_id": safe_video_id,
                "total_reps": len(reps),
                "reps": reps
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Reps endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{video_id}/summary")
async def get_summary(video_id: str):
    """
    Get summary statistics for a video.

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        Summary metrics and statistics
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

        logger.info(f"[API] Summary request: {safe_video_id}")

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore not configured")

        firestore = FirestoreClient(credentials_path=creds_path)

        # Get video document
        video_doc = firestore.db.collection("videos").document(safe_video_id).get()

        if not video_doc.exists:
            raise HTTPException(status_code=404, detail="Video not found")

        video_data = video_doc.to_dict()

        # Extract summary
        analysis = video_data.get("analysis", {})
        summary = analysis.get("summary", {})

        # Get reps for statistics
        reps_collection = (
            firestore.db.collection("videos")
            .document(safe_video_id)
            .collection("reps")
        )

        rep_docs = list(reps_collection.stream())

        # Compute statistics
        total_reps = len(rep_docs)
        total_duration = sum(
            doc.to_dict().get("duration_seconds", 0)
            for doc in rep_docs
        )

        return {
            "video_id": safe_video_id,
            "jersey_number": video_data.get("jersey_number"),
            "status": video_data.get("status"),
            "statistics": {
                "total_reps": total_reps,
                "total_duration_seconds": total_duration,
                "frame_count": video_data.get("frame_count"),
                "jersey_confidence": video_data.get("jersey_confidence")
            },
            "analysis_summary": summary,
            "created_at": video_data.get("created_at"),
            "completed_at": video_data.get("completed_at")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Summary endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{video_id}/metrics")
async def get_metrics(video_id: str):
    """
    Get detailed metrics for a video.

    Includes:
    - Per-rep trait scores
    - Position-specific metrics
    - Aggregated statistics

    Args:
        video_id: YouTube video ID (will be validated)

    Returns:
        Detailed metrics data
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
        import statistics

        logger.info(f"[API] Metrics request: {safe_video_id}")

        creds_path = Path(__file__).parent.parent.parent / "credentials" / "service-account.json"

        if not creds_path.exists():
            raise HTTPException(status_code=500, detail="Firestore not configured")

        firestore = FirestoreClient(credentials_path=creds_path)

        # Get video document
        video_doc = firestore.db.collection("videos").document(safe_video_id).get()

        if not video_doc.exists:
            raise HTTPException(status_code=404, detail="Video not found")

        video_data = video_doc.to_dict()

        # Get reps with analysis
        reps_collection = (
            firestore.db.collection("videos")
            .document(safe_video_id)
            .collection("reps")
        )

        rep_docs = list(reps_collection.stream())

        # Aggregate metrics
        all_traits = {}
        rep_metrics = []

        for rep_doc in rep_docs:
            rep_data = rep_doc.to_dict()
            analysis = rep_data.get("analysis", {})
            traits = analysis.get("traits", {})

            # Collect traits
            for trait_name, trait_data in traits.items():
                if trait_name not in all_traits:
                    all_traits[trait_name] = []

                if isinstance(trait_data, dict) and "score" in trait_data:
                    all_traits[trait_name].append(trait_data["score"])

            rep_metrics.append({
                "rep_id": rep_doc.id,
                "duration_seconds": rep_data.get("duration_seconds"),
                "traits_count": len(traits),
                "position": analysis.get("position")
            })

        # Compute aggregated statistics
        trait_stats = {}

        for trait_name, scores in all_traits.items():
            if scores:
                trait_stats[trait_name] = {
                    "avg": statistics.mean(scores),
                    "min": min(scores),
                    "max": max(scores),
                    "stddev": statistics.stdev(scores) if len(scores) > 1 else 0,
                    "samples": len(scores)
                }

        return {
            "video_id": safe_video_id,
            "jersey_number": video_data.get("jersey_number"),
            "trait_statistics": trait_stats,
            "rep_metrics": rep_metrics,
            "total_reps_analyzed": len(rep_docs)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Metrics endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
