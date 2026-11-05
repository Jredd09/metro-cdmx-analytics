import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(scope="session")
def db():
    """Database with the pipeline already run (`python -m metro_analytics build`)."""
    from metro_analytics.db import Database
    try:
        d = Database(os.environ.get("DATABASE_URL"))
        d.scalar("SELECT count(*) FROM metro.train")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database not available: {exc}")
    return d


@pytest.fixture(scope="session")
def tables():
    from metro_analytics.generate import Generator
    return Generator(2025).run()
