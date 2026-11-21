"""add user_id to categories (personal and global categories)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL — глобальна категорія, інакше — персональна
    op.add_column("categories", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_categories_user_id_users",
        "categories",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_categories_user_id", table_name="categories")
    op.drop_constraint("fk_categories_user_id_users", "categories", type_="foreignkey")
    op.drop_column("categories", "user_id")
