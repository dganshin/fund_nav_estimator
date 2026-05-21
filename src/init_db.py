from __future__ import annotations

from .db import get_engine
from .models import Base


def init_db(db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)

