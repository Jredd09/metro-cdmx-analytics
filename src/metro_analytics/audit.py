"""Data-quality audit.

Each rule is a SQL query that returns the offending records. Rules are written
against the column names of the new model, so the same rule set runs on the
migrated legacy data (schema ``staging``) and on the clean data (``metro``).
Helper functions (CURP check digit, name normalisation) live in ``metro``.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config as C
from .db import Database

NORMALIZE_FN = r"""
CREATE OR REPLACE FUNCTION metro.normalize_station_name(n TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(a.canonical, x.k)
    FROM (SELECT btrim(regexp_replace(regexp_replace(
                 translate(lower(n), 'áéíóúüñ.', 'aeiouun '),
                 '\s+l\s*([0-9]+|a|b)\s*$', ''),          -- legacy line suffix: 'Pantitlán L5'
                 '\s+', ' ', 'g')) AS k) x
    LEFT JOIN (VALUES                                     -- alternative official/common names
        ('uam-azcapotzalco', 'azcapotzalco'),
        ('la villa', 'la villa-basilica'),
        ('constitucion 1917', 'constitucion de 1917'),
        ('hospital 20 nov', 'hospital 20 de noviembre'),
        ('blvd puerto aereo', 'boulevard puerto aereo'),
        ('m a quevedo', 'miguel angel de quevedo')
    ) AS a(alias, canonical) ON a.alias = x.k
$$;
"""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    severity: str           # critical / major / minor / info
    title: str
    sql: str
    requires: tuple[str, str] | None = None   # (table, column) that must exist


RULES: list[Rule] = [
    # ---------------------------------------------------------------- identity
    Rule("ID-01", "Identity", "critical", "Operator is younger than 18 on the snapshot date",
         "SELECT operator_id FROM operator, snapshot s WHERE birth_date > s.as_of - INTERVAL '18 years'"),
    Rule("ID-02", "Identity", "major", "Operator older than 75 (implausible for an active driver)",
         "SELECT operator_id FROM operator, snapshot s WHERE birth_date < s.as_of - INTERVAL '75 years'"),
    Rule("ID-03", "Identity", "major", "CURP does not follow the official 18-character structure",
         "SELECT operator_id FROM operator WHERE curp !~ "
         "'^[A-Z][AEIOUX][A-Z]{2}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$'"),
    Rule("ID-04", "Identity", "major", "CURP check digit (position 18) is wrong",
         "SELECT operator_id FROM operator WHERE curp ~ '^[A-Z0-9]{17}[0-9]$' "
         "AND substr(curp, 18, 1) <> metro.curp_check_digit(curp)"),
    Rule("ID-05", "Identity", "major", "Birth date does not match the date encoded in the CURP",
         "SELECT operator_id FROM operator WHERE curp ~ '^[A-Z]{4}[0-9]{6}' "
         "AND substr(curp, 5, 6) <> to_char(birth_date, 'YYMMDD')"),
    Rule("ID-06", "Identity", "major", "Sex does not match the sex encoded in the CURP",
         "SELECT operator_id FROM operator WHERE substr(curp, 11, 1) IN ('H','M') "
         "AND substr(curp, 11, 1) IS DISTINCT FROM sex"),
    Rule("ID-07", "Identity", "minor", "State does not match the birth state encoded in the CURP",
         "SELECT operator_id FROM operator WHERE birth_state_code IS NOT NULL "
         "AND substr(curp, 12, 2) <> birth_state_code"),
    # ---------------------------------------------------------------- network
    Rule("NW-01", "Network", "major", "Same physical station stored as several stations",
         "SELECT metro.normalize_station_name(name) AS station, count(*) AS copies FROM station "
         "GROUP BY 1 HAVING count(*) > 1"),
    Rule("NW-02", "Network", "major", "Station not linked to any line",
         "SELECT station_id FROM station s WHERE NOT EXISTS "
         "(SELECT 1 FROM line_station ls WHERE ls.station_id = s.station_id)"),
    Rule("NW-03", "Network", "major", "Station of the real network missing from a line",
         """SELECT rl.line_code, rs.name
            FROM metro.line_station rls
            JOIN metro.line rl ON rl.line_id = rls.line_id
            JOIN metro.station rs ON rs.station_id = rls.station_id
            WHERE NOT EXISTS (
                SELECT 1 FROM line_station x JOIN station y ON y.station_id = x.station_id
                JOIN line l ON l.line_id = x.line_id
                WHERE l.line_code = rl.line_code
                  AND metro.normalize_station_name(y.name) = metro.normalize_station_name(rs.name))"""),
    Rule("NW-04", "Network", "minor", "Entrance open outside its station's operating hours",
         """SELECT e.entrance_id FROM station_entrance e JOIN station s USING (station_id)
            WHERE e.opens_at < s.opens_at
               OR (CASE WHEN e.closes_at = '00:00' THEN 1440 ELSE extract(epoch FROM e.closes_at)/60 END)
                > (CASE WHEN s.closes_at = '00:00' THEN 1440 ELSE extract(epoch FROM s.closes_at)/60 END)"""),
    # ---------------------------------------------------------------- operations
    Rule("OP-01", "Operations", "critical", "Shift with zero or negative duration (overnight shift stored without a date)",
         "SELECT shift_id FROM shift_assignment WHERE end_ts <= start_ts"),
    Rule("OP-02", "Operations", "critical", "Operator assigned to overlapping shifts",
         """SELECT DISTINCT a.shift_id FROM shift_assignment a JOIN shift_assignment b
            ON a.operator_id = b.operator_id AND a.shift_id <> b.shift_id
           AND tsrange(a.start_ts, greatest(a.end_ts, a.start_ts + INTERVAL '1 minute'))
            && tsrange(b.start_ts, greatest(b.end_ts, b.start_ts + INTERVAL '1 minute'))"""),
    Rule("OP-03", "Operations", "critical", "Train driven by two operators at the same time",
         """SELECT DISTINCT a.shift_id FROM shift_assignment a JOIN shift_assignment b
            ON a.train_id = b.train_id AND a.shift_id <> b.shift_id
           AND tsrange(a.start_ts, greatest(a.end_ts, a.start_ts + INTERVAL '1 minute'))
            && tsrange(b.start_ts, greatest(b.end_ts, b.start_ts + INTERVAL '1 minute'))"""),
    Rule("OP-04", "Operations", "critical", "Shift on a train that was in the depot that day",
         """SELECT s.shift_id FROM shift_assignment s JOIN maintenance_event m USING (train_id)
            WHERE s.service_date >= m.start_date AND (m.end_date IS NULL OR s.service_date < m.end_date)"""),
    Rule("OP-05", "Operations", "major", "Train in two maintenance events at the same time",
         """SELECT DISTINCT a.event_id FROM maintenance_event a JOIN maintenance_event b
            ON a.train_id = b.train_id AND a.event_id <> b.event_id
           AND daterange(a.start_date, a.end_date, '[)') && daterange(b.start_date, b.end_date, '[)')"""),
    Rule("OP-06", "Operations", "major", "Maintenance ends before it starts",
         "SELECT event_id FROM maintenance_event WHERE end_date <= start_date"),
    Rule("OP-07", "Operations", "major", "Train status flag contradicts maintenance records",
         """SELECT t.train_id FROM train t CROSS JOIN snapshot s
            WHERE t.legacy_in_service = EXISTS (
                SELECT 1 FROM maintenance_event m WHERE m.train_id = t.train_id
                AND m.start_date <= s.as_of AND (m.end_date IS NULL OR m.end_date > s.as_of))""",
         requires=("train", "legacy_in_service")),
    Rule("OP-08", "Operations", "major", "Record dated after the snapshot (stamped with CURRENT_DATE at load time)",
         """SELECT 'shift' AS kind, shift_id AS id FROM shift_assignment, snapshot s WHERE service_date > s.as_of
            UNION ALL
            SELECT 'maintenance', event_id FROM maintenance_event, snapshot s WHERE start_date > s.as_of"""),
    Rule("OP-09", "Operations", "minor", "Train build year outside 1969 (Metro opening) .. snapshot year",
         "SELECT train_id FROM train, snapshot s WHERE build_year < 1969 OR build_year > extract(year FROM s.as_of)"),
    Rule("OP-10", "Operations", "info", "Active operator with no shift in the dataset",
         """SELECT operator_id FROM operator o WHERE status = 'active'
            AND NOT EXISTS (SELECT 1 FROM shift_assignment s WHERE s.operator_id = o.operator_id)"""),
]


def _has_column(db: Database, schema: str, table: str, column: str) -> bool:
    n = db.scalar(f"SELECT count(*) FROM information_schema.columns WHERE table_schema = '{schema}' "
                  f"AND table_name = '{table}' AND column_name = '{column}'")
    return int(n) > 0


def run_audit(db_url: str | None, schema: str) -> pd.DataFrame:
    base = Database(db_url)
    base.execute(NORMALIZE_FN)
    db = Database(db_url, search_path=f"{schema}, metro, public")
    rows = []
    for r in RULES:
        if r.requires and not _has_column(base, schema, *r.requires):
            rows.append({**r.__dict__, "violations": None, "population": None})
            continue
        n = int(db.scalar(f"SELECT count(*) FROM ({r.sql}) q"))
        rows.append({**r.__dict__, "violations": n})
    out = pd.DataFrame(rows).drop(columns=["sql", "requires"])
    return out


def profile(db_url: str | None, schema: str) -> dict[str, int]:
    db = Database(db_url, search_path=f"{schema}, metro, public")
    tables = ["station", "line_station", "train", "maintenance_event", "operator", "shift_assignment"]
    return {t: int(db.scalar(f"SELECT count(*) FROM {t}")) for t in tables}


def write_report(legacy: pd.DataFrame | None, clean: pd.DataFrame, legacy_profile, clean_profile) -> str:
    lines = [
        "# Data-quality audit",
        "",
        "Generated by `python -m metro_analytics audit`. Each rule is a SQL query in",
        "`src/metro_analytics/audit.py`; the count is the number of offending records.",
        "",
        "* **Legacy** — the original course dataset (Mockaroo + hand-written inserts), migrated",
        "  1:1 into the new model without constraints (`sql/legacy/migrate_to_staging.sql`).",
        "* **Clean** — the dataset produced by this repository's generator and loaded into the",
        "  fully constrained schema.",
        "",
        "## Row counts",
        "",
        "| Table | Legacy | Clean |",
        "|---|---:|---:|",
    ]
    for t, n in clean_profile.items():
        lv = legacy_profile.get(t, "–") if legacy_profile else "–"
        lines.append(f"| `{t}` | {lv:,} | {n:,} |" if isinstance(lv, int) else f"| `{t}` | {lv} | {n:,} |")
    lines += ["", "## Rule results", "", "| Rule | Severity | Check | Legacy | Clean |", "|---|---|---|---:|---:|"]
    leg = legacy.set_index("rule_id") if legacy is not None else None
    for r in clean.itertuples():
        lv = "–"
        if leg is not None:
            v = leg.loc[r.rule_id, "violations"]
            lv = "n/a" if pd.isna(v) else f"{int(v):,}"
        cv = "n/a" if pd.isna(r.violations) else f"{int(r.violations):,}"
        lines.append(f"| {r.rule_id} | {r.severity} | {r.title} | {lv} | {cv} |")
    lines += [
        "",
        "`n/a`: the rule needs a column that only exists in that dataset (the legacy status flag).",
        "`OP-10` is informational: in the clean data the only operators without shifts are those",
        "hired after the shift window or whose rest pattern left them unscheduled; in the legacy",
        "data almost no operator was ever assigned a shift.",
        "",
    ]
    return "\n".join(lines)
