from __future__ import annotations

from sqlalchemy import inspect, text

from .db import get_engine
from .models import Base


def migrate_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "fund_estimates" in table_names:
        columns = {column["name"] for column in inspector.get_columns("fund_estimates")}
        if "missing_assets_json" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE fund_estimates "
                        "ADD COLUMN missing_assets_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )


def init_db(db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    migrate_schema(engine)
