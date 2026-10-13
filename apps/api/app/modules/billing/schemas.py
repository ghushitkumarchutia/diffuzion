from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ProviderID = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]


class BillingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Plan(BillingModel):
    code: Literal["free", "pro"]
    amount_paise: int = Field(ge=0)
    currency: Literal["INR"] = "INR"
    reports: int = Field(ge=1)
    duration_days: int | None = Field(default=None, ge=1)
    storage_bytes: int = Field(ge=1)


class Catalogue(BillingModel):
    plans: list[Plan] = Field(min_length=2, max_length=2)
    mode: Literal["test", "live"]
    key_id: ProviderID

    @model_validator(mode="after")
    def unique_plans(self) -> "Catalogue":
        if {plan.code for plan in self.plans} != {"free", "pro"}:
            raise ValueError("Catalogue requires Free and Pro")
        return self


class Usage(BillingModel):
    period_id: UUID
    kind: Literal["free", "pro"]
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    limit: int = Field(ge=1)
    reserved: int = Field(ge=0)
    consumed: int = Field(ge=0)
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_period(self) -> "Usage":
        if self.ends_at <= self.starts_at or self.reserved + self.consumed > self.limit:
            raise ValueError("Invalid quota period or counters")
        return self


class Entitlement(BillingModel):
    plan: Literal["free", "pro"]
    usage: Usage
    stored_bytes: int = Field(ge=0)
    reserved_upload_bytes: int = Field(ge=0)
    storage_limit_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def matching_plan(self) -> "Entitlement":
        if self.plan != self.usage.kind:
            raise ValueError("Entitlement and period must match")
        return self


class OrderRequest(BillingModel):
    plan_code: Literal["pro"]


class VerificationRequest(BillingModel):
    purchase_id: UUID
    payment_id: ProviderID
    signature: Annotated[str, Field(pattern=r"^[a-fA-F0-9]{64}$")]


class ReconciliationRequest(BillingModel):
    pass


class Purchase(BillingModel):
    id: UUID
    plan_code: Literal["pro"]
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    mode: Literal["test", "live"]
    state: Literal[
        "pending", "creation_unknown", "active", "rejected", "refunded", "revoked", "expired"
    ]
    order_id: ProviderID | None = None
    payment_id: ProviderID | None = None
    created_at: AwareDatetime
    fulfilled_at: AwareDatetime | None = None


class CheckoutOrder(BillingModel):
    purchase_id: UUID
    order_id: ProviderID
    key_id: ProviderID
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    mode: Literal["test", "live"]


class VerificationResult(BillingModel):
    purchase_id: UUID
    status: Literal["active", "pending", "rejected"]
    entitlement: Entitlement | None = None
