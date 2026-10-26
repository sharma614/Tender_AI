"""protect Alembic's public migration bookkeeping table

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This is internal migration metadata; application clients should not read
    # or modify it. The table owner used by Alembic retains access.
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.alembic_version "
        "FROM anon, authenticated, PUBLIC"
    )


def downgrade() -> None:
    # Do not recreate grants: their pre-migration state is environment-specific.
    op.execute("ALTER TABLE public.alembic_version DISABLE ROW LEVEL SECURITY")
