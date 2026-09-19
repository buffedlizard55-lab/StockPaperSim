"""Forward prints: the real per-session prices the live book may execute against.

The live book settles a forward intent only when the target session has a *real*
print for that symbol.  Two independently published sources carry one:

``nasdaq``
    The Nasdaq-published daily price record (``api.nasdaq.com`` historical quote
    endpoint).  Class ``EXCHANGE-PUBLISHED``: for a Nasdaq-listed name this is the
    listing venue's own record; for a NYSE/Arca name it is that venue's published
    consolidated-tape row, **not** the listing venue's official closing price.  It
    does not open the official-price gate, because the repository has no
    documented redistribution permission for it.

``yahoo``
    Yahoo Finance chart API daily bars.  Class ``SECONDARY``: an aggregator, and
    the file class the published research runs already use.

Why a ledger and not just the price files
-----------------------------------------
A price file is rewritten every time the history grows; a *print* is an
observation with a date, a publisher, a retrieval time and a checksum.  Keeping
the prints in their own append-only ledger means a settlement can cite the exact
row it filled against, and the same session can be re-settled identically years
later.  The cross-check between the two sources is stored beside them, so the
claim "two independent publishers agree on this bar" is a number on the page.

Nothing in this module invents a price.  A session that neither publisher has
carried is simply absent, and the caller must report the intent as waiting rather
than fill it.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .calendar import REPO_ROOT

REAL_ROOT = os.path.join(REPO_ROOT, "data", "real")
PRINTS_DIR = os.path.join(REAL_ROOT, "prints")
RAW_DIR = os.path.join(PRINTS_DIR, "raw")
CROSSCHECK_PATH = os.path.join(PRINTS_DIR, "crosscheck.json")
MANIFEST_PATH = os.path.join(PRINTS_DIR, "manifest.json")

#: ``source id -> ledger file name``.  The id is the key the rest of the project
#: uses; the file name is what a reader finds on disk.
LEDGER_FILES: Dict[str, str] = {
    "nasdaq": "nasdaq_daily.jsonl",
    "yahoo": "yahoo_daily.jsonl",
}

#: How each ledger's rows are classed and where they come from.  ``tradable`` is
#: the field the settlement path reads: only a tradable print may carry a fill.
SOURCE_CLASSES: Dict[str, dict] = {
    "nasdaq": {
        "label": "Nasdaq published daily price record",
        "url_template": ("https://api.nasdaq.com/api/quote/{symbol}/historical"
                         "?assetclass={assetclass}&fromdate={start}&todate={end}&limit=5000"),
        "source_class": "EXCHANGE-PUBLISHED",
        "redistribution_status": "NOT-AUTHORIZED-FOR-REPOSITORY-REPRODUCTION",
        "tradable": True,
        "note": "The venue's own published daily row. For a Nasdaq-listed security "
                "this is the listing venue's record; for an NYSE/Arca security it "
                "is that venue's published consolidated-tape row, not the listing "
                "venue's official close. Redistribution permission has not been "
                "granted, so the strict official-price gate stays closed.",
    },
    "yahoo": {
        "label": "Yahoo Finance chart API (v8) daily bars",
        "url_template": ("https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                         "?range={range}&interval=1d"),
        "source_class": "SECONDARY",
        "redistribution_status": "UNRECORDED",
        "tradable": True,
        "note": "An aggregator, not the consolidated tape; classed SECONDARY "
                "everywhere in this project. Tradable inside the forward book "
                "because its bars are real prints, and cross-checked against the "
                "exchange-published record rather than trusted alone.",
    },
}

#: Session-level facts a caller needs before it may settle anything.
PROVISIONAL = "PROVISIONAL-EXCHANGE-PRINTS"
FULL = "FULL-OFFICIAL-SERIES"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text or text in {"-", "--", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def ledger_path(root: str = REAL_ROOT, source: str = "nasdaq") -> str:
    if source not in LEDGER_FILES:
        raise ValueError(f"unknown print source {source!r}; choose {sorted(LEDGER_FILES)}")
    return os.path.join(root, "prints", LEDGER_FILES[source])


class PrintLedger:
    """An indexed, read-only view of one source's collected prints."""

    def __init__(self, source: str, rows: Sequence[dict], path: str = "",
                 sha256: str = "") -> None:
        self.source = source
        self.rows: List[dict] = list(rows)
        self.path = path
        self.sha256 = sha256
        self._index: Dict[Tuple[str, str], dict] = {}
        for row in self.rows:
            key = (str(row.get("symbol", "")), str(row.get("session", "")))
            self._index[key] = row

    # -- lookups -----------------------------------------------------------
    def get(self, symbol: str, session: str) -> Optional[dict]:
        return self._index.get((symbol, session))

    def sessions(self) -> List[str]:
        return sorted({str(r["session"]) for r in self.rows})

    def symbols(self) -> List[str]:
        return sorted({str(r["symbol"]) for r in self.rows})

    def session(self, session: str) -> Dict[str, dict]:
        return {str(r["symbol"]): r for r in self.rows if str(r["session"]) == session}

    def last_session(self) -> Optional[str]:
        dates = self.sessions()
        return dates[-1] if dates else None

    def coverage(self, session: str, symbols: Iterable[str]) -> Tuple[List[str], List[str]]:
        """``(covered, missing)`` for one session over a symbol list."""
        wanted = list(symbols)
        covered = [s for s in wanted if self.get(s, session) is not None]
        missing = [s for s in wanted if self.get(s, session) is None]
        return covered, missing

    def meta(self) -> dict:
        info = SOURCE_CLASSES.get(self.source, {})
        return {
            "source": self.source,
            "label": info.get("label", self.source),
            "source_class": info.get("source_class", "UNKNOWN"),
            "redistribution_status": info.get("redistribution_status", ""),
            "tradable": bool(info.get("tradable", False)),
            "rows": len(self.rows),
            "symbols": len(self.symbols()),
            "sessions": [self.sessions()[0], self.sessions()[-1]] if self.rows else [],
            "file": os.path.relpath(self.path, REPO_ROOT) if self.path else "",
            "sha256": self.sha256,
        }


