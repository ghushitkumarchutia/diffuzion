from decimal import Decimal
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

    public_origin: str
    auth_database_url: SecretStr
    app_database_url: SecretStr
    auth_schema: Literal["auth"] = "auth"
    app_schema: Literal["app"] = "app"
    internal_auth_url: str = "http://web:3000/api/auth/get-session?disableCookieCache=true"
    session_days: int = Field(default=7, ge=1, le=7)
    auth_timeout_seconds: int = Field(default=5, ge=1, le=10)
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: Path = Path("private-uploads")
    bucket_name: str | None = None
    bucket_region: str | None = None
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    gemini_api_key: SecretStr
    generation_model: str = Field(default="gemini-3.5-flash-lite", min_length=1, max_length=100)
    embedding_model: str = Field(default="gemini-embedding-2", min_length=1, max_length=100)
    embedding_dimensions: Literal[768] = 768
    pipeline_version: str = "1"
    chunker_version: str = "1"
    prompt_version: str = "1"
    parser_version: str = "1"
    chunk_tokens: int = Field(default=300, ge=1, le=1000)
    chunk_overlap_tokens: int = Field(default=40, ge=0, le=200)
    free_monthly_reports: int = Field(default=5, ge=1)
    pro_price_paise: int = Field(default=19900, ge=1)
    pro_reports: int = Field(default=50, ge=1)
    pro_days: int = Field(default=30, ge=1)
    free_storage_bytes: int = Field(default=50 * 1024**2, ge=1)
    pro_storage_bytes: int = Field(default=250 * 1024**2, ge=1)
    ai_monthly_budget_usd: Decimal = Field(default=Decimal("10.00"), gt=0)
    ai_run_reservation_usd: Decimal = Field(default=Decimal("0.15"), gt=0)
    max_spec_bytes: int = Field(default=2 * 1024**2, ge=1, le=2 * 1024**2)
    max_spec_operations: int = Field(default=100, ge=1, le=100)
    max_code_files: int = Field(default=20, ge=1, le=20)
    max_code_bytes: int = Field(default=256 * 1024, ge=1, le=256 * 1024)
    max_note_files: int = Field(default=10, ge=0, le=10)
    max_note_bytes: int = Field(default=100 * 1024, ge=1, le=100 * 1024)
    max_revision_bytes: int = Field(default=12 * 1024**2, ge=1, le=12 * 1024**2)
    max_selected_operations: int = Field(default=30, ge=1, le=30)
    max_note_chunks: int = Field(default=200, ge=1, le=200)
    max_embedding_tokens: int = Field(default=40000, ge=1, le=40000)
    max_call_input_tokens: int = Field(default=12000, ge=1, le=12000)
    max_call_output_tokens: int = Field(default=2048, ge=1, le=2048)
    max_run_input_tokens: int = Field(default=160000, ge=1, le=160000)
    max_run_output_tokens: int = Field(default=32000, ge=1, le=32000)
    max_generation_calls: int = Field(default=10, ge=1, le=10)
    max_transient_retries: int = Field(default=2, ge=0, le=2)
    max_repair_calls: int = Field(default=1, ge=0, le=1)
    run_deadline_seconds: int = Field(default=480, ge=1, le=480)
    provider_timeout_seconds: int = Field(default=30, ge=1, le=30)
    worker_heartbeat_seconds: int = Field(default=15, ge=1)
    worker_lease_seconds: int = Field(default=60, ge=1)
    max_worker_attempts: int = Field(default=2, ge=1, le=2)
    max_waiting_jobs: int = Field(default=5, ge=1, le=5)
    reads_per_minute: int = Field(default=120, ge=1)
    uploads_per_minute: int = Field(default=10, ge=1)
    starts_per_minute: int = Field(default=5, ge=1)
    payment_checks_per_minute: int = Field(default=3, ge=1)
    ip_requests_per_minute: int = Field(default=240, ge=1)
    auth_requests_per_minute: int = Field(default=20, ge=1)
    rate_limit_max_entries: int = Field(default=10000, ge=1)
    trusted_proxy_hosts: str = "127.0.0.1,::1"
    yaml_max_depth: int = Field(default=50, ge=1)
    yaml_max_nodes: int = Field(default=20000, ge=1)
    yaml_max_aliases: int = Field(default=0, ge=0, le=0)
    parser_memory_bytes: int = Field(default=256 * 1024**2, ge=16 * 1024**2)
    parser_output_bytes: int = Field(default=4 * 1024**2, ge=1024)
    parser_timeout_seconds: int = Field(default=30, ge=1)
    billing_mode: Literal["test", "live"] = "test"
    razorpay_key_id: str = Field(min_length=1)
    razorpay_key_secret: SecretStr
    razorpay_webhook_secret: SecretStr

    @field_validator("gemini_api_key", "razorpay_key_secret", "razorpay_webhook_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip() or value.get_secret_value() == "replace-me":
            raise ValueError("Required server credentials must be explicitly configured")
        return value

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
            or parsed.port == 0
            or any(char.isspace() for char in value)
        ):
            raise ValueError("PUBLIC_ORIGIN must be an exact HTTP(S) origin without a path")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("PUBLIC_ORIGIN requires HTTPS outside local development")
        return value

    @field_validator("internal_auth_url")
    @classmethod
    def validate_internal_auth(cls, value: str) -> str:
        if value != "http://web:3000/api/auth/get-session?disableCookieCache=true":
            raise ValueError("INTERNAL_AUTH_URL must use the fixed internal auth endpoint")
        return value

    @field_validator("auth_database_url", "app_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or not parsed.hostname:
            raise ValueError("A PostgreSQL connection URL is required")
        return value

    @field_validator("trusted_proxy_hosts")
    @classmethod
    def validate_proxy_hosts(cls, value: str) -> str:
        for host in value.split(","):
            if host.strip():
                ip_address(host.strip())
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> "Settings":
        if self.worker_lease_seconds <= self.worker_heartbeat_seconds:
            raise ValueError("Worker lease must exceed heartbeat interval")
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.ai_run_reservation_usd > self.ai_monthly_budget_usd:
            raise ValueError("Run reservation must fit the monthly budget")
        if self.pro_storage_bytes < self.free_storage_bytes:
            raise ValueError("Pro storage must be at least Free storage")
        if self.storage_backend == "s3" and not all(
            (
                self.bucket_name,
                self.bucket_region,
                self.aws_access_key_id,
                self.aws_secret_access_key,
            )
        ):
            raise ValueError("S3 storage requires bucket, region and server credentials")
        prefix = f"rzp_{self.billing_mode}_"
        if not self.razorpay_key_id.startswith(prefix):
            raise ValueError("Razorpay key ID must match billing mode")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
