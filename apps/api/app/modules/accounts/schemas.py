from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.modules.billing.schemas import Entitlement


class AccountModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnboardingRequest(AccountModel):
    acknowledged: Literal[True]


class Profile(AccountModel):
    user_id: str = Field(min_length=1, max_length=256)
    name: str = Field(max_length=200)
    email: str = Field(min_length=1, max_length=320)
    role: Literal["user", "admin"]
    status: Literal["active", "suspended"]
    onboarded_at: AwareDatetime | None = None
    entitlement: Entitlement
