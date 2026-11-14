"""The weekly research panel: one row per (formation Friday, universe name).

Point in time, by construction:

* **Formation date F** = the last trading day of each calendar week.  Everything used to
  rank or select a name on F is known at F's close.
* **Universe on F** = market-cap ranks 1,001-3,000 among the names that pass, on F, the
  screens of :meth:`qts.data.store.ParquetStore.screened_rank_sql`: as-traded close >= $5
  and the trailing 63-row mean dollar volume >= $1M (at least 31 rows).  Ranks are taken
  over every name in the store on F, including names that later delist (survivorship-free);
  no prefilter is applied, so the prefilter look-ahead of 2026-11-14 cannot occur.
* **Entry E** = the close of the first trading day after F (MOC on Monday): the one-day lag.
  **Exit X_h** = the close h trading days after E.  ``fwd_h = C(X_h) / C(E) - 1`` from
  split- and dividend-adjusted closes.  A name that delists in (E, X_h] earns its last
  close plus the qts delisting return by reason (acquisition 0, performance -30 %,
  voluntary -10 %, unknown -30 %); a name without a price on E is dropped (no fill).
* **Seal**: every price read ends the day before ``holdout_start``; a forward window that
  would end on or after it is left NaN, so no sealed price enters any result.
* ``iwm_h``: IWM total return over the same (E, X_h] window (the hedge leg).
"""

from __future__ import annotations

import os
import tempfile

import duckdb
import pandas as pd
from qts.core.config import CostConfig

HORIZONS = (5, 10, 20)
RANK_LO, RANK_HI = 1001, 3000
MIN_PRICE, MIN_ADV, ADV_WINDOW = 5.0, 1e6, 63
PANEL_START = "2002-01-01"


def _view(con, name: str, store: str) -> None:
    con.execute(
        f"create or replace view {name} as select * from read_parquet('{store}/{name}/year=*/*.parquet')"
    )


def _has(con, t: str) -> bool:
    return bool(
        con.execute(f"select count(*) from information_schema.tables where table_name = '{t}'").fetchone()[0]
    )


