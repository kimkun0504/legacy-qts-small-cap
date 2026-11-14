"""Small-account cost model: hand-computed IBKR Tiered fees, whole shares, impact and hedge carry."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from qts_smallcap.costs.model import SmallAccountConfig, SmallAccountCostModel, whole_shares


def _one(m, shares, price=15.0, adv=2e6, sigma=0.03, date="2026-06-01", venue="auction", hs=None):
    s = pd.Series([shares], index=["X"])
    return m.order_costs(
        s,
        pd.Series([price], index=["X"]),
        pd.Series([adv], index=["X"]),
        pd.Series([sigma], index=["X"]),
        None if hs is None else pd.Series([hs], index=["X"]),
        date=date,
        venue=venue,
    ).iloc[0]


def test_moc_fees_by_hand_with_per_order_minimum():
    m = SmallAccountCostModel()
    # buy 40 sh @ $15 at the close: commission max(40*0.0035, 0.35) = 0.35; clearing 40*0.0002;
    # CAT 40*0.000003; closing-auction fee 40*0.0015; no SEC/TAF on buys
    buy = _one(m, 40)
    assert math.isclose(buy["fees"], 0.35 + 0.008 + 0.00012 + 0.06, rel_tol=1e-9)
    # sell on 2026-06-01: + SEC 600 * 20.60e-6 + TAF 40 * 0.000195
    sell = _one(m, -40)
    assert math.isclose(
        sell["fees"], 0.35 + 0.008 + 0.00012 + 0.06 + 600 * 20.60e-6 + 40 * 0.000195, rel_tol=1e-9
    )
    # FINRA TAF holiday 2026-10-01 .. 2026-12-31
    hol = _one(m, -40, date="2026-11-02")
    assert math.isclose(hol["fees"], 0.35 + 0.008 + 0.00012 + 0.06 + 600 * 20.60e-6, rel_tol=1e-9)
    # MOC pays no half-spread; impact = 0.3 * sigma * Q^1.5 / (0.09 * ADV)^0.5
    assert buy["spread"] == 0.0
    assert math.isclose(buy["impact"], 0.3 * 0.03 * 600**1.5 / (0.09 * 2e6) ** 0.5, rel_tol=1e-9)


def test_continuous_stress_pays_half_spread():
    m = SmallAccountCostModel()
    c = _one(m, 40, venue="continuous", hs=40.0)
    assert math.isclose(c["spread"], 600 * 40e-4, rel_tol=1e-9)
    assert c["fees"] < _one(m, 40)["fees"]  # no closing-auction fee in the continuous session


def test_round_trip_falls_with_order_size_until_impact_takes_over():
    m = SmallAccountCostModel()
    bps = {n: m.round_trip_bps(n, 15.0, 2e6, 0.03) for n in (250, 500, 1_000, 2_500, 10_000, 100_000)}
    # $15 stock, $2M ADV, 3 % daily vol: the $0.35 minimum dominates small orders, the
    # square-root impact (1.4 % of auction volume at $2,500) dominates from about $2k on
    assert bps[250] > bps[500] > bps[1_000]
    assert bps[1_000] < bps[2_500] < bps[10_000] < bps[100_000]
    assert 30 < bps[250] < 40  # 2 x $0.35 on $255 = 27 bp, plus clearing/auction fee/impact
    assert 20 < bps[1_000] < 26


def test_whole_shares_and_hedge_carry():
    q = whole_shares(pd.Series([300.0, -300.0, 10.0, 50.0]), pd.Series([15.0, 15.0, 0.0, 120.0]))
    assert q.tolist() == [20.0, -20.0, 0.0, 0.0]  # $50 of a $120 stock rounds to no order
    m = SmallAccountCostModel(
        SmallAccountConfig(account_nav_usd=50_000, short_rebate_annual_if_eligible=0.03)
    )
    assert m.cfg.short_rebate_annual == 0.0  # NAV below $100k: no proceeds interest
    assert math.isclose(m.hedge_carry_usd(10_000, 365), 50.0, rel_tol=1e-12)
    big = SmallAccountCostModel(
        SmallAccountConfig(account_nav_usd=150_000, short_rebate_annual_if_eligible=0.03)
    )
    assert math.isclose(big.hedge_carry_usd(10_000, 365), 10_000 * (0.005 - 0.03), rel_tol=1e-12)


@pytest.mark.parametrize("venue", ["auction", "continuous"])
def test_zero_order_costs_nothing(venue):
    m = SmallAccountCostModel()
    assert _one(m, 0, venue=venue, hs=30.0)["total"] == 0.0
