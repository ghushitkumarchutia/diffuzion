from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NoteChunk(Base):
    __tablename__ = "note_chunks"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "chunker_version",
            "embedding_model",
            "embedding_version",
            "embedding_task_format",
            "chunk_index",
            name="uq_note_chunks_file_config_index",
        ),
        CheckConstraint(
            (
                "chunk_index >= 0 AND byte_start >= 0 AND byte_end > byte_start AND "
                "line_start > 0 AND line_end >= line_start AND character_start >= 0 AND"
                " character_end > character_start"
            ),
            name="spans",
        ),
        Index("ix_note_chunks_file_id", "file_id"),
        Index("ix_note_chunks_lexical", "lexical", postgresql_using="gin"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    file_id: Mapped[UUID] = mapped_column(ForeignKey("app.files.id", ondelete="CASCADE"))
    byte_start: Mapped[int] = mapped_column(Integer)
    byte_end: Mapped[int] = mapped_column(Integer)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    character_start: Mapped[int] = mapped_column(Integer)
    character_end: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(100))
    embedding_task_format: Mapped[str] = mapped_column(String(100))
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    lexical: Mapped[str] = mapped_column(TSVECTOR)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "kind", "idempotency_key", name="uq_runs_owner_kind_idempotency"
        ),
        CheckConstraint("kind IN ('preview','guidance')", name="kind"),
        CheckConstraint("state IN ('queued','running','succeeded','failed')", name="state"),
        CheckConstraint("attempts BETWEEN 0 AND 2", name="attempts"),
        CheckConstraint(
            "quota_state IS NULL OR quota_state IN ('reserved','consumed','released')",
            name="quota_state",
        ),
        CheckConstraint(
            (
                "(kind = 'preview' AND quota_period_id IS NULL AND quota_state IS NULL "
                "AND preview_run_id IS NULL) OR (kind = 'guidance' AND quota_period_id "
                "IS NOT NULL AND quota_state IS NOT NULL AND preview_run_id IS NOT "
                "NULL)"
            ),
            name="quota_kind",
        ),
        CheckConstraint(
            (
                "ai_reserved_usd >= 0 AND ai_cost_usd >= 0 AND input_tokens >= 0 AND "
                "output_tokens >= 0 AND embedding_tokens >= 0 AND uncertain_cost_usd >="
                " 0"
            ),
            name="usage",
        ),
        CheckConstraint(
            (
                "(state = 'running' AND lease_token IS NOT NULL AND lease_until IS NOT "
                "NULL AND heartbeat_at IS NOT NULL) OR (state != 'running' AND "
                "lease_token IS NULL AND lease_until IS NULL)"
            ),
            name="lease",
        ),
        Index(
            "uq_runs_active_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=text("state IN ('queued','running')"),
        ),
        Index("ix_runs_fifo", "state", "created_at"),
        Index("ix_runs_owner_created", "owner_user_id", "created_at"),
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index(
            "ix_runs_fingerprint",
            "owner_user_id",
            "project_id",
            "fingerprint",
            postgresql_where=text("state = 'succeeded'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[str] = mapped_column(Text, ForeignKey("app.app_accounts.user_id"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.projects.id", ondelete="CASCADE"))
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("app.revisions.id", ondelete="CASCADE"))
    preview_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.runs.id"))
    kind: Mapped[str] = mapped_column(String(10))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    scope_json: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    mappings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pipeline_versions_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(10), server_default="queued")
    stage: Mapped[str] = mapped_column(String(100), server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checkpoint_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quota_period_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.quota_periods.id"))
    quota_state: Mapped[str | None] = mapped_column(String(10))
    ai_budget_month: Mapped[datetime | None] = mapped_column(
        ForeignKey("app.ai_budget_months.month")
    )
    ai_reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
    ai_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
    uncertain_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    embedding_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("decision IN ('accepted','edited','rejected')", name="decision"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            (
                "(decision = 'edited' AND edited_instruction IS NOT NULL AND "
                "char_length(edited_instruction) BETWEEN 1 AND 4000) OR (decision != "
                "'edited' AND edited_instruction IS NULL AND edited_evidence_ids IS "
                "NULL)"
            ),
            name="edited_content",
        ),
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.runs.id", ondelete="CASCADE"), primary_key=True
    )
    recommendation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    decision: Mapped[str] = mapped_column(String(10))
    edited_instruction: Mapped[str | None] = mapped_column(Text)
    edited_evidence_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
