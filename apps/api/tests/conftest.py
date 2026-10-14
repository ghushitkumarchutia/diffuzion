import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    url = make_url(value)
    socket = url.query.get("host")
    if (
        os.environ.get("DIFFUZION_TEST_MODE") != "1"
        or url.database != "diffuzion_test"
        or url.username != "diffuzion_test"
        or url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or (socket is not None and not str(socket).startswith("/tmp/diffuzion-"))
    ):
        raise RuntimeError(
            "Integration tests require the isolated diffuzion_test database and role"
        )
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "diffuzion_test"
        assert connection.scalar(text("SELECT current_user")) == "diffuzion_test"
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(database_engine: Engine, signed_session: dict[str, Any]) -> Iterator[Session]:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
        transaction.rollback()


@pytest.fixture(scope="session")
def node_command() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 24 is required for signed Better Auth session tests")
    if subprocess.check_output([node, "--version"], text=True).strip() != "v24.21.0":
        raise RuntimeError("Run integration tests using the selected Node.js 24.21.0 runtime")
    return node


def session_command(node: str, command: str, argument: str) -> dict[str, Any]:
    environment = dict(os.environ, NODE_ENV="test")
    result = subprocess.run(
        [
            node,
            "--conditions=react-server",
            str(WEB / "tests/support/test-sessions.ts"),
            command,
            argument,
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout) if result.stdout else {}


@pytest.fixture
def signed_session(database_engine: Engine, node_command: str) -> Iterator[dict[str, Any]]:
    data = session_command(node_command, "create", "user")
    try:
        yield data
    finally:
        session_command(node_command, "revoke", data["sessionId"])
        session_command(node_command, "delete", data["userId"])


@pytest.fixture(scope="session")
def auth_server(
    database_engine: Engine, node_command: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    url = "http://127.0.0.1:3100"
    environment = dict(os.environ, NODE_ENV="development", NEXT_TELEMETRY_DISABLED="1")
    environment.pop("NODE_OPTIONS", None)
    log_path = tmp_path_factory.mktemp("auth-server") / "web.log"
    with log_path.open("w") as output:
        process = subprocess.Popen(
            [
                node_command,
                str(WEB / "node_modules/next/dist/bin/next"),
                "dev",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3100",
            ],
            cwd=WEB,
            env=environment,
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("The isolated Next.js auth test server exited")
                try:
                    if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("The isolated Next.js auth test server did not become ready")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
