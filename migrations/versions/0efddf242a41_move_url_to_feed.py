"""move url to feed

Revision ID: 0efddf242a41
Revises: ffc9581f37be
Create Date: 2023-09-14 11:38:01.905173

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0efddf242a41'
down_revision: Union[str, None] = 'ffc9581f37be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE feeds SET url = server_url WHERE type = 'mastodon'")
    op.drop_column('feeds', 'server_url')


def downgrade() -> None:
    op.add_column('feeds', sa.Column('server_url', sa.String(), nullable=True))
