from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(
        schema="app",
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


auth_user = Table(
    "user",
    Base.metadata,
    Column("id", Text, primary_key=True),
    schema="auth",
    info={"reference_only": True},
)
