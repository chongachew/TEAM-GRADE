"""initial postgres schema

Creates the full Postgres schema that replaces Firestore for TEAM-GRADE's
structured data (Pass 2a of the data-layer migration): videos, video_stages,
ingestion_queue, and one table per former Firestore subcollection (frames,
pose, torso_crops, reps, rep_analysis, camera_motion, whistle_events,
detections, tracks, tracks_meta).

Table definitions live in ingest/db_schema.py (single source of truth, also
used at runtime by PostgresClient/firestore_utils.py/QueueManager) - this
migration just applies them via metadata.create_all/drop_all rather than
duplicating column definitions with op.create_table calls.

Revision ID: 2c59f6a20536
Revises:
Create Date: 2026-07-15 16:53:16.831255

"""
import sys
from pathlib import Path
from typing import Sequence, Union

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ingest.db_schema import metadata as target_metadata  # noqa: E402


# revision identifiers, used by Alembic.
revision: str = '2c59f6a20536'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create every table defined in ingest/db_schema.py."""
    bind = op.get_bind()
    target_metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop every table defined in ingest/db_schema.py."""
    bind = op.get_bind()
    target_metadata.drop_all(bind=bind)
