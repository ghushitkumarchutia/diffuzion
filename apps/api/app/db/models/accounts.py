from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppAccount(Base):
    __tablename__ = "app_accounts"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="role"),
        CheckConstraint("status IN ('active','suspended')", name="status"),
        CheckConstraint(
            "stored_bytes >= 0 AND reserved_upload_bytes >= 0", name="storage_counters"
        ),
    )
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("auth.user.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), server_default="user")
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stored_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    reserved_upload_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    __table_args__ = (
        CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="reason"),
        Index("ix_admin_audit_actor_created", "actor_user_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor_user_id: Mapped[str] = mapped_column(Text, ForeignKey("app.app_accounts.user_id"))
    action: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(String(500))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
