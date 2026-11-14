# Pre-registration addendum 1 — S4 selectivity trial (2026-11-14)

Written after the five primary tests (results in `2026-11-14_signal_results.md`) and before
running this trial. It uses one of the construction trials the pre-registration lists
("top-few-percent selectivity") on the only signal with a significant gross effect (S4,
NW t 3.18, 6/6 folds, but net of the $10k cost model -0.18 % per trade).

- **Trial 6, S4-top5:** the S4 signal unchanged; long set = top 5 % of the signal (rank
  percentile > 0.95) among the week's universe names with a value; at least 15 such names
  in the week; primary h = 5; the same three pass criteria (NW t >= 3 with Holm over the
  six tests so far, net of the $10k cost model > 0, >= 4 of 6 folds).
- Rationale fixed in advance: short-term reversal is concentrated in the largest moves, and
  a $321 position pays about 28 bp a round trip whatever the signal, so a larger per-trade
  edge is the only way a weekly signal can clear the cost at this size.
- Family: same (`smallcap_signals:adb23432d840`); trials counted after this one: 6.
