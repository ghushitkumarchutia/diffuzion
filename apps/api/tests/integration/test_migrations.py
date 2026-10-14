import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    AIBudgetMonth,
    File,
    NoteChunk,
    Project,
    Purchase,
    QuotaPeriod,
    Revision,
    RevisionFile,
    Run,
    WebhookEvent,
)
from tests.conftest import ROOT


def test_initial_schema_matches_models_and_preserves_auth(database_engine: Engine) -> None:
    inspector = inspect(database_engine)
    expected = {table.name for table in Base.metadata.tables.values() if table.schema == "app"}
    assert set(inspector.get_table_names(schema="app")) == expected | {"alembic_version"}
    assert {"user", "session", "account", "verification"} <= set(
        inspector.get_table_names(schema="auth")
    )
    with database_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
            == "0.8.2"
        )
        assert connection.scalar(text("SHOW listen_addresses")) == ""
        assert (
            connection.scalar(text("SELECT version_num FROM app.alembic_version"))
            == "0001_initial_app"
        )
        data_type = connection.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
                "attrelid='app.note_chunks'::regclass AND attname='embedding'"
            )
        )
        assert data_type == "vector(768)"
        constraint = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE "
                "conrelid='app.files'::regclass AND "
                "conname='ck_files_upload_reservation'"
            )
        )
        assert "upload_expires_at IS NOT NULL" in constraint


@pytest.fixture
def project(db_session: Session, signed_session: dict[str, Any]) -> Project:
    value = Project(owner_user_id=signed_session["userId"], name="Constraint test")
    db_session.add(value)
    db_session.flush()
    return value


def expect_rejected(session: Session, operation: Callable[[], None]) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        operation()
        session.flush()


