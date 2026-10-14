from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_app"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_budget_months",
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cap_usd", sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column(
            "reserved_usd", sa.Numeric(precision=14, scale=8), server_default="0", nullable=False
        ),
        sa.Column(
            "charged_usd", sa.Numeric(precision=14, scale=8), server_default="0", nullable=False
        ),
        sa.CheckConstraint(
            "month = date_trunc('month', month AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
            name=op.f("ck_ai_budget_months_utc_month"),
        ),
        sa.CheckConstraint(
            (
                "cap_usd > 0 AND reserved_usd >= 0 AND charged_usd >= 0 AND "
                "reserved_usd + charged_usd <= cap_usd"
            ),
            name=op.f("ck_ai_budget_months_counters"),
        ),
        sa.PrimaryKeyConstraint("month", name=op.f("pk_ai_budget_months")),
        schema="app",
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("mode", sa.String(length=4), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("verified_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('test','live')", name=op.f("ck_webhook_events_mode")),
        sa.CheckConstraint(
            "state IN ('pending','processing','succeeded','failed')",
            name=op.f("ck_webhook_events_state"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_webhook_events_attempts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("mode", "event_id", name="uq_webhook_events_mode_event"),
        schema="app",
    )
    op.create_index(
        "ix_webhook_events_retry",
        "webhook_events",
        ["state", "next_retry_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "app_accounts",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=10), server_default="user", nullable=False),
        sa.Column("status", sa.String(length=10), server_default="active", nullable=False),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stored_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserved_upload_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user','admin')", name=op.f("ck_app_accounts_role")),
        sa.CheckConstraint("status IN ('active','suspended')", name=op.f("ck_app_accounts_status")),
        sa.CheckConstraint(
            "stored_bytes >= 0 AND reserved_upload_bytes >= 0",
            name=op.f("ck_app_accounts_storage_counters"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth.user.id"], name=op.f("fk_app_accounts_user_id_user")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_app_accounts")),
        schema="app",
    )
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=256), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 500", name=op.f("ck_admin_audit_reason")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app.app_accounts.user_id"],
            name=op.f("fk_admin_audit_actor_user_id_app_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit")),
        schema="app",
    )
    op.create_index(
        "ix_admin_audit_actor_created",
        "admin_audit",
        ["actor_user_id", "created_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("api_base_url", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active','deleting')", name=op.f("ck_projects_status")),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name=op.f("ck_projects_name")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app.app_accounts.user_id"],
            name=op.f("fk_projects_owner_user_id_app_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        schema="app",
    )
    op.create_index(
        "ix_projects_owner_user_id", "projects", ["owner_user_id"], unique=False, schema="app"
    )
    op.create_table(
        "purchases",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("plan", sa.String(length=10), server_default="pro", nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("mode", sa.String(length=4), nullable=False),
        sa.Column("order_id", sa.String(length=100), nullable=True),
        sa.Column("payment_id", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('test','live')", name=op.f("ck_purchases_mode")),
        sa.CheckConstraint(
            "plan = 'pro' AND amount_paise > 0 AND currency = 'INR'",
            name=op.f("ck_purchases_catalogue"),
        ),
        sa.CheckConstraint(
            (
                "state IN "
                "('pending','creation_unknown','active','rejected','refunded','revoked','expired')"
            ),
            name=op.f("ck_purchases_state"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app.app_accounts.user_id"],
            name=op.f("fk_purchases_owner_user_id_app_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchases")),
        sa.UniqueConstraint("order_id", name=op.f("uq_purchases_order_id")),
        sa.UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_purchases_owner_idempotency"
        ),
        sa.UniqueConstraint("payment_id", name=op.f("uq_purchases_payment_id")),
        schema="app",
    )
    op.create_index(
        "ix_purchases_owner_created",
        "purchases",
        ["owner_user_id", "created_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "uq_purchases_pending_owner",
        "purchases",
        ["owner_user_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("state IN ('pending','creation_unknown')"),
    )
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=10), server_default="uploading", nullable=False),
        sa.Column("parse_cache_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser_version", sa.String(length=100), nullable=True),
        sa.Column("upload_reserved_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("upload_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'code') = (language IS NOT NULL)", name=op.f("ck_files_code_language")
        ),
        sa.CheckConstraint(
            (
                "(state = 'uploading' AND upload_reserved_bytes > 0 AND "
                "upload_started_at IS NOT NULL AND upload_expires_at IS NOT NULL AND "
                "upload_expires_at > upload_started_at) OR (state != 'uploading' AND "
                "upload_reserved_bytes = 0 AND upload_started_at IS NULL AND "
                "upload_expires_at IS NULL)"
            ),
            name=op.f("ck_files_upload_reservation"),
        ),
        sa.CheckConstraint("kind IN ('spec','code','note')", name=op.f("ck_files_kind")),
        sa.CheckConstraint(
            "language IS NULL OR language IN ('typescript','javascript','python')",
            name=op.f("ck_files_language"),
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'", name=op.f("ck_files_sha256")
        ),
        sa.CheckConstraint(
            "state != 'stored' OR (sha256 IS NOT NULL AND storage_key IS NOT NULL)",
            name=op.f("ck_files_stored_input"),
        ),
        sa.CheckConstraint(
            "state IN ('uploading','stored','deleting')", name=op.f("ck_files_state")
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND upload_reserved_bytes >= 0", name=op.f("ck_files_sizes")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["app.projects.id"],
            name=op.f("fk_files_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.UniqueConstraint("project_id", "sha256", name="uq_files_project_hash"),
        sa.UniqueConstraint("storage_key", name=op.f("uq_files_storage_key")),
        schema="app",
    )
    op.create_index("ix_files_project_id", "files", ["project_id"], unique=False, schema="app")
    op.create_index(
        "ix_files_upload_expiry",
        "files",
        ["upload_expires_at"],
        unique=False,
        schema="app",
        postgresql_where=sa.text("state = 'uploading'"),
    )
    op.create_table(
        "quota_periods",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=4), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("purchase_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'pro') = (purchase_id IS NOT NULL)",
            name=op.f("ck_quota_periods_purchase_kind"),
        ),
        sa.CheckConstraint(
            (
                "kind != 'free' OR (starts_at = date_trunc('month', starts_at AT TIME "
                "ZONE 'UTC') AT TIME ZONE 'UTC' AND ends_at = (date_trunc('month', "
                "starts_at AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE "
                "'UTC')"
            ),
            name=op.f("ck_quota_periods_free_utc_month"),
        ),
        sa.CheckConstraint("kind IN ('free','pro')", name=op.f("ck_quota_periods_kind")),
        sa.CheckConstraint(
            '"limit" > 0 AND reserved >= 0 AND consumed >= 0 AND reserved + consumed <= "limit"',
            name=op.f("ck_quota_periods_counters"),
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_quota_periods_dates")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app.app_accounts.user_id"],
            name=op.f("fk_quota_periods_owner_user_id_app_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["app.purchases.id"],
            name=op.f("fk_quota_periods_purchase_id_purchases"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quota_periods")),
        sa.UniqueConstraint("purchase_id", name=op.f("uq_quota_periods_purchase_id")),
        schema="app",
    )
    op.create_index(
        "ix_quota_periods_owner_dates",
        "quota_periods",
        ["owner_user_id", "starts_at", "ends_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "uq_quota_periods_free_owner_month",
        "quota_periods",
        ["owner_user_id", "starts_at"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("kind = 'free'"),
    )
    op.create_table(
        "revisions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("parent_revision_id", sa.UUID(), nullable=True),
        sa.Column("api_base_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["app.revisions.id"],
            name=op.f("fk_revisions_parent_revision_id_revisions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["app.projects.id"],
            name=op.f("fk_revisions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revisions")),
        schema="app",
    )
    op.create_index(
        "ix_revisions_project_created",
        "revisions",
        ["project_id", "created_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "note_chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("byte_start", sa.Integer(), nullable=False),
        sa.Column("byte_end", sa.Integer(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("character_start", sa.Integer(), nullable=False),
        sa.Column("character_end", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunker_version", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("embedding_version", sa.String(length=100), nullable=False),
        sa.Column("embedding_task_format", sa.String(length=100), nullable=False),
        sa.Column("embedding", Vector(dim=768), nullable=False),
        sa.Column("lexical", postgresql.TSVECTOR(), nullable=False),
        sa.CheckConstraint(
            (
                "chunk_index >= 0 AND byte_start >= 0 AND byte_end > byte_start AND "
                "line_start > 0 AND line_end >= line_start AND character_start >= 0 AND"
                " character_end > character_start"
            ),
            name=op.f("ck_note_chunks_spans"),
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["app.files.id"],
            name=op.f("fk_note_chunks_file_id_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_chunks")),
        sa.UniqueConstraint(
            "file_id",
            "chunker_version",
            "embedding_model",
            "embedding_version",
            "embedding_task_format",
            "chunk_index",
            name="uq_note_chunks_file_config_index",
        ),
        schema="app",
    )
    op.create_index(
        "ix_note_chunks_file_id", "note_chunks", ["file_id"], unique=False, schema="app"
    )
    op.create_index(
        "ix_note_chunks_lexical",
        "note_chunks",
        ["lexical"],
        unique=False,
        schema="app",
        postgresql_using="gin",
    )
    op.create_table(
        "revision_files",
        sa.Column("revision_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.CheckConstraint(
            "role IN ('old_spec','new_spec','code','note')", name=op.f("ck_revision_files_role")
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["app.files.id"],
            name=op.f("fk_revision_files_file_id_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["app.revisions.id"],
            name=op.f("fk_revision_files_revision_id_revisions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("revision_id", "file_id", name=op.f("pk_revision_files")),
        schema="app",
    )
    op.create_index(
        "ix_revision_files_file_id", "revision_files", ["file_id"], unique=False, schema="app"
    )
    op.create_index(
        "uq_revision_files_spec_slot",
        "revision_files",
        ["revision_id", "role"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("role IN ('old_spec','new_spec')"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("revision_id", sa.UUID(), nullable=False),
        sa.Column("preview_run_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "scope_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "mappings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "pipeline_versions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("state", sa.String(length=10), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=100), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("quota_period_id", sa.UUID(), nullable=True),
        sa.Column("quota_state", sa.String(length=10), nullable=True),
        sa.Column("ai_budget_month", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ai_reserved_usd", sa.Numeric(precision=14, scale=8), server_default="0", nullable=False
        ),
        sa.Column(
            "ai_cost_usd", sa.Numeric(precision=14, scale=8), server_default="0", nullable=False
        ),
        sa.Column(
            "uncertain_cost_usd",
            sa.Numeric(precision=14, scale=8),
            server_default="0",
            nullable=False,
        ),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("embedding_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            (
                "(kind = 'preview' AND quota_period_id IS NULL AND quota_state IS NULL "
                "AND preview_run_id IS NULL) OR (kind = 'guidance' AND quota_period_id "
                "IS NOT NULL AND quota_state IS NOT NULL AND preview_run_id IS NOT "
                "NULL)"
            ),
            name=op.f("ck_runs_quota_kind"),
        ),
        sa.CheckConstraint(
            (
                "(state = 'running' AND lease_token IS NOT NULL AND lease_until IS NOT "
                "NULL AND heartbeat_at IS NOT NULL) OR (state != 'running' AND "
                "lease_token IS NULL AND lease_until IS NULL)"
            ),
            name=op.f("ck_runs_lease"),
        ),
        sa.CheckConstraint("kind IN ('preview','guidance')", name=op.f("ck_runs_kind")),
        sa.CheckConstraint(
            "quota_state IS NULL OR quota_state IN ('reserved','consumed','released')",
            name=op.f("ck_runs_quota_state"),
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed')", name=op.f("ck_runs_state")
        ),
        sa.CheckConstraint(
            (
                "ai_reserved_usd >= 0 AND ai_cost_usd >= 0 AND input_tokens >= 0 AND "
                "output_tokens >= 0 AND embedding_tokens >= 0 AND uncertain_cost_usd >="
                " 0"
            ),
            name=op.f("ck_runs_usage"),
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 2", name=op.f("ck_runs_attempts")),
        sa.ForeignKeyConstraint(
            ["ai_budget_month"],
            ["app.ai_budget_months.month"],
            name=op.f("fk_runs_ai_budget_month_ai_budget_months"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app.app_accounts.user_id"],
            name=op.f("fk_runs_owner_user_id_app_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["preview_run_id"], ["app.runs.id"], name=op.f("fk_runs_preview_run_id_runs")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["app.projects.id"],
            name=op.f("fk_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quota_period_id"],
            ["app.quota_periods.id"],
            name=op.f("fk_runs_quota_period_id_quota_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["app.revisions.id"],
            name=op.f("fk_runs_revision_id_revisions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint(
            "owner_user_id", "kind", "idempotency_key", name="uq_runs_owner_kind_idempotency"
        ),
        schema="app",
    )
    op.create_index("ix_runs_fifo", "runs", ["state", "created_at"], unique=False, schema="app")
    op.create_index(
        "ix_runs_fingerprint",
        "runs",
        ["owner_user_id", "project_id", "fingerprint"],
        unique=False,
        schema="app",
        postgresql_where=sa.text("state = 'succeeded'"),
    )
    op.create_index(
        "ix_runs_owner_created", "runs", ["owner_user_id", "created_at"], unique=False, schema="app"
    )
    op.create_index(
        "ix_runs_project_created", "runs", ["project_id", "created_at"], unique=False, schema="app"
    )
    op.create_index(
        "uq_runs_active_owner",
        "runs",
        ["owner_user_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.create_table(
        "reviews",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("recommendation_id", sa.String(length=100), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("edited_instruction", sa.Text(), nullable=True),
        sa.Column("edited_evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "(decision = 'edited' AND edited_instruction IS NOT NULL AND "
                "char_length(edited_instruction) BETWEEN 1 AND 4000) OR (decision != "
                "'edited' AND edited_instruction IS NULL AND edited_evidence_ids IS "
                "NULL)"
            ),
            name=op.f("ck_reviews_edited_content"),
        ),
        sa.CheckConstraint(
            "decision IN ('accepted','edited','rejected')", name=op.f("ck_reviews_decision")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_reviews_version")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["app.runs.id"], name=op.f("fk_reviews_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", "recommendation_id", name=op.f("pk_reviews")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("reviews", schema="app")
    op.drop_index(
        "uq_runs_active_owner",
        table_name="runs",
        schema="app",
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.drop_index("ix_runs_project_created", table_name="runs", schema="app")
    op.drop_index("ix_runs_owner_created", table_name="runs", schema="app")
    op.drop_index(
        "ix_runs_fingerprint",
        table_name="runs",
        schema="app",
        postgresql_where=sa.text("state = 'succeeded'"),
    )
    op.drop_index("ix_runs_fifo", table_name="runs", schema="app")
    op.drop_table("runs", schema="app")
    op.drop_index(
        "uq_revision_files_spec_slot",
        table_name="revision_files",
        schema="app",
        postgresql_where=sa.text("role IN ('old_spec','new_spec')"),
    )
    op.drop_index("ix_revision_files_file_id", table_name="revision_files", schema="app")
    op.drop_table("revision_files", schema="app")
    op.drop_index(
        "ix_note_chunks_lexical", table_name="note_chunks", schema="app", postgresql_using="gin"
    )
    op.drop_index("ix_note_chunks_file_id", table_name="note_chunks", schema="app")
    op.drop_table("note_chunks", schema="app")
    op.drop_index("ix_revisions_project_created", table_name="revisions", schema="app")
    op.drop_table("revisions", schema="app")
    op.drop_index(
        "uq_quota_periods_free_owner_month",
        table_name="quota_periods",
        schema="app",
        postgresql_where=sa.text("kind = 'free'"),
    )
    op.drop_index("ix_quota_periods_owner_dates", table_name="quota_periods", schema="app")
    op.drop_table("quota_periods", schema="app")
    op.drop_index(
        "ix_files_upload_expiry",
        table_name="files",
        schema="app",
        postgresql_where=sa.text("state = 'uploading'"),
    )
    op.drop_index("ix_files_project_id", table_name="files", schema="app")
    op.drop_table("files", schema="app")
    op.drop_index(
        "uq_purchases_pending_owner",
        table_name="purchases",
        schema="app",
        postgresql_where=sa.text("state IN ('pending','creation_unknown')"),
    )
    op.drop_index("ix_purchases_owner_created", table_name="purchases", schema="app")
    op.drop_table("purchases", schema="app")
    op.drop_index("ix_projects_owner_user_id", table_name="projects", schema="app")
    op.drop_table("projects", schema="app")
    op.drop_index("ix_admin_audit_actor_created", table_name="admin_audit", schema="app")
    op.drop_table("admin_audit", schema="app")
    op.drop_table("app_accounts", schema="app")
    op.drop_index("ix_webhook_events_retry", table_name="webhook_events", schema="app")
    op.drop_table("webhook_events", schema="app")
    op.drop_table("ai_budget_months", schema="app")
