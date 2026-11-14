"""Newey-West t-statistic by hand, and the long-set rule."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from qts_smallcap.research.evaluate import _long_set, nw_t


def test_nw_t_lag0_is_the_iid_t_with_population_variance():
    x = np.arange(1.0, 13.0)  # 1..12
    m, t, n = nw_t(x, 0)
    var = ((x - x.mean()) ** 2).mean()
    assert n == 12 and math.isclose(m, 6.5) and math.isclose(t, 6.5 / math.sqrt(var / 12))


def test_nw_t_lag1_by_hand():
    x = np.array([1.0, -1.0, 2.0, 0.0, 3.0, 1.0, -2.0, 4.0, 0.5, 1.5])
    e = x - x.mean()
    n = len(x)
    g0 = e @ e / n
    g1 = e[1:] @ e[:-1] / n
    v = g0 + 2 * 0.5 * g1  # Bartlett weight 1 - 1/(1+1)
    _, t, _ = nw_t(x, 1)
    assert math.isclose(t, x.mean() / math.sqrt(v / n), rel_tol=1e-12)


def test_long_set_top_quintile_and_minimum_names():
    g = pd.DataFrame({"value": np.arange(20.0)})
    assert _long_set(g, event=False)["value"].tolist() == [16.0, 17.0, 18.0, 19.0]
    assert _long_set(g.iloc[:10], event=False).empty  # fewer than 15 names: no long set
    assert len(_long_set(g.iloc[:2], event=True)) == 2  # events: every name
