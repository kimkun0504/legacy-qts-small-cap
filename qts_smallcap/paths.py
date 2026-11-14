"""Where things live.  The shared qts store is read-only from this project; every table
this project creates goes to its own store (``data/store`` in this repo)."""

from __future__ import annotations

import os
from pathlib import Path

#: this checkout
REPO_ROOT = Path(__file__).resolve().parents[1]
#: the qts checkout next to this one (override with $QTS_REPO)
QTS_REPO = Path(os.environ.get("QTS_REPO", REPO_ROOT.parent / "quant-trading-system"))
#: the shared Sharadar store written by ``qts data sharadar sync`` (read-only here)
SHARED_STORE = Path(os.environ.get("QTS_SHARED_STORE", QTS_REPO / "data" / "store"))
#: this project's own store (SF2, SF3*, SFP tables and anything derived here)
LOCAL_STORE = Path(os.environ.get("QTS_SC_STORE", REPO_ROOT / "data" / "store"))
#: this project's seal file
SEAL_FILE = REPO_ROOT / "research" / "seal.yaml"
#: this project's trial ledger (own families; never the baseline's; gitignored like qts runs/)
LEDGER = REPO_ROOT / "runs" / "trial_ledger.jsonl"


def use_project_seal() -> None:
    """Point qts's seal machinery at this project's seal file (``$QTS_SEAL_FILE``)."""
    os.environ.setdefault("QTS_SEAL_FILE", str(SEAL_FILE))
