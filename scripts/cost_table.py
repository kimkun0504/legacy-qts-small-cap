"""Round-trip cost of one position by sleeve size, on the pre-registered universe.

Universe on each date: point-in-time market-cap ranks 1,001-3,000 among names priced >= $5
with 20-day median dollar volume >= $1M.  Position = 90 % of the sleeve / 28 names; a
round trip is one MOC buy plus one MOC sell of that position in whole shares (base case),
or the same in the continuous session paying the EDGE half-spread (stress, an upper bound).
Annual drag assumes 4 weekly tranches of 7 names, each replaced in full when it rotates
(52 x 7 round trips a year), i.e. drag = 52 * 7 * (0.9 / 28) * round-trip cost.
Pre-seal dates only.  Prints a markdown table.
"""

from __future__ import annotations

import os
import sys

import duckdb
import numpy as np
import pandas as pd

from qts_smallcap.costs.model import SmallAccountCostModel
from qts_smallcap.paths import LOCAL_STORE, SHARED_STORE

SH = os.environ.get("QTS_SHARED_STORE", str(SHARED_STORE))
LO = str(LOCAL_STORE)
DATES = [f"{y}-{m}" for y in range(2010, 2024) for m in ("06", "12") if f"{y}-{m}" <= "2023-06"]
SLEEVES = (10_000, 50_000, 100_000, 250_000)
N_NAMES, INVESTED = 28, 0.90


def panel(name: str, store: str, y0: int, y1: int) -> str:
    years = ",".join(f"'{store}/{name}/year={y}/data.parquet'" for y in range(y0, y1 + 1))
    return f"read_parquet([{years}])"


def universe(con, month: str) -> pd.DataFrame:
    y = int(month[:4])
    d = con.execute(
        f"select max(date) from {panel('market_cap', SH, y, y)} where strftime(date, '%Y-%m') = '{month}'"
    ).fetchone()[0]
    lo = pd.Timestamp(d) - pd.Timedelta(days=100)
    yy = (lo.year, y)
    q = f"""
    with px as (select date, ticker, value px from {panel("prices_close_raw", SH, *yy)} where date between '{lo.date()}' and '{d}'),
         vol as (select date, ticker, value v from {panel("prices_volume", SH, *yy)} where date between '{lo.date()}' and '{d}'),
         adj as (select date, ticker, value c from {panel("prices_close", SH, *yy)} where date between '{lo.date()}' and '{d}'),
         dv as (select ticker, date, px * v dv from px join vol using (date, ticker)),
         adv as (select ticker, median(dv) adv from (select *, row_number() over (partition by ticker order by date desc) rn from dv) where rn <= 20 group by ticker),
         ret as (select ticker, date, c / lag(c) over (partition by ticker order by date) - 1 r from adj),
         sig as (select ticker, stddev_samp(r) sigma from (select *, row_number() over (partition by ticker order by date desc) rn from ret) where rn <= 60 group by ticker),
         mc as (select ticker, value mcap from {panel("market_cap", SH, y, y)} where date = '{d}'),
         last as (select ticker, px from px where date = '{d}'),
         sp as (select ticker, value hs from {panel("spread_bps", LO, y, y)} where date = '{d}'),
         elig as (select ticker, mcap, px, adv, sigma, hs from mc join last using (ticker) join adv using (ticker)
                  join sig using (ticker) left join sp using (ticker) where px >= 5 and adv >= 1e6 and mcap > 0),
         rk as (select *, row_number() over (order by mcap desc) rk from elig)
    select * from rk where rk between 1001 and 3000"""
    df = con.execute(q).df()
    df["date"] = pd.Timestamp(d)
    return df


def round_trips(m: SmallAccountCostModel, u: pd.DataFrame, pos: float, venue: str) -> np.ndarray:
    """Round-trip cost (bps of the position) per name-date, vectorised by date."""
    out = []
    for d, g in u.groupby("date"):
        idx = g["ticker"].astype(str) + "@" + str(d.date())
        q = pd.Series(np.round(pos / g["px"].to_numpy()), index=idx)
        px, adv, sig = (pd.Series(g[k].to_numpy(), index=idx) for k in ("px", "adv", "sigma"))
        hs = pd.Series(g["hs"].to_numpy(), index=idx) if venue == "continuous" else None
        buy = m.order_costs(q, px, adv, sig, hs, date=d, venue=venue)["total"]
        sell = m.order_costs(-q, px, adv, sig, hs, date=d, venue=venue)["total"]
        notional = (q * px).replace(0.0, np.nan)
        out.append((1e4 * (buy + sell) / notional).to_numpy())
    return np.concatenate(out)


def main() -> int:
    con = duckdb.connect()
    m = SmallAccountCostModel()
    u = pd.concat([universe(con, mo) for mo in DATES], ignore_index=True)
    rows = []
    for sleeve in SLEEVES:
        pos = INVESTED * sleeve / N_NAMES
        rt = round_trips(m, u, pos, "auction")
        st = round_trips(m, u, pos, "continuous")
        zero = float(np.mean(np.round(pos / u["px"].to_numpy()) < 1))
        k = 52 * 7 * (INVESTED / N_NAMES) / 1e4
        rows.append(
            (
                sleeve,
                pos,
                np.nanmedian(rt),
                np.nanpercentile(rt, 90),
                k * np.nanmedian(rt),
                np.nanmedian(st),
                k * np.nanmedian(st),
                zero,
            )
        )
    print(
        f"Universe snapshots: {len(DATES)} dates {DATES[0]} .. {DATES[-1]}, {len(u):,} name-dates "
        f"({len(u) / len(DATES):,.0f} names per date), median ADV ${u['adv'].median() / 1e6:.1f}M, "
        f"median daily vol {u['sigma'].median():.3f}, median EDGE half-spread {u['hs'].median():.0f} bp"
    )
    print(
        "| Sleeve | Position | MOC round trip, median (p90) | Annual drag, MOC | Stress round trip | Annual drag, stress | Names < 1 share |"
    )
    print("|---|---|---|---|---|---|---|")
    for s, p, a, b, d, e, f, z in rows:
        print(
            f"| ${s:,.0f} | ${p:,.0f} | {a:.0f} bp ({b:.0f}) | {100 * d:.1f} %/yr | {e:.0f} bp | {100 * f:.1f} %/yr | {100 * z:.1f} % |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
