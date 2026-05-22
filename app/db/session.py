from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "smartjob.local.sqlite3"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
DATABASE_CONNECT_TIMEOUT_SECONDS = max(1, int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "2")))

database_url = make_url(DATABASE_URL)
engine_kwargs: dict[str, object] = {"future": True}
if database_url.get_backend_name() == "sqlite":
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    if database_url.get_backend_name().startswith("postgresql"):
        engine_kwargs["connect_args"] = {"connect_timeout": DATABASE_CONNECT_TIMEOUT_SECONDS}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped usage."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
