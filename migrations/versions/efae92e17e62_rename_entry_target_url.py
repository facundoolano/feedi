"""rename Entry.target_url

Revision ID: efae92e17e62
Revises: af19df375216
Create Date: 2024-01-02 11:49:05.227712

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'efae92e17e62'
down_revision: Union[str, None] = 'af19df375216'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('entry_url', new_column_name='target_url')


def downgrade() -> None:
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('target_url', new_column_name='entry_url')
