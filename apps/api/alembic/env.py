import os
from logging.config import fileConfig
from pathlib import Path

from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from pydantic import SecretStr
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import SchemaItem

from alembic import context
from app.core.config import Settings
from app.db import models
from app.db.base import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata
assert models.__all__


def database_url() -> str:
    value = os.environ.get("APP_DATABASE_URL")
    if not value or not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("APP_DATABASE_URL must specify the psycopg PostgreSQL driver")
    Settings.validate_database_url(SecretStr(value))
    return value


def include_name(
    name: str | None, type_: NameFilterType, parent_names: NameFilterParentNames
) -> bool:
    if type_ == "schema":
        return name == "app"
    return parent_names.get("schema_name", "app") == "app"


def include_object(
    obj: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: SchemaItem | None
) -> bool:
    if type_ == "table":
        return getattr(obj, "schema", None) == "app" and name != "alembic_version"
    return True


def verify_prerequisites(connection: Connection) -> None:
    verify_sql = Path(__file__).resolve().parents[2] / "web/auth-migrations/verify-schema.sql"
    connection.execute(text(verify_sql.read_text()))
    if connection.scalar(text("SELECT to_regnamespace('app')")) is None:
        raise RuntimeError("Bootstrap must provision the app schema before migrations")
    extension = connection.scalar(
        text(
            "SELECT e.extversion FROM pg_extension e JOIN pg_namespace n "
            "ON n.oid=e.extnamespace WHERE e.extname='vector' AND n.nspname='public'"
        )
    )
    if extension is None:
        raise RuntimeError("Bootstrap must install pgvector before application migrations")
    connection.commit()


def run_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema="app",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            verify_prerequisites(connection)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_schemas=True,
                include_name=include_name,
                include_object=include_object,
                compare_type=True,
                version_table_schema="app",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
