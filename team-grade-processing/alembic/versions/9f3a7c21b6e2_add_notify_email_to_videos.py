"""add notify_email to videos

Adds videos.notify_email (nullable text), set by the new
POST /api/videos/{video_id}/notify-email endpoint - a visitor waiting on a
free-tool submission can leave an email to be notified once processing
finishes, instead of babysitting the progress bar. Read by complete_stage.py
once the pipeline actually finishes, to fire a one-off notification.

Revision ID: 9f3a7c21b6e2
Revises: 6c34fac28274
Create Date: 2026-07-21

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '9f3a7c21b6e2'
down_revision = '6c34fac28274'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('videos', sa.Column('notify_email', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('videos', 'notify_email')
