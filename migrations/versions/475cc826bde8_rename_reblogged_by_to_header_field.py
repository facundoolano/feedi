"""rename reblogged_by to header field

Revision ID: 475cc826bde8
Revises: 80dc4b94cdf0
Create Date: 2023-09-24 13:53:13.314243

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '475cc826bde8'
down_revision: Union[str, None] = '80dc4b94cdf0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('reblogged_by', new_column_name='header')


def downgrade() -> None:
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('header', new_column_name='reblogged_by')
