"""Survival-analysis estimators implemented from first principles (NumPy/SciPy).

* Kaplan-Meier with Greenwood variance and log(-log) confidence intervals
* k-sample log-rank test
* Cox proportional hazards (Breslow ties) fitted by Newton-Raphson
* Parametric Weibull proportional-hazards model fitted by maximum likelihood

Kept dependency-free on purpose: the point of the project is to show the maths,
and the unit tests check each estimator against hand-computed or simulated truth.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, stats

Z95 = stats.norm.ppf(0.975)


# --------------------------------------------------------------------------- KM
def kaplan_meier(durations, events) -> pd.DataFrame:
    """Return the KM curve at each distinct event time.

    Columns: time, at_risk, events, survival, ci_low, ci_high.
    """
    t = np.asarray(durations, float)
    d = np.asarray(events, int)
    times = np.unique(t[d == 1])
    at_risk = np.array([(t >= u).sum() for u in times])
    n_events = np.array([((t == u) & (d == 1)).sum() for u in times])
    surv = np.cumprod(1 - n_events / at_risk)
    with np.errstate(divide="ignore", invalid="ignore"):
        greenwood = np.cumsum(n_events / (at_risk * (at_risk - n_events)))
        # CI on log(-log S) scale keeps bounds inside (0, 1).
        se_loglog = np.sqrt(greenwood) / np.abs(np.log(surv))
        ll = np.log(-np.log(surv))
        lo = np.exp(-np.exp(ll + Z95 * se_loglog))
        hi = np.exp(-np.exp(ll - Z95 * se_loglog))
    out = pd.DataFrame({"time": times, "at_risk": at_risk, "events": n_events,
                        "survival": surv, "ci_low": lo, "ci_high": hi})
    start = pd.DataFrame({"time": [0.0], "at_risk": [len(t)], "events": [0],
                          "survival": [1.0], "ci_low": [1.0], "ci_high": [1.0]})
    return pd.concat([start, out], ignore_index=True)


def survival_at(km: pd.DataFrame, t: float) -> float:
    return float(km.loc[km.time <= t, "survival"].iloc[-1])


# --------------------------------------------------------------------------- log-rank
def logrank_test(durations, events, groups) -> tuple[float, int, float]:
    """k-sample log-rank test. Returns (chi2, dof, p_value)."""
    t = np.asarray(durations, float)
    d = np.asarray(events, int)
    g = np.asarray(groups)
    labels = np.unique(g)
    k = len(labels)
    O = np.zeros(k)
    E = np.zeros(k)
    V = np.zeros((k, k))
    for u in np.unique(t[d == 1]):
        risk = t >= u
        n = risk.sum()
        dt = ((t == u) & (d == 1)).sum()
        n_g = np.array([(risk & (g == lab)).sum() for lab in labels])
        d_g = np.array([((t == u) & (d == 1) & (g == lab)).sum() for lab in labels])
        O += d_g
        E += dt * n_g / n
        if n > 1:
            frac = n_g / n
            V += dt * (n - dt) / (n - 1) * (np.diag(frac) - np.outer(frac, frac))
    diff = (O - E)[:-1]
    chi2 = float(diff @ np.linalg.solve(V[:-1, :-1], diff))
    return chi2, k - 1, float(stats.chi2.sf(chi2, k - 1))


# --------------------------------------------------------------------------- Cox
@dataclass
class FitResult:
    params: pd.Series
    se: pd.Series
    loglik: float
    cov: np.ndarray | None = None

    def summary(self) -> pd.DataFrame:
        z = self.params / self.se
        return pd.DataFrame({
            "coef": self.params, "se": self.se, "z": z,
            "p": 2 * stats.norm.sf(np.abs(z)),
            "ci_low": self.params - Z95 * self.se, "ci_high": self.params + Z95 * self.se,
        })


def cox_ph(df: pd.DataFrame, duration: str, event: str, covariates: list[str],
           tol: float = 1e-9, max_iter: int = 50) -> FitResult:
    """Cox PH model with Breslow handling of tied event times."""
    order = np.argsort(df[duration].to_numpy(), kind="stable")
    t = df[duration].to_numpy(float)[order]
    d = df[event].to_numpy(int)[order]
    X = df[covariates].to_numpy(float)[order]
    n, p = X.shape
    first = np.searchsorted(t, t, side="left")      # start of each subject's risk set
    ev = d == 1
    beta = np.zeros(p)
    ll_old = -np.inf
    for _ in range(max_iter):
        w = np.exp(X @ beta)
        # reverse cumulative sums give the risk-set totals for t_j >= t_i
        S0 = np.cumsum(w[::-1])[::-1]
        S1 = np.cumsum((w[:, None] * X)[::-1], axis=0)[::-1]
        S2 = np.cumsum((w[:, None, None] * X[:, :, None] * X[:, None, :])[::-1], axis=0)[::-1]
        s0, s1, s2 = S0[first][ev], S1[first][ev], S2[first][ev]
        ll = float((X[ev] @ beta).sum() - np.log(s0).sum())
        mean = s1 / s0[:, None]
        grad = (X[ev] - mean).sum(axis=0)
        info = (s2 / s0[:, None, None] - mean[:, :, None] * mean[:, None, :]).sum(axis=0)
        step = np.linalg.solve(info, grad)
        beta = beta + step
        if abs(ll - ll_old) < tol:
            break
        ll_old = ll
    cov = np.linalg.inv(info)
    return FitResult(pd.Series(beta, covariates), pd.Series(np.sqrt(np.diag(cov)), covariates), ll, cov)


# --------------------------------------------------------------------------- Weibull PH
def _numerical_hessian(f, x, eps=1e-4):
    k = len(x)
    H = np.zeros((k, k))
    for i in range(k):
        for j in range(i, k):
            e_i, e_j = np.eye(k)[i] * eps, np.eye(k)[j] * eps
            H[i, j] = H[j, i] = (f(x + e_i + e_j) - f(x + e_i - e_j)
                                 - f(x - e_i + e_j) + f(x - e_i - e_j)) / (4 * eps * eps)
    return H


def weibull_ph(df: pd.DataFrame, duration: str, event: str, covariates: list[str]) -> FitResult:
    """Weibull PH: h(t|x) = (k/lam) (t/lam)^(k-1) exp(x'beta).

    Parameters are returned as log_shape, log_scale and the betas.
    """
    t = df[duration].to_numpy(float)
    d = df[event].to_numpy(int)
    X = df[covariates].to_numpy(float)
    logt = np.log(t)

    def negll(theta):
        logk, loglam, beta = theta[0], theta[1], theta[2:]
        k = np.exp(logk)
        lp = X @ beta
        z = k * (logt - loglam)
        ll = (d * (logk - loglam + (k - 1) * (logt - loglam) + lp)).sum() - np.exp(z + lp).sum()
        return -ll

    x0 = np.r_[0.0, np.log(t.mean()), np.zeros(X.shape[1])]
    res = optimize.minimize(negll, x0, method="BFGS", options={"gtol": 1e-7, "maxiter": 2000})
    cov = np.linalg.inv(_numerical_hessian(negll, res.x))
    names = ["log_shape", "log_scale", *covariates]
    return FitResult(pd.Series(res.x, names), pd.Series(np.sqrt(np.diag(cov)), names), -float(res.fun), cov)
