"""Trial 7 (addendum 2): equal-weight composite of S1a, S1b, S4 in the tranche backtest."""

from __future__ import annotations

import hashlib
import json
import os
import warnings
from pathlib import Path

import duckdb
import pandas as pd
from qts.core.config import CostConfig
from qts.validation.deflated_sharpe import deflated_sharpe_ratio_annual

from qts_smallcap.backtest.tranches import TrancheConfig, run, select_tranches, stats
from qts_smallcap.paths import LEDGER, LOCAL_STORE, REPO_ROOT, SHARED_STORE

START, N_TRIALS = "2003-06-06", 7


def composite(rd: Path) -> pd.DataFrame:
    parts = []
    for k in ("S1a", "S1b", "S4"):
        s = pd.read_parquet(rd / f"signal_{k}.parquet")[["date", "ticker", "value"]]
        s["r"] = s.groupby("date")["value"].rank(pct=True) - 0.5
        parts.append(s[["date", "ticker", "r"]])
    c = (
        pd.concat(parts)
        .groupby(["date", "ticker"], as_index=False)["r"]
        .mean()
        .rename(columns={"r": "score"})
    )
    return c[c["date"] >= pd.Timestamp(START)]


def main() -> int:
    warnings.simplefilter("ignore")
    rd = Path(LOCAL_STORE) / "_research"
    shared = os.environ.get("QTS_SHARED_STORE", str(SHARED_STORE))
    sel = select_tranches(composite(rd), TrancheConfig())
    tick = sorted(sel["ticker"].unique())
    con = duckdb.connect(
        os.environ.get("QTS_SC_PANEL_DB", str(Path.home() / "work/panel.duckdb")), read_only=True
    )
    con.execute("set enable_progress_bar=false")
    con.register("tk", pd.DataFrame({"ticker": tick}))
    px = con.execute(
        f"select date, ticker, c, craw from px2 join tk using (ticker) where date >= date '{START}'"
    ).df()
    days = pd.DatetimeIndex(
        sorted(con.execute(f"select date from cal where date >= date '{START}'").df()["date"])
    )
    con.close()
    close = px.pivot(index="date", columns="ticker", values="c").reindex(days)
    close_raw = px.pivot(index="date", columns="ticker", values="craw").reindex(days)
    panel = pd.read_parquet(rd / "weekly_panel.parquet", columns=["date", "ticker", "adv"])
    panel = panel[panel["ticker"].isin(tick)]
    adv = panel.pivot(index="date", columns="ticker", values="adv")
    sg = pd.read_parquet(rd / "sigma60.parquet")
    sigma = sg[sg["ticker"].isin(tick)].pivot(index="date", columns="ticker", values="sigma")
    fr = pd.DatetimeIndex(sorted(sel["date"].unique()))
    adv, sigma = adv.reindex(fr).ffill(), sigma.reindex(fr).ffill()
    sfp = pd.read_parquet(Path(LOCAL_STORE) / "sharadar_sfp", filters=[("ticker", "=", "IWM")])
    iwm = sfp.set_index(pd.to_datetime(sfp["date"]))[["closeadj", "close"]].reindex(days).ffill()
    reasons = pd.read_parquet(f"{shared}/delisting_reasons/all.parquet").rename(columns={"id": "ticker"})
    cc = CostConfig()
    m = {
        "acquisition": cc.delisting_return_acquisition,
        "performance": cc.delisting_return_performance,
        "voluntary": cc.delisting_return_voluntary,
        "unknown": cc.delisting_return_unknown,
    }
    reasons["dr"] = reasons["reason"].map(m).fillna(cc.delisting_return_unknown)
    out = run(
        sel, close, close_raw, adv, sigma, iwm, reasons[["ticker", "dr", "delisting_date"]], TrancheConfig()
    )
    nav, tr = out["nav"], out["trades"]
    st = stats(nav["nav"])
    yrs = st["n_days"] / 252
    dsr = deflated_sharpe_ratio_annual(
        st["sharpe"], N_TRIALS, 1.0 / yrs, st["n_days"], st["skew"], st["kurt"]
    )
    # per-trade raw gain: buy -> matching sell (or delisting) of the same name, before fees
    b = tr[tr.kind == "buy"][["day", "ticker", "notional"]].rename(columns={"day": "entry"})
    x = tr[tr.kind.isin(["sell", "delist"])][["day", "ticker", "notional"]].rename(
        columns={"day": "exit", "notional": "out"}
    )
    b = b.sort_values("entry")
    x = x.sort_values("exit")
    rt = pd.merge_asof(
        b, x, left_on="entry", right_on="exit", by="ticker", direction="forward", allow_exact_matches=False
    )
    rt = rt.dropna(subset=["out"])
    rt["gain"] = rt["out"].abs() / rt["notional"] - 1.0
    fees = float(tr["fees"].sum())
    impact = float(tr["impact"].sum())
    summary = {
        "trial": 7,
        **st,
        "dsr": dsr,
        "final_nav": float(nav["nav"].iloc[-1]),
        "fees_usd": fees,
        "impact_usd": impact,
        "orders": int((tr.kind != "delist").sum()),
        "round_trips": len(rt),
        "raw_gain_per_trade_mean": float(rt["gain"].mean()),
        "raw_gain_per_trade_median": float(rt["gain"].median()),
        "win_rate": float((rt["gain"] > 0).mean()),
        "avg_names": float(nav["n_names"].iloc[60:].mean()),
        "avg_long": float(nav["long"].iloc[60:].mean()),
        "avg_short": float(nav["short"].iloc[60:].mean()),
        "by_year": {
            str(y): round(float(v), 4)
            for y, v in nav["nav"].resample("YE").last().pct_change().dropna().items()
        },
    }
    (rd / "backtest_trial7_nav.parquet").parent.mkdir(parents=True, exist_ok=True)
    nav.to_parquet(rd / "backtest_trial7_nav.parquet")
    tr.to_parquet(rd / "backtest_trial7_trades.parquet")
    (rd / "backtest_trial7.json").write_text(json.dumps(summary, indent=1, default=float))
    fam = (
        "smallcap_signals:"
        + hashlib.sha256(
            (REPO_ROOT / "docs/notes/2026-11-14_preregistration_signals.md").read_bytes()
        ).hexdigest()[:12]
    )
    with open(LEDGER, "a") as f:
        f.write(
            json.dumps(
                {
                    "ts": pd.Timestamp.now(tz="UTC").isoformat(),
                    "kind": "construction_trial",
                    "family": fam,
                    "signal": "composite_S1a_S1b_S4_tranches",
                    "note": "addendum 2",
                    **summary,
                },
                default=float,
            )
            + "\n"
        )
    print(
        json.dumps(
            {k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary.items() if k != "by_year"}
        )
    )
    print("by year:", summary["by_year"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
