from sqlalchemy import text
from src.db import get_engine
from src.init_db import init_db

engine = get_engine()
with engine.begin() as conn:
    conn.execute(text('DROP TABLE IF EXISTS calibration_residuals'))

init_db()
print("Migration done.")
