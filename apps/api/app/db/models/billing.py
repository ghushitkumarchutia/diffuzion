from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_purchases_owner_idempotency"),
        CheckConstraint("plan = 'pro' AND amount_paise > 0 AND currency = 'INR'", name="catalogue"),
        CheckConstraint("mode IN ('test','live')", name="mode"),
        CheckConstraint(
            (
                "state IN "
                "('pending','creation_unknown','active','rejected','refunded','revoked','expired')"
            ),
            name="state",
        ),
        Index("ix_purchases_owner_created", "owner_user_id", "created_at"),
        Index(
            "uq_purchases_pending_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=text("state IN ('pending','creation_unknown')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[str] = mapped_column(Text, ForeignKey("app.app_accounts.user_id"))
    plan: Mapped[str] = mapped_column(String(10), server_default="pro")
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    mode: Mapped[str] = mapped_column(String(4))
    order_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    payment_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuotaPeriod(Base):
    __tablename__ = "quota_periods"
    __table_args__ = (
        CheckConstraint("kind IN ('free','pro')", name="kind"),
        CheckConstraint("ends_at > starts_at", name="dates"),
        CheckConstraint(
            '"limit" > 0 AND reserved >= 0 AND consumed >= 0 AND reserved + consumed <= "limit"',
            name="counters",
        ),
        CheckConstraint("(kind = 'pro') = (purchase_id IS NOT NULL)", name="purchase_kind"),
        CheckConstraint(
            (
                "kind != 'free' OR (starts_at = date_trunc('month', starts_at AT TIME "
                "ZONE 'UTC') AT TIME ZONE 'UTC' AND ends_at = (date_trunc('month', "
                "starts_at AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE "
                "'UTC')"
            ),
            name="free_utc_month",
        ),
        Index(
            "uq_quota_periods_free_owner_month",
            "owner_user_id",
            "starts_at",
            unique=True,
            postgresql_where=text("kind = 'free'"),
        ),
        Index("ix_quota_periods_owner_dates", "owner_user_id", "starts_at", "ends_at"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[str] = mapped_column(Text, ForeignKey("app.app_accounts.user_id"))
    kind: Mapped[str] = mapped_column(String(4))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    limit: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, server_default="0")
    consumed: Mapped[int] = mapped_column(Integer, server_default="0")
    purchase_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.purchases.id"), unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("mode", "event_id", name="uq_webhook_events_mode_event"),
        CheckConstraint("mode IN ('test','live')", name="mode"),
        CheckConstraint("state IN ('pending','processing','succeeded','failed')", name="state"),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_webhook_events_retry", "state", "next_retry_at"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    mode: Mapped[str] = mapped_column(String(4))
    event_id: Mapped[str] = mapped_column(String(100))
    body_hash: Mapped[str] = mapped_column(String(64))
    verified_fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(10), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AIBudgetMonth(Base):
    __tablename__ = "ai_budget_months"
    __table_args__ = (
        CheckConstraint(
            "month = date_trunc('month', month AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
            name="utc_month",
        ),
        CheckConstraint(
            (
                "cap_usd > 0 AND reserved_usd >= 0 AND charged_usd >= 0 AND "
                "reserved_usd + charged_usd <= cap_usd"
            ),
            name="counters",
        ),
    )
    month: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cap_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
    charged_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
