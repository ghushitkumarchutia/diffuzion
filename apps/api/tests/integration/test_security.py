import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest

from app.core.config import Settings
from app.core.errors import DomainError, register_error_handlers
from app.core.logging import RequestLoggingMiddleware, StructuredFormatter
from app.core.rate_limit import RateLimiter
from app.core.security import SecurityMiddleware, client_ip
from app.core.subprocesses import ProcessFailure, run_bounded
from app.modules.accounts.schemas import OnboardingRequest
from app.modules.admin.schemas import StatusRequest
from app.modules.analysis.schemas import (
    AnalysisRequest,
    CallSite,
    Coverage,
    Finding,
    OperationGuidance,
    ReviewRequest,
    SourceSpan,
    Step,
)
from app.modules.billing.schemas import OrderRequest, Usage, VerificationRequest
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate, RevisionCreate


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("PUBLIC_ORIGIN", "http://localhost:3000")
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://auth:test@localhost/diffuzion")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://app:test@localhost/diffuzion")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-secret")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_example")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-razorpay-secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "test-webhook-secret")
    return Settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/api/v1/projects")
    def create_project(body: ProjectCreate) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/api/v1/error/{status}")
    def error(status: int) -> None:
        raise HTTPException(status, detail="private provider secret")

    @app.get("/api/v1/crash")
    def crash() -> None:
        raise RuntimeError("private uploaded source")

    @app.get("/api/v1/limit")
    def limit() -> None:
        raise DomainError(429, "rate_limited", "Too many requests", retry_after=42)

    @app.post("/api/v1/billing/webhooks/razorpay")
    def webhook(request: Request) -> None:
        raise DomainError(403, "invalid_signature", "A valid payment signature is required")

    return TestClient(
        RequestLoggingMiddleware(SecurityMiddleware(app, settings)),
        raise_server_exceptions=False,
    )


def test_configuration_defaults_and_secret_redaction(settings: Settings) -> None:
    assert settings.free_monthly_reports == 5
    assert settings.pro_price_paise == 19900
    assert settings.pro_reports == 50
    assert settings.pro_days == 30
    assert settings.free_storage_bytes == 50 * 1024**2
    assert settings.pro_storage_bytes == 250 * 1024**2
    assert settings.ai_monthly_budget_usd == 10
    assert "test-gemini-secret" not in repr(settings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("public_origin", "https://example.com/"),
        ("public_origin", "http://example.com"),
        ("public_origin", "https://example.com?key=secret"),
        ("public_origin", "https://user:secret@example.com"),
        ("public_origin", "https://example.com:invalid"),
        ("internal_auth_url", "https://attacker.example/get-session"),
        ("app_database_url", "sqlite:///db"),
        ("billing_mode", "invalid"),
        ("razorpay_key_id", "rzp_live_example"),
        ("max_selected_operations", 31),
        ("max_waiting_jobs", 6),
        ("yaml_max_aliases", 1),
        ("trusted_proxy_hosts", "*"),
        ("chunk_overlap_tokens", 300),
        ("worker_lease_seconds", 15),
        ("ai_monthly_budget_usd", "0.01"),
        ("storage_backend", "s3"),
    ],
)
def test_invalid_configuration(settings: Settings, field: str, value: object) -> None:
    values = settings.model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        Settings(**values)


