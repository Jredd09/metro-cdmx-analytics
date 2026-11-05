"""Load the original course dataset and map it onto the new model for auditing.

The course SQL files are not redistributed in this repository. Place them in
``data/legacy/`` (01_ddl.sql, 02_operators_part1.sql, 03_operators_part2.sql,
04_dml.sql) to reproduce the legacy audit.
"""
from __future__ import annotations

from . import config as C
from .db import Database

LEGACY_FILES = ["01_ddl.sql", "02_operators_part1.sql", "03_operators_part2.sql", "04_dml.sql"]


def available() -> bool:
    return all((C.LEGACY_DIR / f).exists() for f in LEGACY_FILES)


def load_and_migrate(db: Database) -> None:
    db.execute("DROP SCHEMA IF EXISTS legacy CASCADE; CREATE SCHEMA legacy;")
    raw = Database(db.url, search_path="legacy")
    # The course scripts call random(); seed it so the audit is reproducible.
    for f in LEGACY_FILES:
        raw.execute("SELECT setseed(0.42);\n" + (C.LEGACY_DIR / f).read_text(encoding="utf-8"))
    db.execute_file(C.SQL_DIR / "legacy" / "migrate_to_staging.sql")
