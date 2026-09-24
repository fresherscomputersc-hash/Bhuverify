"""SQLAlchemy engine / session plumbing (SRS 6.2 storage approach).

Prototype tier uses SQLite for zero-ops demo reliability. The model layer is
dialect-neutral, so the pilot tier only swaps the connection URL for
PostgreSQL + PostGIS.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - infra glue
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Imperative session scope used by the background worker and seeding."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Additive SQLite migration for existing prototype databases.

    `create_all` never alters tables, so columns added in later versions
    would crash live databases (Render) on first access. This adds the
    missing ones in place; fresh databases already have them.
    """
    from sqlalchemy import inspect, text

    wanted: dict[str, list[tuple[str, str]]] = {
        "land_records": [
            ("owners_json", "JSON"),
            ("khewat_no", "VARCHAR(60)"),
            ("khatiyan_no", "VARCHAR(60)"),
            ("tehsil_no", "VARCHAR(60)"),
            ("boundary_json", "JSON"),
        ],
        "extraction_results": [
            ("status", "VARCHAR(20)"),
            ("reason", "VARCHAR(60)"),
            ("method", "VARCHAR(40)"),
        ],
        "source_documents": [
            ("page_count", "INTEGER"),
        ],
    }
    with engine.begin() as conn:
        for table, columns in wanted.items():
            try:
                have = {col["name"] for col in inspect(conn).get_columns(table)}
            except Exception:
                continue
            for name, ddl in columns:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
