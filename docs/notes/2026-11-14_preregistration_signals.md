# Pre-registration — small-cap signal tests (2026-11-14)

Binding from the commit that adds this file. Approved by the owner on 2026-11-14 ("keep
working… make sure not to introduce any bias"), with the owner's target added as a reported
metric: **average raw stock gain per trade of 4 % before fees** (a diagnostic, never a pass
criterion). Ledger family: `smallcap_signals:<first 12 hex of this file's sha256>`.

## Data and universe (fixed; not trials)
- Weekly research panel, `qts_smallcap/research/panel.py`: formation date F = last trading
  day of each week; universe on F = market-cap ranks 1,001-3,000 among names with as-traded
  close >= $5 and trailing 63-row mean dollar volume >= $1M (>= 31 rows), all names in the
  store (survivorship-free, no prefilter). No hysteresis.
- Entry at the close of the first trading day after F; exit h trading days later; adjusted
  closes; delisting returns by reason (qts); no price on entry day = no trade.
- Seal: prices end 2023-11-08; forward windows that would cross it are dropped.
- Test windows (formation dates): S1a, S1b, S4 from 2003-06-06; S2 from 2008-02-01 (SF2
  coverage); S3 from 2013-08-16 (first SF3A availability). All end at the seal.

## Signals (one primary specification each = one counted trial each)
"New" means visible in the five trading rows (d_F - 5, d_F].
| ID | Definition | Long set on F | Primary h |
|---|---|---|---|
| S1a | Earnings-announcement return: announcement row p from `earnings_dates` (8-K item 2.02 or SF1 filing date); EAR = C(p+1)/C(p-2) - 1 minus IWM over the same rows; visible from row p+2; new announcements only | top quintile of EAR among the week's new announcements in the universe | 20 |
| S1b | SUE: `qts.alpha.features_v2.sue_by_filing` on split-adjusted ARQ EPS (seasonal random walk, 8-quarter sd, >= 4 obs); visible the trading day after the filing `datekey`; new filings only | top quintile of SUE among the week's new filings in the universe | 20 |
| S2 | Insider purchase cluster: Form 4 rows with transaction code P, non-derivative acquisition (`securityadcode` NA, which the CSV reader parsed as missing), owner is an officer or director, value > 0, by usable date (filing + 1 weekday); cluster on F = >= 2 distinct owners in [F - 30 days, F], with >= 1 of those rows in (F - 7 days, F] | all cluster names in the universe | 20 |
| S3 | 13F ownership change: io = SF3A `shrunits` / shares outstanding at the quarter end; Δio = io(q) - io(q-1) for consecutive quarters; usable at quarter end + 45 days; on F the latest usable value no older than 100 days | top quintile of Δio among universe names with a value | 20 |
| S4 | Weekly reversal without news: r5 = C(d_F)/C(d_F - 5) - 1 minus its FF48-industry mean on F (universe names; industries with < 5 names use the universe mean); names with any EVENTS row or earnings announcement in (d_F - 5, d_F] excluded; signal = minus the residual | top quintile of the signal | 5 |

Weeks with fewer than 15 names carrying the signal (S1a, S1b, S3, S4) or no cluster name (S2)
have no long set.

## Statistics and pass criteria (primary horizon; all three must hold)
For each week with a long set: e_t = mean fwd_h of long names - mean fwd_h of all universe
names with fwd_h.
1. **Significance:** Newey-West t of mean(e_t), Bartlett lag 2 x ceil(h / 5), >= 3.0
   (Harvey-Liu-Zhu), and Holm-adjusted two-sided p < 0.05 across the five tests.
2. **Net of costs:** mean over trades (long name-weeks) of fwd_h - universe mean - round-trip
   cost of a $321 position (the $10k sleeve: 90 % / 28 names), MOC, whole shares,
   `SmallAccountCostModel` with the name's price, 63-row ADV and 60-row volatility > 0.
3. **Stability:** mean e_t > 0 in >= 4 of 6 equal-length time folds of the signal's window.

## Reported, not used for selection
Per-trade raw gain (mean, median, trimmed mean, win rate, trades per year) — the owner's
4 % target; per-trade return minus IWM (the hedged view); other horizons (5, 10, 20); weekly
Spearman IC (S1a, S1b, S3, S4); results by year; S3 at a 90-day lag; S2 2008 excluded.

## Accounting
Each primary test is one trial. A rerun after a bug fix with the specification unchanged is
logged as `rerun_bugfix`, not a new trial; any change of specification is a new trial and the
Holm/DSR counts grow. Construction trials (combination weights, names per tranche 6 vs 8,
no-trade band, volatility targeting, top-few-percent selectivity) come after these tests;
the total budget stays <= 10 plus one confirmatory unseal, which needs the owner's explicit
go-ahead.
