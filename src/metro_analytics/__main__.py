"""Command-line entry point.

    python -m metro_analytics all          # build DB, audit, analyse (default)
    python -m metro_analytics build        # generate data + create schema + load
    python -m metro_analytics audit        # data-quality audit (legacy + clean)
    python -m metro_analytics analyze      # reliability + operations analysis
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from . import audit, config as C, legacy
from .analysis import operations, reliability
from .db import Database
from .generate import Generator, write_csvs
from .load import init_schema, load_generated


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build(db: Database) -> dict:
    _log("generating synthetic data")
    write_csvs(Generator(C.SEED).run())
    _log("creating schema and loading (constraints validate every row)")
    init_schema(db)
    counts = load_generated(db)
    for t, n in counts.items():
        _log(f"  {t:<20} {n:>8,}")
    return counts


def run_audit(db: Database) -> dict:
    C.REPORTS_DIR.mkdir(exist_ok=True)
    clean = audit.run_audit(db.url, "metro")
    clean_profile = audit.profile(db.url, "metro")
    legacy_res, legacy_profile = None, None
    if legacy.available():
        _log("loading and migrating the legacy course dataset")
        legacy.load_and_migrate(db)
        legacy_res = audit.run_audit(db.url, "staging")
        legacy_profile = audit.profile(db.url, "staging")
        operations.audit_figure(legacy_res)
    else:
        _log("legacy files not found in data/legacy/ — auditing the clean data only")
    report = audit.write_report(legacy_res, clean, legacy_profile, clean_profile)
    (C.REPORTS_DIR / "data_quality_audit.md").write_text(report, encoding="utf-8")
    _log("wrote reports/data_quality_audit.md")
    out = {"clean_total_violations": int(clean.loc[clean.severity != "info", "violations"].fillna(0).sum())}
    if legacy_res is not None:
        out["legacy"] = {r.rule_id: (None if np.isnan(r.violations) else int(r.violations))
                         for r in legacy_res.itertuples()}
        out["legacy_profile"] = legacy_profile
    return out


def analyze(db: Database, recovery_seeds: int) -> dict:
    _log("reliability analysis")
    rel = reliability.run(db, n_recovery_seeds=recovery_seeds)
    _log("operations analysis")
    ops = operations.run(db)
    return {"reliability": rel, "operations": ops}


def main() -> None:
    p = argparse.ArgumentParser(prog="metro_analytics")
    p.add_argument("command", nargs="?", default="all", choices=["all", "build", "audit", "analyze"])
    p.add_argument("--recovery-seeds", type=int, default=30,
                   help="re-simulations for the parameter-recovery study (0 to skip)")
    args = p.parse_args()
    db = Database()
    _log(f"database backend: {db.backend}")
    metrics_path = C.REPORTS_DIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    if args.command in ("all", "build"):
        metrics["rows"] = build(db)
    if args.command in ("all", "audit"):
        metrics["audit"] = run_audit(db)
    if args.command in ("all", "analyze"):
        metrics.update(analyze(db, args.recovery_seeds))
    C.REPORTS_DIR.mkdir(exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    _log("done — figures/ and reports/ updated")


if __name__ == "__main__":
    main()