def build_weekly_panel(
    shared: str,
    local: str,
    load_end: str,
    start: str = PANEL_START,
    db: str = ":memory:",
    max_steps: int = 99,
) -> pd.DataFrame | None:
    """Return the weekly panel (see module docstring) for formation dates in ``[start, load_end]``.

    ``db``: a duckdb file for the intermediate tables; steps whose table already exists are
    skipped and at most ``max_steps`` steps run per call (so a long build can be resumed
    across short shell sessions).  Returns ``None`` until the last step has run."""
    con = duckdb.connect(db)
    con.execute("set preserve_insertion_order=false")
    con.execute("set enable_progress_bar = false")
    # spill to the system temp dir, never to the working directory (a repo on the owner's disk)
    con.execute(f"set temp_directory = '{os.path.join(tempfile.gettempdir(), 'qts_smallcap_duckdb')}'")
    steps = 0
    for n in ("prices_close", "prices_close_raw", "prices_volume", "market_cap"):
        _view(con, n, shared)
    con.execute(
        f"create or replace view sfp as select * from read_parquet('{local}/sharadar_sfp/year=*/*.parquet')"
    )
    w = f"date between date '{start}' and date '{load_end}'"

    def step(table: str, sql: str) -> bool:
        nonlocal steps
        if _has(con, table):
            return True
        if steps >= max_steps:
            return False
        con.execute(sql)
        steps += 1
        return True

    ok = step(
        "px",
        f"""
        create table px as
        select p.date, p.ticker, p.value c, r.value craw, v.value sh, m.value mcap
        from (select * from prices_close where {w}) p
        left join (select * from prices_close_raw where {w}) r using (date, ticker)
        left join (select * from prices_volume where {w}) v using (date, ticker)
        left join (select * from market_cap where {w}) m using (date, ticker)
        where p.value is not null and p.value > 0""",
    )
    ok = ok and step(
        "cal",
        """
        create table cal as
        select date, row_number() over (order by date) - 1 as d,
               (date = max(date) over (partition by yearweek(date))) as friday
        from (select distinct date from px)""",
    )
    ok = ok and step(
        "px2",
        f"""
        create table px2 as
        select px.*, cal.d, cal.friday,
               avg(craw * sh) over w63 as adv, count(craw * sh) over w63 as n_adv
        from px join cal using (date)
        window w63 as (partition by ticker order by cal.d rows between {ADV_WINDOW - 1} preceding and current row)""",
    )
    ok = ok and step(
        "uni",
        f"""
        create table uni as
        select * from (
            select date, d, ticker, c, craw, mcap, adv,
                   row_number() over (partition by date order by mcap desc, ticker) rk
            from px2
            where friday and craw >= {MIN_PRICE} and adv >= {MIN_ADV} and n_adv >= {ADV_WINDOW // 2} and mcap > 0)
        where rk between {RANK_LO} and {RANK_HI}""",
    )
    if not ok:
        con.close()
        return None
    sel = ", ".join(f"x{h}.c as c_x{h}, cx{h}.date as x{h}_date" for h in HORIZONS)
    joins = " ".join(
        f"left join px2 x{h} on x{h}.ticker = u.ticker and x{h}.d = u.d + 1 + {h} "
        f"left join cal cx{h} on cx{h}.d = u.d + 1 + {h}"
        for h in HORIZONS
    )
    df = con.execute(f"""
        select u.date, u.ticker, u.rk, u.mcap, u.craw, u.adv, u.c as c_f, e.c as c_e, ce.date as e_date, {sel}
        from uni u
        left join px2 e on e.ticker = u.ticker and e.d = u.d + 1
        left join cal ce on ce.d = u.d + 1
        {joins}""").df()
    last = con.execute(
        "select ticker, max(d) last_d, arg_max(c, d) last_c, arg_max(date, d) last_date from px2 group by ticker"
    ).df()
    cal = con.execute("select date, d from cal order by d").df()
    iwm = con.execute(f"select date, closeadj from sfp where ticker = 'IWM' and {w}").df()
    con.close()

    df = df[df["c_e"].notna()].copy()  # no price on the entry day: no fill
    reasons = pd.read_parquet(f"{shared}/delisting_reasons/all.parquet")
    cfg = CostConfig()
    dr_map = {
        "acquisition": cfg.delisting_return_acquisition,
        "performance": cfg.delisting_return_performance,
        "voluntary": cfg.delisting_return_voluntary,
        "unknown": cfg.delisting_return_unknown,
    }
    reasons["dr"] = reasons["reason"].map(dr_map).fillna(cfg.delisting_return_unknown)
    last = last.merge(
        reasons.rename(columns={"id": "ticker"})[["ticker", "delisting_date", "dr"]], on="ticker", how="left"
    )
    df = df.merge(last, on="ticker", how="left")
    d_of = pd.Series(cal["d"].to_numpy(), index=pd.to_datetime(cal["date"]))
    df["e_d"] = d_of.reindex(pd.to_datetime(df["e_date"])).to_numpy()
    iwm_s = pd.Series(iwm["closeadj"].to_numpy(), index=pd.to_datetime(iwm["date"])).reindex(d_of.index)
    iwm_by_d = pd.Series(iwm_s.to_numpy(), index=d_of.to_numpy())
    n_days = len(cal)
    for h in HORIZONS:
        x_d = df["e_d"] + h
        r = df[f"c_x{h}"] / df["c_e"] - 1.0
        delisted = (
            df[f"c_x{h}"].isna()
            & df["delisting_date"].notna()
            & (df["last_d"] >= df["e_d"])
            & (df["last_d"] < x_d)
            & (
                pd.to_datetime(df["delisting_date"])
                <= pd.to_datetime(df["last_date"]) + pd.Timedelta(days=10)
            )
        )
        r = r.where(~delisted, (df["last_c"] / df["c_e"]) * (1.0 + df["dr"]) - 1.0)
        inside = x_d <= n_days - 1  # the exit day exists before the seal (cal ends at load_end)
        df[f"fwd_{h}"] = r.where(inside)
        df[f"delist_{h}"] = delisted & inside
        e_i, x_i = iwm_by_d.reindex(df["e_d"]).to_numpy(), iwm_by_d.reindex(x_d.where(inside)).to_numpy()
        df[f"iwm_{h}"] = x_i / e_i - 1.0
    keep = ["date", "ticker", "rk", "mcap", "craw", "adv", "e_date", *[f"x{h}_date" for h in HORIZONS]]
    keep += [c for h in HORIZONS for c in (f"fwd_{h}", f"iwm_{h}", f"delist_{h}")]
    out = df[keep].sort_values(["date", "rk"]).reset_index(drop=True)
    out["date"] = pd.to_datetime(out["date"])
    out["e_date"] = pd.to_datetime(out["e_date"])
    return out


def main() -> int:
    import argparse
    import time

    from qts.research.seal import load_seal

    from qts_smallcap.paths import LOCAL_STORE, SEAL_FILE, SHARED_STORE

    ap = argparse.ArgumentParser(prog="python -m qts_smallcap.research.panel")
    ap.add_argument("--shared-store", default=str(SHARED_STORE))
    ap.add_argument("--store", default=str(LOCAL_STORE))
    ap.add_argument(
        "--out", default=None, help="parquet path (default <store>/_research/weekly_panel.parquet)"
    )
    ap.add_argument("--db", default=":memory:", help="duckdb file for resumable intermediate tables")
    ap.add_argument("--max-steps", type=int, default=99)
    a = ap.parse_args()
    seal = load_seal(SEAL_FILE)
    t0 = time.time()
    df = build_weekly_panel(a.shared_store, a.store, seal.load_end_iso, db=a.db, max_steps=a.max_steps)
    if df is None:
        print(f"intermediate step done in {time.time() - t0:.0f}s; run again to continue")
        return 0
    out = a.out or f"{a.store}/_research/weekly_panel.parquet"
    import os

    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)
    print(
        f"{len(df):,} rows, {df['date'].nunique()} weeks {df['date'].min().date()} .. {df['date'].max().date()}, "
        f"seal load_end {seal.load_end_iso}, {time.time() - t0:.0f}s -> {out}"
    )
    print(df.groupby(df["date"].dt.year).size().to_string())
    print(
        "fwd_20 non-null share:",
        round(df["fwd_20"].notna().mean(), 4),
        "delisted within 20d:",
        int(df["delist_20"].sum()),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
