# Data model

PostgreSQL schema `metro` — 14 tables, 7 analytical views. DDL in
[`sql/01_schema.sql`](../sql/01_schema.sql), views in [`sql/02_views.sql`](../sql/02_views.sql).

```mermaid
erDiagram
    LINE ||--o{ LINE_STATION : "has (ordered)"
    STATION ||--o{ LINE_STATION : "belongs to"
    STATION ||--o{ STATION_ENTRANCE : has
    STATION ||--o{ STATION_CONNECTION : "connects to"
    TRANSIT_SERVICE ||--o{ STATION_CONNECTION : serves
    STATION ||--o{ DEPOT : hosts
    LINE ||--o{ TRAIN : runs
    DEPOT ||--o{ TRAIN : "home of"
    TRAIN ||--o{ MAINTENANCE_EVENT : undergoes
    DEPOT ||--o{ MAINTENANCE_EVENT : performs
    LINE ||--o{ OPERATOR : "base line"
    STATE ||--o{ OPERATOR : "born in"
    OPERATOR ||--o{ OPERATOR_PHONE : has
    OPERATOR ||--o{ SHIFT_ASSIGNMENT : works
    TRAIN ||--o{ SHIFT_ASSIGNMENT : "driven in"

    LINE {
        smallint line_id PK
        varchar line_code UK
        boolean high_ridership
    }
    STATION {
        int station_id PK
        varchar name UK
        time opens_at
        time closes_at
    }
    LINE_STATION {
        smallint line_id PK
        int station_id PK
        smallint seq
    }
    TRAIN {
        int train_id PK
        smallint line_id FK
        varchar manufacturer
        smallint build_year
    }
    MAINTENANCE_EVENT {
        int event_id PK
        int train_id FK
        varchar kind
        date start_date
        date end_date
    }
    OPERATOR {
        int operator_id PK
        char curp UK
        char sex
        date birth_date
        date hire_date
    }
    SHIFT_ASSIGNMENT {
        bigint shift_id PK
        int operator_id FK
        int train_id FK
        timestamp start_ts
        timestamp end_ts
    }
```

## Integrity rules enforced by the database

| Rule | Mechanism |
|---|---|
| One row per physical station; transfer stations link to several lines | `UNIQUE (name)` + M:N `line_station` with `UNIQUE (line_id, seq)` |
| A train is never in two maintenance events at once | `EXCLUDE USING gist (train_id WITH =, daterange(...) WITH &&)` |
| An operator never drives two trains at once | `EXCLUDE USING gist (operator_id WITH =, tsrange(...) WITH &&)` |
| A train never has two drivers at once | `EXCLUDE USING gist (train_id WITH =, tsrange(...) WITH &&)` |
| No shift on a train that is in the depot that day | `BEFORE INSERT` trigger `trg_block_shift_on_maintenance` |
| Overnight shifts are valid; shifts last 0–12 h | `TIMESTAMP` start/end + `CHECK` |
| Operators are adults when hired | `CHECK (hire_date >= birth_date + 18 years)` |
| CURP is structurally valid with the right check digit | `CHECK (curp_is_valid(curp))` — SQL implementation of RENAPO's algorithm |
| CURP agrees with birth date, sex and birth state | three `CHECK` constraints |
| "Today" is fixed, so every result is reproducible | one-row `snapshot` table read by all views |

## Views

| View | Purpose |
|---|---|
| `v_failure_spell` | Survival dataset: one row per in-service spell, failure flag, age at start (built with `LAG`/`ROW_NUMBER`) |
| `v_line_kpi` | Fleet, age, failures per train-year, MTBF, 12-month availability per line |
| `v_train_status` | Current status of each train, derived from maintenance records |
| `v_transfer_station` | Stations served by more than one line |
| `v_depot_occupancy` | Daily trains-in-depot vs. bays |
| `v_staffing_coverage` | Share of available trains that received a driver, per day/line/shift |
| `v_operator_profile` | Age, tenure, salary and hours worked per operator |

## Changes from the original course model

| Original | Problem | Now |
|---|---|---|
| Transfer stations stored once per line (`Pantitlán`, `Pantitlán L5`, `Pantitlán L9`, `Pantitlán LA`) | 28 duplicated stations; "which stations are transfers?" could not be answered | Physical station table + ordered M:N membership |
| Shift end stored as `TIME` | Night shifts (22:00 → 05:00) had negative duration | `TIMESTAMP` range with exclusion constraints |
| `Tren.Estatus` boolean | Could contradict maintenance records | Derived in `v_train_status` |
| Dates stamped with `CURRENT_DATE` | Dataset changed every time it was loaded | Fixed snapshot date |
| CURP as free text | 87% of CURPs structurally invalid | Validated in the database |
| `Conductor.Estado` | Comment says "employment status", data holds a state name | `birth_state_code` (FK) checked against the CURP, plus a separate `status` |