def test_file_dedup_spec_slots_and_upload_recovery_fields(
    db_session: Session, project: Project
) -> None:
    first = File(
        project_id=project.id,
        original_name="old.yaml",
        kind="spec",
        sha256="a" * 64,
        storage_key=str(uuid4()),
        size_bytes=10,
        state="stored",
    )
    db_session.add(first)
    db_session.flush()
    expect_rejected(
        db_session,
        lambda: db_session.add(
            File(
                project_id=project.id,
                original_name="duplicate.yaml",
                kind="spec",
                sha256="a" * 64,
                storage_key=str(uuid4()),
                size_bytes=10,
                state="stored",
            )
        ),
    )
    second = File(
        project_id=project.id,
        original_name="new.yaml",
        kind="spec",
        sha256="b" * 64,
        storage_key=str(uuid4()),
        size_bytes=10,
        state="stored",
    )
    revision = Revision(project_id=project.id)
    db_session.add_all([second, revision])
    db_session.flush()
    db_session.add(RevisionFile(revision_id=revision.id, file_id=first.id, role="old_spec"))
    db_session.flush()
    expect_rejected(
        db_session,
        lambda: db_session.add(
            RevisionFile(revision_id=revision.id, file_id=second.id, role="old_spec")
        ),
    )
    expect_rejected(
        db_session,
        lambda: db_session.add(
            File(
                project_id=project.id,
                original_name="pending.py",
                kind="code",
                language="python",
                size_bytes=10,
                state="uploading",
                upload_reserved_bytes=10,
                upload_started_at=datetime.now(UTC),
            )
        ),
    )
    pending = File(
        project_id=project.id,
        original_name="pending.py",
        kind="code",
        language="python",
        size_bytes=10,
        state="uploading",
        upload_reserved_bytes=10,
        upload_started_at=datetime.now(UTC),
        upload_expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(pending)
    db_session.flush()
    assert pending.upload_expires_at is not None


def test_purchase_events_and_quota_constraints(db_session: Session, project: Project) -> None:
    owner = project.owner_user_id
    purchase = Purchase(
        owner_user_id=owner,
        amount_paise=19900,
        mode="test",
        idempotency_key="one",
        request_hash="a" * 64,
    )
    db_session.add(purchase)
    db_session.flush()
    expect_rejected(
        db_session,
        lambda: db_session.add(
            Purchase(
                owner_user_id=owner,
                amount_paise=19900,
                mode="test",
                idempotency_key="two",
                request_hash="b" * 64,
            )
        ),
    )
    event = WebhookEvent(mode="test", event_id="event-1", body_hash="a" * 64, verified_fields={})
    db_session.add(event)
    db_session.flush()
    expect_rejected(
        db_session,
        lambda: db_session.add(
            WebhookEvent(mode="test", event_id="event-1", body_hash="a" * 64, verified_fields={})
        ),
    )
    period = QuotaPeriod(
        owner_user_id=owner,
        kind="free",
        starts_at=datetime(2026, 10, 1, tzinfo=UTC),
        ends_at=datetime(2026, 11, 1, tzinfo=UTC),
        limit=5,
    )
    db_session.add(period)
    db_session.flush()
    expect_rejected(db_session, lambda: setattr(period, "consumed", 6))
    expect_rejected(
        db_session,
        lambda: db_session.add(
            QuotaPeriod(
                owner_user_id=owner,
                kind="free",
                starts_at=datetime(2026, 10, 1, tzinfo=UTC),
                ends_at=datetime(2026, 11, 1, tzinfo=UTC),
                limit=5,
            )
        ),
    )
    expect_rejected(
        db_session,
        lambda: db_session.add(
            QuotaPeriod(
                owner_user_id=owner,
                kind="free",
                starts_at=datetime(2026, 10, 2, tzinfo=UTC),
                ends_at=datetime(2026, 11, 1, tzinfo=UTC),
                limit=5,
            )
        ),
    )
    db_session.add(AIBudgetMonth(month=datetime(2026, 10, 1, tzinfo=UTC), cap_usd=Decimal("10")))
    db_session.flush()
    expect_rejected(
        db_session,
        lambda: db_session.add(
            AIBudgetMonth(
                month=datetime(2026, 11, 1, tzinfo=UTC),
                cap_usd=Decimal("10"),
                charged_usd=Decimal("11"),
            )
        ),
    )


def test_one_active_run_and_idempotency(db_session: Session, project: Project) -> None:
    revision = Revision(project_id=project.id)
    db_session.add(revision)
    db_session.flush()

    def run(key: str) -> Run:
        return Run(
            owner_user_id=project.owner_user_id,
            project_id=project.id,
            revision_id=revision.id,
            kind="preview",
            idempotency_key=key,
            request_hash="a" * 64,
            fingerprint="a" * 64,
            input_snapshot_json={},
            pipeline_versions_json={},
        )

    first = run("one")
    db_session.add(first)
    db_session.flush()
    expect_rejected(db_session, lambda: db_session.add(run("two")))
    first.state = "succeeded"
    db_session.flush()
    expect_rejected(db_session, lambda: db_session.add(run("one")))
    expect_rejected(db_session, lambda: setattr(first, "quota_state", "consumed"))


def test_vector_and_source_spans_are_validated_by_postgres(
    db_session: Session, project: Project
) -> None:
    note = File(
        project_id=project.id,
        original_name="notes.md",
        kind="note",
        sha256="c" * 64,
        size_bytes=10,
        storage_key=str(uuid4()),
        state="stored",
    )
    db_session.add(note)
    db_session.flush()

    def chunk(vector: list[float]) -> NoteChunk:
        return NoteChunk(
            file_id=note.id,
            byte_start=0,
            byte_end=10,
            line_start=1,
            line_end=1,
            character_start=0,
            character_end=10,
            text="test note",
            chunk_index=0,
            chunker_version="1",
            embedding_model="gemini-embedding-2",
            embedding_version="1",
            embedding_task_format="title-text-v1",
            embedding=vector,
            lexical="'test':1",
        )

    db_session.add(chunk([0.1] * 768))
    db_session.flush()
    expect_rejected(db_session, lambda: db_session.add(chunk([0.1] * 768)))
    with pytest.raises(StatementError, match="dimensions"), db_session.begin_nested():
        db_session.add(chunk([0.1] * 3))
        db_session.flush()


def test_database_session_rollback(database_engine: Engine, signed_session: dict[str, Any]) -> None:
    from app.db.session import session_context

    name = str(uuid4())
    with pytest.raises(RuntimeError), session_context() as session:
        session.add(Project(owner_user_id=signed_session["userId"], name=name))
        session.flush()
        raise RuntimeError("Expected rollback")
    with database_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM app.projects WHERE name=:name"), {"name": name}
            )
            == 0
        )


def test_alembic_upgrade_downgrade_and_scope(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in Base.metadata.tables.values():
            if table.schema == "app":
                assert connection.scalar(text(f'SELECT count(*) FROM app."{table.name}"')) == 0
        auth_before = connection.execute(text('SELECT id FROM auth."user" ORDER BY id')).all()
    executable = ROOT / "apps/api/.venv/bin/alembic"

    def invoke(*arguments: str) -> str:
        result = subprocess.run(
            [str(executable), "-c", str(ROOT / "apps/api/alembic.ini"), *arguments],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "apps/api")),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    invoke("downgrade", "base")
    assert inspect(database_engine).get_table_names(schema="app") == ["alembic_version"]
    invoke("upgrade", "head")
    invoke("upgrade", "head")
    assert "No new upgrade operations" in invoke("check")
    with database_engine.connect() as connection:
        assert (
            connection.execute(text('SELECT id FROM auth."user" ORDER BY id')).all() == auth_before
        )
        assert (
            connection.scalar(text("SELECT extname FROM pg_extension WHERE extname='vector'"))
            == "vector"
        )
