from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().app_database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
        hide_parameters=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_context() -> Iterator[Session]:
    with get_session_factory()() as session:
        try:
            yield session
        except BaseException:
            session.rollback()
            raise


def get_db_session() -> Generator[Session, None, None]:
    with session_context() as session:
        yield session
