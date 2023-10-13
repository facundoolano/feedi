"""feed name + user unique

Revision ID: 2ab46a53ab1b
Revises: 86e1d4406b0a
Create Date: 2023-10-12 16:52:18.970047

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2ab46a53ab1b'
down_revision: Union[str, None] = '86e1d4406b0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('feeds', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)
        batch_op.drop_index('ix_feeds_name')
        batch_op.create_index('ix_name_user', ['user_id', 'name'], unique=False)
        batch_op.create_unique_constraint('feed_name_user_unique', ['user_id', 'name'])


def downgrade() -> None:
    with op.batch_alter_table('feeds', schema=None) as batch_op:
        batch_op.drop_constraint('feed_name_user_unique', type_='unique')
        batch_op.drop_index('ix_name_user')
        batch_op.create_index('ix_feeds_name', ['name'], unique=False)
        batch_op.alter_column('user_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)
