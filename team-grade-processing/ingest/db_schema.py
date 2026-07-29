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
    func,
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
    # Video-level grade aggregate (Phase D of the per-play redesign): the
    # mean of every rep_analysis row's overall_grade for this video,
    # across every play - set once by complete_stage.py right before it
    # marks the video completed. NULL until then. See
    # complete_stage.py's _compute_overall_grade().
    Column("overall_grade", Float, nullable=True),
    Column("letter_grade", String, nullable=True),
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
    # Per-play redesign: NULL means "whole-video mode" (every video before
    # play_detection existed, and any video where it found nothing and fell
    # back to one whole-video play). A real play_index means this stage's
    # progress is tracked independently per play.
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "stage_name", "play_index", name="uq_video_stages_video_stage_play"),
    # Postgres treats every NULL as distinct under the composite constraint
    # above, so it only actually de-dupes real per-play rows. Whole-video-mode
    # rows (play_index IS NULL) need this partial index - same pattern as
    # uq_pose_video_frame_null_track below - or every upsert with
    # play_index=None would insert a new row instead of updating in place.
    # ON CONFLICT calls target this index specifically when play_index is
    # None (see postgres_client.video_stages_conflict_index).
    Index(
        "uq_video_stages_video_stage_null_play",
        "video_id", "stage_name",
        unique=True,
        postgresql_where=_sql_text("play_index IS NULL"),
    ),
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
    # NULL for whole-video-mode jobs (all jobs before play_detection existed).
    # A real value scopes this queue row to one play - dequeue_next_video()
    # groups claimable rows by (video_id, play_index) so plays of the same
    # video can progress independently instead of blocking each other.
    Column("play_index", Integer, nullable=True),
    Index("ix_ingestion_queue_dequeue", "status", "priority", "created_at"),
    Index("ix_ingestion_queue_video_play", "video_id", "play_index"),
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
    # Filter-only column for Phase C (per-play scoped frame loading) - a
    # frame is still uniquely identified by (video_id, frame_index) below,
    # play_index is not part of its identity, just which play it belongs to.
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "frame_index", name="uq_frames_video_frame"),
    Index("ix_frames_video_play", "video_id", "play_index"),
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
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_pose_video_frame_track"),
    Index("ix_pose_video_track_frame", "video_id", "track_id", "frame_index"),
    Index("ix_pose_video_play", "video_id", "play_index"),
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
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_torso_video_frame_track"),
    # See uq_pose_video_frame_null_track above - same NULL-track_id upsert gap.
    Index(
        "uq_torso_video_frame_null_track",
        "video_id", "frame_index",
        unique=True,
        postgresql_where=_sql_text("track_id IS NULL"),
    ),
    Index("ix_torso_video_play", "video_id", "play_index"),
)

reps = Table(
    "reps",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("rep_index", Integer),
    Column("track_id", Integer, nullable=True),
    # Per-play redesign Phase C: rep_extraction now runs once per play, and
    # segment_reps_from_pose_docs() assigns rep_index starting at 0 on every
    # call - without play_index in the identity tuple, two different plays'
    # rep_index=0 would collide on the same (video_id, rep_index, track_id)
    # row and silently overwrite each other instead of coexisting.
    Column("play_index", Integer, nullable=True),
    Column("start_frame", Integer),
    Column("end_frame", Integer),
    Column("duration_frames", Integer),
    Column("duration_seconds", Float),
    Column("jersey_number", String),
)
# track_id AND play_index are both independently nullable - rather than
# enumerating the 4 partial-index combinations the single-nullable-column
# pattern above uses (uq_pose_video_frame_null_track and siblings), a single
# COALESCE-based expression unique index treats NULL as a normal sentinel
# value (-1, an otherwise-impossible track_id/play_index) so ON CONFLICT can
# target one index regardless of which columns are NULL.
Index(
    "uq_reps_video_rep_track_play",
    reps.c.video_id, reps.c.rep_index,
    func.coalesce(reps.c.track_id, -1), func.coalesce(reps.c.play_index, -1),
    unique=True,
)

