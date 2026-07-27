"""add overall_grade/letter_grade to videos

Phase D of the per-play redesign: complete_stage.py now computes a
video-level grade aggregate (mean of every rep_analysis row's
overall_grade across every play, letter grade via the same
VectorizedTraitScorer._numeric_to_letter() already used per-rep) and
writes it here once the video is fully complete. NULL until then.

Revision ID: 9c1a5e7f2d68
Revises: 2a6e4f0d9b31
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c1a5e7f2d68'
down_revision: Union[str, Sequence[str], None] = '2a6e4f0d9b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("videos", sa.Column("overall_grade", sa.Float(), nullable=True))
    op.add_column("videos", sa.Column("letter_grade", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("videos", "letter_grade")
    op.drop_column("videos", "overall_grade")
