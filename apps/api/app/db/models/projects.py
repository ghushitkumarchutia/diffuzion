from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('active','deleting')", name="status"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name"),
        Index("ix_projects_owner_user_id", "owner_user_id"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[str] = mapped_column(Text, ForeignKey("app.app_accounts.user_id"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, server_default="")
    api_base_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_files_project_hash"),
        CheckConstraint("kind IN ('spec','code','note')", name="kind"),
        CheckConstraint(
            "language IS NULL OR language IN ('typescript','javascript','python')", name="language"
        ),
        CheckConstraint("(kind = 'code') = (language IS NOT NULL)", name="code_language"),
        CheckConstraint("state IN ('uploading','stored','deleting')", name="state"),
        CheckConstraint("size_bytes > 0 AND upload_reserved_bytes >= 0", name="sizes"),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'", name="sha256"),
        CheckConstraint(
            "state != 'stored' OR (sha256 IS NOT NULL AND storage_key IS NOT NULL)",
            name="stored_input",
        ),
        CheckConstraint(
            (
                "(state = 'uploading' AND upload_reserved_bytes > 0 AND "
                "upload_started_at IS NOT NULL AND upload_expires_at IS NOT NULL "
                "AND upload_expires_at > "
                "upload_started_at) OR (state != 'uploading' AND upload_reserved_bytes "
                "= 0 AND upload_started_at IS NULL AND upload_expires_at IS NULL)"
            ),
            name="upload_reservation",
        ),
        Index("ix_files_project_id", "project_id"),
        Index(
            "ix_files_upload_expiry",
            "upload_expires_at",
            postgresql_where=text("state = 'uploading'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.projects.id", ondelete="CASCADE"))
    original_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))
    language: Mapped[str | None] = mapped_column(String(20))
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(512), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(10), server_default="uploading")
    parse_cache_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parser_version: Mapped[str | None] = mapped_column(String(100))
    upload_reserved_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    upload_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (Index("ix_revisions_project_created", "project_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.projects.id", ondelete="CASCADE"))
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.revisions.id", ondelete="SET NULL")
    )
    api_base_url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RevisionFile(Base):
    __tablename__ = "revision_files"
    __table_args__ = (
        CheckConstraint("role IN ('old_spec','new_spec','code','note')", name="role"),
        Index(
            "uq_revision_files_spec_slot",
            "revision_id",
            "role",
            unique=True,
            postgresql_where=text("role IN ('old_spec','new_spec')"),
        ),
        Index("ix_revision_files_file_id", "file_id"),
    )
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.revisions.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.files.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(10))
