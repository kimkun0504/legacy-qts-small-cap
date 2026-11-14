"""Tranche backtest: the construction fixed in the decisions doc §3, priced at the sleeve's
real order sizes.

Weekly, on formation Friday F (signals known at F's close):
* the tranche slot formed four weeks earlier is sold and a new one bought, both at the close
  of the next trading day E (MOC); a position is therefore held about 20 trading days;
* the new tranche takes the ``names_per_tranche`` best-ranked universe names on F that are not
  already held (so no name exceeds one tranche: <= 1/28 of 90 % of the sleeve, under the 5 % cap);
* each tranche gets 90 % of the sleeve's NAV / 4 at formation, split equally, in **whole
  shares** at the as-traded close on E; the first four weeks ramp in one tranche at a time;
* the IWM short is set at E to beta x the long book, beta = 0.5 x the trailing 52-week OLS
  beta of the long book on IWM (weekly returns, past data only) + 0.5 x 1, adjusted only when
  the change exceeds one share and 5 % of the hedge.
Daily marking from adjusted closes (dividends included; a short pays IWM's distributions).
Costs: every order through :class:`qts_smallcap.costs.model.SmallAccountCostModel` (MOC);
hedge borrow 0.5 %/yr; no interest on cash or short proceeds (account NAV < $100k).
A position whose price series ends before its exit is closed at its last close plus the
delisting return by reason.  Every price used is before the seal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from qts_smallcap.costs.model import SmallAccountConfig, SmallAccountCostModel


@dataclass(frozen=True)
class TrancheConfig:
    sleeve_usd: float = 10_000.0
    n_tranches: int = 4
    names_per_tranche: int = 7
    invested: float = 0.90
    hedge: bool = True
    beta_window_weeks: int = 52
    beta_shrink: float = 0.5
    hedge_borrow_annual: float = 0.005
    hedge_band: float = 0.05


def select_tranches(scores: pd.DataFrame, cfg: TrancheConfig) -> pd.DataFrame:
    """``scores``: date (formation Friday), ticker, score (higher = buy).  Returns date, slot,
    ticker for each week's new tranche, skipping names held in the other open slots."""
    out = []
    open_slots: dict[int, set[str]] = {}
    for i, (d, g) in enumerate(scores.sort_values("date").groupby("date", sort=True)):
        slot = i % cfg.n_tranches
        held = set().union(*(v for k, v in open_slots.items() if k != slot)) if open_slots else set()
        pick = g[~g["ticker"].isin(held)].nlargest(cfg.names_per_tranche, "score")["ticker"].tolist()
        open_slots[slot] = set(pick)
        out += [(d, slot, t) for t in pick]
    return pd.DataFrame(out, columns=["date", "slot", "ticker"])


