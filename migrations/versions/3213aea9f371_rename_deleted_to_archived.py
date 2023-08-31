"""rename deleted to archived

Revision ID: 3213aea9f371
Revises: ffc9581f37be
Create Date: 2023-08-31 20:04:38.861409

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3213aea9f371'
down_revision: Union[str, None] = 'ffc9581f37be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('entries', 'deleted', new_column_name='archived')


def downgrade() -> None:
    op.alter_column('entries', 'archived', new_column_name='deleted')
