"""feed.user_id

Revision ID: a91a92bec920
Revises: 2ce33ea56a59
Create Date: 2023-10-12 16:00:39.223953

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a91a92bec920'
down_revision: Union[str, None] = '2ce33ea56a59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('feeds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_feeds_user_id'), ['user_id'], unique=False)
        batch_op.create_foreign_key('user_id_fk', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('feeds', schema=None) as batch_op:
        batch_op.drop_constraint('user_id_fk', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_feeds_user_id'))
        batch_op.drop_column('user_id')
