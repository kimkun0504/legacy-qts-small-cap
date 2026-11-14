"""The five pre-registered signals (docs/notes/2026-11-14_preregistration_signals.md).

Each builder takes a duckdb connection holding the panel's intermediate tables (``px2``:
daily adjusted close ``c`` by ``ticker`` and trading row ``d``; ``cal``: ``date, d, friday``;
``uni``: the universe on each formation Friday) and returns ``date`` (formation Friday),
``ticker``, ``value`` for universe names only.  Every input is joined on the day it became
usable; a formation date only sees rows usable on or before it.
"""

from __future__ import annotations

import duckdb
import pandas as pd
from qts.alpha.features_v2 import sue_by_filing

#: rows of "news" before F that make an event new for F: (d_F - NEW_ROWS, d_F]
NEW_ROWS = 5


def _setup(con: duckdb.DuckDBPyConnection, shared: str, local: str) -> None:
    con.execute("set enable_progress_bar = false")
    con.execute(f"""create or replace view earnings_dates as
        select ticker, date ann from read_parquet('{shared}/earnings_dates/year=*/*.parquet')""")
    con.execute(f"""create or replace view sfp as
        select ticker, date, closeadj from read_parquet('{local}/sharadar_sfp/year=*/*.parquet')""")
    con.execute("""create or replace temp table iwm as
        select cal.d, s.closeadj ic from cal join sfp s on s.date = cal.date and s.ticker = 'IWM'""")
    con.execute("create or replace temp table fri as select date fdate, d fd from cal where friday")


def _first_row_on_or_after(table_sql: str, date_col: str) -> str:
    """SQL mapping each row of ``table_sql`` to the first trading row on or after ``date_col``."""
    return f"(select t.*, c.d as p from ({table_sql}) t asof join cal c on t.{date_col} <= c.date)"


def _to_friday(rows_sql: str, vis_col: str) -> str:
    """Rows -> the first formation Friday whose row is >= ``vis_col`` (so vis in (d_F - 5, d_F])."""
    return f"(select r.*, f.fdate, f.fd from ({rows_sql}) r asof join fri f on r.{vis_col} <= f.fd)"


def s1a_ear(con, shared: str, local: str) -> pd.DataFrame:
    """Earnings-announcement return minus IWM over rows p-2 .. p+1, visible from p+2."""
    _setup(con, shared, local)
    ann = _first_row_on_or_after("select ticker, ann from earnings_dates", "ann")
    q = f"""
        with a as (select distinct ticker, p from {ann}),
        r as (
            select a.ticker, a.p, a.p + 2 as vis,
                   (x1.c / x0.c - 1) - (i1.ic / i0.ic - 1) as value
            from a
            join px2 x0 on x0.ticker = a.ticker and x0.d = a.p - 2
            join px2 x1 on x1.ticker = a.ticker and x1.d = a.p + 1
            join iwm i0 on i0.d = a.p - 2
            join iwm i1 on i1.d = a.p + 1),
        f as {_to_friday("select * from r", "vis")}
        select f.fdate as date, f.ticker, f.value
        from f join uni u on u.date = f.fdate and u.ticker = f.ticker
        where f.fd - f.vis < {NEW_ROWS}"""
    return con.execute(q).df()


