"""rename s3_key to storage_path (Supabase Storage migration)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename the column (works on PostgreSQL; SQLite requires table rebuild
    # but CI uses SQLite only for schema-free unit tests, not migrations).
    with op.batch_alter_table("tenders") as batch_op:
        batch_op.alter_column("s3_key", new_column_name="storage_path")


def downgrade() -> None:
    with op.batch_alter_table("tenders") as batch_op:
        batch_op.alter_column("storage_path", new_column_name="s3_key")
