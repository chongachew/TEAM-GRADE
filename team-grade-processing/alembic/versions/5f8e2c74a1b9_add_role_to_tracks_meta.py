"""add role to tracks_meta

Detection-clutter follow-up: referees/staff standing on the field are real
"person" detections and get tracked like anyone else - a UI-side fix alone
can't distinguish them from players, since spatial filtering (field-boundary,
see the plays.clip_ready-era migrations) only separates on-field from
off-field, and referees are genuinely on the field. role_classification_stage
(new pipeline stage, runs between torso_crop and jersey_ocr) classifies each
track's torso crop via processing/uniform_classifier.py (referee uniforms are
a distinctive black/white stripe, different from either team's colors) and
writes the result here - directly parallel to the existing jersey_number/
jersey_confidence pair on this same table.

Revision ID: 5f8e2c74a1b9
Revises: 4730b85e0236
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f8e2c74a1b9'
down_revision: Union[str, Sequence[str], None] = '4730b85e0236'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tracks_meta", sa.Column("role", sa.String(), nullable=True))
    op.add_column("tracks_meta", sa.Column("role_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tracks_meta", "role_confidence")
    op.drop_column("tracks_meta", "role")
