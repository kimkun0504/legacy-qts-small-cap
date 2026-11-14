"""Bid-ask spread estimates from daily open, high, low and close prices (Sharadar has no quotes).

Primary estimator: EDGE, Ardia, Guidotti & Kroencke (2024), "Efficient estimation of bid-ask
spreads from open, high, low, and close prices", Journal of Financial Economics 161, 103916.
It is unbiased under the paper's assumptions and far more efficient than the two older
estimators below, which both overstate small spreads when volatility is high (the 2026-11-14
check on this store: Corwin-Schultz gives a median half-spread of 24-36 bp for the 1,000
largest stocks, which is several times the real quoted spread).  :func:`edge` is a port of
the authors' reference implementation ``bidask.edge_rolling`` (v2.1.0, MIT licence) from one
price series to wide panels; ``tests/test_spreads.py`` checks it against that package.

Both estimators work on wide panels (dates x ids) of split-adjusted ``high``, ``low``,
``close`` and return the **full** relative spread S (fraction of price) per day, estimated
over a trailing window that ends on that day, so a value at ``t`` uses prices through ``t``
only (point-in-time).  :func:`half_spread_bps` turns S into the qts convention: half-spread
in basis points, ``1e4 * S / 2`` (the ``spread_bps`` panel of :class:`qts.core.types.MarketData`).

Corwin & Schultz (2012), "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low
Prices", Journal of Finance 67(2), 719-760, eqs. (14), (18)-(20) and Section II.B
(overnight adjustment).  For two consecutive days t, t+1 (after the overnight adjustment)::

    beta  = ln(H_t/L_t)^2 + ln(H_t+1/L_t+1)^2
    gamma = ln(max(H_t, H_t+1) / min(L_t, L_t+1))^2
    alpha = (sqrt(2 beta) - sqrt(beta)) / (3 - 2 sqrt 2) - sqrt(gamma / (3 - 2 sqrt 2))
    S     = 2 (e^alpha - 1) / (1 + e^alpha)

Negative two-day estimates are set to 0 before averaging over the window (the paper's
main specification).  Overnight adjustment: when L_t+1 > C_t the day-(t+1) high and low are
shifted down by L_t+1 - C_t; when H_t+1 < C_t they are shifted up by C_t - H_t+1.

Abdi & Ranaldo (2017), "A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and
Low Prices", Review of Financial Studies 30(12), 4437-4480, eq. (8) and the window
estimator of Section 2: with log prices c_t (close) and eta_t = (h_t + l_t) / 2 (mid-range)::

    S^2 = 4 E[(c_t - eta_t)(c_t - eta_t+1)],   S = sqrt(max(S^2, 0))

the expectation taken as the mean over the pairs in the window.

Day pairs are skipped when either day has zero volume, a non-positive price, or high <= low
(no range: the estimators are undefined there).  Windows with fewer than ``min_pairs``
valid pairs are NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

K = 3.0 - 2.0 * np.sqrt(2.0)
#: trading days per estimation window (about one month, as in both papers)
WINDOW = 21
MIN_PAIRS = 12


def _valid_day(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame | None
) -> pd.DataFrame:
    ok = (high > 0) & (low > 0) & (close > 0) & (high > low)
    if volume is not None:
        ok &= volume.reindex_like(high).fillna(0) > 0
    return ok


def corwin_schultz_pairs(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Two-day CS spread for each pair (t-1, t), stamped on day t; negatives set to 0;
    NaN where the pair is invalid."""
    ok = _valid_day(high, low, close, volume)
    pair_ok = ok & ok.shift(1, fill_value=False)
    c_prev = close.shift(1)
    h1, l1 = high.copy(), low.copy()
    down = l1 > c_prev  # gap up: shift day t down
    up = h1 < c_prev  # gap down: shift day t up
    shift = (l1 - c_prev).where(down, 0.0) - (c_prev - h1).where(up, 0.0)
    h1, l1 = h1 - shift, l1 - shift
    h0, l0 = high.shift(1), low.shift(1)
    beta = np.log(h0 / l0) ** 2 + np.log(h1 / l1) ** 2
    gamma = np.log(np.maximum(h0, h1) / np.minimum(l0, l1)) ** 2
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / K - np.sqrt(gamma / K)
    s = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return s.clip(lower=0.0).where(pair_ok)


def abdi_ranaldo_terms(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame | None = None
) -> pd.DataFrame:
    """``4 (c_t-1 - eta_t-1)(c_t-1 - eta_t)`` for each pair (t-1, t), stamped on day t."""
    ok = _valid_day(high, low, close, volume)
    pair_ok = ok & ok.shift(1, fill_value=False)
    c = np.log(close)
    eta = (np.log(high) + np.log(low)) / 2.0
    term = 4.0 * (c.shift(1) - eta.shift(1)) * (c.shift(1) - eta)
    return term.where(pair_ok)


