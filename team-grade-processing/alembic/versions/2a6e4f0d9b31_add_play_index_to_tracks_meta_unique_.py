"""add play_index to tracks_meta unique key

Phase C of the per-play pipeline redesign: tracking_stage.py now runs once
per play, and DensityAwareMemoryTracker.reset_tracker() resets track_id to 0
on every run. Without play_index in tracks_meta's identity, two different
plays' track 0 would collide on the existing (video_id, track_id) uniqueness
and silently overwrite each other's per-track summary row - discovered while
threading play_index through tracking_stage.py, not flagged in the original
per-play redesign plan.

track_id is never nullable in this table (only real, multi-player-mode
tracks get a tracks_meta row) - only play_index is - so this reuses the
existing single-nullable-column partial-index pattern
(uq_pose_video_frame_null_track and siblings) directly, unlike reps/
rep_analysis's two-independently-nullable-columns COALESCE approach in the
previous migration.

Revision ID: 2a6e4f0d9b31
Revises: 7d3f9c2b1a44
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a6e4f0d9b31'
down_revision: Union[str, Sequence[str], None] = '7d3f9c2b1a44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("uq_tracks_meta_video_track", "tracks_meta", type_="unique")
    op.create_unique_constraint(
        "uq_tracks_meta_video_track_play", "tracks_meta", ["video_id", "track_id", "play_index"],
    )
    op.create_index(
        "uq_tracks_meta_video_track_null_play", "tracks_meta",
        ["video_id", "track_id"], unique=True,
        postgresql_where=sa.text("play_index IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_tracks_meta_video_track_null_play", table_name="tracks_meta")
    op.drop_constraint("uq_tracks_meta_video_track_play", "tracks_meta", type_="unique")
    op.create_unique_constraint(
        "uq_tracks_meta_video_track", "tracks_meta", ["video_id", "track_id"],
    )
