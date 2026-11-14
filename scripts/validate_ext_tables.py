"""Sanity checks of the tables written by qts_smallcap.data.sharadar_ext (prints a report).

Row counts against the load report, key uniqueness, point-in-time lags, and spot checks
(IWM prices, insider purchase counts, 13F value/units scale across the 2023 SEC change
from $ thousands to whole dollars).  Exit code 1 if a hard check fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

from qts_smallcap.paths import LOCAL_STORE, SHARED_STORE

STORE = Path(sys.argv[1]) if len(sys.argv) > 1 else LOCAL_STORE
SHARED = Path(sys.argv[2]) if len(sys.argv) > 2 else SHARED_STORE
con = duckdb.connect()
fails: list[str] = []


def q(sql: str):
    return con.execute(sql).fetchall()


def glob(name: str, store: Path = STORE) -> str:
    return f"read_parquet('{store}/{name}/year=*/*.parquet', union_by_name=true)"


def check(ok: bool, msg: str) -> None:
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)


rep = json.loads((STORE / "_ext/load_report.json").read_text())["tables"]
for t, r in sorted(rep.items()):
    n = q(f"select count(*) from {glob(r['dataset'])}")[0][0]
    check(
        n == r["counts"]["kept"],
        f"{t}: stored rows {n:,} == kept {r['counts']['kept']:,} (read {r['counts']['rows']:,})",
    )

# key uniqueness
for name, key in [
    ("sharadar_sfp", "date, ticker"),
    ("sharadar_sf3a", "calendardate, ticker"),
    ("sharadar_sf3b", "calendardate, investorid"),
    ("sharadar_sf3", "calendardate, ticker, investorid, securitytype"),
]:
    d = q(f"select count(*) from (select {key}, count(*) c from {glob(name)} group by all having c > 1)")[0][
        0
    ]
    check(d == 0, f"{name}: no duplicate ({key}) [{d} duplicated keys]")

# point-in-time lags
lo, hi = q(f"select min(date - filingdate), max(date - filingdate) from {glob('sharadar_sf2')}")[0]
check(
    lo.days >= 1 and hi.days <= 3,
    f"sharadar_sf2: usable 1-3 days after filing (min {lo.days}, max {hi.days})",
)
for name in ("sharadar_sf3", "sharadar_sf3a", "sharadar_sf3b"):
    lo, hi = q(f"select min(date - calendardate), max(date - calendardate) from {glob(name)}")[0]
    check(
        lo.days >= 45 and hi.days <= 47,
        f"{name}: usable 45-47 days after quarter end (min {lo.days}, max {hi.days})",
    )
early = q(f"select count(*) from {glob('sharadar_sf2')} where transactiondate > filingdate")[0][0]
print(
    f"INFO sharadar_sf2: {early:,} rows with transactiondate after filingdate (vendor quirk; not used for timing)"
)

# IWM
first, last, n, c_aug = q(
    f"select min(date), max(date), count(*), max(case when date = date '2026-08-20' then closeunadj end) "
    f"from {glob('sharadar_sfp')} where ticker = 'IWM'"
)[0]
print(f"INFO IWM: {first} .. {last}, {n:,} days, close 2026-08-20 = {c_aug}")
check(
    first is not None and str(first) <= "2000-06-01" and str(last) >= "2026-11-06",
    "IWM covers 2000-05 .. 2026-11",
)
check(c_aug is not None and 280 < c_aug < 315, "IWM close on 2026-08-20 near the iShares NAV of 297.5")
mx = q(
    f"select max(abs(r)) from (select closeadj / lag(closeadj) over (order by date) - 1 r "
    f"from {glob('sharadar_sfp')} where ticker = 'IWM')"
)[0][0]
check(mx < 0.20, f"IWM largest daily |return| {mx:.3f} < 20% (no unadjusted split jumps)")

# insiders: open-market purchases per year
rows = q(
    f"select year(date) y, count(*) n, count(distinct ticker) k from {glob('sharadar_sf2')} "
    f"where transactioncode = 'P' group by y order by y"
)
print(
    "INFO sharadar_sf2 open-market purchases (code P) per year: "
    + ", ".join(f"{y}:{n:,}/{k:,} names" for y, n, k in rows)
)

# 13F scale across the 2023 change ($ thousands -> dollars): value / units should look like a price
rows = q(
    f"select calendardate, shrvalue / nullif(shrunits, 0) from {glob('sharadar_sf3a')} "
    f"where ticker = 'AAPL' and calendardate between date '2022-06-30' and date '2023-12-31' order by 1"
)
print("INFO AAPL sf3a shrvalue/shrunits by quarter: " + ", ".join(f"{d}:{v:.2f}" for d, v in rows))
ratios = [v for _, v in rows if v]
check(
    len(ratios) >= 5 and max(ratios) / min(ratios) < 2,
    "13F value/units scale is consistent across 2022-2023 (no x1000 jump)",
)

print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAIL(S)'}")
sys.exit(1 if fails else 0)
