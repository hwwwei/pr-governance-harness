from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


_database_url = _engine_url(get_settings().database_url)
_connect_args = {"check_same_thread": False} if _database_url.startswith("sqlite") else {}
_engine_options = {"poolclass": StaticPool} if _database_url in {"sqlite://", "sqlite:///:memory:"} else {}
engine = create_engine(_database_url, future=True, pool_pre_ping=True, connect_args=_connect_args, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401

    # SQLite is the zero-dependency local mode. PostgreSQL is migrated by
    # `alembic upgrade head` in Compose before the API/worker starts.
    if _database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
