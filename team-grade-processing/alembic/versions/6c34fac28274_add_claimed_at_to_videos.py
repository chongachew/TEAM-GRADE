"""add claimed_at to videos

Adds videos.claimed_at (nullable timestamptz), set by the new
POST /api/videos/{video_id}/mark-claimed endpoint right after Bridge
Athletics successfully claims a video's stats or a highlight reel. The
retention sweep (ingest/retention.py) only purges videos where this is
still NULL past the retention window.

Revision ID: 6c34fac28274
Revises: 2c59f6a20536
Create Date: 2026-07-20

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '6c34fac28274'
down_revision = '2c59f6a20536'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('videos', sa.Column('claimed_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('videos', 'claimed_at')