def test_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example", "x-diffuzion-request": "1"},
        {"origin": "http://localhost:3000"},
        {"origin": "http://localhost:3000", "x-diffuzion-request": "0"},
        {"origin": "null", "x-diffuzion-request": "1"},
    ],
)
def test_untrusted_mutations(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/v1/projects", json={"name": "Demo"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "untrusted_origin"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_allowed_mutation_and_bounded_validation(client: TestClient) -> None:
    headers = {"origin": "http://localhost:3000", "x-diffuzion-request": "1"}
    response = client.post("/api/v1/projects", json={"name": "Demo"}, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    response = client.post(
        "/api/v1/projects", json={"name": "Demo", "owner": "secret"}, headers=headers
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    assert len(response.json()["error"]["details"]["fields"]) <= 10


def test_webhook_origin_exemption_requires_signature_handler(client: TestClient) -> None:
    response = client.post("/api/v1/billing/webhooks/razorpay")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_signature"
    assert client.put("/api/v1/billing/webhooks/razorpay").status_code == 403


@pytest.mark.parametrize("status", [401, 403, 404, 409, 413, 422, 429, 503])
def test_errors_are_consistent_and_redacted(client: TestClient, status: int) -> None:
    response = client.get(f"/api/v1/error/{status}")
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "private" not in response.text


def test_unexpected_error_redaction(client: TestClient) -> None:
    response = client.get("/api/v1/crash")
    assert response.status_code == 500
    assert "private" not in response.text
    assert response.json()["error"]["code"] == "internal_error"


def test_retry_header(client: TestClient) -> None:
    response = client.get("/api/v1/limit")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"


@pytest.mark.parametrize("request_id", ["valid_123", "x" * 65, "private?secret=yes"])
def test_request_id_validation(client: TestClient, request_id: str) -> None:
    response = client.get("/api/v1/error/404", headers={"x-request-id": request_id})
    actual = response.headers["x-request-id"]
    assert len(actual) <= 64
    assert actual == request_id if request_id == "valid_123" else actual != request_id


def test_logs_exclude_sensitive_metadata() -> None:
    record = logging.LogRecord("diffuzion", logging.INFO, "", 0, "request_completed", (), None)
    record.request_id = "safe-id"
    record.cookies = "secret-cookie"
    record.source = "private source"
    record.exc_info = (RuntimeError, RuntimeError("secret exception"), None)
    output = StructuredFormatter().format(record)
    assert json.loads(output)["request_id"] == "safe-id"
    assert "secret" not in output and "private" not in output


def test_forwarded_headers_only_from_trusted_peer(settings: Settings) -> None:
    def request(peer: str, forwarded: str) -> StarletteRequest:
        return StarletteRequest(
            {
                "type": "http",
                "client": (peer, 1),
                "headers": [(b"x-forwarded-for", forwarded.encode())],
            }
        )

    assert client_ip(request("198.51.100.2", "203.0.113.7"), settings) == "198.51.100.2"
    assert client_ip(request("127.0.0.1", "evil, 203.0.113.7"), settings) == "203.0.113.7"
    assert client_ip(request("127.0.0.1", "invalid"), settings) == "127.0.0.1"


@pytest.mark.parametrize(
    "category,limit",
    [("read", 120), ("upload", 10), ("start", 5), ("payment", 3), ("ip", 240), ("auth", 20)],
)
def test_rate_limits(settings: Settings, category: str, limit: int) -> None:
    limiter = RateLimiter(settings)
    for _ in range(limit):
        limiter.check(category, "user")
    with pytest.raises(DomainError) as error:
        limiter.check(category, "user")
    assert error.value.status_code == 429 and error.value.retry_after == 60


def test_rate_window_expiry_and_capacity(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.rate_limit_max_entries = 1
    limiter = RateLimiter(settings)
    monkeypatch.setattr("app.core.rate_limit.monotonic", lambda: 0)
    limiter.check("read", "one")
    with pytest.raises(DomainError):
        limiter.check("read", "two")
    monkeypatch.setattr("app.core.rate_limit.monotonic", lambda: 60)
    limiter.check("read", "two")
    assert len(limiter.entries) == 1


def test_rate_limit_concurrency(settings: Settings) -> None:
    limiter = RateLimiter(settings)

    def attempt(_: int) -> bool:
        try:
            limiter.check("payment", "same-user")
            return True
        except DomainError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(attempt, range(20))) == 3


def test_subprocess_success_and_no_shell(settings: Settings) -> None:
    executable = Path(sys.executable).resolve()
    result = run_bounded(
        executable,
        ["-c", "import sys;print(sys.argv[1])", "$(echo secret)"],
        settings,
        allowed_executables=frozenset({executable}),
    )
    assert result.stdout == b"$(echo secret)\n"
    assert result.stderr == b""


@pytest.mark.parametrize(
    "program,code",
    [
        ("import time;time.sleep(5)", "parser_timeout"),
        ("print('x'*10000)", "parser_output_limit"),
        ("raise RuntimeError('secret')", "parser_failed"),
    ],
)
def test_subprocess_failure_and_cleanup(
    settings: Settings, tmp_path: Path, program: str, code: str
) -> None:
    settings.parser_timeout_seconds = 1
    settings.parser_output_bytes = 1024
    executable = Path(sys.executable).resolve()
    with pytest.raises(ProcessFailure) as error:
        run_bounded(
            executable,
            ["-c", program],
            settings,
            allowed_executables=frozenset({executable}),
            temp_root=tmp_path,
        )
    assert error.value.code == code
    assert "secret" not in error.value.message
    assert list(tmp_path.iterdir()) == []


def test_subprocess_rejects_untrusted_executable(settings: Settings) -> None:
    with pytest.raises(ValueError):
        run_bounded(Path(sys.executable), [], settings, allowed_executables=frozenset())


def test_user_mutations_forbid_privilege_and_price_assertions() -> None:
    with pytest.raises(ValidationError):
        OnboardingRequest(acknowledged=True, role="admin")
    with pytest.raises(ValidationError):
        OrderRequest(plan_code="pro", amount_paise=1)
    with pytest.raises(ValidationError):
        VerificationRequest(
            purchase_id=uuid4(), payment_id="pay_test", signature="a" * 64, active=True
        )
    with pytest.raises(ValidationError):
        StatusRequest(reason=" ", status="active")


def test_project_and_revision_constraints() -> None:
    assert ProjectCreate(name=" Demo ", api_base_url="https://api.example/v1").name == "Demo"
    for url in ["file:///tmp/private", "https://user:secret@api.example", "https://api/{version}"]:
        with pytest.raises(ValidationError):
            ProjectCreate(name="Demo", api_base_url=url)
    with pytest.raises(ValidationError):
        ProjectUpdate()
    with pytest.raises(ValidationError):
        ProjectCreate(name=" ")
    spec_id = uuid4()
    with pytest.raises(ValidationError):
        RevisionCreate(old_spec_id=spec_id, new_spec_id=spec_id, code_ids=[uuid4()])
    with pytest.raises(ValidationError):
        RevisionCreate(old_spec_id=uuid4(), new_spec_id=uuid4(), code_ids=[])


def test_quota_period_invariants() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        Usage(
            period_id=uuid4(),
            kind="free",
            starts_at=now,
            ends_at=now + timedelta(days=30),
            limit=5,
            reserved=1,
            consumed=5,
        )
    with pytest.raises(ValidationError):
        Usage(
            period_id=uuid4(),
            kind="free",
            starts_at=now.replace(tzinfo=None),
            ends_at=now,
            limit=5,
            reserved=0,
            consumed=0,
        )


def test_analysis_scope_mapping_and_span_constraints() -> None:
    assert AnalysisRequest(preview_id=uuid4(), selected_operation_keys=["GET /users/{id}"])
    for keys in [
        ["get /users"],
        ["GET /users/{id"],
        ["GET /users?x=1"],
        ["GET /users", "GET /users"],
        [f"GET /users/{i}" for i in range(31)],
    ]:
        with pytest.raises(ValidationError):
            AnalysisRequest(preview_id=uuid4(), selected_operation_keys=keys)
    with pytest.raises(ValidationError):
        SourceSpan(byte_start=3, byte_end=2, line_start=1, line_end=1)
    with pytest.raises(ValidationError):
        CallSite(
            id="call-1",
            file_id=uuid4(),
            language="python",
            span=SourceSpan(byte_start=0, byte_end=10, line_start=1, line_end=1),
            method="GET",
            url_pattern="/users",
        )
    with pytest.raises(ValidationError):
        Coverage(analyzed_file_ids=[], recognized_calls=2, mapped_calls=1, unresolved_candidates=0)


def test_guidance_requires_evidence_and_unknowns() -> None:
    with pytest.raises(ValidationError):
        Step(id="step-1", instruction="Change call", evidence_ids=[])
    with pytest.raises(ValidationError):
        OperationGuidance(
            operation_key="GET /users", change_ids=["change-1"], status="needs_information"
        )
    with pytest.raises(ValidationError):
        Finding(id="change-1", severity="breaking", change_type="removed", message="Removed")
    with pytest.raises(ValidationError):
        ReviewRequest(decision="edited", expected_version=0)
    with pytest.raises(ValidationError):
        ReviewRequest(decision="accepted", expected_version=0, edited_instruction="Different")


@pytest.mark.parametrize(
    "field,value",
    [
        ("gemini_api_key", ""),
        ("razorpay_key_secret", "replace-me"),
        ("razorpay_webhook_secret", " "),
        ("public_origin", "https://exam\nple.com"),
    ],
)
def test_runtime_credentials_and_origin_are_explicit(
    settings: Settings, field: str, value: str
) -> None:
    values = settings.model_dump()
    values[field] = value
    with pytest.raises(ValidationError) as error:
        Settings(**values)
    assert "input_value" not in str(error.value)


def test_schema_generation_and_snapshot_input_traceability() -> None:
    from app.modules.analysis.schemas import Export, InputFile, InputSnapshot

    assert Export.model_json_schema()["additionalProperties"] is False
    sources = [
        InputFile(id=uuid4(), original_name=name, role=role, sha256="a" * 64)
        for name, role in [
            ("old.yaml", "old_spec"),
            ("new.yaml", "new_spec"),
            ("consumer.py", "code"),
        ]
    ]
    snapshot = InputSnapshot(
        revision_id=uuid4(), api_base_url="https://api.example/v1", files=sources
    )
    assert len(snapshot.files) == 3
    with pytest.raises(ValidationError):
        InputSnapshot(revision_id=uuid4(), api_base_url=None, files=[*sources, sources[-1]])
