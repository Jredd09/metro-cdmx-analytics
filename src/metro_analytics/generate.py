"""Deterministic synthetic data generator.

Everything that is not public network information (fleet ages, failures,
staff, shifts) is simulated from explicit, documented assumptions so that the
data is internally consistent and passes every database constraint. The
failure process is a Weibull proportional-hazards model whose true parameters
live in ``config.py``; the analysis later checks it can recover them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import curp
from .names import FEMALE_NAMES, MALE_NAMES, SURNAMES

LINE_IDS = {"A": 10, "B": 11}
SHIFT_TIMES = {"M": (time(5), 0, time(13)), "V": (time(13), 0, time(21)), "N": (time(21), 1, time(5))}
HIGH_RIDERSHIP = "high_ridership"


def line_id(code: str) -> int:
    return int(code) if code.isdigit() else LINE_IDS[code]


def age_years(day: date, build_year: int) -> float:
    """Train age on ``day`` assuming mid-year delivery. Mirrored in SQL (02_views.sql)."""
    return (day - date(build_year, 7, 1)).days / 365.25


class Generator:
    def __init__(self, seed: int = C.SEED):
        self.rng = np.random.default_rng(seed)
        self.used_phones: set[str] = set()
        self.tables: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ helpers
    def _phone(self) -> str:
        while True:
            p = "55" + "".join(map(str, self.rng.integers(0, 10, 8)))
            if p not in self.used_phones:
                self.used_phones.add(p)
                return p

    def _person_name(self, sex: str) -> tuple[str, str, str]:
        pool = MALE_NAMES if sex == "H" else FEMALE_NAMES
        return (str(self.rng.choice(pool)), str(self.rng.choice(SURNAMES)), str(self.rng.choice(SURNAMES)))

    # ------------------------------------------------------------------ network
    def network(self) -> None:
        ref = C.REFERENCE_DIR
        states = pd.read_csv(ref / "states.csv")
        lines = pd.read_csv(ref / "lines.csv", dtype=str)
        ls = pd.read_csv(ref / "line_stations.csv", dtype={"line_code": str})
        depots = pd.read_csv(ref / "depots.csv")
        services = pd.read_csv(ref / "services.csv")

        fleet = pd.read_csv(ref / "fleet.csv", dtype={"line_code": str})
        high = fleet.groupby("line_code").high_ridership.max().astype(bool)
        line = pd.DataFrame({
            "line_id": lines.line_code.map(line_id), "line_code": lines.line_code,
            "name": lines.name, "color": lines.color, "hex_color": lines.hex.str.upper(),
            "high_ridership": lines.line_code.map(high),
        })

        names = list(dict.fromkeys(ls.station_name))          # first-appearance order
        station_id = {n: i for i, n in enumerate(names, start=1)}
        managers = []
        for _ in names:
            sex = "H" if self.rng.random() < 0.6 else "M"
            f, l1, l2 = self._person_name(sex)
            managers.append(f"{f} {l1} {l2}")
        station = pd.DataFrame({
            "station_id": range(1, len(names) + 1), "name": names,
            "opens_at": "05:00", "closes_at": "00:00",
            "manager_name": managers, "phone": [self._phone() for _ in names],
        })
        line_station = pd.DataFrame({
            "line_id": ls.line_code.map(line_id), "station_id": ls.station_name.map(station_id), "seq": ls.seq,
        })

        n_lines = line_station.groupby("station_id").size()
        entrances, eid = [], 1
        for sid in station.station_id:
            k = int(min(8, 2 + 2 * (n_lines[sid] - 1) + self.rng.integers(0, 3)))
            for j in range(k):
                reduced = self.rng.random() < 0.2
                entrances.append((eid, sid, f"Entrance {chr(65 + j)}",
                                  "06:00" if reduced else "05:00", "22:00" if reduced else "00:00"))
                eid += 1
        station_entrance = pd.DataFrame(entrances, columns=["entrance_id", "station_id", "label", "opens_at", "closes_at"])

        svc_names = list(dict.fromkeys(services.service_name))
        svc_id = {n: i for i, n in enumerate(svc_names, start=1)}
        transit_service = (services.drop_duplicates("service_name")
                           .assign(service_id=lambda d: d.service_name.map(svc_id))
                           .rename(columns={"service_name": "name"})[["service_id", "name", "service_type"]])
        station_connection = pd.DataFrame({
            "station_id": services.station_name.map(station_id), "service_id": services.service_name.map(svc_id),
        })

        depot = pd.DataFrame({
            "depot_id": range(1, len(depots) + 1), "depot_code": depots.depot_code, "name": depots.name,
            "station_id": depots.station_name.map(station_id), "bays": depots.bays,
        })

        self.tables.update(state=states, line=line, station=station, line_station=line_station,
                           station_entrance=station_entrance, transit_service=transit_service,
                           station_connection=station_connection, depot=depot)

    # ------------------------------------------------------------------ fleet
    def fleet(self) -> None:
        fleet = pd.read_csv(C.REFERENCE_DIR / "fleet.csv", dtype={"line_code": str})
        depot_id = dict(zip(self.tables["depot"].depot_code, self.tables["depot"].depot_id))
        rows, counter = [], defaultdict(int)
        for r in fleet.itertuples():
            lid = line_id(r.line_code)
            for _ in range(r.n_trains):
                counter[lid] += 1
                rows.append({
                    "train_id": lid * 100 + counter[lid], "line_id": lid, "home_depot_id": depot_id[r.depot_code],
                    "manufacturer": r.manufacturer, "model": r.model,
                    "build_year": int(self.rng.integers(r.build_year_min, r.build_year_max + 1)),
                    "cars": r.cars, HIGH_RIDERSHIP: r.high_ridership,
                })
        self.train_attrs = pd.DataFrame(rows)
        self.tables["train"] = self.train_attrs.drop(columns=[HIGH_RIDERSHIP])

    # ------------------------------------------------------------------ maintenance
    def _linear_predictor(self, tr, day: date) -> float:
        b = C.TRUE_BETAS
        lp = b.get(f"manufacturer_{tr.manufacturer}", 0.0)
        lp += b["age_decades"] * age_years(day, tr.build_year) / 10
        lp += b["high_ridership"] * tr.high_ridership
        return lp

    def maintenance(self) -> None:
        k, lam, pm = C.TRUE_WEIBULL_SHAPE, C.TRUE_WEIBULL_SCALE_DAYS, C.PREVENTIVE_INTERVAL_DAYS
        last = (C.AS_OF - C.OBS_START).days
        events, eid = [], 1
        for tr in self.train_attrs.itertuples():
            # History starts at the observation start or at delivery (mid build year).
            t = max(0, (date(tr.build_year, 7, 1) - C.OBS_START).days)
            next_pm = t + int(self.rng.integers(1, pm + 1))     # staggered schedules
            while True:
                lp = self._linear_predictor(tr, C.OBS_START + timedelta(days=t))
                T = lam * (-math.log(self.rng.random()) * math.exp(-lp)) ** (1 / k)
                fail_day = t + max(1, round(T))              # daily resolution
                if fail_day < next_pm:
                    start, kind = fail_day, "corrective"
                    dur = float(np.clip(self.rng.lognormal(math.log(6), 0.7), 1, 60))
                    if age_years(C.OBS_START + timedelta(days=start), tr.build_year) > 35:
                        dur *= 1.4                                   # older trains wait longer for parts
                    dur = max(1, round(dur))
                else:
                    start, kind, dur = next_pm, "preventive", int(self.rng.integers(2, 5))
                if start > last:
                    break
                end = start + dur
                events.append((eid, tr.train_id, tr.home_depot_id, kind,
                               C.OBS_START + timedelta(days=start),
                               C.OBS_START + timedelta(days=end) if end <= last else None))
                eid += 1
                if end > last:
                    break
                t, next_pm = end, end + pm
        self.tables["maintenance_event"] = pd.DataFrame(
            events, columns=["event_id", "train_id", "depot_id", "kind", "start_date", "end_date"])

    # ------------------------------------------------------------------ workforce
    def workforce(self) -> None:
        rng = self.rng
        states = self.tables["state"].state_code.tolist()
        other_states = [s for s in states if s not in ("DF", "MC", "NE")]
        trains = self.tables["train"]
        fleet_size = trains.groupby("line_id").size()

        ops, used_curps, oid = [], set(), 1
        self.staffing_ratio = {}
        for lid, n in fleet_size.items():
            ratio = float(np.clip(rng.normal(0.97, 0.08), 0.82, 1.12))
            self.staffing_ratio[lid] = ratio
            per_code = round(n * 7 / 5 * ratio)
            for code in "MVN":
                for j in range(per_code):
                    sex = "H" if rng.random() < 0.78 else "M"
                    first, last1, last2 = self._person_name(sex)
                    age = 22 + rng.beta(2, 2.6) * 41
                    birth = C.AS_OF - timedelta(days=int(age * 365.25))
                    tenure = rng.uniform(0, min(age - 21, 32))
                    hire = C.AS_OF - timedelta(days=int(tenure * 365.25))
                    u = rng.random()
                    state = "DF" if u < 0.52 else "MC" if u < 0.80 else str(rng.choice(other_states))
                    while True:
                        homo = (str(rng.integers(0, 10)) if birth.year < 2000
                                else chr(65 + int(rng.integers(0, 26))))
                        c = curp.build(first, last1, last2, birth, sex, state, homo)
                        if c not in used_curps:
                            used_curps.add(c)
                            break
                    s = rng.random()
                    status = "leave" if s < 0.04 else "terminated" if s < 0.055 else "active"
                    salary = round(13_500 + 410 * tenure + rng.normal(0, 700), 2)
                    ops.append({
                        "operator_id": oid, "curp": c, "first_name": first, "last_name": last1,
                        "second_last_name": last2, "sex": sex, "birth_date": birth,
                        "birth_state_code": state, "hire_date": hire, "base_line_id": lid,
                        "monthly_salary": salary, "status": status,
                        "_shift": code, "_rest": j % 7,
                    })
                    oid += 1
        operators = pd.DataFrame(ops)
        phones = []
        for o in operators.operator_id:
            for _ in range(1 if rng.random() < 0.8 else 2):
                phones.append((o, self._phone()))
        self.operator_meta = operators
        self.tables["operator"] = operators.drop(columns=["_shift", "_rest"])
        self.tables["operator_phone"] = pd.DataFrame(phones, columns=["operator_id", "phone"])

    def shifts(self) -> None:
        rng = self.rng
        trains = self.tables["train"]
        ev = self.tables["maintenance_event"]
        # Days each train is in the depot inside the shift window.
        in_depot = set()
        for e in ev.itertuples():
            end = e.end_date or (C.AS_OF + timedelta(days=1))
            d = max(e.start_date, C.SHIFT_WINDOW_START)
            while d < end and d <= C.AS_OF:
                in_depot.add((e.train_id, d))
                d += timedelta(days=1)

        active = self.operator_meta[self.operator_meta.status == "active"]
        roster = {k: g[["operator_id", "_rest"]].to_numpy() for k, g in active.groupby(["base_line_id", "_shift"])}
        line_trains = {lid: g.train_id.to_numpy() for lid, g in trains.groupby("line_id")}

        rows, sid = [], 1
        day = C.SHIFT_WINDOW_START
        while day <= C.AS_OF:
            wd = day.weekday()
            for lid, tids in line_trains.items():
                available = np.array([t for t in tids if (t, day) not in in_depot])
                for code, (t0, plus, t1) in SHIFT_TIMES.items():
                    team = roster.get((lid, code))
                    if team is None:
                        continue
                    working = team[((wd - team[:, 1]) % 7) >= 2, 0]   # two consecutive rest days
                    ops_today = rng.permutation(working)
                    trains_today = rng.permutation(available)
                    start = datetime.combine(day, t0)
                    end = datetime.combine(day + timedelta(days=plus), t1)
                    for op, tr in zip(ops_today, trains_today):
                        rows.append((sid, int(op), int(tr), day, code, start, end))
                        sid += 1
            day += timedelta(days=1)
        self.tables["shift_assignment"] = pd.DataFrame(
            rows, columns=["shift_id", "operator_id", "train_id", "service_date", "shift_code", "start_ts", "end_ts"])

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, pd.DataFrame]:
        self.tables["snapshot"] = pd.DataFrame({"obs_start": [C.OBS_START], "as_of": [C.AS_OF]})
        self.network()
        self.fleet()
        self.maintenance()
        self.workforce()
        self.shifts()
        return self.tables


LOAD_ORDER = [
    "snapshot", "state", "line", "station", "line_station", "station_entrance", "transit_service",
    "station_connection", "depot", "train", "maintenance_event", "operator", "operator_phone",
    "shift_assignment",
]


def write_csvs(tables: dict[str, pd.DataFrame], out_dir=C.GENERATED_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in LOAD_ORDER:
        tables[name].to_csv(out_dir / f"{name}.csv", index=False)
