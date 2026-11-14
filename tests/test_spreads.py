"""High-low spread estimators: a hand-computed example and a simulated market with a known spread."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from qts_smallcap.costs import spreads as sp


def _frames(h, l, c):
    idx = pd.bdate_range("2024-01-01", periods=len(h))
    return (
        pd.DataFrame({"X": h}, index=idx),
        pd.DataFrame({"X": l}, index=idx),
        pd.DataFrame({"X": c}, index=idx),
    )


def test_corwin_schultz_hand_computation():
    # day 0: H=10.2 L=9.9 C=10.0; day 1: H=10.3 L=10.0 C=10.1 (no gap: L1 <= C0 <= H1)
    h, l, c = _frames([10.2, 10.3], [9.9, 10.0], [10.0, 10.1])
    beta = math.log(10.2 / 9.9) ** 2 + math.log(10.3 / 10.0) ** 2
    gamma = math.log(10.3 / 9.9) ** 2
    k = 3 - 2 * math.sqrt(2)
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
    s = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
    got = sp.corwin_schultz_pairs(h, l, c).iloc[1, 0]
    assert math.isclose(got, max(s, 0.0), rel_tol=1e-12, abs_tol=1e-15)
    assert np.isnan(sp.corwin_schultz_pairs(h, l, c).iloc[0, 0])  # no previous day


def test_corwin_schultz_overnight_gap_adjustment():
    # day 1 gaps up: L1 = 10.5 > C0 = 10.0, so day 1 is shifted down by 0.5 -> H=10.3, L=10.0
    h, l, c = _frames([10.2, 10.8], [9.9, 10.5], [10.0, 10.6])
    h2, l2, c2 = _frames([10.2, 10.3], [9.9, 10.0], [10.0, 10.1])
    a = sp.corwin_schultz_pairs(h, l, c).iloc[1, 0]
    b = sp.corwin_schultz_pairs(h2, l2, c2).iloc[1, 0]
    assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)


def test_abdi_ranaldo_hand_computation():
    h, l, c = _frames([10.2, 10.3], [9.9, 10.0], [10.0, 10.1])
    c0 = math.log(10.0)
    eta0 = (math.log(10.2) + math.log(9.9)) / 2
    eta1 = (math.log(10.3) + math.log(10.0)) / 2
    assert math.isclose(
        sp.abdi_ranaldo_terms(h, l, c).iloc[1, 0], 4 * (c0 - eta0) * (c0 - eta1), rel_tol=1e-12
    )


def _simulate(n_days=400, spread=0.01, sigma_d=0.02, steps=390, seed=7):
    """Efficient log price follows a random walk; every trade prints at bid or ask
    (mid -/+ spread/2, coin flip); daily H/L/C are taken from the trade prints."""
    rng = np.random.default_rng(seed)
    inc = rng.normal(0.0, sigma_d / math.sqrt(steps), size=(n_days, steps))
    logmid = np.cumsum(inc.ravel()).reshape(n_days, steps) + math.log(20.0)
    side = rng.choice([-1.0, 1.0], size=(n_days, steps))
    trade = np.exp(logmid) * (1.0 + side * spread / 2.0)
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    h = pd.DataFrame({"X": trade.max(axis=1)}, index=idx)
    l = pd.DataFrame({"X": trade.min(axis=1)}, index=idx)
    c = pd.DataFrame({"X": trade[:, -1]}, index=idx)
    return h, l, c


def test_estimators_recover_a_known_spread_on_simulated_prints():
    true = 0.01  # 100 bp full spread, 2 % daily vol
    h, l, c = _simulate(spread=true)
    cs = sp.corwin_schultz_pairs(h, l, c).mean().iloc[0]
    ar = math.sqrt(max(sp.abdi_ranaldo_terms(h, l, c).mean().iloc[0], 0.0))
    # Both papers report moderate biases with discrete intraday sampling; require the right
    # order of magnitude (within a factor of ~1.6) rather than a point match.
    assert 0.6 * true < cs < 1.6 * true, cs
    assert 0.6 * true < ar < 1.6 * true, ar


def test_windows_are_point_in_time():
    h, l, c = _simulate(n_days=80)
    full = sp.combined_half_spread_bps(h, l, c)["combined"]
    cut = sp.combined_half_spread_bps(h.iloc[:50], l.iloc[:50], c.iloc[:50])["combined"]
    pd.testing.assert_frame_equal(full.iloc[:50], cut)  # value at t never uses t+1


def test_half_spread_floor_cap_and_zero_volume_days():
    h, l, c = _simulate(n_days=60)
    v = pd.DataFrame(1.0, index=h.index, columns=h.columns)
    v.iloc[10:40] = 0.0  # 30 dead days -> too few valid pairs in windows covering them
    out = sp.combined_half_spread_bps(h, l, c, v)
    assert out["combined"].iloc[30:41].isna().all().all()
    hs = sp.half_spread_bps(pd.DataFrame({"X": [0.0, 0.5, np.nan]}))
    assert hs["X"].iloc[0] == 2.0 and hs["X"].iloc[1] == 1000.0 and np.isnan(hs["X"].iloc[2])


def test_edge_matches_the_authors_reference_implementation():
    bidask = __import__("pytest").importorskip("bidask")
    rng = np.random.default_rng(3)
    n, steps = 300, 120
    idx = pd.bdate_range("2021-01-01", periods=n)
    cols = {}
    for j, spread in enumerate([0.002, 0.01, 0.03]):
        inc = rng.normal(0.0, 0.02 / math.sqrt(steps), size=(n, steps))
        logmid = np.cumsum(inc.ravel()).reshape(n, steps) + math.log(15.0)
        trade = np.exp(logmid) * (1.0 + rng.choice([-1.0, 1.0], size=(n, steps)) * spread / 2.0)
        cols[j] = pd.DataFrame(
            {"open": trade[:, 0], "high": trade.max(1), "low": trade.min(1), "close": trade[:, -1]}, index=idx
        )
    cols[1].iloc[:40] = np.nan  # a name that lists later
    panel = {k: pd.DataFrame({j: cols[j][k] for j in cols}) for k in ("open", "high", "low", "close")}
    got = sp.edge(panel["open"], panel["high"], panel["low"], panel["close"], window=63, min_periods=42)
    for j in cols:
        ref = bidask.edge_rolling(cols[j], window=63, min_periods=42)
        pd.testing.assert_series_equal(got[j], ref, check_names=False, rtol=1e-9, atol=1e-12)
    # Recovery: close for spreads that are large relative to daily volatility (1 %, 3 % at
    # 2 % vol); for a 0.2 % spread the window estimate is noisy and, because it is the root
    # of |s^2|, biased upward (conservative for costs): require only the right magnitude.
    means = [got[j].mean() for j in cols]
    assert abs(means[1] / 0.01 - 1) < 0.25 and abs(means[2] / 0.03 - 1) < 0.25, means
    assert 0.5 < means[0] / 0.002 < 2.5, means
