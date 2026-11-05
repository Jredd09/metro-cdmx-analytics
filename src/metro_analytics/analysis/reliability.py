"""Fleet reliability analysis.

1. Kaplan-Meier curves of time-to-unplanned-failure (by manufacturer, by age)
2. Log-rank tests
3. Cox PH and Weibull PH models, compared with the simulation's true parameters
4. Monte Carlo forecast of 2026 corrective failures per line, conditional on
   each train's current state and propagating parameter uncertainty
5. Preventive-maintenance interval what-if (failures vs. depot downtime)
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from .. import config as C
from ..db import Database
from . import style
from .survival import cox_ph, kaplan_meier, logrank_test, survival_at, weibull_ph

MANUFACTURERS = ["Bombardier", "Alstom", "CAF", "CRRC"]          # Bombardier = reference level
COVARIATES = ["manufacturer_Alstom", "manufacturer_CAF", "manufacturer_CRRC", "age_decades", "high_ridership"]
LABELS = {
    "manufacturer_Alstom": "Alstom vs Bombardier", "manufacturer_CAF": "CAF vs Bombardier",
    "manufacturer_CRRC": "CRRC vs Bombardier", "age_decades": "+10 years of train age",
    "high_ridership": "High-ridership line",
}
AGE_BANDS = [(-np.inf, 15, "< 15 years"), (15, 35, "15–35 years"), (35, np.inf, "> 35 years")]


def design(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for m in MANUFACTURERS[1:]:
        df[f"manufacturer_{m}"] = (df.manufacturer == m).astype(int)
    df["age_decades"] = df.age_years_at_start / 10
    return df


def load_spells(db: Database) -> pd.DataFrame:
    return design(db.query("SELECT * FROM v_failure_spell"))


# --------------------------------------------------------------------------- figures
def plot_km(spells: pd.DataFrame, by: str, order: list[str], title: str, sub: str, path) -> dict:
    style.apply()
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    out = {}
    for i, g in enumerate(order):
        part = spells[spells[by] == g]
        km = kaplan_meier(part.duration_days, part.failed)
        c = style.SERIES[i]
        ax.step(km.time, km.survival, where="post", color=c, label=f"{g} (n={len(part):,})")
        ax.fill_between(km.time, km.ci_low, km.ci_high, step="post", color=c, alpha=0.12, linewidth=0)
        s180 = survival_at(km, C.PREVENTIVE_INTERVAL_DAYS - 1)
        out[g] = s180
        ax.annotate(f"{g}  {s180:.0%}", (km.time.iloc[-1], km.survival.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=style.INK)
    ax.set_xlim(0, 205)
    ax.set_ylim(0, 1.02)
    ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlabel("Days in service since last depot visit")
    ax.set_ylabel("Share still running without unplanned failure")
    ax.legend(loc="lower left")
    style.titles(ax, title, sub)
    fig.savefig(path)
    plt.close(fig)
    return out


def plot_hazard_ratios(cox, path) -> None:
    style.apply()
    s = cox.summary().loc[COVARIATES]
    truth = pd.Series(C.TRUE_BETAS)
    fig, ax = plt.subplots(figsize=(8.2, 3.9))
    y = np.arange(len(s))[::-1]
    ax.axvline(1, color=style.INK_2, linewidth=1)
    ax.hlines(y, np.exp(s.ci_low), np.exp(s.ci_high), color=style.SERIES[0], linewidth=2)
    ax.plot(np.exp(s.coef), y, "o", color=style.SERIES[0], markersize=8, label="Estimated (95% CI)")
    ax.plot(np.exp(truth[s.index]), y, "D", markerfacecolor="none", markeredgecolor=style.SERIES[1],
            markersize=9, markeredgewidth=2, label="True value used in simulation")
    for yi, (hr, lo, hi) in zip(y, zip(np.exp(s.coef), np.exp(s.ci_low), np.exp(s.ci_high))):
        ax.text(hi * 1.03, yi, f"{hr:.2f}", va="center", fontsize=9, color=style.INK)
    ax.set_yticks(y, [LABELS[c] for c in s.index])
    ax.set_xscale("log")
    ax.set_xticks([0.5, 0.75, 1, 1.5, 2], ["0.5", "0.75", "1", "1.5", "2"])
    ax.xaxis.set_minor_formatter(plt.matplotlib.ticker.NullFormatter())
    ax.set_xlabel("Hazard ratio (log scale) — >1 means fails sooner")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.2), ncol=2, fontsize=9)
    style.titles(ax, "What drives unplanned failures", "Cox proportional-hazards model, 2019–2025 in-service spells")
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------- simulation
class FleetSimulator:
    """Renewal simulation of every train under a Weibull PH failure model."""

    def __init__(self, trains: pd.DataFrame, repair_days: dict[str, np.ndarray], rng: np.random.Generator):
        self.trains = trains
        self.repair_days = repair_days
        self.rng = rng

    def run(self, theta: np.ndarray, pm_interval: int, horizon: int, n_sims: int):
        """theta: (n_sims, 2 + p) draws of [log_shape, log_scale, betas...]."""
        rng = self.rng
        k = np.exp(theta[:, 0])
        lam = np.exp(theta[:, 1])
        betas = theta[:, 2:]
        failures = np.zeros((n_sims, len(self.trains)))
        pm_visits = np.zeros_like(failures)
        depot_days = np.zeros_like(failures)
        for j, tr in enumerate(self.trains.itertuples()):
            x_static = np.array([tr.manufacturer == m for m in MANUFACTURERS[1:]], float)
            t = np.full(n_sims, -float(tr.elapsed))             # spell start relative to forecast origin
            if tr.in_depot:
                t = np.full(n_sims, float(tr.remaining_repair))
            next_pm = t + pm_interval
            next_pm = np.maximum(next_pm, 1.0)
            active = t < horizon
            while active.any():
                age = (tr.age_at_origin + t / 365.25) / 10
                lp = betas[:, :3] @ x_static + betas[:, 3] * age + betas[:, 4] * tr.high_ridership
                lam_i = lam * np.exp(-lp / k)
                a = np.maximum(-t, 0.0)                         # time already survived at origin
                u = rng.random(n_sims)
                T = lam_i * ((a / lam_i) ** k - np.log(u)) ** (1 / k)   # conditional on survival to a
                fail_at = t + T
                is_fail = fail_at < next_pm
                event_at = np.where(is_fail, fail_at, next_pm)
                in_window = active & (event_at < horizon)
                rep_c = rng.choice(self.repair_days[tr.age_class], n_sims)
                rep_p = rng.integers(2, 5, n_sims)
                dur = np.where(is_fail, rep_c, rep_p)
                failures[:, j] += in_window & is_fail
                pm_visits[:, j] += in_window & ~is_fail
                depot_days[:, j] += np.where(in_window, np.minimum(dur, horizon - event_at), 0)
                t = np.where(in_window, event_at + dur, horizon)
                next_pm = t + pm_interval
                active = t < horizon
        return failures, pm_visits, depot_days


def current_state(db: Database) -> pd.DataFrame:
    origin = C.AS_OF + timedelta(days=1)
    tr = db.query("""
        SELECT t.train_id, l.line_code, t.manufacturer, t.build_year, l.high_ridership::INT AS high_ridership,
               ts.in_depot::INT AS in_depot, ts.in_depot_since,
               (SELECT max(spell_start) FROM v_failure_spell f WHERE f.train_id = t.train_id) AS last_spell_start
        FROM train t JOIN line l USING (line_id) JOIN v_train_status ts USING (train_id)""")
    tr["age_at_origin"] = [(origin - date(y, 7, 1)).days / 365.25 for y in tr.build_year]
    tr["in_depot"] = tr.in_depot.astype(int).astype(bool)
    tr["elapsed"] = [(origin - pd.Timestamp(s).date()).days if not d else 0
                     for s, d in zip(tr.last_spell_start, tr.in_depot)]
    tr["remaining_repair"] = 3.0
    tr["age_class"] = np.where(tr.age_at_origin > 35, "old", "young")
    return tr


def repair_samples(db: Database) -> dict[str, np.ndarray]:
    rep = db.query("""
        SELECT (end_date - start_date) AS days, (train_age_years(start_date, t.build_year) > 35)::INT AS old
        FROM maintenance_event m JOIN train t USING (train_id)
        WHERE kind = 'corrective' AND end_date IS NOT NULL""")
    return {"old": rep.loc[rep.old.astype(bool), "days"].to_numpy(),
            "young": rep.loc[~rep.old.astype(bool), "days"].to_numpy()}


def forecast(db, weib, n_sims=1000, seed=7):
    rng = np.random.default_rng(seed)
    trains = current_state(db)
    sim = FleetSimulator(trains, repair_samples(db), rng)
    theta = rng.multivariate_normal(weib.params.to_numpy(), weib.cov, n_sims)
    fails, _, ddays = sim.run(theta, C.PREVENTIVE_INTERVAL_DAYS, 365, n_sims)
    per_line = pd.DataFrame(fails, columns=trains.line_code).T.groupby(level=0).sum().T
    summary = pd.DataFrame({
        "expected": per_line.mean(), "p10": per_line.quantile(0.10), "p90": per_line.quantile(0.90),
    })
    total = fails.sum(axis=1)
    return summary, {"mean": float(total.mean()), "p10": float(np.quantile(total, 0.1)),
                     "p90": float(np.quantile(total, 0.9)),
                     "depot_days_mean": float(ddays.sum(axis=1).mean())}


def plot_forecast(summary: pd.DataFrame, total: dict, kpi: pd.DataFrame, path) -> None:
    style.apply()
    order = kpi.sort_values("line_id").line_code.tolist()
    s = summary.reindex(order)
    actual = kpi.set_index("line_code").reindex(order).failures_12m
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    x = np.arange(len(s))
    ax.bar(x, s.expected, width=0.62, color=style.SERIES[0], label="2026 forecast (mean)", zorder=2)
    ax.errorbar(x, s.expected, yerr=[s.expected - s.p10, s.p90 - s.expected], fmt="none",
                ecolor=style.INK, elinewidth=1.2, capsize=3, label="80% interval (P10–P90)", zorder=3)
    ax.plot(x, actual, "_", color=style.SERIES[1], markersize=16, markeredgewidth=2.5,
            label="2025 actual", zorder=4)
    ax.set_xticks(x, [f"L{c}" for c in order])
    ax.set_ylabel("Unplanned failures in the year")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right")
    style.titles(ax, f"2026 forecast: {total['mean']:.0f} unplanned failures "
                     f"(80% interval {total['p10']:.0f}–{total['p90']:.0f})",
                 "Monte Carlo, 1,000 runs; conditional on each train's state on 31 Dec 2025, "
                 "with parameter uncertainty")
    fig.savefig(path)
    plt.close(fig)


def pm_policy(db, weib, intervals=tuple(range(60, 366, 15)), n_sims=300, seed=11):
    """Steady-state fleet outcomes for each preventive-maintenance interval."""
    rng = np.random.default_rng(seed)
    trains = current_state(db)
    trains["in_depot"] = False
    trains["elapsed"] = 0
    sim = FleetSimulator(trains, repair_samples(db), rng)
    theta = np.tile(weib.params.to_numpy(), (n_sims, 1))
    horizon = 3 * 365
    rows = []
    for iv in intervals:
        f, p, d = sim.run(theta, iv, horizon, n_sims)
        rows.append({"interval_days": iv, "failures_per_year": f.sum(1).mean() / 3,
                     "pm_visits_per_year": p.sum(1).mean() / 3, "depot_days_per_year": d.sum(1).mean() / 3})
    return pd.DataFrame(rows)


def best_interval_by_penalty(pol: pd.DataFrame, penalties=np.arange(0, 20.5, 0.5)) -> pd.DataFrame:
    """Interval minimising depot days + penalty x failures.

    The penalty is the cost of one in-service breakdown (passenger disruption,
    rescue train, delays) expressed in train-days of depot time. It is a policy
    input, not something the data can estimate, so the result is shown for a range.
    """
    rows = []
    for pen in penalties:
        cost = pol.depot_days_per_year + pen * pol.failures_per_year
        i = cost.idxmin()
        rows.append({"penalty_train_days": pen, "best_interval_days": int(pol.interval_days[i]),
                     "failures_per_year": pol.failures_per_year[i]})
    return pd.DataFrame(rows)


def plot_pm_policy(pol: pd.DataFrame, best: pd.DataFrame, path) -> None:
    style.apply()
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.3))
    ax = axes[0]
    ax.plot(pol.interval_days, pol.failures_per_year, "-o", color=style.SERIES[1], markersize=5,
            label="Unplanned failures")
    ax.plot(pol.interval_days, pol.pm_visits_per_year, "-o", color=style.SERIES[0], markersize=5,
            label="Preventive visits")
    cur = pol.set_index("interval_days").loc[C.PREVENTIVE_INTERVAL_DAYS]
    ax.axvline(C.PREVENTIVE_INTERVAL_DAYS, color=style.INK_2, linewidth=1, linestyle=":")
    ax.annotate("current policy", (C.PREVENTIVE_INTERVAL_DAYS, cur.pm_visits_per_year + 600), xytext=(4, 0),
                textcoords="offset points", fontsize=9, color=style.INK_2)
    ax.set_xlabel("Preventive interval (days)")
    ax.set_ylabel("Fleet events per year")
    ax.set_ylim(0)
    ax.legend(loc="upper right")
    style.titles(ax, "Depot visits vs. breakdowns", "Simulated steady state, 372 trains")
    ax = axes[1]
    ax.step(best.penalty_train_days, best.best_interval_days, where="post", color=style.SERIES[0])
    ax.set_xlabel("Cost of one in-service breakdown (train-days)")
    ax.set_ylabel("Interval that minimises total cost (days)")
    ax.set_ylim(0, 380)
    style.titles(ax, "The right interval depends on breakdown cost",
                 "total cost = depot days + cost × breakdowns")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------- recovery study
def recovery_study(n_seeds: int = 30) -> pd.DataFrame:
    """Re-simulate the maintenance history with different seeds and refit.

    Reports the mean estimate and the empirical coverage of the 95% CI for each
    true parameter — a check that the estimators are unbiased and calibrated.
    """
    from ..generate import Generator, age_years

    truth = {"shape": C.TRUE_WEIBULL_SHAPE, **C.TRUE_BETAS}
    est = []
    for seed in range(1, n_seeds + 1):
        g = Generator(seed)
        g.tables["snapshot"] = None
        g.network()
        g.fleet()
        g.maintenance()
        tr = g.train_attrs.set_index("train_id")
        rows = []
        for tid, ev in g.tables["maintenance_event"].sort_values(["train_id", "start_date"]).groupby("train_id"):
            start = max(C.OBS_START, date(int(tr.build_year[tid]), 7, 1))
            for e in ev.itertuples():
                rows.append((tid, (e.start_date - start).days, int(e.kind == "corrective"),
                             age_years(start, int(tr.build_year[tid]))))
                start = e.end_date
                if start is None:
                    break
            if start is not None and (C.AS_OF - start).days > 0:
                rows.append((tid, (C.AS_OF - start).days, 0, age_years(start, int(tr.build_year[tid]))))
        sp = pd.DataFrame(rows, columns=["train_id", "duration_days", "failed", "age_years_at_start"])
        sp = sp[sp.duration_days > 0]
        sp["manufacturer"] = tr.manufacturer.reindex(sp.train_id).to_numpy()
        sp["high_ridership"] = tr.high_ridership.reindex(sp.train_id).to_numpy()
        sp = design(sp)
        w = weibull_ph(sp, "duration_days", "failed", COVARIATES).summary()
        w = w.rename(index={"log_shape": "shape"})
        for p, val in truth.items():
            coef, lo, hi = w.loc[p, ["coef", "ci_low", "ci_high"]]
            if p == "shape":
                coef, lo, hi = np.exp([coef, lo, hi])
            est.append({"seed": seed, "parameter": p, "estimate": coef, "covered": lo <= val <= hi, "truth": val})
    df = pd.DataFrame(est)
    return (df.groupby("parameter", sort=False)
              .agg(truth=("truth", "first"), mean_estimate=("estimate", "mean"),
                   sd=("estimate", "std"), ci95_coverage=("covered", "mean"))
              .reset_index())


# --------------------------------------------------------------------------- driver
def run(db: Database, n_recovery_seeds: int = 30) -> dict:
    C.FIGURES_DIR.mkdir(exist_ok=True)
    C.REPORTS_DIR.mkdir(exist_ok=True)
    sp = load_spells(db)
    metrics: dict = {"spells": len(sp), "failures": int(sp.failed.sum()),
                     "censored_share": round(1 - sp.failed.mean(), 3)}

    s180_m = plot_km(sp, "manufacturer", MANUFACTURERS,
                     "Probability of reaching the next service without breaking down",
                     "Kaplan-Meier by manufacturer, shaded 95% CI; label = share surviving 179 days",
                     C.FIGURES_DIR / "km_by_manufacturer.png")
    sp["age_band"] = pd.cut(sp.age_years_at_start, [b[0] for b in AGE_BANDS] + [np.inf],
                            labels=[b[2] for b in AGE_BANDS], right=False).astype(str)
    s180_a = plot_km(sp, "age_band", [b[2] for b in AGE_BANDS],
                     "Older trains break down sooner",
                     "Kaplan-Meier by train age at the start of each spell; label = share surviving 179 days",
                     C.FIGURES_DIR / "km_by_age.png")
    metrics["survival_179d_by_manufacturer"] = {k: round(v, 3) for k, v in s180_m.items()}
    metrics["survival_179d_by_age"] = {k: round(v, 3) for k, v in s180_a.items()}
    chi2, dof, p = logrank_test(sp.duration_days, sp.failed, sp.manufacturer)
    metrics["logrank_manufacturer"] = {"chi2": round(chi2, 1), "dof": dof, "p": p}
    chi2, dof, p = logrank_test(sp.duration_days, sp.failed, sp.age_band)
    metrics["logrank_age"] = {"chi2": round(chi2, 1), "dof": dof, "p": p}

    cox = cox_ph(sp, "duration_days", "failed", COVARIATES)
    weib = weibull_ph(sp, "duration_days", "failed", COVARIATES)
    plot_hazard_ratios(cox, C.FIGURES_DIR / "hazard_ratios.png")
    cs, ws = cox.summary(), weib.summary()
    comparison = pd.DataFrame({
        "true": pd.Series({"shape": C.TRUE_WEIBULL_SHAPE, "scale_days": C.TRUE_WEIBULL_SCALE_DAYS, **C.TRUE_BETAS}),
        "weibull_est": pd.Series({"shape": np.exp(ws.coef["log_shape"]), "scale_days": np.exp(ws.coef["log_scale"]),
                                  **ws.coef[COVARIATES].to_dict()}),
        "weibull_ci": pd.Series({
            "shape": f"{np.exp(ws.ci_low['log_shape']):.3f}–{np.exp(ws.ci_high['log_shape']):.3f}",
            "scale_days": f"{np.exp(ws.ci_low['log_scale']):.0f}–{np.exp(ws.ci_high['log_scale']):.0f}",
            **{c: f"{ws.ci_low[c]:.3f}–{ws.ci_high[c]:.3f}" for c in COVARIATES}}),
        "cox_est": cs.coef.reindex(["shape", "scale_days", *COVARIATES]),
        "cox_ci": pd.Series({c: f"{cs.ci_low[c]:.3f}–{cs.ci_high[c]:.3f}" for c in COVARIATES}),
    })
    comparison.round(3).to_csv(C.REPORTS_DIR / "parameter_recovery.csv")
    metrics["cox_hazard_ratios"] = {c: round(float(np.exp(cs.coef[c])), 2) for c in COVARIATES}
    metrics["weibull_shape"] = round(float(np.exp(ws.coef["log_shape"])), 3)

    kpi = db.query("SELECT * FROM v_line_kpi")
    summary, total = forecast(db, weib)
    plot_forecast(summary, total, kpi, C.FIGURES_DIR / "forecast_2026.png")
    summary.round(1).to_csv(C.REPORTS_DIR / "forecast_2026_by_line.csv")
    metrics["forecast_2026"] = {k: round(v, 1) for k, v in total.items()}
    metrics["actual_2025_failures"] = int(kpi.failures_12m.sum())

    pol = pm_policy(db, weib)
    best = best_interval_by_penalty(pol)
    plot_pm_policy(pol, best, C.FIGURES_DIR / "pm_policy.png")
    pol.round(1).to_csv(C.REPORTS_DIR / "pm_policy.csv", index=False)
    best.to_csv(C.REPORTS_DIR / "pm_best_interval_by_penalty.csv", index=False)
    cur = pol.set_index("interval_days").loc[C.PREVENTIVE_INTERVAL_DAYS]
    b5 = best.set_index("penalty_train_days").loc[5.0]
    metrics["pm_policy"] = {
        "failures_per_year_current": round(float(cur.failures_per_year)),
        "best_interval_if_breakdown_costs_5_days": int(b5.best_interval_days),
        "failures_per_year_at_that_interval": round(float(b5.failures_per_year)),
        "penalty_where_180_stops_being_optimal": float(best.loc[best.best_interval_days < 180, "penalty_train_days"].min()),
        "intervals": {int(r.interval_days): [round(r.failures_per_year), round(r.depot_days_per_year)]
                      for r in pol.itertuples()},
    }

    if n_recovery_seeds:
        rec = recovery_study(n_recovery_seeds)
        rec.round(3).to_csv(C.REPORTS_DIR / "recovery_study.csv", index=False)
        metrics["recovery_min_coverage"] = round(float(rec.ci95_coverage.min()), 2)
    metrics["line_kpi"] = kpi.drop(columns=["line_id"]).to_dict(orient="records")
    return metrics
