"""
Segment metadata — JSON writer and Firestore enqueuer for play segments.

Each play segment gets:
  - A JSON sidecar file (``play_001_metadata.json``) written next to the clip.
  - A Firestore document in ``plays/{game_id}/segments/{segment_id}``
    that Phase 2 workers poll.

Schema (JSON + Firestore document):
  {
    "game_id": "abc123",
    "segment_id": "play_001",
    "segment_path": "gs://bucket/segments/play_001.mp4",
    "start_time": 12.5,
    "end_time": 35.0,
    "duration": 22.5,
    "whistle_confidence": 0.82,
    "motion_score": 0.71,
    "scene_score": 1.0,
    "field_presence": 0.65,
    "fusion_confidence": 0.74,
    "status": "pending",
    "created_at": "2026-03-25T22:00:00Z"
  }
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from preprocessor.config import (
    METADATA_SUFFIX,
    FIRESTORE_PLAYS_COLLECTION,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_metadata(
    game_id: str,
    segment_id: str,
    segment_path: str,
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the full metadata dict for a segment candidate."""
    return {
        "game_id": game_id,
        "segment_id": segment_id,
        "segment_path": segment_path,
        "start_time": candidate["start_time"],
        "end_time": candidate["end_time"],
        "duration": candidate["duration"],
        "whistle_confidence": candidate.get("whistle_confidence", 0.0),
        "motion_score": candidate.get("motion_score", 0.0),
        "scene_score": candidate.get("scene_score", 0.0),
        "field_presence": candidate.get("field_presence", 0.0),
        "fusion_confidence": candidate["fusion_confidence"],
        "status": "pending",
        "created_at": _now_iso(),
    }


def write_json(metadata: Dict[str, Any], output_dir: str, segment_id: str) -> str:
    """Write *metadata* to a JSON sidecar file.  Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"{segment_id}{METADATA_SUFFIX}")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
    logger.info("Metadata written → %s", json_path)
    return json_path


def enqueue_to_firestore(
    metadata: Dict[str, Any],
    game_id: str,
    segment_id: str,
    db=None,
) -> bool:
    """Write segment metadata to Firestore ``plays/{game_id}/segments/{id}``.

    Pass *db* = firestore.Client() instance.  If *db* is None the function
    attempts to import and create the client automatically.

    Returns True on success, False on error.
    """
    if db is None:
        try:
            from google.cloud import firestore
            db = firestore.Client()
        except Exception as exc:  # pragma: no cover
            logger.error("Could not connect to Firestore: %s", exc)
            return False

    try:
        doc_ref = (
            db.collection(FIRESTORE_PLAYS_COLLECTION)
            .document(game_id)
            .collection("segments")
            .document(segment_id)
        )
        doc_ref.set(metadata)
        logger.info("Firestore: enqueued %s/%s", game_id, segment_id)
        return True
    except Exception as exc:
        logger.error("Firestore write failed for %s/%s: %s",
                     game_id, segment_id, exc)
        return False


class SegmentMetadataWriter:
    """Convenience wrapper that writes JSON + optionally enqueues to Firestore."""

    def __init__(
        self,
        output_dir: str,
        game_id: str,
        gcs_base_path: str = "",
        use_firestore: bool = False,
        db=None,
    ) -> None:
        self.output_dir = output_dir
        self.game_id = game_id
        self.gcs_base_path = gcs_base_path.rstrip("/")
        self.use_firestore = use_firestore
        self.db = db

    def write_all(
        self,
        candidates: List[Dict[str, Any]],
        prefix: str = "play",
    ) -> List[Dict[str, Any]]:
        """Write metadata for every candidate.  Returns list of metadata dicts."""
        results: List[Dict[str, Any]] = []
        for i, cand in enumerate(candidates, start=1):
            segment_id = f"{prefix}_{i:03d}"
            seg_file = f"{segment_id}.mp4"
            segment_path = (
                f"{self.gcs_base_path}/{seg_file}"
                if self.gcs_base_path
                else seg_file
            )
            meta = build_metadata(
                self.game_id, segment_id, segment_path, cand
            )
            write_json(meta, self.output_dir, segment_id)
            if self.use_firestore:
                enqueue_to_firestore(meta, self.game_id, segment_id, self.db)
            results.append(meta)
        return results
