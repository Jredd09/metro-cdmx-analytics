# Metro CDMX Analytics

[![CI](https://github.com/Jredd09/metro-cdmx-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/Jredd09/metro-cdmx-analytics/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-4169E1?logo=postgresql&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

An end-to-end data project on Mexico City's Metro: a **constrained PostgreSQL model** of the real
12-line network, a **data-quality audit** that runs as SQL, a **reproducible synthetic-data
generator**, and a **fleet reliability analysis** with survival models built from first principles
(Kaplan-Meier, log-rank, Cox and Weibull proportional hazards) that ends in a 2026 failure forecast
and a maintenance-policy recommendation.

> **About the data.** The network (12 lines, 163 stations, 195 station–line links) is real.
> Fleet composition, failures, staff and shifts are **simulated** from documented assumptions,
> because Metro maintenance and HR records are not public. Simulating has a payoff: the true
> parameters are known, so every estimator is checked against them (see
> [Validation](#validation-do-the-models-recover-the-truth)).

---

## Key results

| | |
|---|---|
| **Data quality** | Audit of the original course dataset: **20 SQL rules**, 1,650 operators — **87% of CURPs structurally invalid**, **182 underage drivers**, **28 duplicated stations**, one driver booked on **378 trains at the same time**. The rebuilt database passes all 20 rules with **0 violations**, enforced by constraints rather than by convention. |
| **Reliability drivers** | Each extra decade of train age raises the hazard of an unplanned failure by **37%**; high-ridership lines by **46%**. Only **17%** of Alstom spells reach the next scheduled service without a breakdown, against **74%** for the newer CRRC fleet. |
| **2026 forecast** | **705 unplanned failures** (80% interval 674–736) vs. 694 observed in 2025, from a Monte Carlo that starts from each train's state on 31 Dec 2025 and propagates parameter uncertainty. |
| **Maintenance policy** | The current 180-day preventive interval is optimal only if a breakdown costs less than **3.5 train-days** of depot time. At 5 train-days, **150 days** is optimal and avoids ~**33 failures a year**. |
| **Workforce** | 96.4% of available train-shifts were staffed in Q4 2025; Line A (87%) and Line 2 (88%) carry most of the **~38 unstaffed train-shifts per day**. Pay-equity regression: adjusted gender gap **−$8/month (95% CI −$93 to +$78)**, i.e. none. |

![Audit of the original dataset](figures/audit_legacy.png)

---

## Pipeline

```mermaid
flowchart LR
    R[Reference data<br/>real network CSVs] --> G[Generator<br/>numpy, seeded]
    G --> L[(PostgreSQL<br/>schema metro<br/>constraints + triggers)]
    X[Original course SQL<br/>not redistributed] --> S[(schema staging<br/>no constraints)]
    L --> A[Audit<br/>20 SQL rules]
    S --> A
    L --> V[SQL views<br/>spells, KPIs, coverage]
    V --> M[Survival models<br/>KM · log-rank · Cox · Weibull]
    M --> F[Monte Carlo<br/>forecast + PM policy]
    A --> O[reports/ + figures/]
    F --> O
```

One command rebuilds everything: `python -m metro_analytics all` (≈45 s).

---

## 1. Data model

The schema ([`sql/01_schema.sql`](sql/01_schema.sql), diagram in [`docs/data_model.md`](docs/data_model.md))
pushes business rules into the database so bad data cannot be loaded:

* **Exclusion constraints** (`btree_gist`) make overlapping shifts, double-booked trains and
  overlapping maintenance impossible.
* A **trigger** rejects a shift on a train that is in the depot that day.
* **CURP validation in SQL**: structure, RENAPO check digit, and consistency with birth date, sex
  and birth state.
* Physical stations are stored once; an ordered many-to-many table links them to lines, so
  transfer stations (Pantitlán → lines 1, 5, 9, A) are queryable.
* Train availability is **derived** from maintenance records, and every view reads "today" from
  a `snapshot` table, so results don't drift with the load date.

## 2. Data-quality audit

[`src/metro_analytics/audit.py`](src/metro_analytics/audit.py) holds 20 rules written against the
new column names. The original course data is migrated **1:1, without fixing anything**, into an
unconstrained `staging` schema ([`sql/legacy/migrate_to_staging.sql`](sql/legacy/migrate_to_staging.sql)),
so the same rules run on both datasets. Full table: [`reports/data_quality_audit.md`](reports/data_quality_audit.md).

| Finding in the original data | Records |
|---|---:|
| CURP check digit wrong | 1,492 / 1,650 |
| Birth date ≠ date encoded in CURP | 1,601 |
| Drivers younger than 18 | 182 |
| Shifts overlapping for the same driver (one driver on 378 trains at once) | 378 |
| Records stamped with `CURRENT_DATE` (dataset changes every load) | 383 |
| Transfer stations duplicated per line | 28 |
| Real stations missing (e.g. Villa de Cortés, Auditorio) | 4 |
| Night shifts with negative duration (`TIME` without date) | 2 |
| Operators who never drove a train | 1,641 / 1,650 |

The main lesson for the redesign: most of these are **modelling** problems (time without date,
duplicated entities, redundant flags), not typos, so the fix is a better schema, not cleaning.

## 3. Fleet reliability

Maintenance history is turned into **in-service spells** in SQL (`v_failure_spell`): the time a
train runs between leaving the depot and its next visit. A spell ending in corrective
maintenance is a failure; one ending in preventive maintenance or at the end of the data is
**right-censored**. 7,514 spells, 4,378 failures, 42% censored.

| | |
|---|---|
| ![KM by manufacturer](figures/km_by_manufacturer.png) | ![KM by age](figures/km_by_age.png) |

Log-rank tests: manufacturer χ²(3) = 1,064, age band χ²(2) = 694 (both p < 10⁻¹⁵⁰).

![Hazard ratios](figures/hazard_ratios.png)

All estimators live in [`analysis/survival.py`](src/metro_analytics/analysis/survival.py), written
with NumPy/SciPy: Kaplan-Meier with Greenwood variance and log(−log) intervals, k-sample log-rank,
Cox PH with Breslow ties by Newton-Raphson, and a Weibull PH model by maximum likelihood with
standard errors from the observed information matrix.

### Line KPIs (`v_line_kpi`) and 2026 forecast

| Line | Fleet | Avg age (y) | Failures / train-year | MTBF (days) | Availability 2025 | Failures 2025 | Forecast 2026 (P10–P90) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 50 | 15.2 | 1.25 | 280 | 95.4% | 66 | 66 (57–76) |
| 2 | 38 | 42.4 | 2.67 | 126 | 90.4% | 126 | 111 (100–122) |
| 3 | 47 | 33.4 | 1.97 | 174 | 92.3% | 101 | 100 (89–112) |
| 4 | 12 | 48.3 | 2.54 | 133 | 91.4% | 32 | 34 (28–40) |
| 5 | 20 | 44.2 | 1.77 | 194 | 92.6% | 40 | 42 (35–49) |
| 6 | 14 | 44.1 | 1.61 | 214 | 93.4% | 25 | 25 (19–31) |
| 7 | 33 | 42.0 | 1.75 | 196 | 94.4% | 61 | 66 (57–75) |
| 8 | 30 | 39.2 | 2.74 | 123 | 91.5% | 81 | 89 (79–100) |
| 9 | 30 | 32.0 | 1.06 | 332 | 95.6% | 30 | 38 (31–46) |
| A | 30 | 29.3 | 1.12 | 315 | 96.4% | 39 | 35 (28–42) |
| B | 38 | 27.6 | 1.50 | 233 | 95.2% | 63 | 66 (57–76) |
| 12 | 30 | 14.5 | 0.93 | 378 | 97.1% | 30 | 32 (25–40) |

![Forecast](figures/forecast_2026.png)

The forecast draws each train's next failure from the fitted Weibull **conditional on the time it
has already run** (residual-life distribution), applies the preventive schedule, samples repair
times from the observed history, and draws the model parameters from their asymptotic normal
distribution in each of 1,000 runs.

### Preventive-maintenance interval

![PM policy](figures/pm_policy.png)

Shorter intervals prevent breakdowns but cost depot time. Minimising
`depot days + c × breakdowns` for a range of breakdown costs `c` turns this into a decision
rule: keep 180 days if a breakdown costs under ~3.5 train-days; move to 150 days at 4–5; below
110 days only if it costs more than ~7.5.

### Validation: do the models recover the truth?

Because the data is simulated, the true parameters are known. On this dataset both models land
on the true hazard ratios ([`reports/parameter_recovery.csv`](reports/parameter_recovery.csv)).
The Weibull shape here is 1.41 vs. a true 1.35, and its 95% CI just misses it. A single CI should
miss about 1 time in 20, so the repository re-simulates the history with **30 different seeds**
([`reports/recovery_study.csv`](reports/recovery_study.csv)):

| Parameter | True | Mean estimate (30 seeds) | 95% CI coverage |
|---|---:|---:|---:|
| Weibull shape | 1.35 | 1.351 | 93% |
| Alstom vs Bombardier (log-HR) | 0.25 | 0.251 | 100% |
| CAF vs Bombardier | −0.20 | −0.213 | 97% |
| CRRC vs Bombardier | −0.40 | −0.423 | 97% |
| Age, per decade | 0.30 | 0.297 | 93% |
| High-ridership line | 0.35 | 0.346 | 100% |

The estimators are unbiased and their intervals are calibrated.

## 4. Workforce

| | |
|---|---|
| ![Staffing](figures/staffing_coverage.png) | ![Salary](figures/salary_tenure.png) |

## SQL showcase

[`sql/04_business_queries.sql`](sql/04_business_queries.sql) answers ten operational questions
using CTEs, window functions (`LAG`, `LEAD`, `RANK`, `ROW_NUMBER`), `FILTER`, range types and
`generate_series`: least reliable lines, worst trains ranked within their line, MTBF by model,
year-over-year failure growth, transfer stations, staffing gaps, succession risk (drivers 58+),
**repeat-failure rate within 30 days of a repair** (13.7% Alstom vs 1.6% CRRC), depot pressure,
and a block showing the constraints rejecting bad inserts.

## Quick start

```bash
git clone https://github.com/Jredd09/metro-cdmx-analytics.git
cd metro-cdmx-analytics
pip install -r requirements.txt
docker compose up -d --wait                        # PostgreSQL 16 (or use your own)
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/metro
PYTHONPATH=src python -m metro_analytics all       # build → audit → analyse
PYTHONPATH=src pytest -q                           # 27 tests
```

Or with `make db all test`. Without `psycopg`, the code falls back to the `psql` client.

## Repository layout

```
sql/
  01_schema.sql            tables, constraints, CURP functions, trigger
  02_views.sql             analytical views (spells, KPIs, occupancy, coverage)
  03_indexes.sql
  04_business_queries.sql  ten business questions
  legacy/                  mapping of the original model into `staging`
src/metro_analytics/
  generate.py              seeded synthetic data (Weibull PH failures, valid CURPs, rosters)
  curp.py                  RENAPO CURP construction and check digit
  audit.py                 20 data-quality rules + report
  analysis/survival.py     KM, log-rank, Cox, Weibull — from scratch
  analysis/reliability.py  figures, forecast, PM policy, recovery study
  analysis/operations.py   staffing coverage, pay equity, depot capacity
data/reference/            real network: lines, stations, states, services; fleet assumptions
reports/                   audit report, metrics.json, CSV outputs
tests/                     unit + database integration tests (run in CI)
```

## Assumptions and limitations

* **Synthetic operations data.** Fleet mixes by line are stylised. Failures follow a Weibull PH
  model with parameters in [`config.py`](src/metro_analytics/config.py), 180-day preventive
  maintenance, and log-normal repair times. Drivers, salaries and rosters are generated. The
  numbers describe the simulated system, not the real Metro; the methods carry over unchanged.
* Age is taken at the start of each spell (not time-varying within it), matching the simulator.
* The breakdown cost in the PM analysis is a policy input, so results are shown across a range.
* The legacy audit needs the original course SQL files in `data/legacy/`. They are not
  redistributed; without them the pipeline audits the clean data only.

## Origin

This started as a team final project for *Manejo de Datos* (Data Management), Facultad de
Ciencias, UNAM, 2025: a 12-table PostgreSQL model of the Metro filled with Mockaroo data. This
repository is an independent rebuild and extension. The schema was redesigned, and the data
generator, audit, analysis, tests and CI are new. The original files are not included; the
original data appears only as aggregate audit counts.

## Tools

Python (NumPy, pandas, SciPy, Matplotlib), PostgreSQL 16, pytest, GitHub Actions and Docker.

## Resumen en español

Proyecto de datos de punta a punta sobre el Metro de la CDMX: modelo relacional en PostgreSQL con
reglas de negocio forzadas por la base de datos (restricciones de exclusión, trigger, validación
de CURP), auditoría de calidad de datos en SQL, generador de datos sintéticos reproducible y
análisis de confiabilidad de la flota con modelos de supervivencia implementados desde cero
(Kaplan-Meier, log-rank, Cox y Weibull). Incluye un pronóstico de fallas para 2026 por Monte
Carlo y una recomendación de intervalo de mantenimiento preventivo.

---

**Jared González** · Actuarial Science, Facultad de Ciencias UNAM ·
[GitHub](https://github.com/Jredd09)
