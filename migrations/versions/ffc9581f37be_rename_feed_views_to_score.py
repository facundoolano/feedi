"""rename feed views to score

Revision ID: ffc9581f37be
Revises: 72ac46dfbe54
Create Date: 2023-08-24 19:28:42.445498

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ffc9581f37be'
down_revision: Union[str, None] = '72ac46dfbe54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('feeds', 'views', new_column_name='score')


def downgrade() -> None:
    op.alter_column('feeds', 'score', new_column_name='views')
