"""Integration tests: the schema must reject every class of bad record."""
import pytest

from metro_analytics import audit

BAD_ROWS = {
    "underage operator": "INSERT INTO operator SELECT 999999, curp, first_name, last_name, second_last_name, sex, "
                         "birth_date, birth_state_code, birth_date + 3000, base_line_id, monthly_salary, status "
                         "FROM operator LIMIT 1",
    "wrong CURP check digit": "INSERT INTO operator VALUES (999999, 'HEGG560427MVZRRL05', 'Gloria', 'Hernández', "
                              "'García', 'M', '1956-04-27', 'VZ', '2000-01-01', 1, 15000, 'active')",
    "CURP sex mismatch": "INSERT INTO operator VALUES (999999, 'HEGG560427MVZRRL04', 'Gloria', 'Hernández', "
                         "'García', 'H', '1956-04-27', 'VZ', '2000-01-01', 1, 15000, 'active')",
    "overlapping shift": "INSERT INTO shift_assignment SELECT 99999999, operator_id, train_id, service_date, "
                         "shift_code, start_ts, end_ts FROM shift_assignment LIMIT 1",
    "overlapping maintenance": "INSERT INTO maintenance_event SELECT 99999999, train_id, depot_id, kind, start_date, "
                               "end_date FROM maintenance_event WHERE end_date IS NOT NULL LIMIT 1",
    "shift while in depot": """INSERT INTO shift_assignment
        SELECT 99999999, (SELECT min(operator_id) FROM operator WHERE status = 'leave'), m.train_id,
               m.start_date, 'M', m.start_date + TIME '05:00', m.start_date + TIME '13:00'
        FROM maintenance_event m CROSS JOIN snapshot s
        WHERE m.start_date >= s.as_of - 60 LIMIT 1""",
    "duplicate station name": "INSERT INTO station SELECT 99999, name, opens_at, closes_at, NULL, NULL "
                              "FROM station LIMIT 1",
}


@pytest.mark.parametrize("label", list(BAD_ROWS))
def test_constraint_rejects(db, label):
    with pytest.raises(Exception):
        db.execute(BAD_ROWS[label])


def test_audit_is_clean_on_generated_data(db):
    res = audit.run_audit(db.url, "metro")
    hard = res[res.severity != "info"]
    assert hard.violations.fillna(0).sum() == 0, hard[hard.violations > 0]


def test_views_return_rows(db):
    for view in ["v_train_status", "v_failure_spell", "v_line_kpi", "v_transfer_station",
                 "v_staffing_coverage", "v_operator_profile"]:
        assert int(db.scalar(f"SELECT count(*) FROM {view}")) > 0, view


def test_spells_cover_whole_history(db):
    # in-service days + depot days = calendar days for every train
    bad = db.scalar("""
        WITH x AS (
            SELECT t.train_id,
                   coalesce((SELECT sum(duration_days) FROM v_failure_spell f WHERE f.train_id = t.train_id), 0)
                 + depot_days(t.train_id, greatest(s.obs_start, make_date(t.build_year, 7, 1)), s.as_of)
                 - (s.as_of - greatest(s.obs_start, make_date(t.build_year, 7, 1))) AS diff
            FROM train t CROSS JOIN snapshot s)
        SELECT count(*) FROM x WHERE diff <> 0""")
    assert int(bad) == 0
