"""
Postgres Client Module
Replaces ingest/firestore_client.py's FirestoreClient for TEAM-GRADE's
structured data (Pass 2a of the data-layer migration off Firestore).

Exposes the SAME public video/queue-adjacent methods as FirestoreClient, with
the same signatures and the same dict-shaped return values, so callers that
already invoke firestore_client.<method>(...) don't need to change.

It also exposes a `.db` attribute: a small Firestore-API-compatible shim
(collection()/document()/get()/set()/update()/delete()/stream()/where()/
order_by()/batch()) backed by the same Postgres tables. A large amount of
existing pipeline code (every active ingest/stages/*.py handler, api/server.py,
processing/rep_extraction.py) reaches directly into `firestore_client.db...`
rather than going through a wrapper method - those call sites are out of
scope to hand-edit for this migration (stage business logic and server.py's
route logic must not change), so this shim lets that code keep working
completely unmodified against Postgres. See ingest/db_schema.py for the
table definitions this all reads/writes.

The real ingestion-queue read/write path (add_to_queue, dequeue_next_video,
mark_completed/failed/processing, stats) lives on the rewritten QueueManager
(ingest/queue_manager.py), not here - this class only carries the handful of
queue-adjacent convenience/diagnostic methods that non-stage callers
(api/server.py, ingest/ingestion_watchdog.py) invoke directly on
firestore_client rather than through queue_manager.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy import create_engine, event, select, update as sa_update, delete as sa_delete, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.pool import QueuePool

from ingest.db_schema import (
    videos,
    video_stages,
    ingestion_queue,
    frames,
    pose,
    torso_crops,
    reps,
    rep_analysis,
    camera_motion,
    whistle_events,
    detections,
    tracks,
    tracks_meta,
    plays,
    normalize_database_url,
)

logger = logging.getLogger(__name__)

# Firestore doc.update({"stages.<stage>.<field>": value}) keys resolve to these
# known video_stages columns; anything else lands in the JSONB "extra" column.
# Shared by the .db compat shim (_VideoDocRef) and firestore_utils.py's
# update_stage_status()/mark_stage_attempt() so both write paths merge into the
# exact same row shape.
STAGE_KNOWN_COLUMNS = {"status", "attempt", "started_at", "completed_at"}

# Firestore subcollection name -> Postgres table. Mirrors the collection
# constants in ingest/utils/firestore_utils.py and config/settings.py.
SUBCOLLECTION_TABLES = {
    "frames": frames,
    "pose": pose,
    "torso": torso_crops,
    "reps": reps,
    "analysis": rep_analysis,
    "camera_motion": camera_motion,
    "whistle_events": whistle_events,
    "detections": detections,
    "tracks": tracks,
    "tracks_meta": tracks_meta,
    "plays": plays,
}

# Tables where track_id is nullable and participates in a uniqueness tuple -
# Postgres treats every NULL as distinct under a plain UNIQUE constraint, so
# single-athlete-mode rows (track_id IS NULL, the default pipeline mode) need
# the partial "..._null_track" index instead (see ingest/db_schema.py) to
# upsert idempotently rather than accumulating duplicate rows on every re-write.
_NULLABLE_TRACK_UNIQUE = {
    "pose": (pose, ["video_id", "frame_index"], "uq_pose_video_frame_null_track"),
    "torso_crops": (torso_crops, ["video_id", "frame_index"], "uq_torso_video_frame_null_track"),
}

# reps/rep_analysis have TWO independently-nullable identity columns
# (track_id, play_index as of the per-play redesign Phase C) - see
# db_schema.py's uq_reps_video_rep_track_play / uq_rep_analysis_video_rep_track_play
# COALESCE-based expression unique indexes and upsert_row_coalesce_keys below.
_COALESCE_KEY_TABLES = {
    "reps": (reps, ["video_id", "rep_index"], ["track_id", "play_index"]),
    "rep_analysis": (rep_analysis, ["video_id", "rep_index"], ["track_id", "play_index"]),
}


def _utc_now():
    return datetime.now(timezone.utc)


def make_engine(database_url: Optional[str] = None):
    """Build a SQLAlchemy engine for `database_url` (or $DATABASE_URL)."""
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set the DATABASE_URL environment variable "
            "(postgresql://user:pass@host:port/dbname) or pass database_url= explicitly."
        )
    engine = create_engine(normalize_database_url(url), poolclass=QueuePool, pool_pre_ping=True, future=True)

    @event.listens_for(engine, "connect")
    def _force_custom_plan(dbapi_connection, connection_record):
        # upsert_row_coalesce_keys' ON CONFLICT target uses a bound
        # parameter inside COALESCE (coalesce(track_id, %(coalesce_1)s))
        # to match reps'/rep_analysis' expression unique indexes. Postgres
        # only re-plans a given prepared statement shape from scratch
        # ("custom plan") for its first ~5 executions on a connection; after
        # that it switches to a cached "generic plan" for that shape - and
        # generic plans cannot infer a match against an expression index
        # through a bound parameter, so every write after the 5th on the
        # same pooled connection fails with psycopg.errors.
        # InvalidColumnReference ("no unique or exclusion constraint
        # matching the ON CONFLICT specification"). Confirmed live
        # 2026-08-06: reps rows 0-9 in a batch wrote fine, row 10 (6th
        # write of that exact statement shape on the connection) failed
        # every time, and forcing custom plans made all 29 succeed. Only
        # upsert_row_coalesce_keys (reps/rep_analysis) is affected -
        # video_stages_conflict_target/tracks_meta_conflict_target below use
        # partial indexes with plain-column conflict targets instead of a
        # COALESCE expression, so they don't hit this.
        with dbapi_connection.cursor() as cur:
            cur.execute("SET plan_cache_mode = force_custom_plan")
        # psycopg3 connections default to autocommit=False - without this,
        # the SET runs inside an implicit transaction that SQLAlchemy's own
        # first BEGIN/ROLLBACK on this connection discards before any real
        # query ever sees it (confirmed live: SHOW plan_cache_mode read
        # back "auto" - the SET had silently no-op'd - until this commit
        # was added).
        dbapi_connection.commit()

    return engine


def video_stages_conflict_target(play_index: Optional[int] = None):
    """Which video_stages ON CONFLICT arbiter to target - the partial
    "...WHERE play_index IS NULL" index (whole-video-mode rows, every video
    before the per-play redesign and any video where play_detection found no
    boundaries) or the full 3-column composite constraint (real per-play
    rows). Postgres never treats two NULLs as conflicting under a plain
    multi-column UNIQUE constraint, so a play_index-unaware ON CONFLICT would
    insert a duplicate row on every whole-video-mode upsert instead of
    updating in place - mirrors upsert_row_nullable_track's identical
    branching for nullable track_id below.

    Returns (index_elements, index_where) - index_where is None for the
    real-play_index case, since the full composite constraint isn't partial.
    Shared by every ON CONFLICT call site against video_stages:
    upsert_stage_fields (this module), queue_manager.mark_failed,
    firestore_utils.update_stage_status, firestore_utils.mark_stage_attempt.
    """
    if play_index is None:
        return ["video_id", "stage_name"], video_stages.c.play_index.is_(None)
    return ["video_id", "stage_name", "play_index"], None


def tracks_meta_conflict_target(play_index: Optional[int] = None):
    """Which tracks_meta ON CONFLICT arbiter to target - same NULL-uniqueness
    reasoning as video_stages_conflict_target above, for tracks_meta's
    (video_id, track_id, play_index) identity. track_id resets to 0 for
    every play (see db_schema.py's comment on tracks_meta), so a
    play_index-unaware ON CONFLICT would insert a duplicate row - or worse,
    silently overwrite a different play's track 0 summary - instead of
    upserting the right one.
    """
    if play_index is None:
        return ["video_id", "track_id"], tracks_meta.c.play_index.is_(None)
    return ["video_id", "track_id", "play_index"], None


def upsert_stage_fields(
    conn, video_id: str, stage_name: str, fields: Dict[str, Any], play_index: Optional[int] = None
) -> None:
    """Partial-update (or create) one video_stages row, matching Firestore's
    per-leaf-field update({"stages.<stage>.<field>": value, ...}) semantics:
    only the given fields change, sibling fields already on the row (e.g. an
    "attempt" set earlier by mark_stage_attempt) are left untouched.
    """
    known = {k: v for k, v in fields.items() if k in STAGE_KNOWN_COLUMNS}
    extra = {k: v for k, v in fields.items() if k not in STAGE_KNOWN_COLUMNS}

    insert_values = {
        "video_id": video_id,
        "stage_name": stage_name,
        "status": known.get("status"),
        "attempt": known.get("attempt", 0),
        "started_at": known.get("started_at"),
        "completed_at": known.get("completed_at"),
        "extra": extra,
        "play_index": play_index,
    }
    stmt = pg_insert(video_stages).values(**insert_values)

    update_cols = {k: getattr(stmt.excluded, k) for k in known}
    if extra:
        update_cols["extra"] = func.coalesce(video_stages.c.extra, text("'{}'::jsonb")).op("||")(stmt.excluded.extra)

    index_elements, index_where = video_stages_conflict_target(play_index)
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements, index_where=index_where, set_=update_cols
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements, index_where=index_where)
    conn.execute(stmt)


def upsert_row_nullable_track(conn, table_name: str, keys: Dict[str, Any], values: Dict[str, Any]) -> None:
    """Insert-or-update a row in a table whose uniqueness tuple includes a
    nullable track_id, targeting the correct index (the normal composite
    UNIQUE constraint when track_id is a real int, the partial "IS NULL"
    index from ingest/db_schema.py when it's None) so repeated writes for the
    same key upsert in place instead of accumulating duplicates.
    """
    table, base_cols, null_index_name = _NULLABLE_TRACK_UNIQUE[table_name]
    row = {**keys, **values}
    stmt = pg_insert(table).values(**row)
    update_cols = {k: getattr(stmt.excluded, k) for k in values}

    track_id = keys.get("track_id")
    if track_id is None:
        stmt = stmt.on_conflict_do_update(
            index_elements=base_cols,
            index_where=table.c.track_id.is_(None),
            set_=update_cols,
        ) if update_cols else stmt.on_conflict_do_nothing(
            index_elements=base_cols, index_where=table.c.track_id.is_(None)
        )
    else:
        index_elements = base_cols + ["track_id"]
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements, set_=update_cols
        ) if update_cols else stmt.on_conflict_do_nothing(index_elements=index_elements)
    conn.execute(stmt)


def upsert_row_coalesce_keys(conn, table_name: str, keys: Dict[str, Any], values: Dict[str, Any]) -> None:
    """Insert-or-update a row in a table whose uniqueness tuple includes two
    or more independently-nullable columns (reps/rep_analysis: track_id AND
    play_index). Postgres's ON CONFLICT inference matches an expression
    unique index by listing the SAME expressions as conflict target - so
    this targets db_schema.py's COALESCE(col, -1) index directly rather than
    enumerating the 2^n partial-index combinations
    upsert_row_nullable_track's single-nullable-column approach would need.
    """
    table, base_cols, nullable_cols = _COALESCE_KEY_TABLES[table_name]
    row = {**keys, **values}
    stmt = pg_insert(table).values(**row)
    update_cols = {k: getattr(stmt.excluded, k) for k in values}
    index_elements = [table.c[c] for c in base_cols] + [
        func.coalesce(table.c[c], -1) for c in nullable_cols
    ]
    stmt = stmt.on_conflict_do_update(
        index_elements=index_elements, set_=update_cols
    ) if update_cols else stmt.on_conflict_do_nothing(index_elements=index_elements)
    conn.execute(stmt)


class PostgresClient:
    """Manage video metadata (and queue-adjacent diagnostics) in Postgres."""

    def __init__(self, database_url: Optional[str] = None):
        self.engine = make_engine(database_url)
        # Cheap connectivity check at startup, same spirit as
        # FirestoreClient's non-blocking _test_connection().
        try:
            with self.engine.connect() as conn:
                conn.execute(select(1))
            logger.info("[OK] Postgres client initialized")
        except Exception as e:
            logger.warning(f"[WARN] Postgres connection test failed (non-blocking): {e}")

        self.db = _FirestoreCompatDB(self.engine)

    # ------------------------------------------------------------------
    # Video metadata
    # ------------------------------------------------------------------

    def save_video_metadata(self, video_id: str, metadata: Dict[str, Any]) -> bool:
        """Save or update video metadata (upsert), mirroring FirestoreClient's
        set(doc_data, merge=True) with default status/timestamps.
        """
        try:
            logger.info(f"Saving metadata for video: {video_id}")
            now = _utc_now()
            data = dict(metadata)
            data.pop("video_id", None)
            known_cols = {c.name for c in videos.columns} - {"video_id"}
            row = {k: v for k, v in data.items() if k in known_cols}

            insert_values = {
                "video_id": video_id,
                "status": row.pop("status", "pending"),
                "created_at": row.pop("created_at", now),
                "updated_at": now,
                **row,
            }
            stmt = pg_insert(videos).values(**insert_values)
            update_cols = {k: v for k, v in insert_values.items() if k not in ("video_id", "created_at")}
            update_set = {k: getattr(stmt.excluded, k) for k in update_cols}
            stmt = stmt.on_conflict_do_update(index_elements=["video_id"], set_=update_set)
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"[OK] Saved metadata for {video_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save metadata for {video_id}: {e}")
            return False

    def get_video_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        return self.get_video_status(video_id)

    def get_video_status(self, video_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    select(videos).where(videos.c.video_id == video_id)
                ).mappings().first()
                if not row:
                    logger.warning(f"Video not found: {video_id}")
                    return None
                data = dict(row)
                data["stages"] = _load_stages(conn, video_id)
                return data
        except Exception as e:
            logger.error(f"Failed to retrieve status for {video_id}: {e}")
            return None

    def update_video_status(self, video_id: str, status, error_message: Optional[str] = None) -> bool:
        try:
            status_value = status.value if hasattr(status, "value") else status
            values = {"status": status_value, "updated_at": _utc_now()}
            if error_message:
                values["error"] = error_message
            with self.engine.begin() as conn:
                conn.execute(
                    sa_update(videos).where(videos.c.video_id == video_id).values(**values)
                )
            logger.info(f"[OK] Updated {video_id} status to {status_value}")
            return True
        except Exception as e:
            logger.error(f"Failed to update status for {video_id}: {e}")
            return False

    def update_video_field(self, video_id: str, field: str, value: Any) -> bool:
        try:
            with self.engine.begin() as conn:
                if field.startswith("stages."):
                    _, stage_name, stage_field = field.split(".", 2)
                    upsert_stage_fields(conn, video_id, stage_name, {stage_field: value})
                elif field in {c.name for c in videos.columns}:
                    conn.execute(
                        sa_update(videos)
                        .where(videos.c.video_id == video_id)
                        .values(**{field: value, "updated_at": _utc_now()})
                    )
                else:
                    logger.warning(f"update_video_field: unknown column '{field}', ignoring")
                    return False
            logger.debug(f"[OK] Updated {video_id}.{field}")
            return True
        except Exception as e:
            logger.error(f"Failed to update {video_id}.{field}: {e}")
            return False

    def list_videos_by_status(self, status, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            status_value = status.value if hasattr(status, "value") else status
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(videos).where(videos.c.status == status_value).limit(limit)
                ).mappings().all()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list videos: {e}")
            return []

    def archive_old_videos(self, days: int = 30) -> int:
        try:
            cutoff = _utc_now() - timedelta(days=days)
            with self.engine.begin() as conn:
                result = conn.execute(
                    sa_update(videos)
                    .where(videos.c.status == "completed")
                    .where(videos.c.updated_at < cutoff)
                    .values(archived=True, archived_at=_utc_now())
                )
                count = result.rowcount or 0
            logger.info(f"Archived {count} videos")
            return count
        except Exception as e:
            logger.error(f"Failed to archive videos: {e}")
            return 0

    def get_pending_videos(self, max_age_minutes: Optional[int] = None) -> List[Dict[str, Any]]:
        try:
            stmt = select(videos).where(videos.c.status == "pending")
            if max_age_minutes:
                cutoff = _utc_now() - timedelta(minutes=max_age_minutes)
                stmt = stmt.where(videos.c.created_at < cutoff)
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
            results = []
            for r in rows:
                d = dict(r)
                d["_doc_id"] = d["video_id"]
                results.append(d)
            logger.debug(f"Found {len(results)} pending videos")
            return results
        except Exception as e:
            logger.error(f"Failed to get pending videos: {e}")
            return []

    # ------------------------------------------------------------------
    # Queue-adjacent diagnostics (called directly by api/server.py and
    # ingest/ingestion_watchdog.py, bypassing QueueManager - kept here for
    # source-compatibility with those call sites)
    # ------------------------------------------------------------------

    def get_queue_status(self) -> Dict[str, int]:
        try:
            with self.engine.connect() as conn:
                queued = conn.execute(
                    select(func.count()).select_from(ingestion_queue).where(ingestion_queue.c.status == "queued")
                ).scalar_one()
                processing = conn.execute(
                    select(func.count()).select_from(ingestion_queue).where(ingestion_queue.c.status == "processing")
                ).scalar_one()
            return {"queued": queued, "processing": processing, "total": queued + processing}
        except Exception as e:
            logger.error(f"Failed to get queue status: {e}")
            return {"queued": 0, "processing": 0, "total": 0}

    def queue_entry_exists(self, video_id: str) -> bool:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    select(ingestion_queue.c.id).where(ingestion_queue.c.video_id == video_id).limit(1)
                ).first()
            return row is not None
        except Exception as e:
            logger.error(f"Failed to check queue entry for {video_id}: {e}")
            return False

    def check_queue_integrity(self, video_id: str) -> Dict[str, Any]:
        try:
            video_data = self.get_video_status(video_id)
            video_exists = video_data is not None
            queue_exists = self.queue_entry_exists(video_id)
            return {
                "video_exists": video_exists,
                "queue_exists": queue_exists,
                "status": video_data.get("status", "unknown") if video_exists else None,
                "stage": video_data.get("stage", "metadata") if video_exists else None,
                "error": video_data.get("error") if video_exists else None,
            }
        except Exception as e:
            logger.error(f"Failed to check queue integrity for {video_id}: {e}")
            return {"video_exists": False, "queue_exists": False, "status": None, "stage": None, "error": str(e)}

    def add_to_queue(
        self, video_id: str, stage, priority: int = 5, metadata: Optional[Dict[str, Any]] = None,
        play_index: Optional[int] = None,
    ) -> bool:
        try:
            stage_str = stage.value if hasattr(stage, "value") else str(stage)
            # SQL NULL is never equal to NULL, so a naive == comparison would
            # never match an existing play_index=None row and every
            # whole-video-mode enqueue would insert a duplicate - same NULL
            # gotcha as video_stages_conflict_target above, different table.
            play_filter = (
                ingestion_queue.c.play_index.is_(None) if play_index is None
                else ingestion_queue.c.play_index == play_index
            )
            with self.engine.begin() as conn:
                existing = conn.execute(
                    select(ingestion_queue.c.id)
                    .where(ingestion_queue.c.video_id == video_id)
                    .where(ingestion_queue.c.stage == stage_str)
                    .where(play_filter)
                    .where(ingestion_queue.c.status.in_(["queued", "processing"]))
                    .limit(1)
                ).first()
                if existing:
                    logger.warning(f"[DUPLICATE] Job already exists: {video_id}/{stage_str} (play_index={play_index}). Skipping enqueue.")
                    return True
                now = _utc_now()
                conn.execute(
                    ingestion_queue.insert().values(
                        video_id=video_id,
                        stage=stage_str,
                        priority=priority,
                        status="queued",
                        created_at=now,
                        updated_at=now,
                        retry_count=0,
                        max_retries=3,
                        metadata=metadata or {},
                        play_index=play_index,
                    )
                )
            return True
        except Exception as e:
            logger.error(f"Failed to add {video_id} to queue: {e}")
            return False

    def verify_and_heal_queue(self, video_id: str) -> bool:
        try:
            video_data = self.get_video_status(video_id)
            if not video_data:
                return False
            if self.queue_entry_exists(video_id):
                return True
            logger.warning(f"[REPAIR] Queue entry missing for {video_id}, recreating...")
            stage = video_data.get("stage", "metadata")
            priority = video_data.get("priority", 5)
            success = self.add_to_queue(
                video_id, stage=stage, priority=priority,
                metadata={"healed": True, "healed_at": _utc_now().isoformat()},
            )
            if success:
                logger.warning(f"[OK] Successfully healed queue entry for {video_id}")
            return success
        except Exception as e:
            logger.error(f"Queue healing error for {video_id}: {e}")
            return False

    def get_stale_queue_entries(self, max_age_minutes: int = 60) -> List[Dict[str, Any]]:
        try:
            cutoff = _utc_now() - timedelta(minutes=max_age_minutes)
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(ingestion_queue)
                    .where(ingestion_queue.c.created_at < cutoff)
                    .where(ingestion_queue.c.status == "queued")
                ).mappings().all()
            results = []
            for r in rows:
                d = dict(r)
                d["_doc_id"] = str(d["id"])
                results.append(d)
            return results
        except Exception as e:
            logger.error(f"Failed to get stale queue entries: {e}")
            return []

    def delete_queue_entry_by_video_id(self, video_id: str) -> bool:
        try:
            with self.engine.begin() as conn:
                result = conn.execute(sa_delete(ingestion_queue).where(ingestion_queue.c.video_id == video_id))
            deleted = (result.rowcount or 0) > 0
            if deleted:
                logger.info(f"[OK] Deleted queue entry for {video_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete queue entry for {video_id}: {e}")
            return False


def _load_stages(conn, video_id: str) -> Dict[str, Any]:
    rows = conn.execute(
        select(video_stages).where(video_stages.c.video_id == video_id)
    ).mappings().all()
    stages: Dict[str, Any] = {}
    for r in rows:
        entry = {
            "status": r["status"],
            "attempt": r["attempt"],
        }
        if r["started_at"] is not None:
            entry["started_at"] = r["started_at"]
        if r["completed_at"] is not None:
            entry["completed_at"] = r["completed_at"]
        if r["extra"]:
            entry.update(r["extra"])
        stages[r["stage_name"]] = entry
    return stages


# ==========================================================================
# Firestore-API-compatible shim (`PostgresClient.db`)
# ==========================================================================
# See module docstring - this backs every `firestore_client.db.collection(...)`
# call site that couldn't reasonably be rewritten without touching stage/
# server.py business logic. Supports exactly the operations this codebase's
# call sites actually use (catalogued via a full-repo grep before writing
# this): document get/set/update/delete, subcollection stream/where/order_by,
# and simple batched writes.

def _play_suffix(row: Dict[str, Any]) -> str:
    """Doc-id suffix disambiguating rows across plays. track_id and
    rep_index both reset to 0 on every play (Phase C), so tracks_meta/
    analysis/reps doc IDs built from those alone collide across plays -
    e.g. two different plays' track_id=0 rows produced the identical doc.id
    "000", which meant GET /api/tracks's int(doc.id) parsing (before it was
    rewritten to read track_id from the row data directly) silently merged
    or dropped distinct players. Omitted entirely for play_index=None
    (whole-video-mode / pre-per-play-redesign videos) to keep those doc IDs
    unchanged.
    """
    play_index = row.get("play_index")
    return f"_p{play_index:04d}" if play_index is not None else ""


_DOC_ID_BUILDERS = {
    "tracks_meta": lambda row: f"{row['track_id']:03d}{_play_suffix(row)}",
    "analysis": lambda row: f"rep_{row['rep_index']}{_play_suffix(row)}",
    "pose": lambda row: (
        f"{row['frame_index']:06d}_{row['track_id']:03d}" if row.get("track_id") is not None
        else f"{row['frame_index']:06d}"
    ),
    "torso": lambda row: (
        f"{row['frame_index']:06d}_{row['track_id']:03d}" if row.get("track_id") is not None
        else f"{row['frame_index']:06d}"
    ),
    "reps": lambda row: (
        f"{row['track_id']:03d}_{row['rep_index']:04d}" if row.get("track_id") is not None
        else f"{row['rep_index']:06d}"
    ) + _play_suffix(row),
    "detections": lambda row: f"{row['frame_index']:06d}_{row['detection_index']:02d}",
    "tracks": lambda row: f"{row['frame_index']:06d}_{row['track_id']:03d}",
    "frames": lambda row: f"{row['frame_index']:06d}",
    "camera_motion": lambda row: f"{row['frame_index']:06d}",
    "whistle_events": lambda row: f"{row.get('event_index', 0):06d}",
    "plays": lambda row: f"{row['play_index']:04d}",
}


class _DocSnapshot:
    """Mimics google.cloud.firestore.DocumentSnapshot: .exists, .to_dict(), .id, .reference."""

    def __init__(self, doc_id, data, exists, reference=None):
        self.id = doc_id
        self.exists = exists
        self._data = data
        self.reference = reference

    def to_dict(self):
        if not self.exists:
            return None
        return dict(self._data) if self._data else {}


class _FirestoreCompatDB:
    def __init__(self, engine):
        self.engine = engine

    def collection(self, name):
        return _CollectionRef(self.engine, name)

    def batch(self):
        return _Batch(self.engine)


class _Batch:
    """Mimics google.cloud.firestore.WriteBatch: queues .set()/.update() calls
    against document refs, executes them all in commit().

    Not wrapped in one shared SQL transaction - each ref.set()/update() call
    already runs (and commits) its own small transaction via the same
    upsert logic _VideoDocRef/_SubDocRef use directly, so this class reuses
    those rather than re-implementing the upsert SQL a second time. The only
    real caller (biomechanics_stage_vectorized.py) batches a handful of
    per-rep analysis rows per video, so N small transactions instead of one
    larger one is a non-issue here.
    """

    def __init__(self, engine):
        self.engine = engine
        self._ops: List[tuple] = []

    def set(self, ref, data, merge=False):
        self._ops.append(("set", ref, data, merge))

    def update(self, ref, data):
        self._ops.append(("update", ref, data))

    def commit(self):
        ops, self._ops = self._ops, []
        for op in ops:
            if op[0] == "set":
                _, ref, data, merge = op
                ref.set(data, merge=merge)
            else:
                _, ref, data = op
                ref.update(data)


class _CollectionRef:
    """Represents either a root collection ("videos"/"ingestion_queue") or a
    videos/{video_id}/{name} subcollection, depending on whether parent_video_id is set.
    """

    def __init__(self, engine, name, parent_video_id=None):
        self.engine = engine
        self.name = name
        self.parent_video_id = parent_video_id

    def document(self, doc_id):
        if self.parent_video_id is None:
            if self.name == "videos":
                return _VideoDocRef(self.engine, doc_id)
            elif self.name == "ingestion_queue":
                return _QueueDocRef(self.engine, doc_id)
            raise NotImplementedError(f"Unsupported root collection: {self.name}")
        return _SubDocRef(self.engine, self.parent_video_id, self.name, str(doc_id))

    def where(self, field, op, value):
        return _Query(self.engine, self.parent_video_id, self.name).where(field, op, value)

    def order_by(self, field):
        return _Query(self.engine, self.parent_video_id, self.name).order_by(field)

    def stream(self):
        return _Query(self.engine, self.parent_video_id, self.name).stream()


class _Query:
    def __init__(self, engine, video_id, subname):
        self.engine = engine
        self.video_id = video_id
        self.subname = subname
        self.filters: List[tuple] = []
        self.order_field: Optional[str] = None

    def where(self, field, op, value):
        q = _Query(self.engine, self.video_id, self.subname)
        q.filters = self.filters + [(field, op, value)]
        q.order_field = self.order_field
        return q

    def order_by(self, field):
        q = _Query(self.engine, self.video_id, self.subname)
        q.filters = list(self.filters)
        q.order_field = field
        return q

    def stream(self):
        table = SUBCOLLECTION_TABLES[self.subname]
        stmt = select(table).where(table.c.video_id == self.video_id)
        for field, op, value in self.filters:
            col = table.c[field]
            if op == "==":
                stmt = stmt.where(col == value)
            elif op == ">=":
                stmt = stmt.where(col >= value)
            elif op == "<=":
                stmt = stmt.where(col <= value)
            elif op == ">":
                stmt = stmt.where(col > value)
            elif op == "<":
                stmt = stmt.where(col < value)
            else:
                raise NotImplementedError(f"Unsupported Firestore-compat operator: {op}")
        if self.order_field:
            stmt = stmt.order_by(table.c[self.order_field])

        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        builder = _DOC_ID_BUILDERS.get(self.subname, lambda r: str(r.get("id")))
        results = []
        for row in rows:
            data = dict(row)
            doc_id = builder(data)
            ref = _SubDocRef(self.engine, self.video_id, self.subname, doc_id, row_id=data.get("id"))
            results.append(_DocSnapshot(doc_id, data, True, reference=ref))
        return results


class _VideoDocRef:
    def __init__(self, engine, video_id):
        self.engine = engine
        self.video_id = video_id

    def get(self):
        with self.engine.connect() as conn:
            row = conn.execute(select(videos).where(videos.c.video_id == self.video_id)).mappings().first()
            if not row:
                return _DocSnapshot(self.video_id, None, False, reference=self)
            data = dict(row)
            data["stages"] = _load_stages(conn, self.video_id)
            return _DocSnapshot(self.video_id, data, True, reference=self)

    def set(self, data, merge=False):
        data = dict(data)
        data.pop("video_id", None)
        stages_data = data.pop("stages", None)
        known_cols = {c.name for c in videos.columns} - {"video_id"}
        row = {k: v for k, v in data.items() if k in known_cols}

        with self.engine.begin() as conn:
            stmt = pg_insert(videos).values(video_id=self.video_id, **row)
            if row:
                update_cols = {k: getattr(stmt.excluded, k) for k in row}
                stmt = stmt.on_conflict_do_update(index_elements=["video_id"], set_=update_cols)
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=["video_id"])
            conn.execute(stmt)

            if stages_data:
                for stage_name, stage_fields in stages_data.items():
                    upsert_stage_fields(conn, self.video_id, stage_name, stage_fields)

    def update(self, data):
        stage_updates: Dict[str, Dict[str, Any]] = {}
        flat_updates: Dict[str, Any] = {}
        known_cols = {c.name for c in videos.columns}
        for key, value in data.items():
            if key.startswith("stages."):
                parts = key.split(".", 2)
                stage_name = parts[1]
                stage_field = parts[2] if len(parts) > 2 else "status"
                stage_updates.setdefault(stage_name, {})[stage_field] = value
            elif key in known_cols:
                flat_updates[key] = value
            else:
                logger.debug(f"[.db shim] Ignoring unknown video field in update(): {key}")

        with self.engine.begin() as conn:
            if flat_updates:
                conn.execute(
                    sa_update(videos).where(videos.c.video_id == self.video_id).values(**flat_updates)
                )
            for stage_name, fields in stage_updates.items():
                upsert_stage_fields(conn, self.video_id, stage_name, fields)

    def delete(self):
        with self.engine.begin() as conn:
            conn.execute(sa_delete(videos).where(videos.c.video_id == self.video_id))

    def collection(self, subname):
        return _CollectionRef(self.engine, subname, parent_video_id=self.video_id)


class _QueueDocRef:
    """Only .delete() is exercised anywhere in this codebase (maintenance
    scripts) - queue_doc_id is the ingestion_queue row's stringified id.
    """

    def __init__(self, engine, doc_id):
        self.engine = engine
        self.doc_id = doc_id

    def delete(self):
        with self.engine.begin() as conn:
            conn.execute(sa_delete(ingestion_queue).where(ingestion_queue.c.id == int(self.doc_id)))

    def update(self, data):
        values = {k: v for k, v in data.items() if k in {c.name for c in ingestion_queue.columns}}
        if not values:
            return
        with self.engine.begin() as conn:
            conn.execute(
                sa_update(ingestion_queue).where(ingestion_queue.c.id == int(self.doc_id)).values(**values)
            )


class _SubDocRef:
    """A document ref within a videos/{video_id}/{subname} subcollection.

    doc_id follows the same encoding _DOC_ID_BUILDERS produces (e.g. "003"
    for a tracks_meta track_id, "rep_5" for an analysis rep index), so a
    caller can round-trip a snapshot's `.reference` straight back into a
    working ref, and metadata_stage-style construction via
    `.document(f"{track_id:03d}")` resolves consistently.
    """

    def __init__(self, engine, video_id, subname, doc_id, row_id=None):
        self.engine = engine
        self.video_id = video_id
        self.subname = subname
        self.doc_id = doc_id
        self.row_id = row_id

    def get(self):
        if self.subname == "tracks_meta":
            track_id = int(self.doc_id)
            # Same reasoning as .set() below - this generic ref can only
            # ever address the whole-video-mode row (play_index IS NULL);
            # without this filter, a video with real per-play tracks_meta
            # rows (Phase C) could have this arbitrarily return any one of
            # several same-track_id rows across different plays.
            with self.engine.connect() as conn:
                row = conn.execute(
                    select(tracks_meta)
                    .where(tracks_meta.c.video_id == self.video_id)
                    .where(tracks_meta.c.track_id == track_id)
                    .where(tracks_meta.c.play_index.is_(None))
                ).mappings().first()
            if not row:
                return _DocSnapshot(self.doc_id, None, False, reference=self)
            return _DocSnapshot(self.doc_id, dict(row), True, reference=self)

        if self.subname == "analysis":
            rep_index = int(self.doc_id.split("_", 1)[1])
            with self.engine.connect() as conn:
                row = conn.execute(
                    select(rep_analysis)
                    .where(rep_analysis.c.video_id == self.video_id)
                    .where(rep_analysis.c.rep_index == rep_index)
                    .limit(1)
                ).mappings().first()
            if not row:
                return _DocSnapshot(self.doc_id, None, False, reference=self)
            return _DocSnapshot(self.doc_id, dict(row), True, reference=self)

        raise NotImplementedError(f"_SubDocRef.get() not implemented for '{self.subname}'")

    def set(self, fields, merge=False):
        fields = dict(fields)
        if self.subname == "tracks_meta":
            track_id = int(self.doc_id)
            fields.pop("track_id", None)
            # This generic Firestore-compat doc ref has no way to know
            # which play a track belongs to (unlike upsert_track_meta(),
            # which takes play_index explicitly) - its only real caller is
            # tracking_stage.py's best-effort re-ID hook, which already
            # documents this as a known scope limitation. Target the
            # whole-video-mode (play_index IS NULL) row specifically, since
            # that's the only row this generic path can address - since
            # Phase C, tracks_meta's plain (video_id, track_id) index no
            # longer exists on its own (play_index is now part of its
            # identity), so this MUST use the same partial-index target
            # upsert_track_meta() uses instead of a bare 2-column one.
            index_elements, index_where = tracks_meta_conflict_target(play_index=None)
            with self.engine.begin() as conn:
                stmt = pg_insert(tracks_meta).values(video_id=self.video_id, track_id=track_id, **fields)
                update_cols = {k: getattr(stmt.excluded, k) for k in fields}
                stmt = stmt.on_conflict_do_update(
                    index_elements=index_elements, index_where=index_where, set_=update_cols
                ) if update_cols else stmt.on_conflict_do_nothing(
                    index_elements=index_elements, index_where=index_where
                )
                conn.execute(stmt)
            return

        if self.subname == "analysis":
            rep_index = int(self.doc_id.split("_", 1)[1])
            track_id = fields.get("track_id")
            play_index = fields.get("play_index")
            values = {
                "traits": fields.get("traits"),
                "buckets": fields.get("buckets"),
                "overall_grade": fields.get("overall_grade"),
            }
            with self.engine.begin() as conn:
                upsert_row_coalesce_keys(
                    conn, "rep_analysis",
                    keys={
                        "video_id": self.video_id, "rep_index": rep_index,
                        "track_id": track_id, "play_index": play_index,
                    },
                    values=values,
                )
            return

        raise NotImplementedError(f"_SubDocRef.set() not implemented for '{self.subname}'")

    def update(self, fields):
        # Only exercised for "reps" (rep boundary correction) and
        # "tracks_meta" (track_id_conflict flag) in this codebase.
        table = SUBCOLLECTION_TABLES.get(self.subname)
        if table is None or self.row_id is None:
            raise NotImplementedError(f"_SubDocRef.update() not implemented for '{self.subname}'")
        values = {k: v for k, v in fields.items() if k in {c.name for c in table.columns}}
        if not values:
            return
        with self.engine.begin() as conn:
            conn.execute(sa_update(table).where(table.c.id == self.row_id).values(**values))
