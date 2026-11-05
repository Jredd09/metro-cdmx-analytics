"""Project-wide paths and parameters."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
REFERENCE_DIR = ROOT / "data" / "reference"
GENERATED_DIR = ROOT / "data" / "generated"
LEGACY_DIR = ROOT / "data" / "legacy"          # course files; git-ignored, not redistributed
FIGURES_DIR = ROOT / "figures"
REPORTS_DIR = ROOT / "reports"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/metro")

SEED = 2025
OBS_START = date(2019, 1, 1)        # start of the maintenance history
AS_OF = date(2025, 12, 31)          # snapshot date ("today" for the dataset)
SHIFT_WINDOW_START = date(2025, 10, 1)

# Ground-truth parameters of the simulated failure process (Weibull proportional
# hazards). They are kept here so the analysis can check that the estimators
# recover them — the main reason to simulate instead of inventing numbers.
TRUE_WEIBULL_SHAPE = 1.35
TRUE_WEIBULL_SCALE_DAYS = 420.0     # baseline: Bombardier, brand-new, regular line
TRUE_BETAS = {
    "manufacturer_Alstom": 0.25,
    "manufacturer_CAF": -0.20,
    "manufacturer_CRRC": -0.40,
    "age_decades": 0.30,
    "high_ridership": 0.35,
}
PREVENTIVE_INTERVAL_DAYS = 180
