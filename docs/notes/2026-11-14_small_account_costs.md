# 2026-11-14 — Small-account cost model and spread panels

Engineering fixes (not trials). Code: `qts_smallcap/costs/{spreads,model}.py`; tables in this
project's store: `sep_ohlc` (SEP open/high/low/close, 37.3M rows), `spread_bps` (EDGE
half-spread, 63-day window), `spread_cs_bps`, `spread_ar_bps` (21-day, diagnostics).

## What the qts cost model already had, and what was missing
- Already in `qts.tcost`: IBKR Pro Tiered fees **including the $0.35 per-order minimum**, the
  1 % cap, clearing, CAT, dated SEC Section 31 and FINRA TAF (holiday Oct-Dec 2026), a
  closing-auction fee and the square-root impact law with auction volume. The minimum only
  bites when orders are priced at their real size, which the $5M baseline never needed.
- Missing for this project: (1) orders priced at sleeve size in **whole shares**;
  (2) any spread data — real-data runs used a flat 2 bp half-spread; (3) hedge carry at an
  account below $100k (no interest on short proceeds).

## Spread estimators: a finding
EDGE (Ardia, Guidotti & Kroencke 2024; checked against the authors' `bidask` package to
1e-9), Corwin-Schultz (2012) and Abdi-Ranaldo (2017) all put the **median half-spread of the
1,000 largest stocks at 18-37 bp** on pre-seal June dates 2005-2022, several times the real
quoted half-spread of such names (low single digits). Abdi-Ranaldo is often 0 (floored at
2 bp). Likely cause: Sharadar's daily high/low include prints the estimators read as spread.
So the high-low spreads are **not** used as the base-case cost. They are used only as an upper
bound in a continuous-session stress test.

| Ranks (screened, June dates) | 2005 | 2010 | 2015 | 2019 | 2022 |
|---|---|---|---|---|---|
| 1-1,000: EDGE median half-spread | 21.5 bp | 29.5 | 18.4 | 22.0 | 30.3 |
| 1,001-2,000 | 28.8 | 36.9 | 24.0 | 28.4 | 37.2 |
| 2,001-3,000 | 49.7 | 57.7 | 34.8 | 42.0 | 32.1 |

## Base case: MOC execution
The backtest fills at the official close, which is the closing-auction price, so an MOC
order's cost against the backtest price is fees + auction impact (no half-spread).
Closing-auction exchange fee: $0.0015/share, a conservative upper bound: NYSE's 2025 price
list charges $0.0007-$0.0011 per MOC share by tier and NYSE Arca $0.0010 (checked 2026-11-14);
qts uses $0.001. The difference is about $0.01 per order at these sizes. Impact: `0.3 * sigma * Q^1.5 / (0.09 * ADV)^0.5`
(qts defaults).

## Round-trip cost by sleeve size (`scripts/cost_table.py`)
Universe snapshots on 27 pre-seal dates (June/December 2010-06 .. 2023-06): screened ranks
1,001-3,000 (price >= $5, 20-day median ADV >= $1M), about 1,640 names a date, median ADV
$6.5M, median daily vol 2.4 %. Position = 90 % of the sleeve / 28 names. Annual drag assumes
each weekly tranche of 7 names is replaced in full (52 x 7 round trips a year): an upper bound
on turnover.

| Sleeve | Position | MOC round trip, median (p90) | Annual drag, MOC | Stress: continuous + EDGE spread | Annual drag, stress |
|---|---|---|---|---|---|
| $10,000 | $321 | 27 bp (34) | 3.2 %/yr | 95 bp | 11.1 %/yr |
| $50,000 | $1,607 | 14 bp (30) | 1.7 %/yr | 83 bp | 9.8 %/yr |
| $100,000 | $3,214 | 16 bp (37) | 1.9 %/yr | 86 bp | 10.1 %/yr |
| $250,000 | $8,036 | 22 bp (53) | 2.6 %/yr | 93 bp | 10.9 %/yr |

Reading: at $10k the $0.35 minimum makes each round trip about 0.27 % of the position,
small against the owner's 4 % per-trade target. Costs are lowest around $1.5-3k positions and
rise beyond that through impact. MOC execution matters: paying a spread in the continuous
session (even at the overstated EDGE level) would cost about 10 %/yr.

Hedge carry at NAV < $100k: IWM distributions (about 0.9 %/yr, through `closeadj`) plus a 0.5 %
borrow placeholder, no proceeds rebate.