def load_ledger(root: str = REAL_ROOT, source: str = "nasdaq") -> PrintLedger:
    """Read one print ledger.  A missing ledger is not an error - it is empty."""
    path = ledger_path(root, source)
    rows: List[dict] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return PrintLedger(source, rows, path=path,
                       sha256=sha256_file(path) if os.path.exists(path) else "")


def write_ledger(rows: Sequence[dict], path: str) -> dict:
    """Write a ledger deterministically: sorted, one JSON object per line."""
    ordered = sorted(rows, key=lambda r: (str(r.get("session", "")), str(r.get("symbol", "")),
                                          str(r.get("source", ""))))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in ordered:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {"path": os.path.relpath(path, REPO_ROOT), "rows": len(ordered),
            "sha256": sha256_file(path)}


def crosscheck(nasdaq: PrintLedger, yahoo: PrintLedger,
               tol_bps: float = 5.0) -> dict:
    """Compare the two publishers bar by bar on the sessions they share.

    The comparison is on the close first (the price every mark-to-market number
    uses) and then on the whole OHLC set.  Differences are reported in basis
    points of the reference price, so a penny of vendor rounding on a $400 ETF is
    visibly different from a cent of disagreement on a $5 stock.
    """
    per_symbol: Dict[str, dict] = {}
    worst = 0.0
    worst_where = None
    compared = 0
    close_disagreements: List[dict] = []
    for symbol in sorted(set(nasdaq.symbols()) & set(yahoo.symbols())):
        rows = []
        for session in sorted(set(nasdaq.sessions()) & set(yahoo.sessions())):
            a, b = nasdaq.get(symbol, session), yahoo.get(symbol, session)
            if a is None or b is None:
                continue
            compared += 1
            entry = {"session": session}
            for field in ("close", "open", "high", "low"):
                av, bv = _number(a.get(field)), _number(b.get(field))
                if av is None or bv is None:
                    entry[field + "_bps"] = None
                    continue
                bps = abs(av - bv) / max(abs(av), 1e-9) * 10000.0
                entry[field + "_bps"] = round(bps, 4)
                if field == "close":
                    entry["nasdaq_close"] = av
                    entry["yahoo_close"] = bv
                    if bps > tol_bps:
                        close_disagreements.append({
                            "symbol": symbol, "session": session,
                            "nasdaq_close": av, "yahoo_close": bv,
                            "difference_bps": round(bps, 4)})
                if bps > worst:
                    worst, worst_where = bps, {"symbol": symbol, "session": session,
                                               "field": field, "nasdaq": av, "yahoo": bv}
            av, bv = _number(a.get("volume")), _number(b.get("volume"))
            entry["volume_difference_pct"] = (
                round(100.0 * abs(av - bv) / av, 4) if av and bv else None)
            rows.append(entry)
        if rows:
            per_symbol[symbol] = rows
    worst_close = max((max(r["close_bps"] for r in rows if r["close_bps"] is not None)
                       for rows in per_symbol.values() if any(
                           r["close_bps"] is not None for r in rows)), default=0.0)
    return {
        "sources": {"a": nasdaq.meta(), "b": yahoo.meta()},
        "tolerance_bps": tol_bps,
        "symbol_sessions_compared": compared,
        "symbols_compared": len(per_symbol),
        "worst_field_difference_bps": round(worst, 4),
        "worst_field": worst_where,
        "worst_close_difference_bps": round(worst_close, 4),
        "close_disagreements_beyond_tolerance": close_disagreements,
        "verdict": ("AGREEMENT: both publishers describe the same bars for every "
                    "compared session"
                    if not close_disagreements else
                    f"DISAGREEMENT on {len(close_disagreements)} close(s) beyond "
                    f"{tol_bps} bps - a settlement must not proceed on a disputed bar"),
        "per_symbol": per_symbol,
    }


def merge_rows(existing: Sequence[dict], incoming: Sequence[dict]) -> List[dict]:
    """Union of two ledgers, the newer row winning on (source, symbol, session)."""
    merged: Dict[Tuple[str, str, str], dict] = {}
    for row in list(existing) + list(incoming):
        key = (str(row.get("source", "")), str(row.get("symbol", "")),
               str(row.get("session", "")))
        if not all(key):
            continue
        merged[key] = row
    return list(merged.values())


__all__ = ["PRINTS_DIR", "RAW_DIR", "CROSSCHECK_PATH", "MANIFEST_PATH",
           "LEDGER_FILES", "SOURCE_CLASSES", "PROVISIONAL", "FULL",
           "PrintLedger", "load_ledger", "write_ledger", "crosscheck", "merge_rows",
           "ledger_path", "sha256_file"]
