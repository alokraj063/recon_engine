"""match_rule_sets.zone_directory: the customer's zone -> segment directory

Purely additive, nullable: NULL means db/zones.DEFAULT_ZONE_DIRECTORY, so
no backfill. Display-only reference data (the Analyst queue's Segment
column); the matcher never reads it.

Revision ID: f3b8d1a6c4e2
Revises: d8a4f2c6e1b9
Create Date: 2026-09-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3b8d1a6c4e2'
down_revision: Union[str, None] = 'd8a4f2c6e1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.add_column(sa.Column(
            'zone_directory',
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()),
                                   'postgresql'),
            nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.drop_column('zone_directory')
