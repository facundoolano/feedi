"""not nullable feed url

Revision ID: b975c1a56ab3
Revises: 0efddf242a41
Create Date: 2023-09-14 11:57:35.538878

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b975c1a56ab3'
down_revision: Union[str, None] = '0efddf242a41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('feeds') as batch_op:
        batch_op.alter_column('url',
                              existing_type=sa.String(),
                              nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('feeds') as batch_op:
        batch_op.alter_column('url',
                              existing_type=sa.String(),
                              nullable=True)
