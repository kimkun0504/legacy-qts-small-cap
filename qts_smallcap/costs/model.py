"""Small-account cost model: the qts cost model at the sleeve's real order sizes.

Execution is MOC (closing auction): the backtest fills at the official close, which is the
auction price, so the cost against the backtest price is **fees + auction impact**; no
half-spread is paid.  The stress case prices the same orders in the continuous session,
paying the EDGE half-spread from ``spread_bps`` (an upper bound in this data: see below).

* Fees: :class:`qts.tcost.fees.FeeSchedule` (IBKR Pro Tiered 2026: $0.0035/share, **$0.35
  minimum per order**, 1 % cap; clearing, CAT, dated SEC Section 31 and FINRA TAF on sells)
  applied per order to whole shares.  The per-order minimum is what dominates at $10k:
  a $300 order pays at least 0.35 / 300 = 11.7 bp of commission.
* Closing-auction exchange fee: ``auction_exchange_fee_per_share`` = $0.0015 (placeholder,
  above qts's $0.001 until IBKR's NYSE/Nasdaq closing-cross fees are checked).
* Impact: qts square-root law ``b * sigma * Q^1.5 / V^0.5`` with V = auction share (9 %) x
  ADV and ``b = impact_b_auction`` (0.3, qts design decision D2).
* Hedge carry: a short IWM pays the fund's distributions (charged through ``closeadj``
  returns) and a borrow fee; short-sale proceeds earn interest only when the **account**
  NAV exceeds $100k (IBKR), so the rebate is 0 for this account (NAV < $100k, owner
  2026-11-14).  Cash interest is ignored (pro-rated below $100k; conservative).

Spread levels: the high-low estimators (``qts_smallcap.costs.spreads``) put the median
half-spread of the 1,000 largest stocks at 18-30 bp on pre-seal dates (2005-2022), several
times the real quoted spread of such names, probably because Sharadar's daily high/low
include prints the estimators treat as spread.  They are therefore used only as an upper
bound (the continuous-session stress), never as the base case.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from qts.core.config import CostConfig
from qts.core.costs import VENUE_AUCTION, LiquiditySnapshot
from qts.tcost.model import TransactionCostModel

#: IBKR pays short-sale proceeds interest only above this account NAV (USD)
SHORT_REBATE_NAV_USD = 100_000.0


@dataclass(frozen=True)
class SmallAccountConfig:
    auction_exchange_fee_per_share: float = 0.0015
    impact_b_auction: float = 0.3
    impact_b_continuous: float = 1.0
    auction_share: float = 0.09
    hedge_borrow_fee_annual: float = 0.005
    account_nav_usd: float = 50_000.0
    short_rebate_annual_if_eligible: float = 0.0  # set to (benchmark - spread) when NAV > $100k

    def cost_config(self) -> CostConfig:
        return replace(
            CostConfig(),
            auction_exchange_fee_per_share=self.auction_exchange_fee_per_share,
            impact_b_auction=self.impact_b_auction,
            impact_b_continuous=self.impact_b_continuous,
            auction_volume_share_default=self.auction_share,
        )

    @property
    def short_rebate_annual(self) -> float:
        return self.short_rebate_annual_if_eligible if self.account_nav_usd > SHORT_REBATE_NAV_USD else 0.0


def whole_shares(target_usd: pd.Series, price: pd.Series) -> pd.Series:
    """Signed whole-share order sizes nearest to ``target_usd / price`` (0 where price <= 0)."""
    p = price.reindex(target_usd.index).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where((p > 0) & np.isfinite(p), np.round(target_usd.to_numpy(float) / p.to_numpy()), 0.0)
    return pd.Series(q, index=target_usd.index, name="shares")


class SmallAccountCostModel:
    """Per-order costs in USD for whole-share orders at the sleeve's real sizes."""

    def __init__(self, cfg: SmallAccountConfig | None = None):
        self.cfg = cfg or SmallAccountConfig()
        self.tcm = TransactionCostModel(self.cfg.cost_config(), default_venue=VENUE_AUCTION)

    def order_costs(
        self,
        shares: pd.Series,
        price: pd.Series,
        adv_usd: pd.Series,
        sigma_daily: pd.Series,
        half_spread_bps: pd.Series | None = None,
        date=None,
        venue: str = VENUE_AUCTION,
    ) -> pd.DataFrame:
        """USD ``spread``, ``impact``, ``fees`` and ``total`` per order (signed ``shares``;
        negative = sell).  ``venue`` "auction" (base case, MOC) or "continuous" (stress)."""
        idx = shares.index
        px = price.reindex(idx).astype(float)
        hs = (half_spread_bps.reindex(idx) / 1e4) if half_spread_bps is not None else None
        snap = LiquiditySnapshot(
            date=None if date is None else pd.Timestamp(date),
            tickers=idx,
            price=px,
            adv_usd=adv_usd.reindex(idx),
            adv_shares=adv_usd.reindex(idx) / px,
            sigma_daily=sigma_daily.reindex(idx),
            half_spread=hs if hs is not None else pd.Series(np.nan, index=idx),
            borrow_fee=pd.Series(0.0, index=idx),
            auction_share=self.cfg.auction_share,
        )
        trades_usd = (shares.astype(float) * px).fillna(0.0)
        est = self.tcm.estimate(trades_usd, snap, venue=venue, date=date)
        f = est.frame
        out = pd.DataFrame(
            {"spread": f["spread_usd"], "impact": f["impact_usd"], "fees": f["fees_usd"]}, index=idx
        )
        out["total"] = out.sum(axis=1)
        out["notional"] = trades_usd.abs()
        with np.errstate(divide="ignore", invalid="ignore"):
            out["bps"] = np.where(out["notional"] > 0, 1e4 * out["total"] / out["notional"], 0.0)
        return out

    def round_trip_bps(
        self,
        notional_usd: float,
        price: float,
        adv_usd: float,
        sigma_daily: float,
        half_spread_bps: float | None = None,
        date=None,
        venue: str = VENUE_AUCTION,
    ) -> float:
        """Buy then sell ``notional_usd`` of one name (whole shares), cost in bps of the notional."""
        q = float(np.round(notional_usd / price))
        if q <= 0:
            return float("nan")
        idx = pd.Index(["buy", "sell"])
        c = self.order_costs(
            pd.Series([q, -q], index=idx),
            pd.Series(price, index=idx),
            pd.Series(adv_usd, index=idx),
            pd.Series(sigma_daily, index=idx),
            None if half_spread_bps is None else pd.Series(half_spread_bps, index=idx),
            date=date,
            venue=venue,
        )
        return float(1e4 * c["total"].sum() / (q * price))

    def hedge_carry_usd(self, short_usd: float, days: float) -> float:
        """Borrow fee minus proceeds rebate for a short of ``short_usd`` held ``days`` calendar
        days (distributions are charged through ``closeadj`` returns, not here)."""
        net = self.cfg.hedge_borrow_fee_annual - self.cfg.short_rebate_annual
        return float(abs(short_usd) * net * days / 365.0)
