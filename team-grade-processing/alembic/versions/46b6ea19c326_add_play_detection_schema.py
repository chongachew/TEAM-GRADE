"""add play detection schema

Phase B of the per-play pipeline redesign: a new play_detection stage
(between motion_compensation and detection) persists per-play frame ranges
to a new `plays` table and enqueues one `detection` job per play, tagged
with play_index, instead of one job for the whole video. See
C:\\Users\\ricky\\.claude\\plans\\adaptive-finding-planet.md and
quirky-waddling-octopus.md.

The video_stages constraint change needs special care: Postgres never
treats two NULLs as conflicting under a multi-column UNIQUE constraint, so
adding play_index to uq_video_stages_video_stage would silently start
inserting duplicate rows for every pre-existing (play_index IS NULL) video
instead of updating in place. Fixed with the same partial-unique-index
pattern this codebase already uses for pose/torso_crops/reps/rep_analysis's
nullable track_id (uq_pose_video_frame_null_track and siblings).

Revision ID: 46b6ea19c326
Revises: 9f3a7c21b6e2
Create Date: 2026-07-25 15:52:39.041636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46b6ea19c326'
down_revision: Union[str, Sequence[str], None] = '9f3a7c21b6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that just get a plain nullable, indexed play_index filter column -
# no unique-key change, since a row in each of these is still uniquely
# identified by its existing key regardless of which play it belongs to.
_PLAY_INDEX_TABLES = [
    "ingestion_queue", "frames", "detections", "tracks",
    "tracks_meta", "pose", "torso_crops",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "plays",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(), sa.ForeignKey("videos.video_id")),
        sa.Column("play_index", sa.Integer(), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("end_frame", sa.Integer(), nullable=False),
        sa.Column("status", sa.String()),
        sa.Column("detection_method", sa.String()),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("video_id", "play_index", name="uq_plays_video_play_index"),
    )
    op.create_index("ix_plays_video_id", "plays", ["video_id"])

    for table in _PLAY_INDEX_TABLES:
        op.add_column(table, sa.Column("play_index", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_video_play", table, ["video_id", "play_index"])

    # video_stages: play_index column + the NULL-safe uniqueness fix.
    op.add_column("video_stages", sa.Column("play_index", sa.Integer(), nullable=True))
    op.drop_constraint("uq_video_stages_video_stage", "video_stages", type_="unique")
    op.create_unique_constraint(
        "uq_video_stages_video_stage_play", "video_stages",
        ["video_id", "stage_name", "play_index"],
    )
    op.create_index(
        "uq_video_stages_video_stage_null_play", "video_stages",
        ["video_id", "stage_name"],
        unique=True,
        postgresql_where=sa.text("play_index IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_video_stages_video_stage_null_play", table_name="video_stages")
    op.drop_constraint("uq_video_stages_video_stage_play", "video_stages", type_="unique")
    op.create_unique_constraint(
        "uq_video_stages_video_stage", "video_stages", ["video_id", "stage_name"],
    )
    op.drop_column("video_stages", "play_index")

    for table in _PLAY_INDEX_TABLES:
        op.drop_index(f"ix_{table}_video_play", table_name=table)
        op.drop_column(table, "play_index")

    op.drop_index("ix_plays_video_id", table_name="plays")
    op.drop_table("plays")
