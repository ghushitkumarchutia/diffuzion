from collections.abc import Generator
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from typing import Annotated

import httpx
from fastapi import Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.db.models.accounts import AppAccount
from app.db.session import get_db_session


class VerifiedSession(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(alias="userId", min_length=1, max_length=256)
    expires_at: AwareDatetime = Field(alias="expiresAt")


class VerifiedUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(max_length=200)
    email: str = Field(min_length=1, max_length=320)


class VerifiedIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session: VerifiedSession
    user: VerifiedUser


def get_auth_client() -> Generator[httpx.Client, None, None]:
    settings = get_settings()
    with httpx.Client(
        timeout=settings.auth_timeout_seconds, follow_redirects=False, trust_env=False
    ) as client:
        yield client


def session_cookie(request: Request, settings: Settings) -> str:
    name = (
        "__Secure-better-auth.session_token"
        if settings.public_origin.startswith("https://")
        else "better-auth.session_token"
    )
    cookie_headers = request.headers.getlist("cookie")
    if len(cookie_headers) != 1 or len(cookie_headers[0]) > 16384:
        raise DomainError(401, "unauthenticated", "Sign in is required")
    raw = cookie_headers[0]
    if sum(part.strip().split("=", 1)[0] == name for part in raw.split(";")) != 1:
        raise DomainError(401, "unauthenticated", "Sign in is required")
    parsed: SimpleCookie = SimpleCookie()
    try:
        parsed.load(raw)
    except CookieError:
        raise DomainError(401, "unauthenticated", "Sign in is required") from None
    if name not in parsed:
        raise DomainError(401, "unauthenticated", "Sign in is required")
    value = parsed[name].value
    if not value or len(value) > 4096 or any(char in value for char in ';\r\n"'):
        raise DomainError(401, "unauthenticated", "Sign in is required")
    return f"{name}={value}"


def verify_identity(request: Request, settings: Settings, client: httpx.Client) -> VerifiedIdentity:
    cookie = session_cookie(request, settings)
    try:
        response = client.get(
            settings.internal_auth_url,
            headers={"Cookie": cookie},
            follow_redirects=False,
            timeout=settings.auth_timeout_seconds,
        )
    except httpx.HTTPError:
        raise DomainError(503, "auth_unavailable", "Session verification is unavailable") from None
    if response.status_code == 401:
        raise DomainError(401, "unauthenticated", "Sign in is required")
    if response.status_code != 200 or len(response.content) > 65536:
        raise DomainError(503, "auth_unavailable", "Session verification is unavailable")
    try:
        payload = response.json()
        if payload is None:
            raise DomainError(401, "unauthenticated", "Sign in is required")
        identity = VerifiedIdentity.model_validate(payload)
    except (ValueError, ValidationError, RecursionError):
        raise DomainError(
            503, "auth_invalid_response", "Session verification is unavailable"
        ) from None
    if identity.session.user_id != identity.user.id or identity.session.expires_at <= datetime.now(
        UTC
    ):
        raise DomainError(401, "unauthenticated", "Sign in is required")
    return identity


def get_current_account(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.Client, Depends(get_auth_client)],
    session: Annotated[Session, Depends(get_db_session)],
) -> AppAccount:
    identity = verify_identity(request, settings, client)
    try:
        with session.begin():
            session.execute(
                insert(AppAccount)
                .values(user_id=identity.user.id)
                .on_conflict_do_nothing(index_elements=[AppAccount.user_id])
            )
            account = session.get(AppAccount, identity.user.id, populate_existing=True)
            if account is None:
                raise DomainError(503, "account_unavailable", "Account is unavailable")
            if account.status != "active":
                raise DomainError(403, "account_suspended", "Account is suspended")
            request.state.verified_identity = identity
        return account
    except SQLAlchemyError:
        raise DomainError(503, "account_unavailable", "Account is unavailable") from None


def require_admin(account: Annotated[AppAccount, Depends(get_current_account)]) -> AppAccount:
    if account.role != "admin":
        raise DomainError(403, "admin_required", "Administrator access is required")
    return account
