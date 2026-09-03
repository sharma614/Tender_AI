"""add agent observability columns, chunk provenance, and a vector index

Motivated by two gaps:

1. /admin/metrics was documented as returning agent latency, success rates and
   token counts, but `tool_calls` had no columns to aggregate — the endpoint
   could only count rows.
2. Retrieved chunks could not be cited back to a location in the source PDF,
   because chunks stored no character offset or page number.

Also adds an approximate-nearest-neighbour index on the embedding column. The
prior schema had no vector index at all, so every similarity search was a
sequential scan.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_tender_chunks_embedding_cosine"


def _pgvector_version() -> tuple:
    """Installed pgvector version as a comparable tuple, e.g. (0, 8, 1)."""
    raw = op.get_bind().execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if not raw:
        return (0, 0, 0)
    parts = []
    for piece in str(raw).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def upgrade() -> None:
    # --- 1. Observability columns on tool_calls ---------------------------
    op.add_column("tool_calls", sa.Column("model_name", sa.String(), nullable=True))
    op.add_column("tool_calls", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("tool_calls", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("tool_calls", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("tool_calls", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column("status", sa.String(), nullable=True, server_default="success"),
    )
    op.add_column("tool_calls", sa.Column("error_message", sa.Text(), nullable=True))
    op.create_index(op.f("ix_tool_calls_status"), "tool_calls", ["status"])

    # --- 2. Chunk provenance ---------------------------------------------
    op.add_column("tender_chunks", sa.Column("char_start", sa.Integer(), nullable=True))
    op.add_column("tender_chunks", sa.Column("page_number", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_tender_chunks_page_number"), "tender_chunks", ["page_number"])

    # --- 3. ANN index for cosine similarity search ------------------------
    # HNSW needs pgvector >= 0.5.0; fall back to IVFFlat on older servers rather
    # than failing the migration or silently leaving the table unindexed.
    if _pgvector_version() >= (0, 5, 0):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON tender_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    else:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON tender_chunks "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")

    op.drop_index(op.f("ix_tender_chunks_page_number"), table_name="tender_chunks")
    op.drop_column("tender_chunks", "page_number")
    op.drop_column("tender_chunks", "char_start")

    op.drop_index(op.f("ix_tool_calls_status"), table_name="tool_calls")
    for column in (
        "error_message",
        "status",
        "total_tokens",
        "completion_tokens",
        "prompt_tokens",
        "latency_ms",
        "model_name",
    ):
        op.drop_column("tool_calls", column)
