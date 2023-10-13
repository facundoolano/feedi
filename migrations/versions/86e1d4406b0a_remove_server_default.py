"""remove server default

Revision ID: 86e1d4406b0a
Revises: a91a92bec920
Create Date: 2023-10-12 16:10:48.729564

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '86e1d4406b0a'
down_revision: Union[str, None] = 'a91a92bec920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('feeds', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              server_default=None,
                              nullable=True)


def downgrade() -> None:
    pass
