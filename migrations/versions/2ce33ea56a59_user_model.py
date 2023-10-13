"""user model

Revision ID: 2ce33ea56a59
Revises: b91a4a54b490
Create Date: 2023-10-12 15:29:33.722866

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from werkzeug.security import generate_password_hash

# revision identifiers, used by Alembic.
revision: str = '2ce33ea56a59'
down_revision: Union[str, None] = 'b91a4a54b490'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table('users',
                            sa.Column('id', sa.Integer(), nullable=False),
                            sa.Column('email', sa.String(length=100), nullable=False),
                            sa.Column('password', sa.String(length=100), nullable=False),
                            sa.PrimaryKeyConstraint('id'),
                            sa.UniqueConstraint('email')
                            )

    op.bulk_insert(
        table,
        [{"id": 1,
          "email": "admin@admin.com",
          "password": generate_password_hash("admin")}])


def downgrade() -> None:
    op.drop_table('users')