def corwin_schultz(
    high, low, close, volume=None, window: int = WINDOW, min_pairs: int = MIN_PAIRS
) -> pd.DataFrame:
    """Trailing-window mean of the two-day CS estimates (full spread, fraction)."""
    s = corwin_schultz_pairs(high, low, close, volume)
    return s.rolling(window, min_periods=min_pairs).mean()


def abdi_ranaldo(
    high, low, close, volume=None, window: int = WINDOW, min_pairs: int = MIN_PAIRS
) -> pd.DataFrame:
    """Trailing-window AR estimate ``sqrt(max(mean(term), 0))`` (full spread, fraction)."""
    m = abdi_ranaldo_terms(high, low, close, volume).rolling(window, min_periods=min_pairs).mean()
    return np.sqrt(m.clip(lower=0.0))


def half_spread_bps(
    full_spread: pd.DataFrame, floor_bps: float = 2.0, cap_bps: float = 1000.0
) -> pd.DataFrame:
    """Full spread (fraction) -> half-spread in bps, clipped to ``[floor_bps, cap_bps]``.

    The floor is qts's default half-spread (``CostConfig.default_half_spread_bps``): both
    estimators return 0 for many liquid names on quiet months, and a zero spread is not
    credible.  NaN stays NaN (the cost model then uses its cross-sectional median)."""
    return (1e4 * full_spread / 2.0).clip(lower=floor_bps, upper=cap_bps)


def combined_half_spread_bps(
    high, low, close, volume=None, window: int = WINDOW, **kw
) -> dict[str, pd.DataFrame]:
    """``{"cs": ..., "ar": ..., "combined": ...}`` half-spreads in bps; combined = mean of the
    two where both exist, else whichever exists."""
    cs = half_spread_bps(corwin_schultz(high, low, close, volume, window), **kw)
    ar = half_spread_bps(abdi_ranaldo(high, low, close, volume, window), **kw)
    comb = pd.concat([cs.stack(), ar.stack()], axis=1).mean(axis=1).unstack()
    comb = comb.reindex(index=cs.index, columns=cs.columns)
    return {"cs": cs, "ar": ar, "combined": comb}


def edge(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    window: int = 63,
    min_periods: int | None = 42,
    sign: bool = False,
) -> pd.DataFrame:
    """Rolling EDGE spread (full spread, fraction) for every column of the panels.

    Same algebra as ``bidask.edge_rolling(df, window, min_periods=...)`` applied column by
    column (AGK 2024, eqs. (3)-(4) and the GMM weighting of Section 2.3): the estimator is
    a variance-weighted average of two moment conditions built from the mid-range, open and
    previous close; ``tau`` drops flat days (high == low == previous close)."""
    o, h, l, c = (np.log(x) for x in (open_, high, low, close))
    m = (h + l) / 2.0
    h1, l1, c1, m1 = h.shift(1), l.shift(1), c.shift(1), m.shift(1)
    r1, r2, r3, r4, r5 = m - o, o - m1, m - c1, c1 - m1, o - c1

    def ind(cond: pd.DataFrame, *nan_of: pd.DataFrame) -> pd.DataFrame:
        bad = nan_of[0].isna()
        for f in nan_of[1:]:
            bad |= f.isna()
        return cond.astype(float).mask(bad)

    tau = ind((h != l) | (l != c1), h, l, c1)
    po1 = tau * ind(o != h, o, h)
    po2 = tau * ind(o != l, o, l)
    pc1 = tau * ind(c1 != h1, c1, h1)
    pc2 = tau * ind(c1 != l1, c1, l1)
    r12, r15, r34, r45 = r1 * r2, r1 * r5, r3 * r4, r4 * r5
    tr1, tr2, tr4, tr5 = tau * r1, tau * r2, tau * r4, tau * r5
    x = {
        1: r12, 2: r34, 3: r15, 4: r45, 5: tau, 6: r1, 7: tr2, 8: r3, 9: tr4, 10: r5,
        11: r12**2, 12: r34**2, 13: r15**2, 14: r45**2, 15: r12 * r34, 16: r15 * r45,
        17: tr2 * r2, 18: tr4 * r4, 19: tr5 * r5, 20: tr2 * r12, 21: tr4 * r34, 22: tr5 * r15,
        23: tr4 * r45, 24: tr4 * r12, 25: tr2 * r34, 26: tr2 * r4, 27: tr1 * r45, 28: tr5 * r45,
        29: tr4 * r5, 30: tr5, 31: po1, 32: po2, 33: pc1, 34: pc2,
    }  # fmt: skip
    w = max(0, int(window) - 1)
    mp = None if min_periods is None else max(0, int(min_periods) - 1)
    first = x[1].index[0]
    mean = {}
    for k, v in x.items():
        v = v.copy()
        v.loc[first] = np.nan
        x[k] = v
        mean[k] = v.rolling(w, min_periods=mp).mean()
    pt = mean[5]
    po = mean[31] + mean[32]
    pc = mean[33] + mean[34]
    nt = x[5].rolling(w, min_periods=mp).sum()
    dead = (nt < 2) | (po == 0) | (pc == 0)
    mean = {k: v.mask(dead) for k, v in mean.items()}
    pt, po, pc = mean[5], mean[31] + mean[32], mean[33] + mean[34]
    a1, a2 = -4.0 / po, -4.0 / pc
    a3, a4, a5, a6 = mean[6] / pt, mean[9] / pt, mean[8] / pt, mean[10] / pt
    a12, a11, a22, a33, a55, a66 = 2 * a1 * a2, a1**2, a2**2, a3**2, a5**2, a6**2
    M = mean
    e1 = a1 * (M[1] - a3 * M[7]) + a2 * (M[2] - a4 * M[8])
    e2 = a1 * (M[3] - a3 * M[30]) + a2 * (M[4] - a4 * M[10])
    v1 = -(e1**2) + (
        a11 * (M[11] - 2 * a3 * M[20] + a33 * M[17])
        + a22 * (M[12] - 2 * a5 * M[21] + a55 * M[18])
        + a12 * (M[15] - a3 * M[25] - a5 * M[24] + a3 * a5 * M[26])
    )
    v2 = -(e2**2) + (
        a11 * (M[13] - 2 * a3 * M[22] + a33 * M[19])
        + a22 * (M[14] - 2 * a6 * M[23] + a66 * M[18])
        + a12 * (M[16] - a3 * M[28] - a6 * M[27] + a3 * a6 * M[29])
    )
    vt = v1 + v2
    s2 = ((v2 * e1 + v1 * e2) / vt).where(vt > 0, (e1 + e2) / 2.0)
    s = np.sqrt(np.abs(s2))
    return s * np.sign(s2) if sign else s


