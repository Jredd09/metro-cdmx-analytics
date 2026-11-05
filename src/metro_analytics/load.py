"""Create the schema and bulk-load the generated CSVs."""
from __future__ import annotations

from . import config as C
from .db import Database
from .generate import LOAD_ORDER

SCHEMA_FILES = ["01_schema.sql", "02_views.sql", "03_indexes.sql"]


def init_schema(db: Database) -> None:
    for f in SCHEMA_FILES:
        db.execute_file(C.SQL_DIR / f)


def load_generated(db: Database, data_dir=C.GENERATED_DIR) -> dict[str, int]:
    counts = {}
    for table in LOAD_ORDER:
        db.copy_csv(table, data_dir / f"{table}.csv")
        counts[table] = int(db.scalar(f"SELECT count(*) FROM {table}"))
    db.execute("ANALYZE;")
    return counts
