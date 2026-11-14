"""Sharadar tables the qts sync does not load: SF2 (insiders), SF3 / SF3A / SF3B (13F) and
SFP (funds and ETFs).

Each table is streamed from the bulk export (zip or csv, through
:func:`qts.data.sharadar.iter_csv_chunks`), resolved to the shared store's panel ids where it
has a stock ticker, spilled per year to a staging directory and written once per year to
**this project's store** as ``sharadar_<table>`` in the :class:`qts.data.store.ParquetStore`
layout (``<name>/year=YYYY/data.parquet`` + ``_meta.json``), so ``ParquetStore.read_long`` and
``duckdb_query`` read it.  The shared qts store is only read (its ``security_master``); nothing
is written there.

Point-in-time anchors.  The stored ``date`` column is always the **first day a row may be
used**; signal code joins on ``date <= t`` and never on the vendor dates kept beside it.

=========  ===================================================================================
``SF2``    Forms 3/4/5.  ``date`` = ``filingdate`` + 1 calendar day, rolled forward to a weekday
           (the rule qts applies to SF1 ``datekey``: a filing may land after the close).
           ``transactiondate`` is kept for analysis only; it is not an availability date
           (Form 4 is due two business days after the trade, and late filings are common).
``SF3``    13F holdings by investor x ticker; ``SF3A`` aggregated by ticker; ``SF3B`` by
           investor.  Sharadar keys them on the quarter end only, so ``date`` = quarter end +
           :data:`F13_LAG_DAYS` (45: the Form 13F deadline, SEC Rule 13f-1) rolled forward to a
           weekday, and ``calendardate`` keeps the quarter end.  Late filers and amendments that
           the vendor folds into a quarter after the deadline are a residual look-ahead this lag
           cannot remove: 13F signals must also be reported at a longer lag (robustness check).
``SFP``    Daily fund/ETF prices (IWM for the hedge).  Known at the close of ``date``, the same
           as SEP.  Stored under the vendor ticker: funds are not in the stock master, so there
           is no id resolution.  ``closeadj`` includes distributions, so a short's total-return
           P&L from ``closeadj`` already charges the dividends it pays.
``SEP``    Daily stock OHLC (split-adjusted) -> ``sep_ohlc``: the high/low/open the qts store
           does not keep, needed for the high-low spread estimators.  Known at the close of
           ``date``; ids resolved by price date (prints outside a holder's range are dropped,
           as in qts).
=========  ===================================================================================

Ids follow qts: ``ticker`` holds the panel id and ``sharadar_ticker`` the vendor ticker; rows
whose ticker is not in the kept master (ADRs, funds, OTC names, ...) are dropped and counted.

Usage::

    python -m qts_smallcap.data.sharadar_ext --bulk "~/Desktop/11-09-26 Sharadar Bulk" \\
        --tables SF2 SF3A SF3B SFP SF3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from qts.data.sharadar import ResolveCounts, find_table_files, iter_csv_chunks, master_from_store, resolve_ids
from qts.data.store import ParquetStore

from qts_smallcap.paths import LOCAL_STORE, SHARED_STORE

log = logging.getLogger("qts_smallcap.data.sharadar_ext")

#: Form 13F is due 45 calendar days after the quarter end (SEC Rule 13f-1(a)(1)).
F13_LAG_DAYS = 45
#: SF2 filings become usable the calendar day after ``filingdate`` (as SF1 ``datekey`` in qts).
SF2_LAG_DAYS = 1
#: processing order (small tables first)
EXT_TABLES: tuple[str, ...] = ("SF3B", "SF3A", "SF2", "SFP", "SF3", "SEP")
#: dataset written for each table (default ``sharadar_<table>``)
DATASET = {"SEP": "sep_ohlc"}
#: load report inside this project's store
REPORT_FILE = "_ext/load_report.json"

SF2_DATES = ("transactiondate", "dateexercisable", "expirationdate")
SF2_NUMBERS = (
    "sharesownedbeforetransaction",
    "transactionshares",
    "sharesownedfollowingtransaction",
    "transactionpricepershare",
    "transactionvalue",
    "priceexercisable",
    "rownum",
)
SF2_FLAGS = ("isdirector", "isofficer", "istenpercentowner")
SF2_TEXT = (
    "formtype",
    "issuername",
    "ownername",
    "officertitle",
    "securityadcode",
    "transactioncode",
    "securitytitle",
    "directorindirect",
    "natureofownership",
)
#: non-numeric columns of the 13F tables (everything else is parsed as a number)
F13_TEXT = ("ticker", "investorid", "investorname", "securitytype", "name")
SFP_NUMBERS = ("open", "high", "low", "close", "volume", "closeadj", "closeunadj")
SEP_COLUMNS = ("ticker", "date", "open", "high", "low", "close", "volume", "closeunadj")


# =============================================================================
# Pure helpers and transforms (chunk -> stored rows)
# =============================================================================
def available_from(d: pd.Series, days: int) -> pd.Series:
    """``d + days`` calendar days, rolled forward to a weekday; NaT stays NaT.

    >>> available_from(pd.Series(pd.to_datetime(["2024-03-08", "2024-03-31"])), 1).dt.date.tolist()
    [datetime.date(2024, 3, 11), datetime.date(2024, 4, 1)]
    """
    shifted = pd.to_datetime(d) + pd.Timedelta(days=days)
    out = pd.Series(pd.NaT, index=d.index, dtype="datetime64[ns]")
    ok = shifted.notna()
    if ok.any():
        days64 = shifted[ok].to_numpy().astype("datetime64[D]")
        out.loc[ok] = np.busday_offset(days64, 0, roll="forward").astype("datetime64[ns]")
    return out


def _text(s: pd.Series) -> pd.Series:
    """Strings with ``None`` for missing (stable arrow type ``string`` across chunks)."""
    return s.astype(object).where(s.notna(), None).map(lambda v: None if v is None else str(v).strip())


@dataclass
class Counts:
    rows: int = 0
    kept: int = 0
    not_in_master: int = 0
    out_of_range: int = 0
    gap_assigned: int = 0
    bad_date: int = 0

    def add(self, other: Counts) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


def _counts(c: ResolveCounts, bad: int) -> Counts:
    return Counts(
        rows=c.n_rows + bad,
        kept=c.n_kept,
        not_in_master=c.n_not_in_master,
        out_of_range=c.n_out_of_range,
        gap_assigned=c.n_gap_assigned,
        bad_date=bad,
    )


def _dated(chunk: pd.DataFrame, col: str) -> tuple[pd.DataFrame, int]:
    d = chunk.copy()
    d[col] = pd.to_datetime(d[col], errors="coerce")
    bad = int(d[col].isna().sum())
    return d[d[col].notna()].reset_index(drop=True), bad


def transform_sf2(chunk: pd.DataFrame, ranges: pd.DataFrame, lag_days: int = SF2_LAG_DAYS) -> tuple[pd.DataFrame, Counts]:
    """SF2 rows -> ``date`` (first usable day), ``ticker`` (id), ``sharadar_ticker``, vendor columns."""
    d, bad = _dated(chunk, "filingdate")
    res, c = resolve_ids(d, ranges, date_col="filingdate", gap_policy="next")
    out = res.rename(columns={"ticker": "sharadar_ticker", "id": "ticker"})
    out.insert(0, "date", available_from(out["filingdate"], lag_days))
    for col in SF2_DATES:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    for col in SF2_NUMBERS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    for col in SF2_FLAGS:
        if col in out.columns:
            out[col] = out[col].astype(str).str.strip().str.upper().eq("Y")
    for col in (*SF2_TEXT, "sharadar_ticker", "ticker"):
        if col in out.columns:
            out[col] = _text(out[col])
    return out, _counts(c, bad)


def transform_13f(
    chunk: pd.DataFrame, ranges: pd.DataFrame | None, lag_days: int = F13_LAG_DAYS
) -> tuple[pd.DataFrame, Counts]:
    """SF3 / SF3A (``ranges`` given: ids resolved at the quarter end) or SF3B (``ranges=None``)
    -> ``date`` (quarter end + lag, weekday), ``calendardate`` (quarter end), vendor columns."""
    d, bad = _dated(chunk.rename(columns={"date": "calendardate"}), "calendardate")
    if ranges is not None:
        res, c = resolve_ids(d, ranges, date_col="calendardate", gap_policy="next")
        out = res.rename(columns={"ticker": "sharadar_ticker", "id": "ticker"})
    else:
        out = d
        c = ResolveCounts(n_rows=len(d), n_kept=len(d))
    out.insert(0, "date", available_from(out["calendardate"], lag_days))
    for col in list(out.columns):
        if col in ("date", "calendardate"):
            continue
        if col in F13_TEXT or col == "sharadar_ticker":
            out[col] = _text(out[col])
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    return out, _counts(c, bad)


def transform_sfp(chunk: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """SFP rows verbatim with parsed types; ``ticker`` is the vendor fund ticker."""
    d, bad = _dated(chunk, "date")
    d["ticker"] = _text(d["ticker"].astype(str).str.upper())
    for col in SFP_NUMBERS:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce").astype(float)
    if "lastupdated" in d.columns:
        d["lastupdated"] = pd.to_datetime(d["lastupdated"], errors="coerce")
    return d, Counts(rows=len(d) + bad, kept=len(d), bad_date=bad)


def transform_sep_ohlc(chunk: pd.DataFrame, ranges: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """SEP rows -> ``date``, ``ticker`` (id), split-adjusted ``open/high/low/close``, ``closeunadj``,
    ``volume`` (vendor, split-adjusted)."""
    d, bad = _dated(chunk, "date")
    res, c = resolve_ids(d, ranges, date_col="date", gap_policy="drop")
    out = res.rename(columns={"ticker": "sharadar_ticker", "id": "ticker"})
    out = out[["date", "ticker", *[k for k in SEP_COLUMNS[2:] if k in out.columns]]].copy()
    for col in SEP_COLUMNS[2:]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    out["ticker"] = _text(out["ticker"])
    return out, _counts(c, bad)


def find_files(bulk: str | os.PathLike[str], table: str) -> list[Path]:
    """Bulk files for ``table``; for SEP a plain CSV beats the zip (same export, faster to parse)."""
    if table == "SEP":
        csvs = sorted(Path(bulk).glob("SHARADAR_SEP_*.csv"))
        if csvs:
            return [max(csvs, key=lambda p: p.stat().st_mtime)]
    return find_table_files(bulk, table)


# =============================================================================
# Staging and writing (ParquetStore layout)
# =============================================================================
class YearSpill:
    """Per-year parquet parts in a staging directory (removed by :meth:`cleanup`)."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.n = 0

    def add(self, df: pd.DataFrame) -> None:
        for year, part in df.groupby(df["date"].dt.year):
            d = self.root / f"year={int(year)}"
            d.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pandas(part, preserve_index=False), d / f"part-{self.n:06d}.parquet")
            self.n += 1

    def years(self) -> list[int]:
        return sorted(int(p.name.split("=")[1]) for p in self.root.glob("year=*"))

    def read_year(self, year: int) -> pa.Table:
        files = sorted((self.root / f"year={year}").glob("part-*.parquet"))
        return pa.concat_tables([pq.read_table(f) for f in files], promote_options="default")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _sort(t: pa.Table) -> pa.Table:
    keys = [(c, "ascending") for c in ("date", "ticker") if c in t.column_names]
    return t.take(pc.sort_indices(t, sort_keys=keys)) if keys else t


