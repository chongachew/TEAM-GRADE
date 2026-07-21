"""
Postgres Schema (SQLAlchemy Core)
==================================
Single source of truth for the Postgres data layer that replaced Firestore
(Pass 2a of the data-layer migration). Table objects defined here are used by:

- alembic/versions/*.py (the migration that actually creates these tables in
  the real database via ``metadata.create_all``/``drop_all``)
- ingest/postgres_client.py (PostgresClient + the Firestore-compat ``.db``
  shim other pipeline code still calls into)
- ingest/utils/firestore_utils.py (per-collection batch writers)
- ingest/queue_manager.py (the atomic SELECT ... FOR UPDATE SKIP LOCKED queue)

Deliberately plain SQLAlchemy Core (Table/Column), not the ORM - this
codebase's existing style is close-to-the-metal dict-based (see the
Firestore client this replaces), and Core keeps that same shape: callers get
plain dicts back, not mapped objects.
"""

from sqlalchemy import (
    MetaData,
    Table,
    Column,
    BigInteger,
    Integer,
    Float,
    String,
    Text,
    Boolean,
    TIMESTAMP,
    UniqueConstraint,
    Index,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import text as _sql_text

metadata = MetaData()


def normalize_database_url(url: str) -> str:
    """Force the psycopg3 driver ("postgresql+psycopg://") for plain
    "postgresql://"/"postgres://" URLs.

    requirements.txt pins psycopg[binary] (v3), not psycopg2 - but
    SQLAlchemy's default dialect for a bare "postgresql://" scheme is still
    psycopg2. Secrets Manager's stored URL (and the local Docker Postgres
    DATABASE_URL used for dev/tests) both come in as plain "postgresql://",
    so every engine in this codebase is built through this helper instead of
    calling create_engine(url) directly, to avoid a ModuleNotFoundError for
    psycopg2 in production.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url

videos = Table(
    "videos",
    metadata,
    Column("video_id", String, primary_key=True),
    Column("video_url", Text),
    Column("status", String),
    Column("team_id", String),
    Column("player_number", Integer),
    Column("position", String),
    Column("download_path", Text),
    Column("frame_count", Integer),
    Column("jersey_number", String),
    Column("jersey_confidence", Float),
    Column("jersey_source_frame", Integer),
    Column("archived", Boolean, server_default="false"),
    Column("archived_at", TIMESTAMP(timezone=True)),
    Column("error", Text),
    Column("authenticity_signals", JSONB),
    # Set by POST /api/videos/{video_id}/mark-claimed, called by Bridge
    # Athletics right after a successful claim (film-stats or a reel).
    # NULL means "never claimed" - the retention sweep only ever purges
    # videos where this is still NULL past the retention window.
    Column("claimed_at", TIMESTAMP(timezone=True)),
    # Set by POST /api/videos/{video_id}/notify-email. NULL means no
    # reminder was requested. Read once by complete_stage.py to fire a
    # one-off "your film is ready" notification, then left as-is (not
    # cleared) - it's a fire-once trigger, not a subscription.
    Column("notify_email", Text),
    Column("created_at", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
    Column("completed_at", TIMESTAMP(timezone=True)),
)

video_stages = Table(
    "video_stages",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("stage_name", String),
    Column("status", String),
    Column("attempt", Integer, server_default="0"),
    Column("started_at", TIMESTAMP(timezone=True)),
    Column("completed_at", TIMESTAMP(timezone=True)),
    Column("extra", JSONB),
    UniqueConstraint("video_id", "stage_name", name="uq_video_stages_video_stage"),
)

ingestion_queue = Table(
    "ingestion_queue",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("stage", String),
    Column("priority", Integer),
    Column("status", String),
    Column("created_at", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
    Column("retry_count", Integer, server_default="0"),
    Column("max_retries", Integer, server_default="3"),
    Column("processing_started_at", TIMESTAMP(timezone=True)),
    Column("completed_at", TIMESTAMP(timezone=True)),
    Column("failed_at", TIMESTAMP(timezone=True)),
    Column("error", Text),
    Column("metadata", JSONB),
    Index("ix_ingestion_queue_dequeue", "status", "priority", "created_at"),
)

frames = Table(
    "frames",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("timestamp_seconds", Float),
    Column("path", Text),
    Column("width", Integer),
    Column("height", Integer),
    UniqueConstraint("video_id", "frame_index", name="uq_frames_video_frame"),
)

pose = Table(
    "pose",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("track_id", Integer, nullable=True),
    Column("timestamp_seconds", Float),
    Column("landmarks", JSONB),
    Column("confidence_mean", Float),
    Column("created_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_pose_video_frame_track"),
    Index("ix_pose_video_track_frame", "video_id", "track_id", "frame_index"),
    # Postgres treats every NULL as distinct under a plain UNIQUE constraint, so
    # uq_pose_video_frame_track above only actually de-dupes multi-player rows
    # (real track_id). Single-athlete-mode rows (track_id IS NULL, the default
    # pipeline mode) would silently accumulate duplicates on every re-write
    # without this partial index - ON CONFLICT upserts in postgres_client.py
    # target this index specifically when track_id is None.
    Index(
        "uq_pose_video_frame_null_track",
        "video_id", "frame_index",
        unique=True,
        postgresql_where=_sql_text("track_id IS NULL"),
    ),
)

torso_crops = Table(
    "torso_crops",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("track_id", Integer, nullable=True),
    Column("crop_path", Text),
    Column("crop_box", JSONB),
    Column("created_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_torso_video_frame_track"),
    # See uq_pose_video_frame_null_track above - same NULL-track_id upsert gap.
    Index(
        "uq_torso_video_frame_null_track",
        "video_id", "frame_index",
        unique=True,
        postgresql_where=_sql_text("track_id IS NULL"),
    ),
)

reps = Table(
    "reps",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("rep_index", Integer),
    Column("track_id", Integer, nullable=True),
    Column("start_frame", Integer),
    Column("end_frame", Integer),
    Column("duration_frames", Integer),
    Column("duration_seconds", Float),
    Column("jersey_number", String),
    UniqueConstraint("video_id", "rep_index", "track_id", name="uq_reps_video_rep_track"),
    # See uq_pose_video_frame_null_track above - same NULL-track_id upsert gap.
    Index(
        "uq_reps_video_rep_null_track",
        "video_id", "rep_index",
        unique=True,
        postgresql_where=_sql_text("track_id IS NULL"),
    ),
)

rep_analysis = Table(
    "rep_analysis",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("rep_index", Integer),
    Column("track_id", Integer, nullable=True),
    Column("traits", JSONB),
    Column("buckets", JSONB),
    Column("overall_grade", Float),
    UniqueConstraint("video_id", "rep_index", "track_id", name="uq_rep_analysis_video_rep_track"),
    # See uq_pose_video_frame_null_track above - same NULL-track_id upsert gap.
    Index(
        "uq_rep_analysis_video_rep_null_track",
        "video_id", "rep_index",
        unique=True,
        postgresql_where=_sql_text("track_id IS NULL"),
    ),
)

camera_motion = Table(
    "camera_motion",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("homography_to_prev", JSONB),
    Column("homography_to_ref", JSONB),
    Column("translation_x", Float),
    Column("translation_y", Float),
    Column("rotation_deg", Float),
    Column("scale", Float),
    Column("num_matches", Integer),
    Column("num_inliers", Integer),
    Column("inlier_ratio", Float),
    Column("low_confidence", Boolean),
    UniqueConstraint("video_id", "frame_index", name="uq_camera_motion_video_frame"),
)

whistle_events = Table(
    "whistle_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("event_index", Integer),
    Column("timestamp", Float),
    Column("confidence", Float),
    Column("onset_strength", Float),
    Column("spectral_centroid", Float),
    Column("duration", Float),
    Column("type", String),
    UniqueConstraint("video_id", "event_index", name="uq_whistle_video_event"),
)

detections = Table(
    "detections",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("detection_index", Integer),
    Column("bbox", JSONB),
    Column("confidence", Float),
    Column("class_name", String),
    Column("created_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("video_id", "frame_index", "detection_index", name="uq_detections_video_frame_idx"),
)

tracks = Table(
    "tracks",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("frame_index", Integer),
    Column("track_id", Integer),
    Column("bbox", JSONB),
    Column("mask_area_px", Float),
    Column("confidence", Float),
    Column("matched_via", String),
    Column("occlusion_state", String),
    Column("frames_since_last_seen", Integer),
    Column("created_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_tracks_video_frame_track"),
    Index("ix_tracks_video_track_frame", "video_id", "track_id", "frame_index"),
)

tracks_meta = Table(
    "tracks_meta",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("track_id", Integer),
    Column("jersey_number", String),
    Column("jersey_confidence", Float),
    Column("jersey_source_frame", Integer),
    Column("first_frame", Integer),
    Column("last_frame", Integer),
    Column("total_frames_tracked", Integer),
    Column("status", String),
    Column("track_id_conflict", Boolean),
    UniqueConstraint("video_id", "track_id", name="uq_tracks_meta_video_track"),
)
