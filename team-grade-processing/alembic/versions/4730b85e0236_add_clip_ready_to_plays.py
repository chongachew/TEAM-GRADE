"""add clip_ready to plays

Watch-dashboard redesign: the watch page now plays a standalone per-play
clip instead of the whole video, cut+uploaded to S3 inline from
play_detection_stage right after each play's row is written (best-effort,
not retried on failure - the frontend falls back to seeking within the full
video for any play where this stays False). The clip's S3 key is derived
deterministically from (video_id, play_index) via
ingest.s3_client.play_clip_key() - no URL/key needs to be stored, just
whether the upload succeeded.

Revision ID: 4730b85e0236
Revises: 9c1a5e7f2d68
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4730b85e0236'
down_revision: Union[str, Sequence[str], None] = '9c1a5e7f2d68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "plays",
        sa.Column("clip_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("plays", "clip_ready")
