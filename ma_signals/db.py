"""Initialisation de la base et fabrique de sessions."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

# SQLite a besoin de check_same_thread=False pour être utilisé hors du thread créateur.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _ensure_columns() -> None:
    """Migration legere : ajoute les colonnes manquantes sur une base existante.

    create_all() ne fait que CREER les tables absentes ; il n'ALTERe pas une table
    deja presente. On ajoute donc story_key a la main si elle manque (SQLite et
    autres backends supportent ADD COLUMN)."""
    insp = inspect(engine)
    if "signals" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("signals")}
    with engine.begin() as conn:
        if "story_key" not in cols:
            conn.execute(text("ALTER TABLE signals ADD COLUMN story_key VARCHAR(128) DEFAULT ''"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_signals_story_key ON signals (story_key)"))


def init_db() -> None:
    """Crée les tables si elles n'existent pas, puis applique les migrations légères."""
    Base.metadata.create_all(engine)
    _ensure_columns()


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