def s1b_sue(con, shared: str, local: str) -> pd.DataFrame:
    """SUE per quarterly filing (qts ``sue_by_filing``), visible the trading day after ``datekey``."""
    _setup(con, shared, local)
    arq = con.execute(f"""
        select ticker, date as datekey, calendardate, eps, split_cum
        from read_parquet('{shared}/earnings_fundamentals/year=*/*.parquet') where dimension = 'ARQ'""").df()
    sue = sue_by_filing(arq).dropna(subset=["sue"])
    # bug guard (2026-11-14, rerun_bugfix, spec unchanged): when the trailing EPS differences
    # are all equal but for rounding, their sd is floating-point noise and u / sd explodes
    # (one filing gave 1.2e15).  |SUE| > 1,000 is that artefact, not a surprise.
    sue = sue[sue["sue"].abs() <= 1_000]
    sue["vis_date"] = pd.to_datetime(sue["datekey"]) + pd.Timedelta(days=1)
    con.register("sue_df", sue[["ticker", "vis_date", "sue"]])
    rows = _first_row_on_or_after("select * from sue_df", "vis_date")
    q = f"""
        with f as {_to_friday(f"select ticker, p as vis, sue as value from {rows}", "vis")}
        select f.fdate as date, f.ticker, f.value
        from f join uni u on u.date = f.fdate and u.ticker = f.ticker
        where f.fd - f.vis < {NEW_ROWS}"""
    out = con.execute(q).df()
    con.unregister("sue_df")
    # one value per name-week (two filings in one week: the later one)
    return out.drop_duplicates(["date", "ticker"], keep="last")


def s2_insider_clusters(con, shared: str, local: str) -> pd.DataFrame:
    """Names with >= 2 distinct officer/director open-market buyers in [F - 30d, F], at least one
    of them usable in (F - 7d, F]; value = dollars bought in the window / market cap (reported)."""
    _setup(con, shared, local)
    q = f"""
        with buys as (
            select ticker, date, ownername, transactionvalue v
            from read_parquet('{local}/sharadar_sf2/year=*/*.parquet')
            where formtype = '4' and transactioncode = 'P'
              and (securityadcode is null or securityadcode = 'NA')
              and (isofficer or isdirector) and transactionvalue > 0),
        w as (
            select f.fdate, b.ticker, b.ownername, b.v, b.date
            from fri f join buys b
              on b.date > f.fdate - interval 30 day and b.date <= f.fdate),
        agg as (
            select fdate, ticker, count(distinct ownername) n_owners, sum(v) dollars,
                   max(date) last_date
            from w group by fdate, ticker)
        select a.fdate as date, a.ticker, a.dollars / u.mcap as value, a.n_owners
        from agg a join uni u on u.date = a.fdate and u.ticker = a.ticker
        where a.n_owners >= 2 and a.last_date > a.fdate - interval 7 day"""
    return con.execute(q).df()


def s3_13f_change(
    con, shared: str, local: str, max_age_days: int = 100, lag_extra_days: int = 0
) -> pd.DataFrame:
    """Quarter-over-quarter change of 13F-held shares / shares outstanding, usable at quarter
    end + 45 days (+ ``lag_extra_days`` for the robustness diagnostic)."""
    _setup(con, shared, local)
    q = f"""
        with s3 as (
            select ticker, date + interval {int(lag_extra_days)} day as usable, calendardate, shrunits
            from read_parquet('{local}/sharadar_sf3a/year=*/*.parquet') where shrunits > 0),
        so as (select ticker, date, value so from read_parquet('{shared}/shares_outstanding/year=*/*.parquet') where value > 0),
        io as (
            select s3.ticker, s3.usable, s3.calendardate, s3.shrunits / so.so as io
            from s3 asof join so on s3.ticker = so.ticker and s3.calendardate >= so.date),
        d as (
            select ticker, usable, calendardate,
                   io - lag(io) over (partition by ticker order by calendardate) as dio,
                   datediff('day', lag(calendardate) over (partition by ticker order by calendardate), calendardate) gap
            from io)
        select u.date, u.ticker, d.dio as value
        from uni u asof join (select * from d where dio is not null and gap between 80 and 100) d
          on u.ticker = d.ticker and u.date >= d.usable
        where u.date - d.usable <= interval {int(max_age_days)} day"""
    return con.execute(q).df()