def write_year(store_root: str | os.PathLike[str], name: str, year: int, table: pa.Table) -> None:
    """``<store>/<name>/year=<year>/data.parquet`` via a temp file and ``os.replace``."""
    d = Path(store_root) / name / f"year={year}"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".tmp-{uuid.uuid4().hex}.partial"
    try:
        pq.write_table(_sort(table), tmp, compression="zstd")
        os.replace(tmp, d / "data.parquet")
    finally:
        if tmp.exists():
            tmp.unlink()


def write_meta(store_root: str | os.PathLike[str], name: str, columns: Sequence[str]) -> None:
    d = Path(store_root) / name
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"_meta.json.tmp-{uuid.uuid4().hex}"
    tmp.write_text(json.dumps({"columns": list(columns), "kind": "long", "partitioned": True}, indent=1, sort_keys=True))
    os.replace(tmp, d / "_meta.json")


# =============================================================================
# Load
# =============================================================================
@dataclass
class TableReport:
    table: str
    dataset: str
    sources: list[str] = field(default_factory=list)
    counts: Counts = field(default_factory=Counts)
    date_min: str | None = None
    date_max: str | None = None
    vendor_date_min: str | None = None
    vendor_date_max: str | None = None
    n_ids: int = 0
    years: int = 0
    seconds: float = 0.0
    note: str = ""


