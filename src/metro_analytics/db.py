"""Thin PostgreSQL access layer.

Uses psycopg 3 when it is installed. If it is not, falls back to the ``psql``
command-line client, so the pipeline also runs on machines where only the
PostgreSQL client tools are available.
"""
from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pandas as pd

from .config import DATABASE_URL

try:  # pragma: no cover - exercised depending on the environment
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


class Database:
    def __init__(self, url: str | None = None, search_path: str = "metro, public"):
        self.url = url or DATABASE_URL
        self.search_path = search_path
        if psycopg is None and shutil.which("psql") is None:
            raise RuntimeError("Install psycopg (`pip install psycopg[binary]`) or the psql client.")

    @property
    def backend(self) -> str:
        return "psycopg" if psycopg is not None else "psql"

    def _prefix(self) -> str:
        return f"SET search_path TO {self.search_path};\n"

    # -- statements -----------------------------------------------------------
    def execute(self, sql: str) -> None:
        """Run one or more statements (no parameters)."""
        if psycopg is not None:
            with psycopg.connect(self.url, autocommit=True) as conn:
                conn.execute(self._prefix() + sql)
        else:
            self._psql([], stdin=self._prefix() + sql)

    def execute_file(self, path: str | Path) -> None:
        self.execute(Path(path).read_text(encoding="utf-8"))

    # -- queries --------------------------------------------------------------
    def query(self, sql: str) -> pd.DataFrame:
        if psycopg is not None:
            with psycopg.connect(self.url, autocommit=True) as conn:
                conn.execute(self._prefix())
                cur = conn.execute(sql)
                cols = [d.name for d in cur.description]
                df = pd.DataFrame(cur.fetchall(), columns=cols)
            # NUMERIC arrives as Decimal; convert numeric-looking object columns.
            for c in df.columns:
                non_null = df[c].dropna()
                if df[c].dtype == object and len(non_null) and type(non_null.iloc[0]).__name__ == "Decimal":
                    df[c] = df[c].astype(float)
            return df
        out = self._psql(["--csv"], stdin=self._prefix() + sql, capture=True)
        # psql echoes "SET" for the first statement; drop it.
        text = out.split("\n", 1)[1] if out.startswith("SET") else out
        return pd.read_csv(io.StringIO(text)) if text.strip() else pd.DataFrame()

    def scalar(self, sql: str):
        df = self.query(sql)
        return df.iat[0, 0]

    # -- bulk load ------------------------------------------------------------
    def copy_csv(self, table: str, csv_path: str | Path) -> None:
        csv_path = Path(csv_path)
        header = csv_path.open(encoding="utf-8").readline().strip()
        cols = header  # CSV header uses the table's column names
        if psycopg is not None:
            with psycopg.connect(self.url, autocommit=True) as conn:
                conn.execute(self._prefix())
                with conn.cursor() as cur:
                    with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
                        with csv_path.open("rb") as fh:
                            while chunk := fh.read(1 << 20):
                                cp.write(chunk)
        else:
            self._psql(["-c", self._prefix(),
                        "-c", f"\\copy {table} ({cols}) FROM '{csv_path}' WITH (FORMAT csv, HEADER true)"])

    # -- internals ------------------------------------------------------------
    def _psql(self, args: list[str], stdin: str | None = None, capture: bool = False) -> str:
        cmd = ["psql", self.url, "-X", "-q", "-v", "ON_ERROR_STOP=1", *args]
        if stdin is not None:
            cmd += ["-f", "-"]
        res = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())
        return res.stdout if capture else ""
