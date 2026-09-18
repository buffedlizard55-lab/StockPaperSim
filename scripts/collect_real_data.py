#!/usr/bin/env python3
"""Collect real, citable market and event data from official / primary sources.

WHY THIS EXISTS
---------------
The simulation must never invent a price, a date or an event.  The sandbox this
project was developed in blocks outbound HTTPS, so collection is delegated to a
GitHub Actions runner (``.github/workflows/collect-real-data.yml``), which has
ordinary internet access.  Everything the runner downloads is written verbatim
under ``data/real/`` together with the exact URL, the HTTP status, the byte
count and the SHA-256 of the bytes, so any row on the published site can be
traced back to a retrievable source and re-checked by hand.

WHAT IS OFFICIAL HERE AND WHAT IS NOT
-------------------------------------
This distinction is enforced in code (``SOURCE_CLASS``), not in prose:

* ``OFFICIAL``      - the publisher of record.  SEC EDGAR (Form 4 and the
                      ticker->CIK map), FDA openFDA, NOAA/NCEI, FRED (the
                      Federal Reserve Bank of St. Louis redistributing the
                      official series), MLB StatsAPI, the NBA CDN, NCAA.com.
* ``OFFICIAL-VENDOR`` - an exchange or venue publishing its own data (Kalshi
                      trade API).
* ``SECONDARY``     - an aggregator whose numbers are not the consolidated tape
                      (Yahoo chart endpoint, Stooq, ESPN).  These are allowed
                      for cross-checking and for breadth, and every file
                      records the class, so a strategy that depends on them can
                      be flagged.

Nothing is written if a fetch fails: failures land in the manifest with the
error and the run continues, because an honest gap is worth more than a filled
one.

USAGE
-----
    python3 scripts/collect_real_data.py --out data/real [--max-seconds 1500]
                                         [--only prices,sec,fda,...]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Window: warm-up starts a year before the competition so lookback indicators
# are real, not synthesised.
# --------------------------------------------------------------------------
WARMUP_START = "2024-09-16"
SEASON_START = "2025-09-17"
SEASON_END = "2026-09-16"
COLLECT_END = "2026-09-17"

# The header set SEC asks an automated client to send, quoted from the page it
# publishes them on ("Accessing EDGAR Data", retrieved 2026-09-18, excerpt kept
# at data/real/regulatory/sec-accessing-edgar-data-headers.txt):
#
#   Sample Declared Bot Request Headers:
#     User-Agent:      Sample Company Name AdminContact@<sample company domain>.com
#     Accept-Encoding: gzip, deflate
#     Host:            www.sec.gov
#
# The collector's first version declared a User-Agent with a contact address but
# left urllib's default ``Accept-Encoding: identity``, and the collection run of
# 2026-09-18 was answered HTTP 403 for the ticker->CIK map.  Both headers in the
# published set are now sent, the UA in the published shape (a name followed by a
# contact address), and what came back is recorded in the manifest either way.
#   SOURCE: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
SEC_UA = "StockPaperSim buffedlizard55-lab@users.noreply.github.com"
BROWSER_UA = ("Mozilla/5.0 (compatible; StockPaperSim/2.0; "
              "+https://github.com/buffedlizard55-lab/StockPaperSim)")

# What every request sends before a caller adds anything: SEC's documented
# Accept-Encoding, and an Accept that does not pretend to be a browser.
BASE_HEADERS: Dict[str, str] = {"Accept": "*/*", "Accept-Encoding": "gzip, deflate"}

#: Per-kind policy for how many attempts a URL gets and how long each may take.
#: Nasdaq's quote API has timed out on every run so far - eight symbols, three
#: attempts each, 45 seconds per attempt, which is eighteen of the collection's
#: twenty-five minutes spent proving the same timeout, and the reason the SEC
#: section (which runs next) did not start until minute nineteen.  The failure is
#: recorded exactly as before; the policy changes how long the run spends proving
#: it, not what the record says.
FETCH_POLICY: Dict[str, dict] = {
    "nasdaq": {"tries": 2, "timeout": 20.0},
}
DEFAULT_FETCH_POLICY: dict = {"tries": 3, "timeout": 45.0}

# Yahoo tickers.  ^GSPC is included specifically as an independent copy of the
# S&P 500 index so the FRED SP500 series can be cross-checked against it.
EQUITY_UNIVERSE: Tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "GLD", "UNG", "XLU", "XBI", "IBB", "TLT",
    "DKNG", "FLUT", "PENN", "SRAD", "GENI",
    "AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "TSLA", "MU", "T",
    "^GSPC", "^VIX",
)

# FRED series: id -> short human description used in the site's source register.
FRED_SERIES: Dict[str, str] = {
    "SP500": "S&P 500 index level (daily close)",
    "VIXCLS": "CBOE Volatility Index (VIX) close",
    "DGS10": "10-year Treasury constant-maturity yield",
    "DGS3MO": "3-month Treasury constant-maturity yield",
    "DCOILWTICO": "Cushing WTI crude oil spot price",
    "DTWEXBGS": "Nominal broad US dollar index",
    # FRED discontinued GOLDPMGBD228NLBM; both the AM and PM fix ids are
    # requested and whichever resolves is what the site cites. A 404 is
    # recorded in the manifest rather than hidden.
    "GOLDAMGBD228NLBM": "LBMA gold price, AM fix (USD/troy oz)",
    "GOLDPMGBD228NLBM": "LBMA gold price, PM fix (USD/troy oz)",
}

# Issuers for the SEC Form 4 (insider) study.  CIK is re-resolved from the
# official SEC ticker map at run time; if a ticker is missing there, the issuer
# is skipped and the gap is recorded rather than guessed.
INSIDER_TICKERS: Tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "JPM", "XOM",
                                    "JNJ", "PG", "TSLA", "MU", "T")

MAX_FORM4_FILINGS = 600
SEC_MIN_INTERVAL = 0.13          # SEC asks for <= 10 requests/second
GENERIC_MIN_INTERVAL = 0.35

SOURCE_CLASS = {
    "yahoo": "SECONDARY", "stooq": "SECONDARY", "nasdaq": "SECONDARY",
    "fred": "OFFICIAL",
    "sec": "OFFICIAL", "fda": "OFFICIAL", "mlb": "OFFICIAL",
    "nocode": "OFFICIAL", "espn": "SECONDARY", "nba": "OFFICIAL",
     "kalshi": "OFFICIAL-VENDOR", "derived": "DERIVED",
}


def _decode_body(raw: bytes, content_encoding: Optional[str]) -> bytes:
    """Undo the transport encoding, so a hash is of the representation.

    ``Accept-Encoding: gzip, deflate`` is what SEC's published header set asks
    for and what every host here supports, but urllib does not decompress for
    us.  The byte count and SHA-256 recorded in the manifest are of the decoded
    bytes - the same bytes a reader gets when they re-fetch the URL by hand.
    """
    enc = (content_encoding or "").strip().lower()
    if enc in ("gzip", "x-gzip"):
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _snippet(body: bytes, limit: int = 300) -> str:
    """First ``limit`` characters of a response body, whitespace flattened."""
    text = body.decode("utf-8", "replace")
    return re.sub(r"\s+", " ", text).strip()[:limit]


class Fetcher:
    """Rate-limited HTTP GET with a complete, honest audit trail."""

    def __init__(self, manifest: List[dict], max_seconds: float) -> None:
        self.manifest = manifest
        self.t0 = time.time()
        self.max_seconds = max_seconds
        self._last: Dict[str, float] = {}
        self.budget_exhausted = False

    def _wait(self, host_key: str, interval: float) -> None:
        last = self._last.get(host_key, 0.0)
        delay = interval - (time.time() - last)
        if delay > 0:
            time.sleep(delay)
        self._last[host_key] = time.time()

    def timed_out(self) -> bool:
        if time.time() - self.t0 > self.max_seconds:
            self.budget_exhausted = True
        return self.budget_exhausted

    def get(self, url: str, kind: str, headers: Optional[Dict[str, str]] = None,
            interval: float = GENERIC_MIN_INTERVAL, tries: Optional[int] = None,
            note: str = "", timeout: Optional[float] = None) -> Optional[bytes]:
        if self.timed_out():
            return None
        policy = FETCH_POLICY.get(kind, DEFAULT_FETCH_POLICY)
        tries = policy["tries"] if tries is None else tries
        timeout = policy["timeout"] if timeout is None else timeout
        host_key = url.split("/")[2]
        hdrs = {"User-Agent": SEC_UA}
        hdrs.update(BASE_HEADERS)
        hdrs.update(headers or {})
        last_error = ""
        last_status = 0
        error_body = ""
        attempts = 0
        for attempt in range(tries):
            attempts = attempt + 1
            self._wait(host_key, interval)
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    body = _decode_body(resp.read(), resp.headers.get("Content-Encoding"))
                    self.manifest.append({
                        "url": url, "kind": kind, "source_class": SOURCE_CLASS.get(kind, "UNKNOWN"),
                        "status": int(resp.status), "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "ok": True, "attempts": attempts, "note": note,
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })
                    return body
            except urllib.error.HTTPError as exc:
                # The status code is what a reader needs to compare with a
                # re-fetch, so it is recorded as itself rather than as 0; the
                # body is where SEC explains a refusal ("Undeclared Automated
                # Tool" and the header it wants are in there), so the first
                # characters of it are kept too.
                last_status = int(exc.code)
                last_error = f"HTTP {exc.code}"
                try:
                    error_body = _snippet(_decode_body(
                        exc.read(), exc.headers.get("Content-Encoding")
                        if exc.headers else None))
                except Exception:  # noqa: BLE001 - a body we cannot read is not a new failure
                    error_body = ""
                if exc.code in (400, 401, 403, 404, 410):
                    break
            except Exception as exc:  # noqa: BLE001 - network variety is unbounded
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.8 * (attempt + 1))
        self.manifest.append({
            "url": url, "kind": kind, "source_class": SOURCE_CLASS.get(kind, "UNKNOWN"),
            "status": last_status, "bytes": 0, "sha256": "", "ok": False,
            "attempts": attempts, "error": last_error, "error_body": error_body,
            "note": note, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return None


def write_bytes(path: str, body: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(body)


def write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False)
        handle.write("\n")


def write_jsonl(path: str, rows: Iterable[dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            n += 1
    return n


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


# --------------------------------------------------------------------------
# 1. Prices - Yahoo (SECONDARY, breadth + warm-up) and Stooq (SECONDARY, the
#    independent cross-check), plus FRED (OFFICIAL macro series).
# --------------------------------------------------------------------------
def collect_yahoo(fetcher: Fetcher, out: str) -> dict:
    summary = {"ok": [], "failed": [], "rows": {}}
    for symbol in EQUITY_UNIVERSE:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(symbol)}?period1={_epoch(WARMUP_START)}"
               f"&period2={_epoch(COLLECT_END)}&interval=1d&events=div%2Csplit")
        body = fetcher.get(url, "yahoo", headers={"User-Agent": BROWSER_UA},
                           note=f"{symbol} daily bars")
        if body is None:
            summary["failed"].append(symbol)
            continue
        try:
            parsed = _parse_yahoo(symbol, body)
        except Exception as exc:  # noqa: BLE001
            summary["failed"].append(f"{symbol}: {exc}")
            continue
        write_json(os.path.join(out, "prices", "yahoo", f"{_slug(symbol)}.json"), parsed)
        summary["ok"].append(symbol)
        summary["rows"][symbol] = len(parsed["bars"])
    return summary


def _epoch(date_iso: str) -> int:
    import calendar as _cal
    import datetime as _dt
    d = _dt.date.fromisoformat(date_iso)
    return _cal.timegm(d.timetuple())


def _parse_yahoo(symbol: str, body: bytes) -> dict:
    payload = json.loads(body.decode("utf-8"))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"no chart result: {(payload.get('chart') or {}).get('error')}")
    res = result[0]
    meta = res.get("meta") or {}
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    adj = (((res.get("indicators") or {}).get("adjclose") or [{}])[0]
           .get("adjclose") or [])
    offset = int(meta.get("gmtoffset") or 0)
    rows: List[dict] = []
    for i, epoch in enumerate(stamps):
        close = (quote.get("close") or [None])[i]
        if close is None:
            continue
        rows.append({
            "date": time.strftime("%Y-%m-%d", time.gmtime(epoch + offset)),
            "open": _num(quote.get("open", [None])[i]),
            "high": _num(quote.get("high", [None])[i]),
            "low": _num(quote.get("low", [None])[i]),
            "close": _num(close),
            "volume": int(quote.get("volume", [0])[i] or 0),
            "adjclose": _num(adj[i]) if i < len(adj) else _num(close),
        })
    events = res.get("events") or {}
    dividends = []
    for _, item in sorted((events.get("dividends") or {}).items(),
                          key=lambda kv: kv[1].get("date", 0)):
        dividends.append({"date": time.strftime("%Y-%m-%d", time.gmtime(item["date"] + offset)),
                          "amount": item.get("amount")})
    splits = []
    for _, item in sorted((events.get("splits") or {}).items(),
                          key=lambda kv: kv[1].get("date", 0)):
        splits.append({"date": time.strftime("%Y-%m-%d", time.gmtime(item["date"] + offset)),
                       "numerator": item.get("numerator"),
                       "denominator": item.get("denominator")})
    return {
        "symbol": symbol, "provider": "yahoo-v8-chart", "source_class": "SECONDARY",
        "currency": meta.get("currency"), "exchange": meta.get("fullExchangeName"),
        "timezone": meta.get("exchangeTimezoneName"),
        "first_bar": rows[0]["date"] if rows else None,
        "last_bar": rows[-1]["date"] if rows else None,
        "bars": rows, "dividends": dividends, "splits": splits,
    }


def _num(value) -> Optional[float]:
    return None if value is None else round(float(value), 6)


def collect_stooq(fetcher: Fetcher, out: str) -> dict:
    """Independent second source for the cross-check. Stooq blocks some hosts."""
    summary = {"ok": [], "failed": [], "rows": {}}
    for symbol in ("SPY", "QQQ", "GLD", "XBI", "DKNG", "AAPL", "MSFT", "NVDA"):
        url = (f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
               f"&d1={WARMUP_START.replace('-', '')}&d2={COLLECT_END.replace('-', '')}")
        body = fetcher.get(url, "stooq", headers={"User-Agent": BROWSER_UA},
                           note=f"{symbol} daily CSV (cross-check)")
        if body is None:
            summary["failed"].append(symbol)
            continue
        text = body.decode("utf-8", "replace")
        if "access denied" in text.lower() or text.lstrip().startswith("<"):
            summary["failed"].append(f"{symbol}: host refused (Access denied)")
            fetcher.manifest[-1]["ok"] = False
            fetcher.manifest[-1]["error"] = "host refused (Access denied)"
            continue
        rows = []
        for line in text.splitlines()[1:]:
            parts = line.strip().split(",")
            if len(parts) < 6 or not parts[0][:2] == "20":
                continue
            rows.append({"date": parts[0], "open": float(parts[1]), "high": float(parts[2]),
                         "low": float(parts[3]), "close": float(parts[4]),
                         "volume": int(float(parts[5]))})
        write_json(os.path.join(out, "prices", "stooq", f"{_slug(symbol)}.json"),
                   {"symbol": symbol, "provider": "stooq", "source_class": "SECONDARY",
                    "bars": rows})
        summary["ok"].append(symbol)
        summary["rows"][symbol] = len(rows)
    return summary


NASDAQ_ASSETCLASS = {"SPY": "etf", "QQQ": "etf", "GLD": "etf", "XBI": "etf",
                     "DKNG": "stocks", "AAPL": "stocks", "MSFT": "stocks", "NVDA": "stocks"}


def collect_nasdaq(fetcher: Fetcher, out: str) -> dict:
    """Nasdaq's own historical quote API - a second publisher for the same bars.

    NASDAQ is the exchange operator, so this is a venue-published series rather
    than a re-aggregator, but it is still not the consolidated tape (it prints
    the Nasdaq-listed session, no after-hours).  Files are labelled SECONDARY.
    """
    summary = {"ok": [], "failed": [], "rows": {}}
    for symbol, asset in NASDAQ_ASSETCLASS.items():
        url = ("https://api.nasdaq.com/api/quote/" + symbol +
               f"/historical?assetclass={asset}&fromdate={WARMUP_START}"
               f"&limit=1000&todate={COLLECT_END}")
        body = fetcher.get(url, "nasdaq",
                           headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                           note=f"{symbol} daily history (cross-check)")
        if body is None:
            summary["failed"].append(symbol)
            continue
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            summary["failed"].append(f"{symbol}: {exc}")
            continue
        rows = []
        for row in ((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or []:
            try:
                rows.append({"date": _us_date_to_iso(row["date"]),
                             "close": float(row["close"].replace("$", "").replace(",", "")),
                             "volume": int(row["volume"].replace(",", "") or 0),
                             "open": float(row["open"].replace("$", "").replace(",", "")),
                             "high": float(row["high"].replace("$", "").replace(",", "")),
                             "low": float(row["low"].replace("$", "").replace(",", ""))})
            except (KeyError, ValueError, AttributeError):
                continue
        rows.sort(key=lambda r: r["date"])
        write_json(os.path.join(out, "prices", "nasdaq", f"{_slug(symbol)}.json"),
                   {"symbol": symbol, "provider": "api.nasdaq.com", "source_class": "SECONDARY",
                    "bars": rows})
        summary["ok"].append(symbol)
        summary["rows"][symbol] = len(rows)
    return summary


def _us_date_to_iso(text: str) -> str:
    import datetime as _dt
    return _dt.datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()


def collect_fred(fetcher: Fetcher, out: str) -> dict:
    summary = {"ok": [], "failed": [], "rows": {}}
    for series, description in FRED_SERIES.items():
        url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
               f"&cosd={WARMUP_START}&coed={COLLECT_END}")
        body = fetcher.get(url, "fred", headers={"User-Agent": BROWSER_UA},
                           note=f"{series}: {description}")
        if body is None:
            summary["failed"].append(series)
            continue
        path = os.path.join(out, "fred", f"{series}_{WARMUP_START}_{COLLECT_END}.csv")
        write_bytes(path, body)
        rows = 0
        for line in body.decode("utf-8", "replace").splitlines()[1:]:
            if line.strip() and line.split(",")[-1].strip():
                rows += 1
        summary["ok"].append(series)
        summary["rows"][series] = rows
    return summary


# --------------------------------------------------------------------------
# 2. SEC EDGAR - Form 4 insider filings (OFFICIAL, primary).
# --------------------------------------------------------------------------
SEC_TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
#: Per-company filing feed, the fallback when the bulk map is refused.  Same host
#: as the map and a different script, so the outcome separates "this one document
#: was refused" from "this client may not read sec.gov at all".
SEC_COMPANY_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?"
                    "action=getcompany&CIK={ticker}&type=4&owner=include"
                    "&count=10&output=atom")
SEC_CIK_TAG = re.compile(r"<cik>\s*(\d+)\s*</cik>", re.I)
SEC_CIK_IN_URL = re.compile(r"CIK=(\d{10})")
SEC_TICKERS_TAG = re.compile(r"<tickers>\s*([^<]*?)\s*</tickers>", re.I)


def cik_from_company_feed(body: bytes, ticker: str) -> Optional[int]:
    """The CIK named by one company feed, or None if the feed does not name it.

    Tolerant on purpose: the tag is the documented shape and the same number is
    also printed inside a URL on the page.  A feed that names a different ticker
    than the one asked for is rejected rather than used, because the mapping is
    what decides which company's filings get read.
    """
    text = body.decode("utf-8", "replace")
    named = [t.strip().upper() for t in SEC_TICKERS_TAG.findall(text)]
    if named and ticker.upper() not in named:
        return None
    match = SEC_CIK_TAG.search(text) or SEC_CIK_IN_URL.search(text)
    if not match:
        return None
    return int(match.group(1))


def _sec_cik_map(fetcher: Fetcher, out: str, summary: dict) -> Optional[Dict[str, int]]:
    """ticker -> CIK for the issuers this study reads, with its source recorded.

    The official bulk map is requested first, with the header set SEC publishes.
    If it is refused, the per-company feed is asked once per insider ticker
    instead: it is the same publisher and a different endpoint, and its CIK is
    enough to continue into the submissions API, so a refusal of one document
    does not become a refusal of the whole insider study.  A third option -
    presenting a browser User-Agent - is deliberately not implemented: this
    project does not misrepresent its client to a regulator, and a 403 that says
    why is worth more than a 200 that does not say how it was obtained.
    """
    body = fetcher.get(SEC_TICKER_MAP, "sec", headers={"Accept": "application/json"},
                       interval=SEC_MIN_INTERVAL, note="official ticker->CIK map")
    if body is not None:
        write_bytes(os.path.join(out, "sec", "company_tickers.json"), body)
        mapping = json.loads(body.decode("utf-8"))
        rows = {str(row["ticker"]).upper(): int(row["cik_str"])
                for row in mapping.values()}
        summary["cik_map_source"] = "company_tickers.json (official bulk map)"
        return {t: rows[t] for t in INSIDER_TICKERS if t in rows}

    summary["errors"].append(
        "company_tickers.json refused; falling back to the per-company feed")
    resolved: Dict[str, int] = {}
    for ticker in INSIDER_TICKERS:
        feed = fetcher.get(SEC_COMPANY_FEED.format(ticker=ticker), "sec",
                           interval=SEC_MIN_INTERVAL,
                           note=f"{ticker} company feed (ticker->CIK fallback)")
        if feed is None:
            continue
        cik = cik_from_company_feed(feed, ticker)
        if cik is None:
            summary["errors"].append(f"{ticker}: company feed named no CIK")
            continue
        resolved[ticker] = cik
    if resolved:
        summary["cik_map_source"] = "per-company feed (browse-edgar, atom)"
        write_json(os.path.join(out, "sec", "cik_map.json"), {
            "window": [WARMUP_START, COLLECT_END],
            "source": ("https://www.sec.gov/cgi-bin/browse-edgar?"
                       "action=getcompany&type=4"),
            "why": ("company_tickers.json was refused; the CIK in each company's "
                    "own filing feed is the same identifier, so the study "
                    "continues from it and says so here."),
            "map": resolved,
        })
        return resolved
    return None


def collect_sec(fetcher: Fetcher, out: str) -> dict:
    summary = {"tickers_ok": [], "tickers_failed": [], "filings": 0,
               "transactions": 0, "skipped_tickers": [], "errors": [],
               "cik_map_source": ""}
    ticker_to_cik = _sec_cik_map(fetcher, out, summary)
    if not ticker_to_cik:
        summary["errors"].append("no ticker->CIK mapping could be retrieved")
        return summary

    transactions: List[dict] = []
    index_rows: List[dict] = []
    for ticker in INSIDER_TICKERS:
        cik = ticker_to_cik.get(ticker)
        if cik is None:
            summary["skipped_tickers"].append(f"{ticker} (not in the official SEC map)")
            continue
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        body = fetcher.get(url, "sec", headers={"Accept": "application/json"},
                           interval=SEC_MIN_INTERVAL, note=f"{ticker} filing index")
        if body is None:
            summary["tickers_failed"].append(ticker)
            continue
        try:
            submissions = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            summary["tickers_failed"].append(f"{ticker}: {exc}")
            continue
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary = recent.get("primaryDocument") or []
        issuer_name = submissions.get("name") or ticker
        kept = 0
        for i, form in enumerate(forms):
            if not form.startswith("4") or i >= len(dates):
                continue
            filed = dates[i]
            if not (WARMUP_START <= filed <= COLLECT_END):
                continue
            accn = accessions[i]
            doc = primary[i] if i < len(primary) else ""
            index_rows.append({"ticker": ticker, "cik": cik, "issuer": issuer_name,
                               "form": form, "filed": filed, "accession": accn,
                               "primary_document": doc,
                               "filing_index": (f"https://www.sec.gov/Archives/edgar/data/"
                                                f"{cik}/{accn.replace('-', '')}/"
                                                f"{accn}-index.htm")})
            kept += 1
        summary["tickers_ok"].append(f"{ticker} ({kept} Form 4 filings in window)")

    # Fetch and parse the filings themselves, newest first, inside the budget.
    index_rows.sort(key=lambda r: r["filed"], reverse=True)
    for row in index_rows[:MAX_FORM4_FILINGS]:
        if fetcher.timed_out():
            summary["errors"].append("time budget reached during Form 4 downloads")
            break
        if not row["primary_document"]:
            continue
        url = (f"https://www.sec.gov/Archives/edgar/data/{row['cik']}/"
               f"{row['accession'].replace('-', '')}/{row['primary_document']}")
        body = fetcher.get(url, "sec", headers={"Accept": "application/xml,text/xml,*/*"},
                           interval=SEC_MIN_INTERVAL,
                           note=f"{row['ticker']} Form 4 {row['accession']}")
        if body is None:
            continue
        digest = hashlib.sha256(body).hexdigest()
        write_bytes(os.path.join(out, "sec", "form4", f"{row['ticker']}_{row['accession']}.xml"),
                    body)
        summary["filings"] += 1
        try:
            parsed = parse_form4(body, row, url, digest)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{row['accession']}: parse failed ({exc})")
            continue
        transactions.extend(parsed)
    write_json(os.path.join(out, "sec", "form4_index.json"),
               {"window": [WARMUP_START, COLLECT_END], "filings": index_rows})
    summary["transactions"] = write_jsonl(
        os.path.join(out, "sec", "form4_transactions.jsonl"), transactions)
    return summary


_TAG = re.compile(r"\{[^}]*\}")


def _text(node, path: str) -> Optional[str]:
    for candidate in path.split("/"):
        if node is None:
            return None
        node = node.find(candidate)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def parse_form4(body: bytes, row: dict, url: str, digest: str) -> List[dict]:
    """Extract non-derivative transactions from a verbatim Form 4 XML."""
    root = ET.fromstring(body)
    for node in root.iter():
        node.tag = _TAG.sub("", node.tag)
    owner = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner") if root is not None else None
    relationship = rel.find("reportingOwnerRelationship") if rel is not None else None
    roles = []
    if relationship is not None:
        for flag, label in (("isDirector", "director"), ("isOfficer", "officer"),
                            ("isTenPercentOwner", "10% owner"), ("isOther", "other")):
            value = _text(relationship, flag)
            if value and value.lower() in ("1", "true"):
                roles.append(label)
    title = _text(relationship, "officerTitle") if relationship is not None else None
    tenb5 = _text(root, "aff10b5One")
    out: List[dict] = []
    table = root.find("nonDerivativeTable") if root is not None else None
    if table is None:
        return out
    for txn in table.findall("nonDerivativeTransaction"):
        shares = _text(txn, "transactionAmounts/transactionShares/value")
        price = _text(txn, "transactionAmounts/transactionPricePerShare/value")
        out.append({
            "ticker": row["ticker"], "cik": row["cik"], "issuer": row["issuer"],
            "insider": owner, "roles": roles, "title": title,
            "transaction_date": _text(txn, "transactionDate/value"),
            "filed_date": row["filed"],
            "code": _text(txn, "transactionCoding/transactionCode"),
            "security": _text(txn, "securityTitle/value"),
            "shares": float(shares) if shares else None,
            "price": float(price) if price else None,
            "shares_after": _text(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
            "ownership": _text(txn, "ownershipNature/directOrIndirectOwnership/value"),
            "tenb5_1_plan": tenb5,
            "accession": row["accession"], "filing_index": row["filing_index"],
            "source_url": url, "source_sha256": digest,
            "source_class": "OFFICIAL",
        })
    return out


# --------------------------------------------------------------------------
# 3. FDA - openFDA drug approval/supplement decisions (OFFICIAL).
# --------------------------------------------------------------------------
def collect_fda(fetcher: Fetcher, out: str) -> dict:
    summary = {"rows": 0, "failed": [], "pages": 0}
    rows: List[dict] = []
    skip = 0
    start_compact = WARMUP_START.replace("-", "")
    end_compact = COLLECT_END.replace("-", "")
    while True:
        url = ("https://api.fda.gov/drug/drugsfda.json?search="
               f"submissions.submission_status_date:[{start_compact}+TO+{end_compact}]"
               f"&limit=1000&skip={skip}")
        body = fetcher.get(url, "fda", headers={"Accept": "application/json"},
                           note=f"openFDA drugsfda skip={skip}")
        if body is None:
            summary["failed"].append(f"skip={skip}")
            break
        payload = json.loads(body.decode("utf-8"))
        results = payload.get("results") or []
        summary["pages"] += 1
        for application in results:
            for sub in application.get("submissions") or []:
                date = str(sub.get("submission_status_date") or "")
                if not (start_compact <= date <= end_compact):
                    continue
                products = application.get("products") or [{}]
                rows.append({
                    "application_number": application.get("application_number"),
                    "sponsor_name": application.get("sponsor_name"),
                    "submission_type": sub.get("submission_type"),
                    "submission_number": sub.get("submission_number"),
                    "status": sub.get("submission_status"),
                    "status_date": date,
                    "review_priority": sub.get("review_priority"),
                    "class_code": sub.get("submission_class_code"),
                    "brand_name": (products[0] or {}).get("brand_name"),
                    "generic_name": next((ing.get("name") for ing in
                                          ((products[0] or {}).get("active_ingredients") or [])), None),
                    "source": "openFDA /drug/drugsfda",
                    "source_class": "OFFICIAL",
                })
        total = (payload.get("meta") or {}).get("results", {}).get("total", 0)
        skip += 1000
        if skip >= total or not results:
            break
    summary["rows"] = write_jsonl(os.path.join(out, "fda", "openfda_decisions.jsonl"), rows)
    return summary


# --------------------------------------------------------------------------
# 4. Sports - official league endpoints where they exist, ESPN (SECONDARY)
#    for breadth, with the class recorded per file.
# --------------------------------------------------------------------------
def collect_mlb(fetcher: Fetcher, out: str) -> dict:
    url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-03-01"
           "&endDate=2026-09-17&gameType=R&fields=dates,date,games,gamePk,officialDate,"
           "status,detailedState,teams,away,home,team,id,name,score,isWinner,"
           "leagueRecord,wins,losses,pct,venue")
    body = fetcher.get(url, "mlb", headers={"Accept": "application/json"},
                       note="MLB 2026 regular-season schedule and finals")
    if body is None:
        return {"rows": 0, "ok": False}
    payload = json.loads(body.decode("utf-8"))
    rows = []
    for day in payload.get("dates") or []:
        for game in day.get("games") or []:
            away = ((game.get("teams") or {}).get("away") or {})
            home = ((game.get("teams") or {}).get("home") or {})
            rows.append({
                "date": game.get("officialDate") or day.get("date"),
                "game_pk": game.get("gamePk"),
                "status": ((game.get("status") or {}).get("detailedState")),
                "away": (away.get("team") or {}).get("name"),
                "away_score": away.get("score"),
                "away_record": _record(away),
                "home": (home.get("team") or {}).get("name"),
                "home_score": home.get("score"),
                "home_record": _record(home),
                "venue": (game.get("venue") or {}).get("name"),
                "source": "https://statsapi.mlb.com/api/v1/schedule",
                "source_class": "OFFICIAL",
            })
    write_bytes(os.path.join(out, "sports", "mlb_schedule_2026.json"), body)
    n = write_jsonl(os.path.join(out, "sports", "mlb_games_2026.jsonl"), rows)
    return {"rows": n, "ok": True}


def _record(side: dict) -> Optional[str]:
    rec = side.get("leagueRecord") or {}
    if rec.get("wins") is None:
        return None
    return f"{rec.get('wins')}-{rec.get('losses')}"


ESPN_SPORTS = {
    "nfl": "football/nfl", "ncaaf": "football/college-football",
    "nba": "basketball/nba", "mlb": "baseball/mlb",
}

#: (season, seasontype, first week, last week).  ESPN's scoreboard endpoint
#: answers HTTP 400 for a date *range*; it accepts a season + seasontype + week,
#: which is what the first collection run got wrong.
ESPN_WEEKS = {
    "nfl": [(2025, 2, 1, 18), (2025, 3, 1, 4)],
    "ncaaf": [(2025, 2, 1, 15), (2025, 3, 1, 1)],
    "nba": [(2025, 2, 1, 25), (2026, 3, 1, 4)],
    "mlb": [(2026, 2, 1, 27)],
}


def collect_espn(fetcher: Fetcher, out: str) -> dict:
    summary: Dict[str, dict] = {}
    for key, path in ESPN_SPORTS.items():
        rows: List[dict] = []
        failed = 0
        for season, seasontype, first_week, last_week in ESPN_WEEKS.get(key, []):
            for week in range(first_week, last_week + 1):
                url = (f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
                       f"?dates={season}&seasontype={seasontype}&week={week}&limit=1000")
                body = fetcher.get(url, "espn",
                                   note=f"{key} {season} type {seasontype} week {week} (secondary)")
                if body is None:
                    failed += 1
                    continue
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    failed += 1
                    continue
                for event in payload.get("events") or []:
                    competitions = event.get("competitions") or [{}]
                    comp = competitions[0]
                    sides = comp.get("competitors") or []
                    if len(sides) < 2:
                        continue
                    home = next((s for s in sides if s.get("homeAway") == "home"), sides[0])
                    away = next((s for s in sides if s.get("homeAway") == "away"), sides[1])
                    status = ((comp.get("status") or {}).get("type") or {}).get("name")
                    rows.append({
                        "league": key, "date": (event.get("date") or "")[:10],
                        "event_id": event.get("id"), "name": event.get("name"),
                        "status": status,
                        "home": ((home.get("team") or {}).get("displayName")),
                        "home_score": _score(home), "away": ((away.get("team") or {}).get("displayName")),
                        "away_score": _score(away),
                        "venue": ((comp.get("venue") or {}).get("fullName")),
                        "source": "https://site.api.espn.com/apis/site/v2/sports",
                        "source_class": "SECONDARY",
                    })
        n = write_jsonl(os.path.join(out, "sports", f"{key}_scoreboard.jsonl"), rows)
        summary[key] = {"rows": n, "chunks_failed": failed}
    return summary


def _score(side: dict) -> Optional[float]:
    try:
        return float(side.get("score"))
    except (TypeError, ValueError):
        return None


def _chunks(start: str, end: str, days: int) -> List[Tuple[str, str]]:
    import datetime as dt
    out = []
    cur = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while cur <= stop:
        nxt = min(cur + dt.timedelta(days=days - 1), stop)
        out.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt + dt.timedelta(days=1)
    return out


def collect_nba_official(fetcher: Fetcher, out: str) -> dict:
    """The NBA publishes its own schedule JSON on cdn.nba.com."""
    url = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
    body = fetcher.get(url, "nba", headers={"Accept": "application/json"},
                       note="NBA official schedule feed (game days, no final scores)")
    if body is None:
        return {"ok": False, "rows": 0}
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rows": 0, "error": str(exc)}
    rows = []
    for day in ((payload.get("leagueSchedule") or {}).get("gameDates") or []):
        for game in day.get("games") or []:
            rows.append({
                "date": (day.get("gameDate") or "")[:10].replace("/", "-"),
                "game_id": game.get("gameId"),
                "home": ((game.get("homeTeam") or {}).get("teamName")),
                "away": ((game.get("awayTeam") or {}).get("teamName")),
                "status": game.get("gameStatusText"),
                "source": url, "source_class": "OFFICIAL",
            })
    write_bytes(os.path.join(out, "sports", "nba_schedule_official.json"), body)
    n = write_jsonl(os.path.join(out, "sports", "nba_schedule_official.jsonl"), rows)
    return {"ok": True, "rows": n}


OFFICIAL_SPORTS_DOCS = {
    "nba_injury_report_index": "https://official.nba.com/nba-injury-report-2025-26-season/",
    "nfl_injuries": "https://www.nfl.com/injuries/",
    "ncaa_scoreboard_fbs_2025_week10":
        "https://data.ncaa.com/casablanca/scoreboard/football/fbs/2025/10/scoreboard.json",
    "nba_stats_scoreboard_probe":
        "https://stats.nba.com/stats/scoreboardv3?GameDate=2026-03-15&LeagueID=00",
}


def collect_official_sports_docs(fetcher: Fetcher, out: str) -> dict:
    """Probe the league-published documents the injury/score adapters cite.

    These are the documents a *forward* test would read.  They are fetched here
    so the site can show, with a hash and a date, that the adapter target exists
    and is official - even where no historical archive of the same feed exists.
    """
    summary: Dict[str, dict] = {}
    for key, url in OFFICIAL_SPORTS_DOCS.items():
        headers = {"Accept": "application/json,text/html,*/*"}
        if "stats.nba.com" in url:
            headers.update({"User-Agent": BROWSER_UA, "Referer": "https://www.nba.com/"})
        body = fetcher.get(url, "nocode", headers=headers, note=f"official sports document {key}")
        if body is None:
            summary[key] = {"ok": False}
            continue
        path = os.path.join(out, "sports", "official", f"{key}.raw")
        write_bytes(path, body)
        summary[key] = {"ok": True, "bytes": len(body)}
    return summary


# --------------------------------------------------------------------------
# 5. Weather - NOAA/NCEI daily summaries (OFFICIAL) for San Francisco.
# --------------------------------------------------------------------------
WEATHER_STATIONS = {"USW00023272": "San Francisco (downtown / SFO-area co-op station)",
                    "USW00023234": "San Francisco International Airport"}


def collect_weather(fetcher: Fetcher, out: str) -> dict:
    summary: Dict[str, dict] = {}
    for station, label in WEATHER_STATIONS.items():
        url = ("https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries"
               f"&stations={station}&startDate={WARMUP_START}&endDate={COLLECT_END}"
               "&dataTypes=PRCP,TMAX,TMIN,TAVG&format=json&units=standard")
        body = fetcher.get(url, "nocode", headers={"Accept": "application/json"},
                           note=f"NCEI daily summaries {station}: {label}")
        if body is None:
            summary[station] = {"ok": False}
            continue
        write_bytes(os.path.join(out, "weather", f"{station}_daily.json"), body)
        try:
            rows = json.loads(body.decode("utf-8"))
            summary[station] = {"ok": True, "rows": len(rows), "label": label}
        except Exception as exc:  # noqa: BLE001
            summary[station] = {"ok": False, "error": str(exc)}
    return summary


# --------------------------------------------------------------------------
# 6. Kalshi - the venue's own settled market data (OFFICIAL-VENDOR).
# --------------------------------------------------------------------------
KALSHI_SERIES = ("KXNFLGAME", "KXNBA", "KXMLBGAME", "KXHIGHNY", "KXAAAGASM")


def collect_kalshi(fetcher: Fetcher, out: str) -> dict:
    summary: Dict[str, dict] = {}
    for series in KALSHI_SERIES:
        url = ("https://api.elections.kalshi.com/trade-api/v2/markets"
               f"?status=settled&limit=200&series_ticker={series}")
        body = fetcher.get(url, "kalshi", headers={"Accept": "application/json"},
                           note=f"{series} settled markets")
        if body is None:
            summary[series] = {"ok": False}
            continue
        payload = json.loads(body.decode("utf-8"))
        markets = payload.get("markets") or []
        rows = [{"ticker": m.get("ticker"), "event_ticker": m.get("event_ticker"),
                 "title": m.get("title"), "close_time": m.get("close_time"),
                 "settlement_value": m.get("settlement_value"),
                 "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
                 "last_price": m.get("last_price"), "volume": m.get("volume"),
                 "open_interest": m.get("open_interest"),
                 "source": "https://api.elections.kalshi.com/trade-api/v2/markets",
                 "source_class": "OFFICIAL-VENDOR"} for m in markets]
        n = write_jsonl(os.path.join(out, "kalshi", f"{series}_settled.jsonl"), rows)
        summary[series] = {"ok": True, "rows": n}
    return summary


# --------------------------------------------------------------------------
# 7. Derived cross-checks - the evidence that the two price sources agree.
# --------------------------------------------------------------------------
def crosscheck(out: str) -> dict:
    def load_yahoo(symbol: str) -> Dict[str, float]:
        path = os.path.join(out, "prices", "yahoo", f"{_slug(symbol)}.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return {b["date"]: b["close"] for b in payload["bars"] if b.get("close")}

    report: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pairs": [], "notes": []}
    # (a) SPY ETF vs the S&P 500 index (Yahoo ^GSPC) - different instruments, so
    #     the test is return correlation, not price equality.
    gspc, spy = load_yahoo("^GSPC"), load_yahoo("SPY")
    common = sorted(set(gspc) & set(spy))
    if len(common) > 30:
        rets_y = [gspc[common[i]] / gspc[common[i - 1]] - 1 for i in range(1, len(common))]
        rets_s = [spy[common[i]] / spy[common[i - 1]] - 1 for i in range(1, len(common))]
        report["pairs"].append({
            "kind": "yahoo-vs-yahoo", "a": "SPY", "b": "^GSPC", "n": len(rets_y),
            "return_correlation": _corr(rets_y, rets_s),
            "note": "same provider, different instruments: a sanity check, not independence",
        })
    # (b) Yahoo ^GSPC vs FRED SP500 - independent publishers of the same index.
    fred_path = os.path.join(out, "fred", f"SP500_{WARMUP_START}_{COLLECT_END}.csv")
    if os.path.exists(fred_path) and gspc:
        fred: Dict[str, float] = {}
        with open(fred_path, "r", encoding="utf-8") as handle:
            for line in handle.read().splitlines()[1:]:
                parts = line.split(",")
                if len(parts) == 2 and parts[1].strip():
                    try:
                        fred[parts[0].strip()] = float(parts[1])
                    except ValueError:
                        continue
        common = sorted(set(fred) & set(gspc))
        if len(common) > 30:
            diffs = [abs(fred[d] - gspc[d]) / fred[d] for d in common]
            rets_f = [fred[common[i]] / fred[common[i - 1]] - 1 for i in range(1, len(common))]
            rets_g = [gspc[common[i]] / gspc[common[i - 1]] - 1 for i in range(1, len(common))]
            report["pairs"].append({
                "kind": "fred-vs-yahoo", "a": "FRED SP500", "b": "Yahoo ^GSPC",
                "n": len(common), "max_abs_level_diff": max(diffs),
                "median_abs_level_diff": _median(diffs),
                "return_correlation": _corr(rets_f, rets_g),
                "max_abs_daily_return_diff": max(abs(a - b) for a, b in zip(rets_f, rets_g)),
                "note": ("two independent publishers of the same index; level differences "
                         "come from the fact that FRED's SP500 is a daily close index series"),
            })
    # (c) Yahoo vs an independent publisher (Nasdaq's own API, else Stooq).
    for symbol in ("SPY", "QQQ", "GLD", "XBI", "DKNG", "AAPL", "MSFT", "NVDA"):
        for provider in ("nasdaq", "stooq"):
            other_path = os.path.join(out, "prices", provider, f"{_slug(symbol)}.json")
            yahoo_path = os.path.join(out, "prices", "yahoo", f"{_slug(symbol)}.json")
            if not (os.path.exists(other_path) and os.path.exists(yahoo_path)):
                continue
            with open(other_path, "r", encoding="utf-8") as handle:
                other = {b["date"]: b["close"] for b in json.load(handle)["bars"]}
            with open(yahoo_path, "r", encoding="utf-8") as handle:
                yahoo = {b["date"]: b["close"] for b in json.load(handle)["bars"]}
            common = sorted(set(other) & set(yahoo))
            if len(common) < 30:
                continue
            diffs_bps = [abs(other[d] - yahoo[d]) / yahoo[d] * 10000.0 for d in common]
            report["pairs"].append({
                "kind": f"yahoo-vs-{provider}", "a": "Yahoo", "b": provider,
                "symbol": symbol, "n": len(common),
                "max_abs_diff_bps": max(diffs_bps),
                "median_abs_diff_bps": _median(diffs_bps),
                "note": ("both are publishers rather than the consolidated tape; "
                         "agreement is a necessary check, not a sufficient one"),
            })
            break

    # (c2) Yahoo vs Stooq closes on common dates.
    for symbol in ("SPY", "QQQ", "GLD", "XBI", "DKNG", "AAPL", "MSFT", "NVDA"):
        stooq_path = os.path.join(out, "prices", "stooq", f"{_slug(symbol)}.json")
        if not os.path.exists(stooq_path):
            continue
        with open(stooq_path, "r", encoding="utf-8") as handle:
            stooq = {b["date"]: b["close"] for b in json.load(handle)["bars"]}
        yahoo = {b["date"]: b["close"] for b in
                 json.load(open(os.path.join(out, "prices", "yahoo", f"{_slug(symbol)}.json"),
                                encoding="utf-8"))["bars"]}
        common = sorted(set(stooq) & set(yahoo))
        if not common:
            continue
        diffs_bps = [abs(stooq[d] - yahoo[d]) / yahoo[d] * 10000.0 for d in common]
        report["pairs"].append({
            "kind": "yahoo-vs-stooq", "a": "Yahoo", "b": "Stooq", "symbol": symbol,
            "n": len(common), "max_abs_diff_bps": max(diffs_bps),
            "median_abs_diff_bps": _median(diffs_bps),
            "note": "both are aggregators; agreement is necessary but not sufficient",
        })
    write_json(os.path.join(out, "crosschecks", "price_crosscheck.json"), report)
    return report


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return float("nan")
    ma, mb = sum(a[:n]) / n, sum(b[:n]) / n
    va = sum((x - ma) ** 2 for x in a[:n])
    vb = sum((x - mb) ** 2 for x in b[:n])
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    if va <= 0 or vb <= 0:
        return float("nan")
    return cov / (va ** 0.5 * vb ** 0.5)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return float("nan")
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


# --------------------------------------------------------------------------
# 8. Coverage report - what Season 2 needs versus what we actually have.
# --------------------------------------------------------------------------
def coverage_report(out: str, results: dict) -> dict:
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window": {"warmup_start": WARMUP_START, "season_start": SEASON_START,
                   "season_end": SEASON_END},
        "collected": results,
        "sources": [],
    }
    for path, label, klass in (
        (os.path.join(out, "prices", "yahoo"), "Yahoo Finance chart API", "SECONDARY"),
        (os.path.join(out, "prices", "stooq"), "Stooq daily CSV (blocked)", "SECONDARY"),
        (os.path.join(out, "prices", "nasdaq"), "Nasdaq quote API", "SECONDARY"),
        (os.path.join(out, "fred"), "FRED (Federal Reserve Bank of St. Louis)", "OFFICIAL"),
        (os.path.join(out, "sec"), "SEC EDGAR (Form 4 + ticker map)", "OFFICIAL"),
        (os.path.join(out, "fda"), "FDA openFDA /drug/drugsfda", "OFFICIAL"),
        (os.path.join(out, "sports"), "League scoreboards", "MIXED"),
        (os.path.join(out, "weather"), "NOAA/NCEI daily summaries", "OFFICIAL"),
        (os.path.join(out, "kalshi"), "Kalshi trade API (settled markets)", "OFFICIAL-VENDOR"),
    ):
        files, size = 0, 0
        for dirpath, _, filenames in os.walk(path):
            for name in filenames:
                files += 1
                size += os.path.getsize(os.path.join(dirpath, name))
        report["sources"].append({"label": label, "source_class": klass,
                                  "path": os.path.relpath(path, os.path.dirname(out)),
                                  "files": files, "bytes": size})
    write_json(os.path.join(out, "coverage_report.json"), report)
    return report


# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "real"))
    parser.add_argument("--max-seconds", type=float, default=1500.0)
    parser.add_argument("--only", default="prices,sec,fda,sports,weather,kalshi")
    args = parser.parse_args(argv)

    out = os.path.abspath(args.out)
    only = {p.strip() for p in args.only.split(",") if p.strip()}
    manifest: List[dict] = []
    fetcher = Fetcher(manifest, args.max_seconds)
    results: dict = {}

    if "prices" in only:
        results["yahoo"] = collect_yahoo(fetcher, out)
        print(f"yahoo: {len(results['yahoo']['ok'])} ok, {len(results['yahoo']['failed'])} failed")
        results["stooq"] = collect_stooq(fetcher, out)
        print(f"stooq: {len(results['stooq']['ok'])} ok, {len(results['stooq']['failed'])} failed")
        results["fred"] = collect_fred(fetcher, out)
        print(f"fred: {results['fred']['ok']}")
        results["nasdaq"] = collect_nasdaq(fetcher, out)
        print(f"nasdaq: {len(results['nasdaq']['ok'])} ok")
    if "sec" in only:
        results["sec"] = collect_sec(fetcher, out)
        print(f"sec: {results['sec']['transactions']} transactions from "
              f"{results['sec']['filings']} filings")
    if "fda" in only:
        results["fda"] = collect_fda(fetcher, out)
        print(f"fda: {results['fda']['rows']} decision rows")
    if "sports" in only:
        results["mlb"] = collect_mlb(fetcher, out)
        results["espn"] = collect_espn(fetcher, out)
        results["nba"] = collect_nba_official(fetcher, out)
        results["official_docs"] = collect_official_sports_docs(fetcher, out)
        print(f"sports: mlb={results['mlb']} espn={results['espn']} nba={results['nba']}")
    if "weather" in only:
        results["weather"] = collect_weather(fetcher, out)
        print(f"weather: {results['weather']}")
    if "kalshi" in only:
        results["kalshi"] = collect_kalshi(fetcher, out)
        print(f"kalshi: {results['kalshi']}")

    if "prices" in only:
        results["crosscheck"] = crosscheck(out)

    manifest_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "collector": "scripts/collect_real_data.py",
        "window": {"warmup_start": WARMUP_START, "season_start": SEASON_START,
                   "season_end": SEASON_END},
        "requests": manifest,
        "ok": sum(1 for m in manifest if m["ok"]),
        "failed": sum(1 for m in manifest if not m["ok"]),
        "budget_exhausted": fetcher.budget_exhausted,
    }
    write_json(os.path.join(out, "collection_manifest.json"), manifest_payload)
    coverage = coverage_report(out, results)
    print(json.dumps({"ok_requests": manifest_payload["ok"],
                      "failed_requests": manifest_payload["failed"],
                      "budget_exhausted": manifest_payload["budget_exhausted"],
                      "sources": coverage["sources"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
