"""
Cloud Function wrapper for the pre-ingestion segmentation pipeline.

This function is triggered via HTTP (or Pub/Sub) and runs the segmenter
on a game that has ``status == "ready-for-segmentation"``.

Deploy:
    gcloud functions deploy segment_game \\
        --runtime python311 \\
        --trigger-http \\
        --allow-unauthenticated \\
        --entry-point segment_game \\
        --source functions/ \\
        --memory 2048MB \\
        --timeout 540s

HTTP payload (JSON):
    {
        "game_id": "abc123",
        "video_path": "gs://bucket/normalized/abc123.mp4",
        "output_dir": "gs://bucket/segments/"
    }

Or omit all fields to have the function poll Firestore itself (processes the
oldest ``ready-for-segmentation`` game).
"""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger("functions.main")

# Allow the preprocessor package to be found when deployed alongside
# the functions/ directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def segment_game(request):
    """HTTP Cloud Function entry point."""
    import flask  # available in Cloud Functions runtime

    payload: dict = {}
    if request.content_type and "json" in request.content_type:
        payload = request.get_json(silent=True) or {}

    game_id = payload.get("game_id")
    video_path = payload.get("video_path")
    output_dir = payload.get("output_dir", "gs://teamgrade-segments/segments/")
    use_firestore = payload.get("use_firestore", True)

    # If no game_id supplied, poll Firestore for the next pending job
    if not game_id or not video_path:
        try:
            from google.cloud import firestore as _fs
            db = _fs.Client()
            query = (
                db.collection("games")
                .where("status", "==", "ready-for-segmentation")
                .limit(1)
            )
            docs = list(query.stream())
            if not docs:
                return flask.jsonify({"status": "no_jobs"}), 200
            doc = docs[0]
            game_id = doc.id
            game = doc.to_dict()
            video_path = game.get("normalizedVideoPath") or game.get("rawVideoPath")
            if not video_path:
                return flask.jsonify({"status": "error",
                                      "message": "no video path"}), 400
        except Exception as exc:
            logger.exception("Firestore poll failed: %s", exc)
            return flask.jsonify({"status": "error", "message": str(exc)}), 500

    try:
        from preprocessor.cli import run_pipeline
        results = run_pipeline(
            video_path=video_path,
            output_dir=f"{output_dir.rstrip('/')}/{game_id}/",
            game_id=game_id,
            use_firestore=use_firestore,
        )
        return flask.jsonify({
            "status": "ok",
            "game_id": game_id,
            "segments": len(results),
        }), 200
    except Exception as exc:
        logger.exception("Segmentation failed for %s: %s", game_id, exc)
        return flask.jsonify({"status": "error", "message": str(exc)}), 500
