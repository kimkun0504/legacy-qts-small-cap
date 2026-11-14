"""Tranche backtest on a tiny synthetic market with hand-checkable numbers."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from qts_smallcap.backtest.tranches import TrancheConfig, run, select_tranches


def _market():
    days = pd.bdate_range("2024-01-01", periods=30)  # Mon 2024-01-01 .. Fri 2024-02-09
    close = pd.DataFrame({"A": 10.0, "B": 20.0, "C": 50.0}, index=days)
    close.loc[days[10] :, "A"] = 11.0  # A +10 % on day 10
    close.loc[days[15] :, "C"] = np.nan  # C stops trading after day 14 (delisted)
    iwm = pd.DataFrame({"closeadj": 100.0, "close": 100.0}, index=days)
    iwm.loc[days[12] :, ["closeadj", "close"]] = 105.0  # IWM +5 % on day 12
    flat = pd.DataFrame(1e7, index=days, columns=close.columns)
    sig = pd.DataFrame(0.02, index=days, columns=close.columns)
    return days, close, iwm, flat, sig


def test_selection_skips_names_held_in_other_slots():
    sc = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-05"] * 3 + ["2024-01-12"] * 3),
            "ticker": list("ABC") * 2,
            "score": [3, 2, 1] * 2,
        }
    )
    sel = select_tranches(sc, TrancheConfig(names_per_tranche=2))
    assert sel[sel.date == "2024-01-05"]["ticker"].tolist() == ["A", "B"]
    assert sel[sel.date == "2024-01-12"]["ticker"].tolist() == ["C"]  # A, B held in slot 0


def test_unhedged_pnl_costs_and_delisting_by_hand():
    days, close, iwm, flat, sig = _market()
    sel = pd.DataFrame(
        {"date": pd.to_datetime(["2024-01-05", "2024-01-05"]), "slot": 0, "ticker": ["A", "C"]}
    )
    delist = pd.DataFrame({"ticker": ["C"], "dr": [-0.30], "delisting_date": [days[14]]})
    cfg = TrancheConfig(sleeve_usd=10_000, n_tranches=4, names_per_tranche=2, hedge=False)
    out = run(sel, close, close, flat, sig, iwm, delist, cfg)
    nav, tr = out["nav"]["nav"], out["trades"]
    # entry Mon 2024-01-08 (day 5): budget 0.9 * 10,000 / 4 = 2,250 -> 1,125 each
    buys = tr[tr.kind == "buy"].set_index("ticker")
    assert buys.loc["A", "shares"] == 112 and buys.loc["C", "shares"] == 22  # round(1125/10), round(1125/50)
    cost = buys["fees"].sum() + buys["impact"].sum()
    assert math.isclose(nav.iloc[5], 10_000 - cost, rel_tol=1e-9)
    # A gains 10 % of 1,120 on day 10; C (1,100) delists after day 14 with -30 %
    assert math.isclose(nav.iloc[10] - nav.iloc[9], 112.0, rel_tol=1e-9)
    assert math.isclose(nav.iloc[15] - nav.iloc[14], -0.30 * 1_100, rel_tol=1e-9)
    assert out["nav"]["n_names"].iloc[16] == 1


def test_hedge_loses_when_iwm_rises():
    _days, close, iwm, flat, sig = _market()
    sel = pd.DataFrame({"date": pd.to_datetime(["2024-01-05"]), "slot": 0, "ticker": ["B"]})
    cfg = TrancheConfig(sleeve_usd=10_000, names_per_tranche=1, hedge=True, hedge_borrow_annual=0.0)
    out = run(
        sel, close, close, flat, sig, iwm, pd.DataFrame(columns=["ticker", "dr", "delisting_date"]), cfg
    )
    n = out["nav"]
    short_sh = n["short"].iloc[6] / 100.0
    assert short_sh == round(2_240 / 100)  # beta 1 x long value (112 sh x $20) / $100
    assert math.isclose(n["nav"].iloc[12] - n["nav"].iloc[11], -short_sh * 5.0, rel_tol=1e-9)
