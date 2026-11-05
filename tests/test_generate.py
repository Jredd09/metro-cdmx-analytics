from datetime import date

import pandas as pd

from metro_analytics import curp
from metro_analytics.generate import Generator


def test_generator_is_deterministic(tables):
    other = Generator(2025).run()
    for name in ("operator", "maintenance_event", "shift_assignment"):
        pd.testing.assert_frame_equal(tables[name], other[name])


def test_network_matches_real_metro(tables):
    assert len(tables["line"]) == 12
    assert len(tables["line_station"]) == 195     # station-line memberships
    assert len(tables["station"]) == 163          # physical stations


def test_operators_are_adults_with_valid_unique_curps(tables):
    op = tables["operator"]
    assert op.curp.is_unique
    assert op.curp.map(curp.is_valid).all()
    age_at_hire = (pd.to_datetime(op.hire_date) - pd.to_datetime(op.birth_date)).dt.days / 365.25
    assert (age_at_hire >= 18).all()


def test_no_overlapping_shifts(tables):
    s = tables["shift_assignment"]
    assert not s.duplicated(["operator_id", "service_date"]).any()
    assert not s.duplicated(["train_id", "service_date", "shift_code"]).any()


def test_no_shift_while_train_in_depot(tables):
    s = tables["shift_assignment"]
    m = tables["maintenance_event"]
    j = s.merge(m, on="train_id")
    end = j.end_date.fillna(date(2100, 1, 1))
    clash = (j.service_date >= j.start_date) & (j.service_date < end)
    assert not clash.any()


def test_maintenance_never_overlaps(tables):
    m = tables["maintenance_event"].sort_values(["train_id", "start_date"])
    prev_end = m.groupby("train_id").end_date.shift()
    assert (prev_end.isna() | (m.start_date >= prev_end)).all()
