"""Run the pre-registered signal tests from the saved signal tables; log each to the ledger.

python scripts/run_signal_tests.py S1a [S1b ...] [--note rerun_bugfix:...]
Results: data/store/_research/results_<signal>.json; ledger: runs/trial_ledger.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from qts_smallcap.paths import LEDGER, LOCAL_STORE, REPO_ROOT
from qts_smallcap.research.evaluate import evaluate, log_trial
from qts_smallcap.research.signals import SIGNALS, START

PREREG = REPO_ROOT / "docs/notes/2026-11-14_preregistration_signals.md"
FAMILY = "smallcap_signals:" + hashlib.sha256(PREREG.read_bytes()).hexdigest()[:12]


def main() -> int:
    warnings.simplefilter("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("signals", nargs="+")
    ap.add_argument("--kind", default="signal_test", help="signal_test | rerun_bugfix | diagnostic")
    ap.add_argument("--note", default="")
    ap.add_argument("--top-frac", type=float, default=0.2, help="long set = top fraction of the signal")
    ap.add_argument("--label", default=None, help="name to log (default: the signal id)")
    ap.add_argument("--panel", default="", help="panel suffix, e.g. 'micro' -> weekly_panel_micro.parquet")
    ap.add_argument("--signal-file", default=None, help="signal table name (default signal_<id>)")
    ap.add_argument(
        "--max-fwd", type=float, default=None, help="data-error guard: drop forward returns above this"
    )
    a = ap.parse_args()
    rd = Path(LOCAL_STORE) / "_research"
    sfx = f"_{a.panel}" if a.panel else ""
    panel = pd.read_parquet(rd / f"weekly_panel{sfx}.parquet")
    panel = panel.merge(pd.read_parquet(rd / f"sigma60{sfx}.parquet"), on=["date", "ticker"], how="left")
    if a.max_fwd is not None:
        for c in [c for c in panel.columns if c.startswith("fwd_")]:
            bad = panel[c] > a.max_fwd
            print(f"data-error guard: {int(bad.sum()):,} {c} values above {a.max_fwd:+.0%} dropped")
            panel.loc[bad, c] = float("nan")
    for k in a.signals:
        sig = pd.read_parquet(rd / f"{a.signal_file or 'signal_' + k}.parquet")
        label = a.label or k
        step = 21 if k == "A" else 5  # monthly formation for the slow composite
        res = evaluate(
            label, sig, panel, SIGNALS[k][1], START[k], event=(k == "S2"), top_frac=a.top_frac, step_days=step
        )
        (rd / f"results_{label}.json").write_text(json.dumps(asdict(res), indent=1, default=float))
        log_trial(LEDGER, FAMILY, res, kind=a.kind, note=a.note)
        r = res
        print(
            f"{label} h={r.horizon} {r.start}..{r.end} weeks={r.weeks} trades={r.trades:,} ({r.trades_per_year:,.0f}/yr) | "
            f"excess/wk {100 * r.mean_excess:.2f}% NW t={r.nw_t:.2f} p={r.p_two_sided:.4f} [t>=3: {r.pass_t}] | "
            f"net/trade {100 * r.net_excess_per_trade:.2f}% [{r.pass_net}] | folds+ {r.folds_positive}/6 [{r.pass_folds}] | "
            f"raw gain/trade mean {100 * r.raw_gain_mean:.2f}% median {100 * r.raw_gain_median:.2f}% trimmed {100 * r.raw_gain_trimmed:.2f}% "
            f"win {100 * r.win_rate:.0f}% | hedged {100 * r.hedged_mean:.2f}% | cost {r.cost_round_trip_bps:.0f}bp | "
            f"top1% {r.tail_top1_excess if r.tail_top1_excess is None else round(100 * r.tail_top1_excess, 2)}% | IC {r.ic_mean if r.ic_mean is None else round(r.ic_mean, 4)} t={r.ic_t if r.ic_t is None else round(r.ic_t, 2)} | "
            f"other {json.dumps({h: {kk: round(v, 4) for kk, v in d.items()} for h, d in r.other_horizons.items()})} | {r.seconds}s"
        )
    print("family", FAMILY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