rep_analysis = Table(
    "rep_analysis",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("rep_index", Integer),
    Column("track_id", Integer, nullable=True),
    Column("play_index", Integer, nullable=True),
    Column("traits", JSONB),
    Column("buckets", JSONB),
    Column("overall_grade", Float),
)
# See reps' uq_reps_video_rep_track_play above - same two-independently-
# nullable-columns reasoning applies here.
Index(
    "uq_rep_analysis_video_rep_track_play",
    rep_analysis.c.video_id, rep_analysis.c.rep_index,
    func.coalesce(rep_analysis.c.track_id, -1), func.coalesce(rep_analysis.c.play_index, -1),
    unique=True,
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
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "frame_index", "detection_index", name="uq_detections_video_frame_idx"),
    Index("ix_detections_video_play", "video_id", "play_index"),
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
    Column("play_index", Integer, nullable=True),
    UniqueConstraint("video_id", "frame_index", "track_id", name="uq_tracks_video_frame_track"),
    Index("ix_tracks_video_track_frame", "video_id", "track_id", "frame_index"),
    Index("ix_tracks_video_play", "video_id", "play_index"),
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
    # Set by role_classification_stage.py (processing/uniform_classifier.py):
    # "player" | "referee" | "uncertain", or NULL for tracks from before this
    # stage existed. Directly parallel to jersey_number/jersey_confidence.
    Column("role", String, nullable=True),
    Column("role_confidence", Float, nullable=True),
    Column("first_frame", Integer),
    Column("last_frame", Integer),
    Column("total_frames_tracked", Integer),
    Column("status", String),
    Column("track_id_conflict", Boolean),
    Column("play_index", Integer, nullable=True),
    # DensityAwareMemoryTracker.reset_tracker() resets track_id to 0 for
    # EVERY play (tracking_stage.py runs once per play_index) - without
    # play_index in this table's identity, two different plays' track 0
    # would collide on (video_id, track_id) and overwrite each other's
    # summary row. Only one column (play_index) is nullable here (track_id
    # is always a real int in this table), so the existing single-nullable-
    # column partial-index pattern applies directly - see
    # uq_pose_video_frame_null_track above.
    UniqueConstraint("video_id", "track_id", "play_index", name="uq_tracks_meta_video_track_play"),
    Index(
        "uq_tracks_meta_video_track_null_play",
        "video_id", "track_id",
        unique=True,
        postgresql_where=_sql_text("play_index IS NULL"),
    ),
    Index("ix_tracks_meta_video_play", "video_id", "play_index"),
)

plays = Table(
    "plays",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("video_id", String, ForeignKey("videos.video_id")),
    Column("play_index", Integer, nullable=False),
    Column("start_frame", Integer, nullable=False),
    Column("end_frame", Integer, nullable=False),
    Column("status", String),
    # e.g. "ocr+camera_motion" (real signal) or "fallback_whole_video" (both
    # signals found nothing - one play spanning the whole video, so the
    # video still makes forward progress instead of silently stalling).
    Column("detection_method", String),
    # Left NULL - no per-candidate confidence score exists in the validated
    # signal yet, not invented from nothing.
    Column("confidence", Float, nullable=True),
    # Set once play_clip_export (run inline from play_detection_stage, right
    # after this row is written) successfully cuts + uploads a standalone
    # clip for this play's frame range to S3 at ingest.s3_client.play_clip_key().
    # Best-effort: a cut failure just leaves this False permanently for the
    # play, not retried - the watch page falls back to seeking within the
    # full video for that one play, never a hard failure.
    Column("clip_ready", Boolean, nullable=False, server_default=_sql_text("false")),
    Column("created_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("video_id", "play_index", name="uq_plays_video_play_index"),
    Index("ix_plays_video_id", "video_id"),
)
