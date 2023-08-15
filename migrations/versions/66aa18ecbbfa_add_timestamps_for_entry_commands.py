"""add timestamps for entry commands

Revision ID: 66aa18ecbbfa
Revises: 0f2e07db0a84
Create Date: 2023-08-15 13:11:31.513112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66aa18ecbbfa'
down_revision: Union[str, None] = '0f2e07db0a84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('entries', sa.Column('deleted', sa.TIMESTAMP(), nullable=True))
    op.add_column('entries', sa.Column('favorited', sa.TIMESTAMP(), nullable=True))
    op.add_column('entries', sa.Column('pinned', sa.TIMESTAMP(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('entries', 'pinned')
    op.drop_column('entries', 'favorited')
    op.drop_column('entries', 'deleted')
    # ### end Alembic commands ###
