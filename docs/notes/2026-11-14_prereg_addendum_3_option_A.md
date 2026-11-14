# Pre-registration addendum 3 — trial 8: slow composite, 3-month horizon (2026-11-14)

Status: **fixed before running.** Option A of the round-2 plan
(`docs/notes/2026-11-14_signal_results.md`); run only with the owner's go-ahead or, failing a
reply, as the announced default.

- **Disclosure:** value, profitability, investment and momentum are the kind of signals the
  qts baseline used; its mid-cap results (2003-2026) are known to the owner and to this
  session, which biases the choice of this family. The seal still protects the final check.
- **Signal** (`qts_smallcap.research.signals.slow_composite`): on the last formation Friday
  of each month, the mean of centred percentile ranks among universe names of earnings
  yield, book-to-price, gross profitability, minus asset growth (qts PIT panels, visible from
  the filing date + 1 day) and 12-1 momentum C(d-21)/C(d-252) - 1; names need >= 3 of 5.
- **Test:** formation dates 2003-06 .. the seal; long set = top quintile; h = 63 trading days;
  Newey-West lag 2 x ceil(63 / 21) = 6 monthly observations.
- **Pass (all three):** NW t >= 3 and Holm p < 0.05 over the 8 tests; mean net per-trade
  excess after the $10k round trip ($321 position, MOC) > 0; >= 4 of 6 folds positive.
- **Reported, not selection:** the top-1 % tail excess (whether a 28-30-name book can hold
  the edge), 5/10/20-day excess, IC, raw gain per trade (owner's 4 % target), hedged gain,
  by year.
- Trial 9 (the tranche backtest of this composite) is specified only after trial 8, in its
  own addendum, before it is run. Trials counted after trial 8: 8 of 10.
