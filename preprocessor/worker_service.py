"""
Worker service — always-running daemon that polls Firestore for new game jobs
and runs the segmentation pipeline on each one.

Workflow:
  1. Poll ``games`` collection for documents with ``status == "ready-for-segmentation"``.
  2. Mark the game ``status = "segmenting"``.
  3. Run the full segmentation pipeline (audio + motion + scene + field + fusion + cut).
  4. Write metadata JSON + enqueue each segment to Firestore ``plays/{game_id}/segments/``.
  5. Mark the game ``status = "segmentation-complete"``.
  6. Sleep and repeat.

Run as a long-lived process:
    python -m preprocessor.worker_service

Or as a Docker entrypoint:
    CMD ["python", "-m", "preprocessor.worker_service"]
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("preprocessor.worker_service")

POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "15"))
SEGMENTS_BASE_DIR = os.environ.get("SEGMENTS_BASE_DIR", "segments")
USE_FIRESTORE = os.environ.get("USE_FIRESTORE", "true").lower() == "true"


def _get_firestore_client():
    try:
        from google.cloud import firestore
        return firestore.Client()
    except Exception as exc:
        logger.error("Cannot create Firestore client: %s", exc)
        return None


def _get_next_job(db):
    """Return (game_id, game_dict) or (None, None)."""
    try:
        query = (
            db.collection("games")
            .where("status", "==", "ready-for-segmentation")
            .limit(1)
        )
        for doc in query.stream():
            return doc.id, doc.to_dict()
    except Exception as exc:
        logger.error("Firestore query failed: %s", exc)
    return None, None


def _update_status(db, game_id: str, status: str, extra: dict | None = None):
    try:
        data = {"status": status}
        if extra:
            data.update(extra)
        db.collection("games").document(game_id).update(data)
    except Exception as exc:
        logger.error("Firestore update failed for %s: %s", game_id, exc)


def _run_job(db, game_id: str, game: dict) -> bool:
    from preprocessor.cli import run_pipeline

    video_path = game.get("normalizedVideoPath") or game.get("rawVideoPath")
    if not video_path:
        logger.error("Job %s has no video path; skipping.", game_id)
        _update_status(db, game_id, "segmentation_failed",
                       {"errorMessage": "No video path"})
        return False

    output_dir = os.path.join(SEGMENTS_BASE_DIR, game_id)
    logger.info("Starting segmentation: game=%s video=%s", game_id, video_path)

    try:
        results = run_pipeline(
            video_path=video_path,
            output_dir=output_dir,
            game_id=game_id,
            use_firestore=USE_FIRESTORE,
            dry_run=False,
        )
        _update_status(db, game_id, "segmentation-complete", {
            "segmentCount": len(results),
            "segmentsDir": output_dir,
        })
        logger.info("Segmentation complete: game=%s segments=%d",
                    game_id, len(results))
        return True
    except Exception as exc:
        logger.exception("Segmentation failed for %s: %s", game_id, exc)
        _update_status(db, game_id, "segmentation_failed",
                       {"errorMessage": str(exc)})
        return False


def worker_loop() -> None:
    logger.info("Segmentation worker started (poll interval: %ds)", POLL_INTERVAL_S)
    db = _get_firestore_client()
    if db is None:
        logger.error("Exiting: no Firestore connection.")
        return

    while True:
        game_id, game = _get_next_job(db)
        if game_id is None:
            time.sleep(POLL_INTERVAL_S)
            continue

        _update_status(db, game_id, "segmenting")
        _run_job(db, game_id, game)
        time.sleep(1)  # brief pause between jobs


if __name__ == "__main__":
    worker_loop()
