"""add play_index to reps and rep_analysis

Phase C of the per-play pipeline redesign: rep_extraction now runs once per
play (previously once per whole video), and segment_reps_from_pose_docs()
assigns rep_index starting at 0 on every call. Without play_index in reps'/
rep_analysis' identity tuple, two different plays' rep_index=0 rows would
collide on the existing (video_id, rep_index, track_id) uniqueness and
silently overwrite each other instead of coexisting - not caught by Phase B's
"scope correction" note, which only covered frames/pose/torso_crops/
detections/tracks/tracks_meta.

track_id and play_index are now BOTH independently nullable in the same
identity tuple. Rather than enumerating the 4 partial-index combinations the
existing single-nullable-column pattern uses (uq_pose_video_frame_null_track
and siblings), this replaces reps'/rep_analysis' uniqueness with a single
COALESCE-based expression unique index - NULL is treated as an ordinary
sentinel value (-1, an otherwise-impossible track_id/play_index) so ON
CONFLICT can target one index regardless of which columns are NULL. See
ingest/postgres_client.py's new upsert_row_coalesce_keys().

Revision ID: 7d3f9c2b1a44
Revises: 46b6ea19c326
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d3f9c2b1a44'
down_revision: Union[str, Sequence[str], None] = '46b6ea19c326'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["reps", "rep_analysis"]


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        op.add_column(table, sa.Column("play_index", sa.Integer(), nullable=True))

    op.drop_constraint("uq_reps_video_rep_track", "reps", type_="unique")
    op.drop_index("uq_reps_video_rep_null_track", table_name="reps")
    op.create_index(
        "uq_reps_video_rep_track_play", "reps",
        ["video_id", "rep_index", sa.text("COALESCE(track_id, -1)"), sa.text("COALESCE(play_index, -1)")],
        unique=True,
    )

    op.drop_constraint("uq_rep_analysis_video_rep_track", "rep_analysis", type_="unique")
    op.drop_index("uq_rep_analysis_video_rep_null_track", table_name="rep_analysis")
    op.create_index(
        "uq_rep_analysis_video_rep_track_play", "rep_analysis",
        ["video_id", "rep_index", sa.text("COALESCE(track_id, -1)"), sa.text("COALESCE(play_index, -1)")],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_rep_analysis_video_rep_track_play", table_name="rep_analysis")
    op.create_index(
        "uq_rep_analysis_video_rep_null_track", "rep_analysis",
        ["video_id", "rep_index"], unique=True,
        postgresql_where=sa.text("track_id IS NULL"),
    )
    op.create_unique_constraint(
        "uq_rep_analysis_video_rep_track", "rep_analysis", ["video_id", "rep_index", "track_id"],
    )

    op.drop_index("uq_reps_video_rep_track_play", table_name="reps")
    op.create_index(
        "uq_reps_video_rep_null_track", "reps",
        ["video_id", "rep_index"], unique=True,
        postgresql_where=sa.text("track_id IS NULL"),
    )
    op.create_unique_constraint(
        "uq_reps_video_rep_track", "reps", ["video_id", "rep_index", "track_id"],
    )

    for table in _TABLES:
        op.drop_column(table, "play_index")
