# Pre-registration addendum 4 — trial 9: news-free reversal in micro caps (2026-11-14)

Written after trials 1-8 (none passed; trial 8, option A: excess -0.20 % per 63 days, t -0.70)
and before running trial 9.

- **Hypothesis (from the kickoff brief):** a small account's edge is capacity, i.e. trading
  stocks too small for funds. Short-term reversal is liquidity provision and is documented to
  pay most where liquidity is scarce; S4 was the only signal with a significant gross effect
  in ranks 1,001-3,000 (NW t 3.18, 6/6 folds) but too small for the cost there.
- **Universe (micro caps):** formation Friday ranks 3,001-6,000 by market cap among names
  with as-traded close >= $2 and trailing 63-row mean dollar volume >= $100k (>= 31 rows);
  all names in the store; otherwise the panel rules of the pre-registration (entry at the
  next close, delisting returns, seal).
- **Data-error guard (fixed now):** name-weeks with a 5-day forward return above +200 % are
  excluded as possible bad prints (the count is reported); this can only lower the result.
- **Signal:** S4 unchanged (minus the FF48-industry-demeaned 5-day return; names with an
  EVENTS row or earnings announcement in the five rows excluded).
- **Test:** long set = top quintile; h = 5; formation dates 2003-06 .. the seal.
- **Pass (all three):** NW t >= 3 and Holm p < 0.05 over 9 tests; mean net per-trade excess
  after the $10k round trip ($321, MOC, the name's price, ADV and volatility) > 0; >= 4 of 6
  folds. Reported: the same diagnostics, plus the round-trip cost distribution (thin
  closing auctions make impact larger here).
- Trials counted after this one: 9 of 10. Trial 10 is used only for the tranche backtest of
  this signal if it passes; otherwise the search stops.