def _transform_for(table: str, ranges: pd.DataFrame, lag_13f: int) -> Callable[[pd.DataFrame], tuple[pd.DataFrame, Counts]]:
    if table == "SF2":
        return lambda ch: transform_sf2(ch, ranges)
    if table in ("SF3", "SF3A"):
        return lambda ch: transform_13f(ch, ranges, lag_13f)
    if table == "SF3B":
        return lambda ch: transform_13f(ch, None, lag_13f)
    if table == "SFP":
        return transform_sfp
    if table == "SEP":
        return lambda ch: transform_sep_ohlc(ch, ranges)
    raise ValueError(f"unknown table {table!r}; known: {EXT_TABLES}")


_VENDOR_DATE = {
    "SF2": "filingdate",
    "SF3": "calendardate",
    "SF3A": "calendardate",
    "SF3B": "calendardate",
    "SFP": "date",
    "SEP": "date",
}


def load_table(
    table: str,
    files: Sequence[Path],
    store_root: str | os.PathLike[str],
    ranges: pd.DataFrame,
    staging_root: str | os.PathLike[str] | None = None,
    chunksize: int = 1_000_000,
    lag_13f: int = F13_LAG_DAYS,
) -> TableReport:
    """Stream ``files`` of ``table`` into ``<store_root>/sharadar_<table>`` (replaces the dataset)."""
    table = table.upper()
    name = DATASET.get(table, f"sharadar_{table.lower()}")
    rep = TableReport(table=table, dataset=name, sources=[str(p) for p in files])
    t0 = time.perf_counter()
    transform = _transform_for(table, ranges, lag_13f)
    spill = YearSpill(tempfile.mkdtemp(prefix=f"{name}-", dir=staging_root))
    ids: set[str] = set()
    dates: list[pd.Timestamp] = []
    vdates: list[pd.Timestamp] = []
    try:
        for p in files:
            dtype = {"ticker": str} if table != "SF3B" else {"investorid": str}
            usecols = SEP_COLUMNS if table == "SEP" else None
            for chunk in iter_csv_chunks(p, chunksize, usecols=usecols, dtype=dtype):
                out, c = transform(chunk)
                rep.counts.add(c)
                if out.empty:
                    continue
                ids.update(out["ticker"].dropna().unique().tolist() if "ticker" in out.columns else [])
                dates += [out["date"].min(), out["date"].max()]
                vcol = _VENDOR_DATE[table]
                vdates += [out[vcol].min(), out[vcol].max()]
                spill.add(out)
                log.info("%s: %s rows read, %s kept", table, f"{rep.counts.rows:,}", f"{rep.counts.kept:,}")
        dest = Path(store_root) / name
        if dest.exists():
            shutil.rmtree(dest)
        columns: list[str] = []
        for y in spill.years():
            t = spill.read_year(y)
            columns = list(dict.fromkeys(columns + t.column_names))
            write_year(store_root, name, y, t)
            rep.years += 1
        write_meta(store_root, name, columns)
    finally:
        spill.cleanup()
    if dates:
        rep.date_min, rep.date_max = str(min(dates).date()), str(max(dates).date())
        rep.vendor_date_min, rep.vendor_date_max = str(min(vdates).date()), str(max(vdates).date())
    rep.n_ids = len(ids)
    rep.seconds = round(time.perf_counter() - t0, 1)
    return rep


