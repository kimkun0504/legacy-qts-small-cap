# 2026-11-14 — Decision: stop the small-cap stock-selection search

The owner stopped the search at 14:57 ET after nine pre-registered trials, none of which
passed (`2026-11-14_signal_results.md`). Nothing goes live from this family
(`smallcap_signals:adb23432d840`). The sealed holdout (from 2023-11-09) was never opened, so it
stays clean for any future project.

Kept for reuse: the extra Sharadar tables and loaders, the small-account cost model, the
look-ahead-safe weekly panel, the signal tests and the tranche backtester.

Left open, if work resumes: micro-cap news-free reversal executed with passive limit
orders, which needs intraday quote data and a new pre-registered family that counts these
nine trials.
