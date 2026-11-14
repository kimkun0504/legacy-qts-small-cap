"""Pre-registered evaluation of one signal (docs/notes/2026-11-14_preregistration_signals.md).

Long set each week (top quintile of the signal, or every event name for S2) against the
universe mean; Newey-West t with Bartlett lag 2 x ceil(h / 5) for the overlapping weekly
samples of h-day returns; net per-trade excess after the $10k sleeve's round-trip cost;
six equal time folds.  Diagnostics: the owner's per-trade raw gain, the IWM-hedged gain,
other horizons, weekly Spearman IC, results by year.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from qts_smallcap.costs.model import SmallAccountCostModel

HLZ_T = 3.0
POSITION_USD = 0.90 * 10_000 / 28  # $321: the $10k sleeve, 28 names, 90 % invested
MIN_NAMES = 15
#: horizons present in the weekly panel
HS = (5, 10, 20, 63)


def nw_t(x: np.ndarray, lag: int) -> tuple[float, float, int]:
    """(mean, Newey-West t of the mean with Bartlett weights, n)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return float("nan"), float("nan"), n
    m = x.mean()
    e = x - m
    v = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        v += 2.0 * w * (e[k:] @ e[:-k]) / n
    se = math.sqrt(max(v, 1e-30) / n)
    return float(m), float(m / se), n


def two_sided_p(t: float) -> float:
    return float(math.erfc(abs(t) / math.sqrt(2.0))) if math.isfinite(t) else float("nan")


@dataclass
class SignalResult:
    signal: str
    horizon: int
    start: str
    end: str
    weeks: int
    trades: int
    trades_per_year: float
    mean_excess: float  # per week, long minus universe, h-day return
    nw_t: float
    p_two_sided: float
    pass_t: bool
    net_excess_per_trade: float
    pass_net: bool
    folds_positive: int
    pass_folds: bool
    raw_gain_mean: float  # the owner's 4 % target metric (h-day, before fees)
    raw_gain_median: float
    raw_gain_trimmed: float
    win_rate: float
    hedged_mean: float  # minus IWM over the same window
    cost_round_trip_bps: float
    tail_top1_excess: float | None = None  # top 1 % of the signal (diagnostic: the small-book tail)
    ic_mean: float | None = None
    ic_t: float | None = None
    other_horizons: dict = field(default_factory=dict)
    by_year: dict = field(default_factory=dict)
    seconds: float = 0.0


def _long_set(g: pd.DataFrame, event: bool) -> pd.DataFrame:
    if event:
        return g
    if len(g) < MIN_NAMES:
        return g.iloc[0:0]
    return g[g["value"].rank(pct=True) > 0.8]


def _costs(m: SmallAccountCostModel, trades: pd.DataFrame) -> pd.Series:
    out = []
    for d, g in trades.groupby("date"):
        idx = g.index
        q = pd.Series(np.round(POSITION_USD / g["craw"].to_numpy()), index=idx)
        px = pd.Series(g["craw"].to_numpy(), index=idx)
        adv = pd.Series(g["adv"].to_numpy(), index=idx)
        sig = pd.Series(g["sigma"].fillna(g["sigma"].median()).to_numpy(), index=idx)
        buy = m.order_costs(q, px, adv, sig, date=d)["total"]
        sell = m.order_costs(-q, px, adv, sig, date=d)["total"]
        out.append(1e4 * (buy + sell) / (q * px).replace(0.0, np.nan))
    return pd.concat(out).reindex(trades.index)


