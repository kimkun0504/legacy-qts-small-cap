# 2026-11-14 — SF2, SF3, SF3A, SF3B and SFP loaded into this project's store

Command (run in the session VM against the owner's folders; same command works on the Mac):
`python -m qts_smallcap.data.sharadar_ext --bulk "<Sharadar Bulk>" --tables SF3B SF3A SF2 SFP SF3`.
Ids were resolved against the shared store's `security_master` as of the qts incremental sync
the owner ran at 11:59 ET the same day (15,723 ids). Nothing was written to the shared store.

| Table | Raw rows (= CSV lines) | Kept | Dropped (not in stock master) | Vendor dates | Stored `date` (first usable day) | Ids |
|---|---|---|---|---|---|---|
| SF2 (insider forms) | 11,597,970 | 11,396,054 | 201,916 | filings 2008-01-02 .. 2026-11-06 | filing + 1 day, weekday | 10,336 |
| SF3 (13F by investor x ticker) | 82,490,068 | 60,376,059 | 22,114,009 | quarters 2013-06-30 .. 2026-09-30 | quarter end + 45 days, weekday | 9,851 |
| SF3A (13F by ticker) | 682,593 | 292,131 | 390,462 | quarters 2013-06-30 .. 2026-09-30 | quarter end + 45 days, weekday | 9,851 |
| SF3B (13F by investor) | 311,402 | 311,402 | — (no ticker) | quarters 2013-06-30 .. 2026-09-30 | quarter end + 45 days, weekday | — |
| SFP (fund/ETF prices) | 15,864,132 | 15,864,132 | — (vendor tickers) | 1997-12-31 .. 2026-11-09 | price date | 10,056 tickers |

`scripts/validate_ext_tables.py`: all checks pass — stored rows = kept rows; no duplicate keys
(SFP date x ticker; SF3A quarter x ticker; SF3B quarter x investor; SF3 quarter x ticker x
investor x security type); SF2 usable 1–3 days after filing; 13F usable 45–47 days after the
quarter end; IWM 2000-05-26 .. 2026-11-09 (6,653 days), close 297.67 on 2026-08-20 (iShares NAV
297.55), largest daily |return| 13.3 %; AAPL 13F value/units ratio tracks the share price
through the 2023 SEC change from $ thousands to dollars (no ×1000 jump).

## Findings that change the plan
1. **13F data starts in Q2 2013**, not 2003. Signal S3 has about ten pre-seal years
   (2013-08 .. 2023-11), i.e. roughly 40 quarterly observations: low power; expect a wide
   confidence interval. Record this in the S3 test spec before running it.
2. **Insider data starts in 2008** (filings from 2008-01-02). S2 has about 16 pre-seal years.
   Open-market purchases (code P) run at about 17k–42k filings a year on 1,800–2,800 names
   (2008: 104k, possibly a backfill year — check before using 2008).
3. 211 SF2 rows have a transaction date after the filing date (vendor quirk). The stored
   `date` never uses the transaction date, so they cannot leak.
4. 13F residual look-ahead: late filings and amendments folded into a quarter after the
   45-day deadline cannot be removed with Sharadar's quarter-end keys. S3 must also be
   reported at a 90-day lag as a robustness diagnostic (not a separate trial).
