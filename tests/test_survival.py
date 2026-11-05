import numpy as np
import pandas as pd
import pytest
from scipy import optimize

from metro_analytics.analysis.survival import cox_ph, kaplan_meier, logrank_test, weibull_ph


def test_kaplan_meier_hand_example():
    km = kaplan_meier([1, 2, 2, 3, 4], [1, 1, 0, 1, 0])
    assert km.survival.round(6).tolist() == [1.0, 0.8, 0.6, 0.3]
    assert km.at_risk.tolist() == [5, 5, 4, 2]
    assert (km.ci_low <= km.survival).all() and (km.survival <= km.ci_high).all()


def test_logrank_identical_groups_is_not_significant():
    rng = np.random.default_rng(0)
    t = rng.exponential(10, 2000)
    e = rng.random(2000) < 0.8
    g = np.repeat(["a", "b"], 1000)
    _, dof, p = logrank_test(t, e, g)
    assert dof == 1 and p > 0.01


def test_logrank_detects_difference():
    rng = np.random.default_rng(1)
    t = np.r_[rng.exponential(10, 500), rng.exponential(5, 500)]
    _, _, p = logrank_test(t, np.ones(1000), np.repeat(["a", "b"], 500))
    assert p < 1e-6


def _breslow_negll(beta, t, d, X):
    eta = X @ beta
    ll = 0.0
    for i in np.where(d == 1)[0]:
        ll += eta[i] - np.log(np.exp(eta[t >= t[i]]).sum())
    return -ll


def test_cox_matches_direct_optimisation_with_ties():
    rng = np.random.default_rng(2)
    n = 300
    X = rng.normal(size=(n, 2))
    t = np.ceil(rng.exponential(np.exp(-X @ [0.7, -0.4]) * 20))      # integer times -> ties
    d = (rng.random(n) < 0.75).astype(int)
    df = pd.DataFrame({"t": t, "d": d, "x1": X[:, 0], "x2": X[:, 1]})
    ours = cox_ph(df, "t", "d", ["x1", "x2"]).params.to_numpy()
    ref = optimize.minimize(_breslow_negll, np.zeros(2), args=(t, d, X), method="BFGS").x
    np.testing.assert_allclose(ours, ref, atol=1e-4)


def test_weibull_ph_recovers_parameters():
    rng = np.random.default_rng(3)
    n, k, lam, beta = 20_000, 1.5, 100.0, 0.6
    x = rng.integers(0, 2, n)
    T = lam * (-np.log(rng.random(n)) * np.exp(-beta * x)) ** (1 / k)
    C = rng.uniform(0, 250, n)
    df = pd.DataFrame({"t": np.minimum(T, C), "d": (T < C).astype(int), "x": x})
    fit = weibull_ph(df, "t", "d", ["x"])
    assert np.exp(fit.params["log_shape"]) == pytest.approx(k, rel=0.03)
    assert np.exp(fit.params["log_scale"]) == pytest.approx(lam, rel=0.05)
    assert fit.params["x"] == pytest.approx(beta, abs=0.05)