# =============================================================================
# Store panels
# =============================================================================
#: datasets written by :func:`build_store_panels` (half-spread bps, wide panels)
PANELS = {"edge": "spread_bps", "cs": "spread_cs_bps", "ar": "spread_ar_bps"}
#: EDGE window (trading days) for the primary panel: one quarter, for stable cost inputs
EDGE_WINDOW = 63
EDGE_MIN_PERIODS = 42
#: calendar days of history read before each year so its first windows are full
WARMUP_DAYS = 100


def build_store_panels(
    store_root: str, years: list[int] | None = None, window: int = WINDOW
) -> dict[int, int]:
    """Compute the half-spread panels from ``sep_ohlc`` year by year (with a warm-up from the
    previous year) and write them to ``store_root`` as wide panels: ``spread_bps`` (EDGE,
    63-day window: the cost input), ``spread_cs_bps`` and ``spread_ar_bps`` (21-day windows,
    diagnostics only).  Returns ``{year: non-NaN EDGE values}``."""
    from qts.data.store import ParquetStore

    store = ParquetStore(store_root)
    if years is None:
        years = sorted(
            int(p.split("year=")[1].split("/")[0]) for p in store._files("sep_ohlc") if "year=" in p
        )
    out: dict[int, int] = {}
    for y in years:
        start = pd.Timestamp(f"{y}-01-01")
        df = store.read_long(
            "sep_ohlc",
            start=start - pd.Timedelta(days=WARMUP_DAYS),
            end=pd.Timestamp(f"{y}-12-31"),
            columns=["date", "ticker", "open", "high", "low", "close", "volume"],
        )
        if df.empty:
            continue
        wide = {
            k: df.pivot(index="date", columns="ticker", values=k).sort_index()
            for k in ("open", "high", "low", "close", "volume")
        }
        res = combined_half_spread_bps(wide["high"], wide["low"], wide["close"], wide["volume"], window)
        res["edge"] = half_spread_bps(
            edge(wide["open"], wide["high"], wide["low"], wide["close"], EDGE_WINDOW, EDGE_MIN_PERIODS)
        )
        for key, name in PANELS.items():
            panel = res[key].loc[res[key].index >= start]
            store.write_wide(name, panel, mode="upsert")
        out[y] = int(res["edge"].loc[res["edge"].index >= start].notna().sum().sum())
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    from qts_smallcap.paths import LOCAL_STORE

    ap = argparse.ArgumentParser(
        prog="python -m qts_smallcap.costs.spreads", description="Build spread_bps panels from sep_ohlc"
    )
    ap.add_argument("--store", default=str(LOCAL_STORE))
    ap.add_argument("--years", nargs="*", type=int, default=None)
    a = ap.parse_args(argv)
    for y, n in build_store_panels(a.store, a.years).items():
        print(f"{y}: {n:,} EDGE half-spread values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
