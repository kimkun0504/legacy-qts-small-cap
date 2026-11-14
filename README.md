# qts-small-cap

Small-cap strategy built on the `qts` infrastructure
([kimkun0504/quant-trading-system](https://github.com/kimkun0504/quant-trading-system)).
It reads the shared Sharadar store (`../quant-trading-system/data/store`, read-only from here)
and keeps everything it creates in its own places: store tables in `data/store`, configs,
trial-ledger families (`runs/trial_ledger.jsonl`), seal file (`research/seal.yaml`) and paper
books. Nothing here touches the `qts` baseline (`configs/baseline.yaml`, ledger family
`pipeline_phase2:34d0b5de91a6`, its paper books).

## Design (summary)
- Universe: point-in-time market-cap ranks 1,001-3,000, price >= $5, 20-day median ADV >= $1M.
- Long book in 4 staggered weekly tranches (~4-week holds), IWM short hedge sized to beta;
  ramp in and out one tranche a week; <= 5 % of the sleeve per name; <= ~90 % invested.
- Weekly cadence: signals from Friday's close, MOC orders for Monday's close.
- Broker: IBKR Pro (Tiered), taxable margin account. First live sleeve $10k, scaled at most
  2x per gate. Reported per trade: raw stock gain before fees (owner's target: 4 % average),
  net of costs, and hedged.

Decisions, cost facts, the draft pre-registration and the draft scaling rule: project doc
"Small-cap strategy — decisions and draft pre-registration".

## Setup (owner's Mac)
```bash
cd ~/Desktop/qts-small-cap
python -m venv .venv && source .venv/bin/activate      # or reuse ../quant-trading-system/.venv
pip install -e ../quant-trading-system -e ".[dev]"
pytest
```

## Data: tables the qts sync does not load
```bash
python -m qts_smallcap.data.sharadar_ext --bulk "$HOME/Desktop/11-09-26 Sharadar Bulk"   # SF2 SF3 SF3A SF3B SFP
python scripts/validate_ext_tables.py
```
Writes `data/store/sharadar_{sf2,sf3,sf3a,sf3b,sfp}` and `data/store/_ext/load_report.json`.
Point-in-time anchors (the stored `date` is the first usable day): SF2 = filing date + 1 day;
13F tables = quarter end + 45 days; SFP = price date. See the module docstring and
`docs/notes/2026-11-14_ext_tables_load.md`.

## Status
- 2026-11-14: repo created; SF2/SF3/SF3A/SF3B/SFP loaded and validated. No signal tested yet.