def evaluate(
    name: str,
    sig: pd.DataFrame,
    panel: pd.DataFrame,
    horizon: int,
    start: str,
    event: bool = False,
    top_frac: float = 0.2,
    step_days: int = 5,
    cost_model: SmallAccountCostModel | None = None,
) -> SignalResult:
    t0 = time.time()
    p = panel[panel["date"] >= pd.Timestamp(start)]
    uni_mean = {h: p.groupby("date")[f"fwd_{h}"].mean() for h in HS}
    x = p.merge(sig[["date", "ticker", "value"]], on=["date", "ticker"], how="inner")
    x = x[x[f"fwd_{horizon}"].notna()]
    if event:
        longs = x.copy()
    else:
        n_wk = x.groupby("date")["value"].transform("size")
        pct = x.groupby("date")["value"].rank(pct=True)
        longs = x[(n_wk >= MIN_NAMES) & (pct > 1.0 - top_frac)].copy()
    for h in HS:
        longs[f"ex_{h}"] = longs[f"fwd_{h}"] - longs["date"].map(uni_mean[h])
    weekly = longs.groupby("date")[f"ex_{horizon}"].mean()
    lag = 2 * math.ceil(horizon / step_days)
    mean_ex, t, n_weeks = nw_t(weekly.to_numpy(), lag)
    cost_bps = _costs(cost_model or SmallAccountCostModel(), longs)
    net = longs[f"ex_{horizon}"] - cost_bps / 1e4
    folds = np.array_split(weekly.to_numpy(), 6)
    folds_pos = int(sum(np.nanmean(f) > 0 for f in folds if len(f)))
    raw = longs[f"fwd_{horizon}"]
    years = (weekly.index.max() - weekly.index.min()).days / 365.25
    other = {}
    for h in HS:
        if h == horizon:
            continue
        wk = longs.groupby("date")[f"ex_{h}"].mean()
        mh, th, _ = nw_t(wk.to_numpy(), 2 * math.ceil(h / step_days))
        other[str(h)] = {"mean_excess": mh, "nw_t": th, "raw_gain_mean": float(longs[f"fwd_{h}"].mean())}
    ic_mean = ic_t = None
    if not event:

        def ic(g: pd.DataFrame) -> float:
            return g["value"].rank().corr(g[f"fwd_{horizon}"].rank()) if len(g) >= MIN_NAMES else np.nan

        ics = x.groupby("date")[["value", f"fwd_{horizon}"]].apply(ic).dropna()
        ic_mean, ic_t, _ = nw_t(ics.to_numpy(), lag)
    tail = None
    if not event:
        pct_all = x.groupby("date")["value"].rank(pct=True)
        t1 = x[pct_all > 0.99]
        tail = float((t1[f"fwd_{horizon}"] - t1["date"].map(uni_mean[horizon])).mean()) if len(t1) else None
    by_year = {str(y): round(float(v), 5) for y, v in weekly.groupby(weekly.index.year).mean().items()}
    lo, hi = np.nanquantile(raw, [0.05, 0.95])
    return SignalResult(
        signal=name,
        horizon=horizon,
        start=str(weekly.index.min().date()),
        end=str(weekly.index.max().date()),
        weeks=n_weeks,
        trades=len(longs),
        trades_per_year=len(longs) / years if years > 0 else float("nan"),
        mean_excess=mean_ex,
        nw_t=t,
        p_two_sided=two_sided_p(t),
        pass_t=bool(t >= HLZ_T),
        net_excess_per_trade=float(net.mean()),
        pass_net=bool(net.mean() > 0),
        folds_positive=folds_pos,
        pass_folds=folds_pos >= 4,
        raw_gain_mean=float(raw.mean()),
        raw_gain_median=float(raw.median()),
        raw_gain_trimmed=float(raw.clip(lo, hi).mean()),
        win_rate=float((raw > 0).mean()),
        hedged_mean=float((longs[f"fwd_{horizon}"] - longs[f"iwm_{horizon}"]).mean()),
        cost_round_trip_bps=float(cost_bps.median()),
        tail_top1_excess=tail,
        ic_mean=ic_mean,
        ic_t=ic_t,
        other_horizons=other,
        by_year=by_year,
        seconds=round(time.time() - t0, 1),
    )


def log_trial(
    ledger: str | Path, family: str, result: SignalResult, kind: str = "signal_test", note: str = ""
) -> None:
    rec = {
        "ts": pd.Timestamp.now(tz="UTC").isoformat(),
        "kind": kind,
        "family": family,
        "note": note,
        **asdict(result),
    }
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec, default=float) + "\n")
