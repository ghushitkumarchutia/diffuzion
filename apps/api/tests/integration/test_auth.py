from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.cli import promote_admin
from app.core.auth import get_auth_client, get_current_account, require_admin, verify_identity
from app.core.config import Settings, get_settings
from app.core.errors import DomainError, register_error_handlers
from app.db.models.accounts import AdminAudit, AppAccount
from app.db.session import get_db_session
from tests.conftest import session_command


@pytest.fixture
def auth_client(database_engine: Engine, auth_server: str) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/account")
    def account(account: Annotated[AppAccount, Depends(get_current_account)]) -> dict[str, str]:
        return {"id": account.user_id, "role": account.role, "status": account.status}

    @app.get("/admin")
    def admin(account: Annotated[AppAccount, Depends(require_admin)]) -> dict[str, str]:
        return {"id": account.user_id}

    def session():
        with Session(database_engine, expire_on_commit=False) as value:
            yield value

    def relay(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://web:3000/api/auth/get-session?disableCookieCache=true"
        assert "authorization" not in request.headers
        assert "unrelated-secret" not in request.headers.get("cookie", "")
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as live:
            response = live.get(
                auth_server + request.url.raw_path.decode(),
                headers={"Cookie": request.headers["cookie"]},
            )
        return httpx.Response(response.status_code, content=response.content, request=request)

    def verifier():
        with httpx.Client(transport=httpx.MockTransport(relay), follow_redirects=False) as value:
            yield value

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_auth_client] = verifier
    app.dependency_overrides[get_settings] = lambda: Settings()
    return TestClient(app)


def test_genuine_session_and_account_creation(
    auth_client: TestClient, database_engine: Engine, signed_session: dict[str, Any]
) -> None:
    with database_engine.begin() as connection:
        connection.execute(delete(AppAccount).where(AppAccount.user_id == signed_session["userId"]))
    headers = {
        "Cookie": signed_session["cookie"] + "; unrelated-secret=private",
        "Authorization": "Bearer forged-role",
        "X-Role": "admin",
    }
    response = auth_client.get("/account", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"id": signed_session["userId"], "role": "user", "status": "active"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(
            pool.map(lambda _: auth_client.get("/account", headers=headers).status_code, range(8))
        )
    assert statuses == [200] * 8
    with Session(database_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AppAccount)
                .where(AppAccount.user_id == signed_session["userId"])
            )
            == 1
        )
    assert auth_client.get("/admin", headers=headers).status_code == 403


def test_forged_missing_and_duplicate_cookies(
    auth_client: TestClient, signed_session: dict[str, Any]
) -> None:
    assert auth_client.get("/account").status_code == 401
    assert (
        auth_client.get(
            "/account", headers={"Cookie": "better-auth.session_token=forged"}
        ).status_code
        == 401
    )
    duplicate = signed_session["cookie"] + "; " + signed_session["cookie"]
    assert auth_client.get("/account", headers={"Cookie": duplicate}).status_code == 401


def test_revoked_and_expired_sessions(
    auth_client: TestClient,
    signed_session: dict[str, Any],
    database_engine: Engine,
    node_command: str,
) -> None:
    headers = {"Cookie": signed_session["cookie"]}
    assert auth_client.get("/account", headers=headers).status_code == 200
    with database_engine.begin() as connection:
        connection.exec_driver_sql(
            'UPDATE auth.session SET "expiresAt" = %s WHERE id = %s',
            (datetime.now(UTC) - timedelta(seconds=1), signed_session["sessionId"]),
        )
    assert auth_client.get("/account", headers=headers).status_code == 401
    session_command(node_command, "revoke", signed_session["sessionId"])
    assert auth_client.get("/account", headers=headers).status_code == 401


def test_current_suspension_and_audited_admin_assignment(
    auth_client: TestClient, signed_session: dict[str, Any], database_engine: Engine
) -> None:
    headers = {"Cookie": signed_session["cookie"]}
    with database_engine.begin() as connection:
        connection.execute(
            update(AppAccount)
            .where(AppAccount.user_id == signed_session["userId"])
            .values(status="suspended")
        )
    assert auth_client.get("/account", headers=headers).status_code == 403
    with Session(database_engine) as session:
        with pytest.raises(ValueError):
            promote_admin(session, signed_session["userId"], "Test initial administrator")
    with database_engine.begin() as connection:
        connection.execute(
            update(AppAccount)
            .where(AppAccount.user_id == signed_session["userId"])
            .values(status="active")
        )
    with Session(database_engine) as session:
        assert promote_admin(session, signed_session["userId"], "Test initial administrator")
    with Session(database_engine) as session:
        assert not promote_admin(session, signed_session["userId"], "Repeated assignment")
    assert auth_client.get("/admin", headers=headers).status_code == 200
    with Session(database_engine) as session:
        audit = session.scalars(
            select(AdminAudit).where(AdminAudit.actor_user_id == signed_session["userId"])
        ).all()
        assert len(audit) == 1 and audit[0].before_json == {"role": "user"}


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("null", 401),
        ("redirect", 503),
        ("error", 503),
        ("invalid", 503),
        ("mismatch", 401),
        ("expired", 401),
        ("timeout", 503),
        ("oversized", 503),
    ],
)
def test_auth_verification_fails_closed(
    kind: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "PUBLIC_ORIGIN": "http://localhost:3100",
        "AUTH_DATABASE_URL": "postgresql://x@localhost/db",
        "APP_DATABASE_URL": "postgresql+psycopg://x@localhost/db",
        "GEMINI_API_KEY": "test",
        "RAZORPAY_KEY_ID": "rzp_test_example",
        "RAZORPAY_KEY_SECRET": "test",
        "RAZORPAY_WEBHOOK_SECRET": "test",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    payload = {
        "session": {
            "id": "session",
            "userId": "user",
            "expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
        "user": {"id": "user", "name": "Test", "email": "test@example.com"},
    }
    if kind == "mismatch":
        payload["user"]["id"] = "another-user"
    if kind == "expired":
        payload["session"]["expiresAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    def respond(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("private secret", request=request)
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.invalid"})
        if kind == "error":
            return httpx.Response(500, text="private provider secret")
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * 65537)
        if kind == "null":
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json={} if kind == "invalid" else payload)

    request = Request(
        {"type": "http", "headers": [(b"cookie", b"better-auth.session_token=signed")]}
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DomainError) as error:
            verify_identity(request, settings, client)
        assert error.value.status_code == expected and "private" not in error.value.message
