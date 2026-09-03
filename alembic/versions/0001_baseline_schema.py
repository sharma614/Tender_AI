"""baseline schema as created by the pre-Alembic create_all() bootstrap

This revision reproduces the four tables exactly as app/models.py defined them
before observability and provenance columns were introduced.

- Fresh database:    `alembic upgrade head` runs this, then 0002.
- Existing database already created by the old `Base.metadata.create_all()`:
  run `alembic stamp 0001` first, then `alembic upgrade head` to apply only 0002.

Revision ID: 0001
Revises:
Create Date: 2026-09-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector must exist before any Vector column is created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.String(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("risks", sa.JSON(), nullable=True),
        sa.Column("compliance_status", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tenders_id"), "tenders", ["id"])
    op.create_index(op.f("ix_tenders_title"), "tenders", ["title"])
    op.create_index(op.f("ix_tenders_s3_key"), "tenders", ["s3_key"])

    op.create_table(
        "tender_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tender_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tender_chunks_id"), "tender_chunks", ["id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tender_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("input_arguments", sa.JSON(), nullable=True),
        sa.Column("output_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_calls_id"), "tool_calls", ["id"])
    op.create_index(op.f("ix_tool_calls_agent_name"), "tool_calls", ["agent_name"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tender_id", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_id"), "jobs", ["id"])
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"])
    op.create_index(op.f("ix_jobs_celery_task_id"), "jobs", ["celery_task_id"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("tool_calls")
    op.drop_table("tender_chunks")
    op.drop_table("tenders")