def run(
    sel: pd.DataFrame,
    close: pd.DataFrame,
    close_raw: pd.DataFrame,
    adv: pd.DataFrame,
    sigma: pd.DataFrame,
    iwm: pd.DataFrame,
    delist: pd.DataFrame,
    cfg: TrancheConfig | None = None,
    cost_cfg: SmallAccountConfig | None = None,
) -> dict[str, pd.DataFrame]:
    """Daily simulation.  ``close``/``close_raw``: wide (trading days x tickers, adjusted /
    as-traded); ``adv``/``sigma``: wide on formation dates (ffilled to days is fine);
    ``iwm``: index = days, columns ``closeadj``, ``close``; ``delist``: ticker -> ``dr``,
    ``delisting_date``.  Returns ``nav`` (daily), ``trades`` (one row per order), ``book``."""
    cfg = cfg or TrancheConfig()
    cm = SmallAccountCostModel(cost_cfg)
    days = close.index
    fridays = sorted(sel["date"].unique())
    # entry day for each formation Friday: the next trading day
    entry = {}
    for f in fridays:
        j = days.searchsorted(pd.Timestamp(f), side="right")
        if j < len(days):
            entry[days[j]] = pd.Timestamp(f)
    sel_by_f = dict(list(sel.groupby("date")))
    ret = close.pct_change(fill_method=None)
    iwm_r = iwm["closeadj"].reindex(days).pct_change(fill_method=None).fillna(0.0)
    iwm_px = iwm["close"].reindex(days).ffill()
    dr = delist.set_index("ticker")["dr"] if len(delist) else pd.Series(dtype=float)
    last_valid = close.apply(lambda s: s.last_valid_index())

    nav = cfg.sleeve_usd
    cash = nav
    slots: dict[int, dict[str, float]] = {}  # slot -> ticker -> market value
    slot_entry: dict[int, pd.Timestamp] = {}
    short_sh = 0.0  # IWM shares short
    rows, trades = [], []
    book_hist: list[tuple[pd.Timestamp, float, float]] = []  # (day, long value, long return)
    week_i = 0

    def order(ticker: str, shares: float, px: float, day, kind: str, a: float, s: float) -> float:
        if shares == 0:
            return 0.0
        c = cm.order_costs(
            pd.Series([shares], index=[ticker]),
            pd.Series([px], index=[ticker]),
            pd.Series([a], index=[ticker]),
            pd.Series([s], index=[ticker]),
            date=day,
        ).iloc[0]
        trades.append((day, ticker, kind, shares, px, shares * px, c["fees"], c["impact"]))
        return float(c["total"])

    for k, day in enumerate(days):
        # 1. mark to market from yesterday's close
        long_val_prev = sum(sum(v.values()) for v in slots.values())
        pnl = 0.0
        for s_id in list(slots):
            for t, v in list(slots[s_id].items()):
                r = ret.at[day, t] if t in ret.columns else np.nan
                if np.isnan(r):
                    if last_valid.get(t) is not None and day > last_valid[t]:
                        # delisted: last close plus the delisting return, then gone
                        d_r = float(dr.get(t, -0.30))
                        pnl += v * d_r
                        cash += v * (1 + d_r)
                        trades.append((day, t, "delist", 0.0, np.nan, v * (1 + d_r), 0.0, 0.0))
                        del slots[s_id][t]
                    continue
                pnl += v * r
                slots[s_id][t] = v * (1 + r)
        short_val = short_sh * float(iwm_px.iat[k - 1]) if (short_sh and k > 0) else 0.0
        # a short of value V loses V x the total return (price change plus the distribution owed)
        h_pnl = -short_val * float(iwm_r.iat[k])
        borrow = short_val * cfg.hedge_borrow_annual / 252.0
        nav += pnl + h_pnl - borrow
        cash += h_pnl - borrow
        long_val = sum(sum(v.values()) for v in slots.values())
        if long_val_prev > 0:
            book_hist.append((day, long_val, pnl / long_val_prev))
        cost_today = 0.0
        # 2. rotation at the close of entry days
        if day in entry:
            f = entry[day]
            slot = week_i % cfg.n_tranches
            week_i += 1
            # sell the slot being replaced
            for t, v in list(slots.get(slot, {}).items()):
                px = close_raw.at[day, t] if t in close_raw.columns else np.nan
                if np.isnan(px) or px <= 0:
                    continue
                sh = -round(v / px)
                cost_today += order(
                    t,
                    sh,
                    px,
                    day,
                    "sell",
                    adv.at[f, t] if t in adv.columns else 1e6,
                    sigma.at[f, t] if t in sigma.columns else 0.03,
                )
                cash += v
            slots[slot] = {}
            # buy the new tranche
            budget = cfg.invested * nav / cfg.n_tranches
            picks = sel_by_f[f]["ticker"].tolist()
            if picks:
                each = budget / len(picks)
                for t in picks:
                    px = close_raw.at[day, t] if t in close_raw.columns else np.nan
                    if np.isnan(px) or px <= 0 or np.isnan(close.at[day, t]):
                        continue  # no price on the entry day: no fill
                    sh = round(each / px)
                    if sh <= 0:
                        continue
                    a = adv.at[f, t] if t in adv.columns else 1e6
                    s = sigma.at[f, t] if t in sigma.columns else 0.03
                    cost_today += order(
                        t, sh, px, day, "buy", a if np.isfinite(a) else 1e6, s if np.isfinite(s) else 0.03
                    )
                    slots[slot][t] = sh * px
                    cash -= sh * px
            slot_entry[slot] = day
            # hedge
            if cfg.hedge:
                long_val = sum(sum(v.values()) for v in slots.values())
                beta = 1.0
                if len(book_hist) > 5 * 26:
                    bh = pd.DataFrame(book_hist, columns=["day", "v", "r"]).set_index("day")["r"]
                    wk = (1 + bh).resample("W-FRI").prod() - 1
                    iw = (1 + iwm_r.reindex(bh.index)).resample("W-FRI").prod() - 1
                    w = pd.concat([wk, iw], axis=1).dropna().iloc[-cfg.beta_window_weeks :]
                    if len(w) >= 26 and w.iloc[:, 1].var() > 0:
                        b_hat = float(np.cov(w.iloc[:, 0], w.iloc[:, 1])[0, 1] / w.iloc[:, 1].var())
                        beta = cfg.beta_shrink * b_hat + (1 - cfg.beta_shrink) * 1.0
                ipx = float(iwm_px.iat[k])
                target = round(beta * long_val / ipx) if ipx > 0 else 0
                if abs(target - short_sh) >= max(1.0, cfg.hedge_band * max(short_sh, 1.0)):
                    cost_today += order("IWM", -(target - short_sh), ipx, day, "hedge", 2e9, 0.015)
                    short_sh = float(target)
            nav -= cost_today
            cash -= cost_today
        long_val = sum(sum(v.values()) for v in slots.values())
        rows.append(
            (
                day,
                nav,
                long_val,
                short_sh * float(iwm["close"].reindex(days).iat[k]),
                cost_today,
                sum(len(v) for v in slots.values()),
            )
        )
    nav_df = pd.DataFrame(rows, columns=["day", "nav", "long", "short", "costs", "n_names"]).set_index("day")
    tr = pd.DataFrame(
        trades, columns=["day", "ticker", "kind", "shares", "price", "notional", "fees", "impact"]
    )
    return {"nav": nav_df, "trades": tr}


def stats(nav: pd.Series) -> dict[str, float]:
    r = nav.pct_change().dropna()
    yrs = len(r) / 252
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    vol = r.std() * math.sqrt(252)
    dd = nav / nav.cummax() - 1
    under = (dd < 0).astype(int)
    longest = int(under.groupby((under == 0).cumsum()).sum().max()) if len(under) else 0
    return {
        "cagr": float(cagr),
        "vol": float(vol),
        "sharpe": float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else float("nan"),
        "max_dd": float(dd.min()),
        "longest_dd_days": longest,
        "skew": float(r.skew()),
        "kurt": float(r.kurt() + 3),
        "n_days": len(r),
    }
