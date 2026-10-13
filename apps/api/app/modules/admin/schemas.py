from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AdminModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReasonRequest(AdminModel):
    reason: str = Field(min_length=1, max_length=500)


class StatusRequest(ReasonRequest):
    status: Literal["active", "suspended"]


class ReconcileRequest(ReasonRequest):
    pass


class PurchaseReconcileRequest(ReasonRequest):
    discovered_order_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^order_[A-Za-z0-9]+$"
    )


class RevokeRequest(ReasonRequest):
    pass


class Account(AdminModel):
    user_id: str = Field(min_length=1, max_length=256)
    role: Literal["user", "admin"]
    status: Literal["active", "suspended"]
    plan: Literal["free", "pro"]
    stored_bytes: int = Field(ge=0)
    onboarded_at: AwareDatetime | None = None


class RedactedFailure(AdminModel):
    run_id: UUID
    request_id: str = Field(min_length=1, max_length=64)
    error_code: str = Field(min_length=1, max_length=100)
    stage: str = Field(min_length=1, max_length=100)
    occurred_at: AwareDatetime


class Overview(AdminModel):
    accounts: int = Field(ge=0)
    suspended_accounts: int = Field(ge=0)
    projects: int = Field(ge=0)
    queued_runs: int = Field(ge=0)
    running_runs: int = Field(ge=0)
    succeeded_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    pending_purchases: int = Field(ge=0)
    ai_reserved_usd: Decimal = Field(ge=0)
    ai_charged_usd: Decimal = Field(ge=0)
    failures: list[RedactedFailure] = Field(default_factory=list, max_length=100)
