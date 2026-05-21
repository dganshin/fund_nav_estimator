from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fund_nav_estimator.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"


def get_database_url() -> str:
    return os.getenv("FUND_NAV_DB_URL", DEFAULT_DB_URL)


def get_engine(db_url: str | None = None):
    url = db_url or get_database_url()
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def get_session_factory(db_url: str | None = None) -> sessionmaker:
    engine = get_engine(db_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

