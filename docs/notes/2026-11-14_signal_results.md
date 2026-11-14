# 2026-11-14 — Results of trials 1-7 (pre-seal data only)

Family `smallcap_signals:adb23432d840`; ledger `runs/trial_ledger.jsonl`. Formation dates
2003-06 .. 2023-10 (S2 from 2008-02, S3 from 2013-11); every price before the seal.

## Trials 1-6: signal tests (pre-registration and addendum 1)
| Trial | Signal (h) | Weeks | Trades/yr | Excess vs universe | NW t | Net of $10k cost | Folds + | Raw gain/trade (owner's 4 % metric) | Hedged/trade | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | S1a EAR (20) | 1,017 | 1,533 | +0.11 % | 0.87 | +0.03 % | 4/6 | 1.15 % (median 0.77) | +0.32 % | fail (t) |
| 2 | S1b SUE (20) | 795 | 1,159 | +0.12 % | 0.84 | -0.18 % | 4/6 | 1.18 % | +0.12 % | fail (t, net) |
| 3 | S2 insider clusters (20) | 819 | 1,001 | -0.27 % | -1.54 | -0.47 % | 2/6 | 1.26 % | -0.14 % | fail |
| 4 | S3 13F change (20) | 517 | 17,880 | -0.17 % | -1.29 | -0.47 % | 2/6 | 0.34 % | -0.26 % | fail |
| 5 | S4 news-free reversal (5) | 1,065 | 12,485 | +0.12 % | **3.18** | -0.18 % | 6/6 | 0.34 % | +0.12 % | fail (net) |
| 6 | S4, top 5 % (5) | 1,065 | 3,141 | +0.11 % | 1.59 | -0.20 % | 4/6 | 0.34 % | +0.12 % | fail |

Holm over six tests: only S4 (p = 0.0015 -> 0.009) is significant gross. Diagnostics (not
selection): S4 IC 0.015 (t 5.95), excess +0.13 % at 10 days and +0.20 % at 20 days; S1a +0.19 %
at 5 days (t 2.07); S3's IC is negative (-0.009, t -2.45: more 13F buying, lower returns).

## Trial 7: equal-weight composite of S1a, S1b, S4 in the tranche backtest (addendum 2)
CAGR -20.9 %, volatility 18.3 %, Sharpe -1.18, DSR 0.00, max drawdown -99 %; 5,797 round trips,
raw gain per trade +0.01 % (median -0.26 %), win rate 49 %; fees $3,645 and impact $357 on a
sleeve that shrank, so the $0.35 minimum grew to a ruinous share of each order. **Fail.**

Why: 7 names a week out of ~1,600 is the top 0.4 % of the composite, and 85 % of the picks
came from S4's extreme tail. That tail does not revert: composite top quintile +0.19 % excess
over 20 days, top 5 % 0.00 %, top 0.5 % -0.75 %. The concentrated book bought falling knives
and paid a full IWM hedge (beta about 1.16) on a long book with no edge.
Backtest reconciliation: for the same 5,797 trades the backtest's per-trade gain and the
panel's 20-day return agree (median difference 0.0, mean -0.10 % from 19-row holds over
holidays and whole-share rounding).

## Reading
1. The published short-horizon effects tested here are weak in ranks 1,001-3,000 over
   2003-2023; only news-free reversal is real, at about 0.1 % per week.
2. At a $10k sleeve a round trip costs about 28 bp ($0.35 x 2 on $321), so a weekly signal
   needs > 0.3 % per trade; none comes close. The owner's 4 % raw gain per trade is out of
   reach for these signals: the 1.2 % they show over 20 days is mostly the market.
3. Concentration is the second failure: these signals' edge lives in the top quintile, and a
   28-name book from 1,600 names picks the extreme tail, where they reverse.
4. 7 of the 10-trial budget are spent. The rest should go to hypotheses that fit the cost
   structure: holds of 2-6 months, where 28 bp is small against the move, and signals
   whose edge survives in the extreme names a small book must hold.

## Trial 8: option A, slow composite, 63 days (addendum 3)
Value (earnings yield, book-to-price), gross profitability, low asset growth and 12-1 momentum,
monthly formation 2003-06 .. 2023-07, 242 months, 78,991 trades: excess -0.20 % per 63 days
(NW t -0.70), net -0.44 %, 2/6 folds, top-1 % tail -2.63 %, IC 0.014 (t 1.27); raw gain per
trade 1.78 % (median 0.97 %), hedged -0.31 %. **Fail.**

## Trial 9: news-free reversal in micro caps (addendum 4)
Ranks 3,001-6,000 (>= $2, >= $100k ADV; median $110M cap, $408k ADV, ~570 names a week);
data-error guard dropped 73 five-day returns above +200 %. 1,051 weeks, 102,187 trades:
excess **+0.32 % per 5 days, NW t 5.22, 6/6 folds, IC 0.031 (t 11.3)**, top-1 % +0.63 %; but
the median round trip is 48 bp (thin closing auctions: impact on top of the $0.35 minimums)
and net per trade is **-0.31 %**. **Fail (net).** Holm over nine tests: S4 micro p < 1e-6.

Cost sensitivity (diagnostic, not a trial and not a pass):
| Impact assumption | $10k sleeve ($321 positions) | $50k sleeve ($1,607) |
|---|---|---|
| Base (b_auction 0.3) | round trip 48 bp, net -0.31 % | 64 bp, net -0.50 % |
| Half the impact (0.15) | 39 bp, net -0.20 % | 43 bp, net -0.26 % |
| Fees only (no impact) | 29 bp, net -0.09 % | 21 bp, net -0.01 % |
The trade-weighted gross excess is about 0.20 % (the weekly mean, 0.32 %, weights thin weeks
more), so the effect does not cover even the fees of a weekly round trip at any sleeve size.

## Where the search stands
Nine trials, none passed; the budget rule in addendum 4 stops the search (trial 10 was
reserved for a backtest of trial 9 if it passed). The one robust finding: short-term
reversal without news is real in small and especially micro caps (gross +0.12 % and +0.32 %
a week), but a weekly round trip costs more than it earns. Any next round should start from
that cost arithmetic, be pre-registered as a new family, and count these nine trials.
