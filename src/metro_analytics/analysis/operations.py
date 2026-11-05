"""Workforce, staffing coverage, depot capacity and audit figures."""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from .. import config as C
from ..db import Database
from . import style

SHIFT_NAMES = {"M": "Morning\n05–13", "V": "Afternoon\n13–21", "N": "Night\n21–05"}


def staffing(db: Database) -> dict:
    cov = db.query("SELECT line_code, shift_code, trains_available, trains_staffed FROM v_staffing_coverage")
    g = cov.groupby(["line_code", "shift_code"])[["trains_available", "trains_staffed"]].sum()
    g["coverage"] = g.trains_staffed / g.trains_available
    mat = g.coverage.unstack("shift_code")[["M", "V", "N"]]
    order = db.query("SELECT line_code FROM line ORDER BY line_id").line_code.tolist()
    mat = mat.reindex(order)

    style.apply()
    fig, ax = plt.subplots(figsize=(5.6, 6.0))
    im = ax.imshow(mat.to_numpy(), cmap=style.SEQ_CMAP, vmin=0.6, vmax=1.0, aspect="auto")
    ax.grid(False)
    ax.set_xticks(range(3), [SHIFT_NAMES[c] for c in mat.columns])
    ax.set_yticks(range(len(mat)), [f"Line {c}" for c in mat.index])
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.iat[i, j]
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=9,
                    color="white" if v > 0.85 else style.INK)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03, format=plt.matplotlib.ticker.PercentFormatter(1.0))
    cb.outline.set_visible(False)
    style.titles(ax, "Share of available trains with a driver", "Q4 2025, by line and shift")
    fig.savefig(C.FIGURES_DIR / "staffing_coverage.png")
    plt.close(fig)

    line_cov = g.groupby(level=0)[["trains_available", "trains_staffed"]].sum()
    line_cov["coverage"] = line_cov.trains_staffed / line_cov.trains_available
    total = line_cov.trains_staffed.sum() / line_cov.trains_available.sum()
    worst = line_cov.coverage.idxmin()
    unstaffed = int((line_cov.trains_available - line_cov.trains_staffed).sum())
    days = cov.groupby("line_code").size().iloc[0] // 3
    return {"overall_coverage": round(float(total), 3), "worst_line": worst,
            "worst_line_coverage": round(float(line_cov.coverage[worst]), 3),
            "unstaffed_train_shifts_per_day": round(unstaffed / days, 1),
            "lines_below_95pct": int((line_cov.coverage < 0.95).sum())}


def pay_equity(db: Database) -> dict:
    """salary ~ tenure + female (OLS). With synthetic data the true gap is zero;
    the point is the method: report the adjusted gap with a confidence interval."""
    op = db.query("SELECT * FROM v_operator_profile WHERE status <> 'terminated'")
    X = np.column_stack([np.ones(len(op)), op.tenure_years, (op.sex == "M").astype(float)])
    y = op.monthly_salary.to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sigma2 = resid @ resid / (len(y) - X.shape[1])
    se = np.sqrt(np.diag(sigma2 * np.linalg.inv(X.T @ X)))
    r2 = 1 - resid.var() / y.var()
    mean_pay = y.mean()
    raw_gap = op.loc[op.sex == "M", "monthly_salary"].mean() / op.loc[op.sex == "H", "monthly_salary"].mean() - 1
    lo, hi = beta[2] - 1.96 * se[2], beta[2] + 1.96 * se[2]

    style.apply()
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    for i, (sex, lab) in enumerate([("H", "Men"), ("M", "Women")]):
        part = op[op.sex == sex]
        ax.scatter(part.tenure_years, part.monthly_salary, s=9, alpha=0.45, color=style.SERIES[i],
                   label=f"{lab} (n={len(part):,})", linewidths=0)
    xs = np.linspace(0, op.tenure_years.max(), 50)
    ax.plot(xs, beta[0] + beta[1] * xs, color=style.INK, linewidth=1.5, label="OLS fit")
    ax.set_xlabel("Tenure (years)")
    ax.set_ylabel("Monthly salary (MXN)")
    ax.yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
    ax.legend(loc="upper left", markerscale=2)
    style.titles(ax, "Salary grows with tenure; no gap by sex after adjusting",
                 f"OLS: +${beta[1]:,.0f}/month per year of tenure; adjusted gap for women "
                 f"${beta[2]:+,.0f} (95% CI ${lo:+,.0f} to ${hi:+,.0f}); R² = {r2:.2f}")
    fig.savefig(C.FIGURES_DIR / "salary_tenure.png")
    plt.close(fig)
    return {"operators": len(op), "salary_per_tenure_year_mxn": round(float(beta[1]), 1),
            "raw_gap_women_vs_men": round(float(raw_gap), 4),
            "adjusted_gap_mxn": round(float(beta[2]), 1),
            "adjusted_gap_ci_mxn": [round(float(lo), 1), round(float(hi), 1)],
            "adjusted_gap_pct_of_mean": round(float(beta[2] / mean_pay), 4),
            "r2": round(float(r2), 3)}


def depot_capacity(db: Database) -> dict:
    occ = db.query("""
        SELECT depot_code, name, bays,
               avg(trains_in_depot)::FLOAT                                   AS mean_occupancy,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY trains_in_depot)::FLOAT AS p95,
               max(trains_in_depot)                                          AS peak,
               avg(over_capacity::INT)::FLOAT                                AS share_days_over
        FROM v_depot_occupancy GROUP BY depot_code, name, bays ORDER BY share_days_over DESC""")
    occ.to_csv(C.REPORTS_DIR / "depot_capacity.csv", index=False)

    top = occ.iloc[0]
    return {"most_congested": top["name"], "share_days_over": round(float(top.share_days_over), 3),
            "depots_ever_over": int((occ.peak > occ.bays).sum())}


def audit_figure(legacy: pd.DataFrame) -> None:
    C.FIGURES_DIR.mkdir(exist_ok=True)
    d = legacy.dropna(subset=["violations"])
    d = d[(d.violations > 0) & (d.severity != "info")].sort_values("violations")
    style.apply()
    fig, ax = plt.subplots(figsize=(8.6, 5.2))
    sev_color = {"critical": style.SERIES[7], "major": style.SERIES[1], "minor": style.SERIES[3]}
    y = np.arange(len(d))
    ax.barh(y, d.violations, height=0.62, color=[sev_color[s] for s in d.severity], zorder=2)
    for yi, r in zip(y, d.itertuples()):
        ax.text(r.violations * 1.08, yi, f"{int(r.violations):,}", va="center", fontsize=9, color=style.INK)
    ax.set_yticks(y, [f"{r.rule_id}  {r.title}" for r in d.itertuples()], fontsize=8.8)
    ax.set_xscale("log")
    ax.set_xlim(0.8, d.violations.max() * 3)
    ax.xaxis.set_major_formatter(plt.matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("Offending records (log scale)")
    ax.grid(axis="y", visible=False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in sev_color.values()]
    ax.legend(handles, [s.capitalize() for s in sev_color], loc="lower right", title="Severity", fontsize=9)
    style.titles(ax, "Data-quality issues found in the original course dataset",
                 "Every check returns 0 on the rebuilt, constrained database")
    fig.savefig(C.FIGURES_DIR / "audit_legacy.png")
    plt.close(fig)


def run(db: Database) -> dict:
    C.FIGURES_DIR.mkdir(exist_ok=True)
    C.REPORTS_DIR.mkdir(exist_ok=True)
    return {"staffing": staffing(db), "pay_equity": pay_equity(db), "depots": depot_capacity(db)}