def write_report(store_root: str | os.PathLike[str], reports: Sequence[TableReport], extra: dict[str, Any]) -> Path:
    """Merge ``reports`` into ``<store_root>/_ext/load_report.json`` (one entry per table)."""
    p = Path(store_root) / REPORT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(p.read_text()) if p.exists() else {"tables": {}}
    for r in reports:
        old["tables"][r.table] = asdict(r)
    old.update(extra)
    tmp = p.with_suffix(f".tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(old, indent=1, sort_keys=True, default=str))
    os.replace(tmp, p)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qts_smallcap.data.sharadar_ext", description=__doc__.split("\n\n")[0])
    ap.add_argument("--bulk", required=True, help="folder with the Sharadar bulk exports (zip/csv)")
    ap.add_argument("--tables", nargs="+", default=list(EXT_TABLES), help=f"subset of {EXT_TABLES}")
    ap.add_argument("--shared-store", default=str(SHARED_STORE), help="qts store (read-only: security_master)")
    ap.add_argument("--store", default=str(LOCAL_STORE), help="this project's store (written)")
    ap.add_argument("--staging", default=None, help="staging directory (default: system temp)")
    ap.add_argument("--chunksize", type=int, default=1_000_000)
    ap.add_argument("--lag-13f", type=int, default=F13_LAG_DAYS, help="days after quarter end before 13F rows are usable")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bulk = Path(os.path.expanduser(a.bulk))
    shared = ParquetStore(a.shared_store)
    ranges = master_from_store(shared).ranges
    Path(a.store).mkdir(parents=True, exist_ok=True)
    reports = []
    order = [t for t in EXT_TABLES if t in {x.upper() for x in a.tables}]
    for table in order:
        files = find_files(bulk, table)
        if not files:
            log.warning("%s: no file under %s; skipped", table, bulk)
            continue
        rep = load_table(table, files, a.store, ranges, a.staging, a.chunksize, a.lag_13f)
        log.info("%s done: %s", table, json.dumps(asdict(rep), default=str))
        reports.append(rep)
        write_report(a.store, [rep], {"shared_store": str(Path(a.shared_store).resolve()), "lag_13f_days": a.lag_13f})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
