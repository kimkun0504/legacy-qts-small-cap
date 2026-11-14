# Pre-registration addendum 2 — trial 7: equal-weight composite in the tranche backtest (2026-11-14)

Written after trials 1-6 (none passed) and before running trial 7. It is the
"combination weights" construction trial of the pre-registration, at its default (equal
weights), and the first run of the full construction.

- **Score on formation Friday F:** for each of S1a, S1b and S4 (the three with a positive
  gross excess in trials 1-5), the percentile rank of the signal among the universe names
  carrying it that week, minus 0.5; the composite is the mean of the ranks a name has.
  Names with none of the three are not eligible.
- **Construction:** `qts_smallcap.backtest.tranches` defaults: $10k sleeve, 4 weekly tranches x
  7 names, 90 % invested, held 4 weeks, names already held skipped, whole shares, MOC costs
  (`SmallAccountCostModel`), IWM short at beta (52-week OLS shrunk 50 % to 1), 5 % hedge band,
  0.5 %/yr borrow, no interest; formations 2003-06-06 .. the seal (2023-11-08).
- **Pass:** net hedged daily returns with a deflated Sharpe ratio > 0.95 at 7 trials
  (cross-trial variance of annual Sharpe taken as 1 / years, stated as an assumption) and net
  CAGR > 0. Reported: Sharpe, CAGR, volatility, max drawdown, longest drawdown, costs by
  component, per-trade raw gain (the owner's 4 % target), the unhedged long book.
- Trials counted after this one: 7 of the budget of 10 (plus one confirmatory unseal).