def s4_reversal(con, shared: str, local: str, uni: str = "uni") -> pd.DataFrame:
    """Minus the FF48-industry-demeaned 5-day return; names with an 8-K / earnings event in the
    5 rows excluded."""
    _setup(con, shared, local)
    ev = _first_row_on_or_after(
        f"select ticker, date ev_date from read_parquet('{shared}/sharadar_events/year=*/*.parquet') "
        f"union all select ticker, ann from earnings_dates",
        "ev_date",
    )
    q = f"""
        with r5 as (
            select u.date, u.ticker, x0.c / xb.c - 1 as r5, coalesce(i.industry, 'Other') ind
            from {uni} u
            join cal c on c.date = u.date
            join px2 x0 on x0.ticker = u.ticker and x0.d = c.d
            join px2 xb on xb.ticker = u.ticker and xb.d = c.d - 5
            left join read_parquet('{shared}/industry/all.parquet') i on i.ticker = u.ticker),
        news as (
            select distinct f.fdate, e.ticker from {ev} e
            join fri f on e.p > f.fd - 5 and e.p <= f.fd),
        clean as (
            select r5.* from r5 left join news n on n.fdate = r5.date and n.ticker = r5.ticker
            where n.ticker is null),
        dm as (
            select *, count(*) over (partition by date, ind) n_ind,
                   avg(r5) over (partition by date, ind) m_ind,
                   avg(r5) over (partition by date) m_all
            from clean)
        select date, ticker, -(r5 - case when n_ind >= 5 then m_ind else m_all end) as value from dm"""
    return con.execute(q).df()


def slow_composite(con, shared: str, local: str) -> pd.DataFrame:
    """Round-2 option A (not pre-registered yet): on the last formation Friday of each month,
    the mean of centred cross-sectional percentile ranks of earnings yield, book-to-price,
    gross profitability, minus asset growth and 12-1 momentum (C(d-21) / C(d-252) - 1), for
    universe names with at least three of the five.  The fundamental panels are the qts PIT
    panels (visible from the filing date + 1 day); momentum ends a month before F."""
    _setup(con, shared, local)
    con.execute(
        "create or replace temp table me as select max(fdate) fdate from fri group by year(fdate), month(fdate)"
    )
    for n in ("earnings_yield", "book_to_price", "gross_profitability", "asset_growth"):
        # read only the month-end rows of the daily panel (semi-join), one panel at a time
        con.execute(f"""create or replace temp table {n}_me as
            select p.date, p.ticker, p.value from read_parquet('{shared}/{n}/year=*/*.parquet') p
            semi join me on me.fdate = p.date""")
    q = """
        with u as (select u.date, u.ticker, c.d from uni u join me on me.fdate = u.date join cal c on c.date = u.date),
        x as (
            select u.date, u.ticker,
                   ey.value ey, bp.value bp, gp.value gp, -ag.value ag_neg,
                   m1.c / m12.c - 1 mom
            from u
            left join earnings_yield_me ey on ey.date = u.date and ey.ticker = u.ticker
            left join book_to_price_me bp on bp.date = u.date and bp.ticker = u.ticker
            left join gross_profitability_me gp on gp.date = u.date and gp.ticker = u.ticker
            left join asset_growth_me ag on ag.date = u.date and ag.ticker = u.ticker
            left join px2 m1 on m1.ticker = u.ticker and m1.d = u.d - 21
            left join px2 m12 on m12.ticker = u.ticker and m12.d = u.d - 252)
        select * from x"""
    x = con.execute(q).df()
    comps = ["ey", "bp", "gp", "ag_neg", "mom"]
    ranks = x.groupby("date")[comps].rank(pct=True) - 0.5
    n = ranks.notna().sum(axis=1)
    x["value"] = ranks.mean(axis=1).where(n >= 3)
    return x.dropna(subset=["value"])[["date", "ticker", "value", *comps]]


SIGNALS = {
    "S1a": (s1a_ear, 20),
    "S1b": (s1b_sue, 20),
    "S2": (s2_insider_clusters, 20),
    "S3": (s3_13f_change, 20),
    "S4": (s4_reversal, 5),
    "A": (slow_composite, 63),  # round 2, option A: test only after the owner picks it
}
START = {
    "S1a": "2003-06-06",
    "S1b": "2003-06-06",
    "S4": "2003-06-06",
    "S2": "2008-02-01",
    "S3": "2013-08-16",
    "A": "2003-06-06",
}
