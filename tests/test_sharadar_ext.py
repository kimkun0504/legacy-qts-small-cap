"""Transforms and the end-to-end load of qts_smallcap.data.sharadar_ext on tiny inputs."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from qts.data.store import ParquetStore

from qts_smallcap.data import sharadar_ext as ext

RANGES = pd.DataFrame(
    {
        "ticker": ["AAA", "DUP", "DUP"],
        "id": ["AAA", "DUP.1", "DUP.2"],
        "start": pd.to_datetime(["1900-01-01", "2000-01-03", "2015-06-01"]),
        "end": pd.to_datetime(["2200-01-01", "2010-12-31", "2200-01-01"]),
    }
)


def test_available_from_rolls_to_weekday():
    d = pd.Series(pd.to_datetime(["2024-03-08", "2024-03-31", "2024-03-13", None]))
    one = ext.available_from(d, 1)
    assert one.iloc[0] == pd.Timestamp("2024-03-11")  # Friday filing -> Monday
    assert one.iloc[2] == pd.Timestamp("2024-03-14")
    assert pd.isna(one.iloc[3])
    # 13F: quarter end 2024-03-31 + 45 days = Wed 2024-05-15
    assert ext.available_from(d, 45).iloc[1] == pd.Timestamp("2024-05-15")
    # 2023-12-31 + 45 = Wed 2024-02-14; 2024-06-30 + 45 = Wed 2024-08-14; 2023-09-30 + 45 = Tue 2023-11-14
    q = pd.Series(pd.to_datetime(["2023-12-31", "2024-06-30", "2023-09-30"]))
    assert ext.available_from(q, 45).dt.strftime("%Y-%m-%d").tolist() == [
        "2024-02-14",
        "2024-08-14",
        "2023-11-14",
    ]


def _sf2_chunk() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAA", "ZZZ", "DUP", "DUP", "AAA"],
            "filingdate": ["2024-03-08", "2024-03-08", "2005-02-01", "2020-02-03", "not a date"],
            "formtype": ["4", "4", "4", "4", "4"],
            "ownername": ["SMITH J", "X", "Y", "Z", "W"],
            "isdirector": ["Y", "N", "N", None, "Y"],
            "isofficer": ["N", "Y", "Y", "Y", "N"],
            "istenpercentowner": ["N", "N", "N", "N", "N"],
            "transactiondate": ["2024-03-06", "2024-03-06", "2005-01-28", "2020-01-30", "2020-01-30"],
            "transactioncode": ["P", "S", "P", "P", "P"],
            "transactionshares": ["1000", "5", "10", "20", "30"],
            "transactionpricepershare": ["12.5", "1", "2", "3", "4"],
            "transactionvalue": ["12500", "5", "20", "60", "120"],
            "rownum": ["1", "1", "1", "1", "1"],
        }
    )


def test_transform_sf2_ids_dates_types_and_counts():
    out, c = ext.transform_sf2(_sf2_chunk(), RANGES)
    assert c.rows == 5 and c.bad_date == 1 and c.not_in_master == 1 and c.kept == 3
    assert out["ticker"].tolist() == ["AAA", "DUP.1", "DUP.2"]  # reused ticker resolved by filing date
    assert out["sharadar_ticker"].tolist() == ["AAA", "DUP", "DUP"]
    assert out["date"].iloc[0] == pd.Timestamp("2024-03-11")  # usable the weekday after filing
    assert out["filingdate"].iloc[0] == pd.Timestamp("2024-03-08")
    assert out["isdirector"].tolist() == [True, False, False]
    assert out["transactionvalue"].dtype == float and out["transactionvalue"].iloc[0] == 12500.0
    assert (out["date"] > out["filingdate"]).all()


def test_transform_13f_lag_and_resolution():
    chunk = pd.DataFrame(
        {
            "ticker": ["AAA", "QQQQ"],
            "investorid": ["INV1", "INV2"],
            "securitytype": ["SHR", "SHR"],
            "date": ["2024-03-31", "2024-03-31"],
            "value": ["10.5", "3"],
            "units": ["100", "7"],
        }
    )
    out, c = ext.transform_13f(chunk, RANGES)
    assert c.kept == 1 and c.not_in_master == 1
    row = out.iloc[0]
    assert row["calendardate"] == pd.Timestamp("2024-03-31") and row["date"] == pd.Timestamp("2024-05-15")
    assert row["ticker"] == "AAA" and row["units"] == 100.0 and row["investorid"] == "INV1"
    # SF3B: no ticker, no resolution
    b = pd.DataFrame(
        {"date": ["2024-06-30"], "investorid": ["INV1"], "investorname": ["A FUND"], "shrunits": ["5"]}
    )
    outb, cb = ext.transform_13f(b, None)
    assert (
        cb.kept == 1
        and outb["date"].iloc[0] == pd.Timestamp("2024-08-14")
        and outb["shrunits"].iloc[0] == 5.0
    )


def test_transform_sfp_passthrough():
    chunk = pd.DataFrame(
        {
            "ticker": ["iwm", "IWM"],
            "date": ["2024-01-02", "bad"],
            "close": ["200.1", "1"],
            "closeadj": ["198.0", "1"],
            "closeunadj": ["200.1", "1"],
            "volume": ["1e6", "1"],
            "lastupdated": ["2024-01-03", "2024-01-03"],
        }
    )
    out, c = ext.transform_sfp(chunk)
    assert (
        c.kept == 1
        and c.bad_date == 1
        and out["ticker"].iloc[0] == "IWM"
        and out["closeadj"].iloc[0] == 198.0
    )


def test_load_table_end_to_end(tmp_path):
    csv = tmp_path / "bulk" / "SHARADAR_SF2_test.csv"
    csv.parent.mkdir()
    _sf2_chunk().to_csv(csv, index=False)
    files = ext.find_table_files(csv.parent, "SF2")
    assert files == [csv]
    store_root = tmp_path / "store"
    rep = ext.load_table("SF2", files, store_root, RANGES, staging_root=tmp_path, chunksize=2)
    assert rep.counts.kept == 3 and rep.years == 3 and rep.n_ids == 3
    assert rep.date_min == "2005-02-02" and rep.date_max == "2024-03-11"
    s = ParquetStore(str(store_root))
    assert "sharadar_sf2" in s.datasets()
    df = s.read_long("sharadar_sf2")
    assert len(df) == 3 and df["date"].is_monotonic_increasing
    meta = json.loads((store_root / "sharadar_sf2" / "_meta.json").read_text())
    assert meta["partitioned"] is True and "filingdate" in meta["columns"]
    # staging cleaned up; a reload replaces the dataset (no duplicates)
    assert not list(tmp_path.glob("sharadar_sf2-*"))
    ext.load_table("SF2", files, store_root, RANGES, staging_root=tmp_path, chunksize=2)
    assert len(s.read_long("sharadar_sf2")) == 3
    p = ext.write_report(store_root, [rep], {"lag_13f_days": 45})
    assert json.loads(p.read_text())["tables"]["SF2"]["counts"]["kept"] == 3


@pytest.mark.parametrize("table", ["SF3", "SF3A", "SF3B", "SFP", "SF2"])
def test_bulk_file_matching_is_exact(tmp_path, table):
    for t in ("SF3", "SF3A", "SF3B", "SFP", "SF2", "SEP"):
        (tmp_path / f"SHARADAR_{t}_2_abc.zip").write_bytes(b"")
    names = [p.name for p in ext.find_table_files(tmp_path, table)]
    assert names == [f"SHARADAR_{table}_2_abc.zip"]
