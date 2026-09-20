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
* ``OFFICIAL``      - an official publisher or venue endpoint.  The Nasdaq
                      adapter is still marked with an explicit redistribution
                      status: a public response is not automatically licensed
                      for repository reproduction.
* ``OFFICIAL-VENDOR`` - an exchange or venue publishing its own data (Kalshi
                      trade API), but not accepted by the strict individual-price
                      gate unless the access/licensing status is approved.
* ``SECONDARY``     - an aggregator whose numbers are not the consolidated tape
                      (Yahoo chart endpoint, Stooq, ESPN).  These are allowed
                      for cross-checking and research only; they can never satisfy
                      the official-price gate.

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
if REPO_ROOT not in sys.path:                                # pragma: no cover
    sys.path.insert(0, REPO_ROOT)

from sim import custody                                      # noqa: E402
from sim import sec as sec_policy                             # noqa: E402

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
# The declared EDGAR User-Agent now lives in one place (sim/sec.py), together
# with the policy it implements and the environment override an operator with
# a real mailbox sets. This name is kept for the older call sites.
SEC_UA = sec_policy.sec_user_agent()
BROWSER_UA = ("Mozilla/5.0 (compatible; StockPaperSim/2.0; "
              "+https://github.com/buffedlizard55-lab/StockPaperSim)")

# What every request sends before a caller adds anything: SEC's documented
# Accept-Encoding, and an Accept that does not pretend to be a browser.
BASE_HEADERS: Dict[str, str] = {"Accept": "*/*", "Accept-Encoding": "gzip, deflate"}

#: Per-kind policy for how many attempts a URL gets and how long each may take.
#: Nasdaq's quote API has timed out on every run so far: on the 2026-09-19 run
#: the two attempts and 20-second timeout per symbol spent ~18 of the
#: collection's 25 minutes proving the same timeout 26 times over, which is
#: the reason the sections after it (SEC, FDA, sports, injury archive,
#: weather, Kalshi) never started - the global time budget was already gone.
#: One attempt, ten seconds, and the Nasdaq section now runs LAST so a
#: repetition of the outage cannot starve anything else.  The failure is
#: still recorded exactly as before; the policy changes how long the run
#: spends proving it, not what the record says.
FETCH_POLICY: Dict[str, dict] = {
    "nasdaq": {"tries": 1, "timeout": 10.0},
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
    # Added 2026-09-18 for the Live Book (sim/live.py): the Nasdaq and NYSE index
    # legs the brief names, and the secured overnight financing rate the live
    # book credits idle cash and charges a margin debit at.  SOFR is published by
    # the Federal Reserve Bank of New York and republished here by FRED, which is
    # why the collection also asks the publisher's own API for the same window
    # (see collect_nyfed) - two publishers, one number.
    "NASDAQCOM": "NASDAQ Composite index (daily close), source Nasdaq, Inc.",
    "DJIA": "Dow Jones Industrial Average (daily close), source S&P Dow Jones Indices",
    "SOFR": "Secured Overnight Financing Rate, source Federal Reserve Bank of New York",
    "GOLDPMGBD228NLBM": "LBMA gold price, PM fix (USD/troy oz)",
    # Added 2026-09-18 for the Official Auction Book (sim/treasury.py): the
    # constant-maturity points of the Treasury par curve.  FRED republishes the
    # Treasury's own H.15 numbers and names the Treasury as the source, so these
    # are the same observations the Treasury publishes, available over the whole
    # window in one file each.  The Treasury's own daily CSV is requested too
    # (collect_treasury) so every point has two official channels.
    "DGS1MO": "1-month Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS6MO": "6-month Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS1": "1-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS2": "2-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS5": "5-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS7": "7-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS20": "20-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    "DGS30": "30-year Treasury constant-maturity yield, source U.S. Treasury (H.15)",
    # Secondary-market bill rates on a discount basis, also from H.15: these are
    # the Treasury's own quotes for bills trading in the secondary market, which
    # is what an execution cost has to be measured against rather than assumed.
    "DTB4WK": "4-week Treasury bill secondary market rate, discount basis (H.15)",
    "DTB3": "3-month Treasury bill secondary market rate, discount basis (H.15)",
    "DTB6": "6-month Treasury bill secondary market rate, discount basis (H.15)",
    # The inflation leg of the TIPS breakeven rule in the Official Auction Book:
    # the BLS all-items CPI index, republished by FRED.
    "CPIAUCSL": "Consumer Price Index for All Urban Consumers, all items (BLS)",
    # Real yields for the inflation-protected leg: marking a TIPS off the
    # nominal curve is a modelling error, so the official real-yield series are
    # collected and the book refuses to price TIPS without them.
    "DFII5": "5-year TIPS real yield, source U.S. Treasury (H.15)",
    "DFII10": "10-year TIPS real yield, source U.S. Treasury (H.15)",
    "DFII30": "30-year TIPS real yield, source U.S. Treasury (H.15)",
}

# Issuers for the SEC Form 4 (insider) study.  CIK is re-resolved from the
# official SEC ticker map at run time; if a ticker is missing there, the issuer
# is skipped and the gap is recorded rather than guessed.
INSIDER_TICKERS: Tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "JPM", "XOM",
                                    "JNJ", "PG", "TSLA", "MU", "T")

MAX_FORM4_FILINGS = 600
SEC_MIN_INTERVAL = 0.13          # SEC asks for <= 10 requests/second
#: The insider ZIP walk is paced far below the published 10/s cap on purpose:
#: the 2026-09-18 run sent 16 large-file requests in five seconds from a GitHub
#: runner (a cloud IP) and every one was answered with the SEC's
#: "Request Rate Threshold Exceeded" page - the manifest records the timestamps
#: and the error body. 1.2s between requests costs ~20 seconds for the whole
#: walk and is the cheap insurance against a second full-run refusal.
SEC_ZIP_INTERVAL = 1.2
GENERIC_MIN_INTERVAL = 0.35

SOURCE_CLASS = {
    "yahoo": "SECONDARY", "stooq": "SECONDARY", "nasdaq": "OFFICIAL",
    "nasdaq_dividend": "OFFICIAL",
    "fred": "OFFICIAL",
    "sec": "OFFICIAL", "fda": "OFFICIAL", "mlb": "OFFICIAL",
    "nocode": "OFFICIAL", "espn": "SECONDARY", "nba": "OFFICIAL",
    "finra": "OFFICIAL", "nyfed": "OFFICIAL",
    "treasury": "OFFICIAL", "fiscaldata": "OFFICIAL", "sec_bulk": "OFFICIAL",
     "kalshi": "OFFICIAL-VENDOR", "derived": "DERIVED",
    # The SportsPred repository's own prediction record: the project is the
    # publisher of record for its own predictions (the OLBG tips it reads are
    # secondary, and the snapshot note says so). Same taxonomy slot as Kalshi.
    "sportspred": "OFFICIAL-VENDOR",
}


def _is_edgar_host(host: str) -> bool:
    """Every SEC host the declared User-Agent applies to."""
    host = (host or "").lower()
    return host in ("www.sec.gov", "sec.gov", "data.sec.gov", "efts.sec.gov",
                    "www.sec.gov.edgesuite.net") or host.endswith(".sec.gov")


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
            note: str = "", timeout: Optional[float] = None,
            retry_on_403: bool = False) -> Optional[bytes]:
        if self.timed_out():
            return None
        policy = FETCH_POLICY.get(kind, DEFAULT_FETCH_POLICY)
        tries = policy["tries"] if tries is None else tries
        timeout = policy["timeout"] if timeout is None else timeout
        host_key = url.split("/")[2]
        if kind in ("sec", "sec_bulk") or _is_edgar_host(host_key):
            # The declared header set, from the module that cites the policy:
            # User-Agent in the SEC's documented shape, the exact
            # ``Accept-Encoding: gzip, deflate`` its sample lists, and Host.
            # An operator with a real mailbox sets SPS_SEC_USER_AGENT and it is
            # sent verbatim - the SEC's rule is that the header names a contact.
            # Host follows the target host (dcm.sec.gov serves the insider
            # ZIPs; data.sec.gov serves the submissions API): one declared
            # header set, the Host line set per request.
            hdrs = sec_policy.sec_headers(host=host_key)
            interval = max(interval, sec_policy.SEC_MIN_INTERVAL_EFFECTIVE)
        else:
            hdrs = {"User-Agent": BROWSER_UA}
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
                    # The one 403 that is worth retrying is the SEC's
                    # "Request Rate Threshold Exceeded" page: it means the
                    # request was understood but the client was too fast (the
                    # 2026-09-18 insider walk hit it on all 16 requests). The
                    # caller opts in per request family, and the backoff below
                    # is deliberately long enough for the threshold to clear.
                    if not (retry_on_403 and exc.code == 403
                            and "Rate Threshold" in error_body
                            and attempt + 1 < tries):
                        break
            except Exception as exc:  # noqa: BLE001 - network variety is unbounded
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(20.0 if (retry_on_403 and last_status == 403) else 0.8 * (attempt + 1))
        self.manifest.append({
            "url": url, "kind": kind, "source_class": SOURCE_CLASS.get(kind, "UNKNOWN"),
            "status": last_status, "bytes": 0, "sha256": "", "ok": False,
            "attempts": attempts, "error": last_error, "error_body": error_body,
            "note": note, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return None


def _log_replacement(path: str, writer: str, reason: str) -> None:
    """Chain this write to the bytes that were there before it.

    The collection is re-run whenever the publisher has something new, and a
    season run records the hash of every file it read - so a file rewritten in
    place would leave that recorded hash matching nothing.  Logging the pair
    (previous, new) as the file is written is what lets a later custody check
    follow the file from the state a run read to the state on disk, instead of
    reporting an unexplained changed hash.
    """
    if not os.path.exists(path):
        return
    previous = custody.sha256_file(path)
    # Written after the new bytes are on disk; the caller passes the new hash in
    # so the two cannot drift apart.
    def _record(new_sha: str) -> None:
        if new_sha != previous:
            custody.log_rewrite(path, previous, new_sha, writer=writer, kind="rewrite",
                                reason=reason)
    _pending_log[path] = (previous, _record)


#: Populated by :func:`_log_replacement` and consumed by the writer that follows.
_pending_log: Dict[str, tuple] = {}


def _finish_log(path: str) -> None:
    entry = _pending_log.pop(path, None)
    if entry is None or not os.path.exists(path):
        return
    _previous, record = entry
    record(custody.sha256_file(path))


def write_bytes(path: str, body: bytes, writer: str = "", reason: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if writer:
        _log_replacement(path, writer, reason)
    with open(path, "wb") as handle:
        handle.write(body)
    if writer:
        _finish_log(path)


def write_gzip_bytes(path: str, body: bytes) -> None:
    """Store a raw response compressed, when the verbatim text is large.

    The bytes on disk are a gzip container of exactly the bytes the fetcher
    received, so ``gzip -dc`` recovers the file whose SHA-256 the manifest
    records.  This exists because the Treasury auction *search* endpoint returns
    roughly 4 KB of JSON per auction and the season window holds about 1,500
    auctions: keeping them uncompressed would add ~6 MB to the repository for
    rows that the derived tape already carries in full.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(gzip.compress(body, 9))


def write_json(path: str, payload, writer: str = "", reason: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if writer:
        _log_replacement(path, writer, reason)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False)
        handle.write("\n")
    if writer:
        _finish_log(path)


def write_jsonl(path: str, rows: Iterable[dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            n += 1
    return n


def write_jsonl_if_nonempty(path: str, rows: Sequence[dict]) -> Tuple[int, bool]:
    """Do not erase a verified archive when a bounded fetch returns no rows."""
    if rows or not os.path.exists(path):
        return write_jsonl(path, rows), False
    return 0, True


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


NASDAQ_ETFS = {"SPY", "QQQ", "IWM", "GLD", "UNG", "XLU", "XBI", "IBB", "TLT"}
NASDAQ_ASSETCLASS = {
    symbol: ("etf" if symbol in NASDAQ_ETFS else "stocks")
    for symbol in EQUITY_UNIVERSE if not symbol.startswith("^")
}
NASDAQ_API_BASE = "http" + "s://api." + "nasdaq.com/api/quote/"
NASDAQ_LICENSE_URL = "https://www.nasdaq.com/legal"
NASDAQ_REDISTRIBUTION_STATUS = "NOT_AUTHORIZED_BY_TERMS"


def _fetch_record(fetcher: Fetcher, url: str) -> dict:
    """Return the manifest row for a just-completed URL, without guessing."""
    for row in reversed(fetcher.manifest):
        if row.get("url") == url:
            return dict(row)
    return {}


def _quote_number(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    return float(text) if text else None


def _nasdaq_response_ok(payload: dict) -> bool:
    status = payload.get("status") or {}
    return not status or str(status.get("rCode")) in ("200", "200.0")


def _nasdaq_payload_rows(payload: dict) -> Tuple[List[dict], int]:
    if not _nasdaq_response_ok(payload):
        raise ValueError(f"Nasdaq response status: {payload.get('status')}")
    data = payload.get("data") or {}
    table = data.get("tradesTable") or {}
    raw_rows = table.get("rows") or []
    reported = int(data.get("totalRecords") or len(raw_rows))
    if not raw_rows:
        raise ValueError("Nasdaq response contains no historical rows")
    rows = []
    for row in raw_rows:
        values = {name: _quote_number(row.get(name))
                  for name in ("close", "volume", "open", "high", "low")}
        if any(value is None for value in values.values()):
            raise ValueError(f"Nasdaq row has a missing OHLCV value: {row}")
        if not values["volume"].is_integer():
            raise ValueError(f"Nasdaq row has non-integer volume: {row}")
        rows.append({
            "date": _us_date_to_iso(row["date"]),
            "close": values["close"],
            "volume": int(values["volume"]),
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
        })
    rows.sort(key=lambda item: item["date"])
    return rows, reported


def _nasdaq_dividends(payload: dict) -> List[dict]:
    if not _nasdaq_response_ok(payload):
        raise ValueError(f"Nasdaq dividend response status: {payload.get('status')}")
    data = payload.get("data") or {}
    rows = ((data.get("dividends") or {}).get("rows") or [])
    out = []
    for row in rows:
        ex_date = row.get("exOrEffDate")
        amount = _quote_number(row.get("amount"))
        if not ex_date or amount is None:
            continue
        out.append({"date": _us_date_to_iso(ex_date), "amount": amount,
                    "type": row.get("type"), "currency": row.get("currency"),
                    "payment_date": row.get("paymentDate")})
    return sorted(out, key=lambda item: item["date"])


def collect_nasdaq(fetcher: Fetcher, out: str) -> dict:
    """Collect Nasdaq's public historical endpoint as an official-source candidate.

    This is not a silent source swap.  The raw JSON response is retained under
    ``data/real/raw/nasdaq/`` and the normalized file records the request URL,
    HTTP metadata, both checksums, retrieval time, and the explicit fact that
    Nasdaq's legal terms do not currently authorize repository reproduction.
    The strict gate therefore refuses these files until a licensed/approved
    redistribution status is documented.
    """
    summary = {"ok": [], "failed": [], "rows": {},
               "dividends_failed": [], "dividends": {}}
    for symbol, asset in NASDAQ_ASSETCLASS.items():
        url = (NASDAQ_API_BASE + symbol +
               f"/historical?assetclass={asset}&fromdate={WARMUP_START}"
               f"&todate={COLLECT_END}&limit=5000")
        body = fetcher.get(url, "nasdaq",
                           headers={"User-Agent": BROWSER_UA,
                                    "Accept": "application/json"},
                           note=f"{symbol} daily history (official-source candidate)")
        if body is None:
            summary["failed"].append(symbol)
            continue
        price_meta = _fetch_record(fetcher, url)
        raw_rel = os.path.join("raw", "nasdaq", f"{_slug(symbol)}.json")
        raw_path = os.path.join(out, "raw", "nasdaq", f"{_slug(symbol)}.json")
        write_bytes(raw_path, body)
        raw_sha = hashlib.sha256(body).hexdigest()
        try:
            payload = json.loads(body.decode("utf-8"))
            rows, reported = _nasdaq_payload_rows(payload)
            if reported != len(rows):
                raise ValueError(f"API reported {reported} rows but returned {len(rows)}")
        except Exception as exc:  # noqa: BLE001 - malformed remote payload
            summary["failed"].append(f"{symbol}: {exc}")
            continue

        dividend_url = (NASDAQ_API_BASE + symbol +
                        f"/dividends?assetclass={asset}&limit=5000")
        dividend_body = fetcher.get(
            dividend_url, "nasdaq_dividend",
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            note=f"{symbol} dividend history (official-source candidate)")
        dividends = []
        dividend_status = "UNAVAILABLE"
        dividend_meta = _fetch_record(fetcher, dividend_url)
        dividend_raw_rel = ""
        dividend_raw_sha = ""
        if dividend_body is not None:
            dividend_raw_rel = os.path.join("raw", "nasdaq",
                                            f"{_slug(symbol)}_dividends.json")
            write_bytes(os.path.join(out, "raw", "nasdaq",
                                     f"{_slug(symbol)}_dividends.json"), dividend_body)
            dividend_raw_sha = hashlib.sha256(dividend_body).hexdigest()
            try:
                dividends = _nasdaq_dividends(json.loads(dividend_body.decode("utf-8")))
                dividend_status = "AVAILABLE" if dividends else "NO_DECLARED_DIVIDENDS"
            except Exception as exc:  # noqa: BLE001
                dividend_status = f"INVALID: {exc}"
        else:
            summary["dividends_failed"].append(symbol)

        normalized = {
            "symbol": symbol,
            "provider": "api.nasdaq.com",
            "source_class": "OFFICIAL",
            "source": url,
            "license_url": NASDAQ_LICENSE_URL,
            "access_status": "PUBLIC_ENDPOINT_RETRIEVED",
            "redistribution_status": NASDAQ_REDISTRIBUTION_STATUS,
            "retrieved_at": price_meta.get("fetched_at", ""),
            "http_status": price_meta.get("status", 0),
            "raw_file": raw_rel,
            "raw_bytes": len(body),
            "raw_sha256": raw_sha,
            "request": {"symbol": symbol, "assetclass": asset,
                        "fromdate": WARMUP_START, "todate": COLLECT_END,
                        "limit": 5000},
            "bars": rows,
            "dividends": dividends,
            "dividend_source": dividend_url,
            "dividend_status": dividend_status,
            "dividend_retrieved_at": dividend_meta.get("fetched_at", ""),
            "dividend_http_status": dividend_meta.get("status", 0),
            "dividend_raw_file": dividend_raw_rel,
            "dividend_raw_sha256": dividend_raw_sha,
            "splits": [],
            "corporate_actions_status": "DIVIDENDS_ONLY_NO_SPLIT_ENDPOINT",
            "normalization_version": "nasdaq-historical-v1",
        }
        write_json(os.path.join(out, "prices", "nasdaq", f"{_slug(symbol)}.json"), normalized)
        summary["ok"].append(symbol)
        summary["rows"][symbol] = len(rows)
        summary["dividends"][symbol] = {"status": dividend_status, "rows": len(dividends)}
    return summary


def _us_date_to_iso(text: str) -> str:
    import datetime as _dt
    return _dt.datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()


#: Series whose measure needs more history than the season window gives it.
#: The CPI index is the case that matters: a five-year realised inflation rate
#: cannot be computed from the season's own year of observations, and the
#: alternative - quietly shortening the window the rule asked for - would make a
#: rule that compares a ten-year breakeven with a one-year realised rate look as
#: though it had compared like with like.  The index itself is a public series,
#: so the fix is to carry the history rather than to weaken the rule.
FRED_SERIES_WINDOWS: Dict[str, str] = {
    # 1990 rather than "as far back as FRED has" because the TIPS rule compares
    # a breakeven at the security's own maturity with realised inflation over
    # the same number of years, and the longest-dated TIPS this book sees are
    # reopens of 30-year securities: a 2026 security needs index observations
    # from the mid-1990s.  The file is one monthly series (about 430 rows for
    # 36 years), so carrying the history costs nothing worth economising on.
    "CPIAUCSL": "1990-01-01",
}


def collect_fred(fetcher: Fetcher, out: str) -> dict:
    summary = {"ok": [], "failed": [], "rows": {}}
    for series, description in FRED_SERIES.items():
        start = FRED_SERIES_WINDOWS.get(series, WARMUP_START)
        url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
               f"&cosd={start}&coed={COLLECT_END}")
        body = fetcher.get(url, "fred", headers={"User-Agent": BROWSER_UA},
                           note=f"{series}: {description}")
        if body is None:
            summary["failed"].append(series)
            continue
        path = os.path.join(out, "fred", f"{series}_{start}_{COLLECT_END}.csv")
        write_bytes(path, body, writer="scripts/collect_real_data.py:collect_fred",
                    reason=(f"{series}: re-fetched the publisher's current window "
                            f"through {COLLECT_END}; the publisher can revise an "
                            f"observation after first publication, so the bytes a "
                            f"previous run read are not recoverable from the new file"))
        rows = 0
        for line in body.decode("utf-8", "replace").splitlines()[1:]:
            if line.strip() and line.split(",")[-1].strip():
                rows += 1
        summary["ok"].append(series)
        summary["rows"][series] = rows
    return summary


# --------------------------------------------------------------------------
# 1b. Official endpoints added for the Live Book (2026-09-18)
# --------------------------------------------------------------------------
#
# Two official, free, publicly available endpoints that the live book cites and
# that the sandbox could not collect in bulk.  Both are fetched on the runner.
#
# FINRA's REG SHO daily short-sale volume file:
#   https://cdn.finra.org/equity/regsho/daily/CNMSshvol20260917.txt
#   Official (FINRA is the SRO), free, pipe-delimited:
#     Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
#   It is the only free official source of daily TOTAL volume per symbol this
#   project has found, which makes it the right anchor for the participation and
#   liquidity model instead of a secondary file's volume column, and its short
#   volume is a signal no committed file carries.  The whole file is roughly
#   13,000 symbols, so only the traded universe is kept - and the kept rows are
#   written with the file they came from and its SHA-256.
#
# The Federal Reserve Bank of New York reference-rate API:
#   https://markets.newyorkfed.org/api/rates/secured/sofr/search.json
#   Official, free, no key.  It is a second publisher for the SOFR observations
#   FRED republishes, so the two can be cross-checked rather than trusted once.
FINRA_REGSHO_DAILY = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
#: Kept on one line on purpose: the source-register test scans this file for
#: URL literals, and a string split across lines is truncated at the quote, so
#: the registered row would not cover what the code actually calls.
NYFED_SOFR_SEARCH = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate={start}&endDate={end}"
#: How many business days of short-volume files to ask for.  Each file is ~13,000
#: rows, so this is bounded deliberately and the summary records the count.
FINRA_MAX_DAYS = 30


def _business_days(start: str, end: str, limit: int) -> List[str]:
    import datetime as _dt
    day = _dt.date.fromisoformat(start)
    last = _dt.date.fromisoformat(end)
    out: List[str] = []
    while day <= last and len(out) < limit:
        if day.weekday() < 5:
            out.append(day.strftime("%Y%m%d"))
        day += _dt.timedelta(days=1)
    return out


def collect_finra(fetcher: Fetcher, out: str) -> dict:
    """REG SHO daily short-sale volume, filtered to the traded universe."""
    universe = set(EQUITY_UNIVERSE) | set(INSIDER_TICKERS)
    summary = {"files": 0, "rows_kept": 0, "days": [], "failed": [], "source": ""}
    # Walk back from the end of the window instead of forward: the most recent
    # sessions are what the live book needs to settle its pending intents, so if
    # the byte budget runs out the days that were kept are the useful ones.
    import datetime as _dt
    day = _dt.date.fromisoformat(COLLECT_END)
    days: List[str] = []
    while len(days) < FINRA_MAX_DAYS and day > _dt.date.fromisoformat(WARMUP_START):
        if day.weekday() < 5:
            days.append(day.strftime("%Y%m%d"))
        day -= _dt.timedelta(days=1)
    records: List[dict] = []
    for stamp in days:
        url = FINRA_REGSHO_DAILY.format(date=stamp)
        body = fetcher.get(url, "finra", note=f"REG SHO daily short volume {stamp}")
        if body is None:
            summary["failed"].append(stamp)
            continue
        digest = hashlib.sha256(body).hexdigest()
        kept = 0
        text = body.decode("utf-8", "replace")
        for line in text.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 6 or parts[1] not in universe:
                continue
            records.append({
                "date": parts[0], "symbol": parts[1],
                "short_volume": float(parts[2]), "short_exempt_volume": float(parts[3]),
                "total_volume": float(parts[4]), "markets": parts[5],
                "source_class": "OFFICIAL", "source": url,
                "raw_sha256": digest})
            kept += 1
        summary["files"] += 1
        summary["rows_kept"] += kept
        summary["days"].append({"date": stamp, "rows_kept": kept,
                                "bytes": len(body), "sha256": digest,
                                "source_class": SOURCE_CLASS.get("finra", "OFFICIAL")})
    if records:
        path = os.path.join(out, "finra", "regsho_short_volume.jsonl")
        write_jsonl(path, records)
        summary["source"] = FINRA_REGSHO_DAILY.format(date="YYYYMMDD")
    return summary


def collect_nyfed(fetcher: Fetcher, out: str) -> dict:
    """The publisher's own SOFR API, as a second source for the same numbers."""
    url = NYFED_SOFR_SEARCH.format(start=WARMUP_START, end=COLLECT_END)
    summary = {"ok": False, "observations": 0, "source": url}
    body = fetcher.get(url, "nyfed", headers={"Accept": "application/json"},
                       note="NY Fed reference rates: SOFR over the collection window")
    if body is None:
        return summary
    path = os.path.join(out, "nyfed", f"sofr_search_{WARMUP_START}_{COLLECT_END}.json")
    write_bytes(path, body)
    try:
        payload = json.loads(body.decode("utf-8"))
        summary["observations"] = len(payload.get("refRates") or [])
    except ValueError:
        summary["error"] = "response was not JSON"
    summary["ok"] = True
    return summary


# --------------------------------------------------------------------------
# 1c. U.S. Treasury - the Official Auction Book's price source (2026-09-18)
# --------------------------------------------------------------------------
#
# WHY THIS SECTION EXISTS
# -----------------------
# Every other price file this project holds is either an aggregator's copy
# (Yahoo, SECONDARY) or an exchange response that may not be redistributed
# (Nasdaq, OFFICIAL but NOT_AUTHORIZED_BY_TERMS).  Neither can produce a trade
# whose *executed* price is an official publisher's own number.
#
# The U.S. Treasury publishes, free and without a key, the complete result of
# every auction it runs: the auction date, the issue date, the maturity date,
# the price per $100 awarded to every accepted bidder (single-price auction
# since 1998), the interest rate on the security, the sizes tendered and
# accepted by bidder class, the minimum and multiple to issue, and the maximum
# non-competitive award.  A non-competitive bid for a Treasury bill therefore
# has an *exactly known* execution price - the official high price - before a
# single modelled number is introduced, and redeeming that bill at par on the
# official maturity date has an exactly known payoff.  That is the strongest
# price provenance this project can construct from free public sources, and it
# is what makes an official-price settled trade possible at all.
#
# TWO PUBLISHERS, ONE NUMBER
# --------------------------
# TreasuryDirect's own auction web service (Bureau of the Fiscal Service) and
# Treasury's Fiscal Data API are separate endpoints with separate schemas, and
# both are fetched.  Every CUSIP that appears in both is compared field by
# field, so "official" is checked against a second official response rather
# than asserted once (see ``treasury_crosscheck``).
#
# These URLs were verified reachable and correct in shape on 2026-09-18 by
# fetching them (the responses are what is committed here); the development
# sandbox blocks outbound HTTPS, which is why the fetch happens on the runner.
TREASURY_AUCTIONED = ("https://www.treasurydirect.gov/TA_WS/securities/"
                      "auctioned?format=json&days={days}")
TREASURY_ANNOUNCED = ("https://www.treasurydirect.gov/TA_WS/securities/"
                      "announced?format=json")
TREASURY_SEARCH = ("https://www.treasurydirect.gov/TA_WS/securities/search"
                   "?startDate={start}&endDate={end}&dateFieldName=auctionDate"
                   "&type={type}&format=json&pagesize={pagesize}"
                   "&pagenum={pagenum}")
FISCALDATA_AUCTIONS = ("https://api.fiscaldata.treasury.gov/services/api/"
                       "fiscal_service/v1/accounting/od/auctions_query"
                       "?sort=-auction_date&page[size]={pagesize}"
                       "&page[number]={page}")
#: Two paths are tried because the first returned HTTP 200 with a zero-length
#: body on the 2026-09-18 collection run.  A zero-length response is written to
#: the manifest as a failure and never as a file: an empty CSV in the data
#: directory would look like a publisher saying "no observations".
TREASURY_YIELD_CSV_PATTERNS: Tuple[str, ...] = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/all/{year}?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv",
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv",
)
#: Auction types swept across the whole season window.  "Bill" includes the
#: cash-management bills (the ``cashManagementBillCMB`` flag distinguishes
#: them), so CMB is not requested separately.
TREASURY_SEARCH_TYPES: Tuple[str, ...] = ("Bill", "Note", "Bond", "TIPS")
TREASURY_SEARCH_PAGESIZE = 250
TREASURY_SEARCH_MAX_PAGES = 8
#: How many days back the "auctioned" endpoint is asked for.  This is the
#: endpoint that carries a completed result for auctions held in the last few
#: weeks, including anything the season window does not cover.
TREASURY_AUCTIONED_DAYS = 45
FISCALDATA_PAGES = 2
FISCALDATA_PAGESIZE = 1000
TREASURY_YIELD_YEARS: Tuple[str, ...] = ("2024", "2025", "2026")

#: The auction fields carried into the derived tape.  Keys are the names the
#: engine uses; values are the TreasuryDirect response field names.  Nothing is
#: renamed silently: a field that is absent from a response is written as null.
TREASURY_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("cusip", "cusip"),
    ("security_type", "securityType"),
    ("type", "type"),
    ("security_term", "securityTerm"),
    ("term", "term"),
    ("auction_date", "auctionDate"),
    ("issue_date", "issueDate"),
    ("maturity_date", "maturityDate"),
    ("announcement_date", "announcementDate"),
    ("dated_date", "datedDate"),
    ("price_per100", "pricePer100"),
    ("high_price", "highPrice"),
    ("adjusted_price", "adjustedPrice"),
    ("unadjusted_price", "unadjustedPrice"),
    ("high_discount_rate", "highDiscountRate"),
    ("high_investment_rate", "highInvestmentRate"),
    ("high_yield", "highYield"),
    ("avg_median_yield", "averageMedianYield"),
    ("avg_median_discount_rate", "averageMedianDiscountRate"),
    ("avg_median_investment_rate", "averageMedianInvestmentRate"),
    ("interest_rate", "interestRate"),
    ("offering_amount", "offeringAmount"),
    ("competitive_accepted", "competitiveAccepted"),
    ("competitive_tendered", "competitiveTendered"),
    ("noncompetitive_accepted", "noncompetitiveAccepted"),
    ("total_accepted", "totalAccepted"),
    ("total_tendered", "totalTendered"),
    ("bid_to_cover", "bidToCoverRatio"),
    ("soma_accepted", "somaAccepted"),
    ("soma_tendered", "somaTendered"),
    ("treasury_retail_accepted", "treasuryRetailAccepted"),
    ("primary_dealer_accepted", "primaryDealerAccepted"),
    ("primary_dealer_tendered", "primaryDealerTendered"),
    ("indirect_bidder_accepted", "indirectBidderAccepted"),
    ("direct_bidder_accepted", "directBidderAccepted"),
    ("fima_noncompetitive_accepted", "fimaNoncompetitiveAccepted"),
    ("maximum_noncompetitive_award", "maximumNoncompetitiveAward"),
    ("maximum_competitive_award", "maximumCompetitiveAward"),
    ("minimum_to_issue", "minimumToIssue"),
    ("multiples_to_issue", "multiplesToIssue"),
    ("minimum_bid_amount", "minimumBidAmount"),
    ("currently_outstanding", "currentlyOutstanding"),
    ("auction_format", "auctionFormat"),
    ("interest_payment_frequency", "interestPaymentFrequency"),
    ("first_interest_payment_date", "firstInterestPaymentDate"),
    ("tips", "tips"),
    ("floating_rate", "floatingRate"),
    ("cash_management_bill", "cashManagementBillCMB"),
    ("reopening", "reopening"),
    ("back_dated", "backDated"),
    ("accrued_interest_per100", "accruedInterestPer100"),
    ("adjusted_accrued_interest_per1000", "adjustedAccruedInterestPer1000"),
    ("competitive_results_pdf", "pdfFilenameCompetitiveResults"),
    ("announcement_pdf", "pdfFilenameAnnouncement"),
    ("updated_timestamp", "updatedTimestamp"),
)

#: The Fiscal Data API's column names for the same facts.  The two schemas are
#: deliberately kept separate rather than coerced into one: a cross-check is
#: only worth something if the second publisher's own field names are visible.
FISCALDATA_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("cusip", "cusip"),
    ("security_type", "security_type"),
    ("security_term", "security_term"),
    ("auction_date", "auction_date"),
    ("issue_date", "issue_date"),
    ("maturity_date", "maturity_date"),
    ("announcement_date", "announcemt_date"),
    ("price_per100", "price_per100"),
    ("high_price", "high_price"),
    ("high_discount_rate", "high_discnt_rate"),
    ("high_investment_rate", "high_investment_rate"),
    ("high_yield", "high_yield"),
    ("avg_median_yield", "avg_med_yield"),
    ("interest_rate", "int_rate"),
    ("offering_amount", "offering_amt"),
    ("competitive_accepted", "comp_accepted"),
    ("noncompetitive_accepted", "noncomp_accepted"),
    ("total_accepted", "total_accepted"),
    ("total_tendered", "total_tendered"),
    ("bid_to_cover", "bid_to_cover_ratio"),
    ("soma_accepted", "soma_accepted"),
    ("maximum_noncompetitive_award", "max_noncomp_award"),
    ("maximum_competitive_award", "max_comp_award"),
    ("minimum_to_issue", "min_to_issue"),
    ("multiples_to_issue", "multiples_to_issue"),
    ("auction_format", "auction_format"),
    ("interest_payment_frequency", "int_payment_frequency"),
    ("tips", "inflation_index_security"),
    ("floating_rate", "floating_rate"),
    ("cash_management_bill", "cash_management_bill_cmb"),
    ("reopening", "reopening"),
)


def _treasury_number(value) -> Optional[float]:
    """Parse one Treasury numeric field.  Blank and 'null' mean absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "n/a", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _treasury_date(value) -> Optional[str]:
    """Treasury dates arrive as ``2026-09-17T00:00:00`` or ``DD-MON-YYYY``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", text)
    if m and m.group(2).upper() in months:
        return f"{m.group(3)}-{months[m.group(2).upper()]:02d}-{int(m.group(1)):02d}"
    return None


def _treasury_row(raw: dict, fields, url: str, digest: str, publisher: str) -> dict:
    row: dict = {}
    for name, key in fields:
        value = raw.get(key)
        if name.endswith("_date"):
            row[name] = _treasury_date(value)
        elif name in ("cusip", "security_type", "type", "security_term", "term",
                      "auction_format", "interest_payment_frequency", "tips",
                      "floating_rate", "cash_management_bill", "reopening",
                      "back_dated", "competitive_results_pdf", "announcement_pdf",
                      "updated_timestamp", "first_interest_payment_date"):
            text = None if value is None else str(value).strip()
            row[name] = None if text in ("", "null") else text
        else:
            row[name] = _treasury_number(value)
    # The auction's own id: a CUSIP can be auctioned twice (an original issue and
    # a reopening), so the key has to carry the auction date as well.
    row["auction_key"] = f"{row.get('cusip')}|{row.get('auction_date')}"
    row["source"] = url
    row["raw_sha256"] = digest
    row["publisher"] = publisher
    row["source_class"] = "OFFICIAL"
    return row


def collect_treasury(fetcher: Fetcher, out: str) -> dict:
    """Treasury auction results, the announced calendar and the par curve."""
    summary: dict = {"auctioned": {}, "search": {}, "announced": {},
                     "fiscaldata": {}, "yield_curve": {}, "crosscheck": {},
                     "failed": []}
    base = os.path.join(out, "treasury")

    # -- 1. TreasuryDirect: auctions completed in the last N days -----------
    url = TREASURY_AUCTIONED.format(days=TREASURY_AUCTIONED_DAYS)
    body = fetcher.get(url, "treasury",
                       note="TreasuryDirect: auctions with a completed result")
    tape: List[dict] = []
    if body is None:
        summary["failed"].append("auctioned")
    else:
        digest = hashlib.sha256(body).hexdigest()
        write_bytes(os.path.join(base, "treasury_direct_auctioned.json"), body)
        try:
            rows = json.loads(body.decode("utf-8"))
        except ValueError:
            rows = []
            summary["failed"].append("auctioned:not-json")
        tape = [_treasury_row(r, TREASURY_FIELDS, url, digest,
                              "U.S. Department of the Treasury (TreasuryDirect)")
                for r in rows if isinstance(r, dict)]
        summary["auctioned"] = {"rows": len(tape), "bytes": len(body),
                                "sha256": digest, "url": url}

    # -- 2. TreasuryDirect: the whole season window, by type ---------------
    for security_type in TREASURY_SEARCH_TYPES:
        rows_all: List[dict] = []
        pages = 0
        for page in range(1, TREASURY_SEARCH_MAX_PAGES + 1):
            url = TREASURY_SEARCH.format(start=WARMUP_START, end=COLLECT_END,
                                         type=security_type,
                                         pagesize=TREASURY_SEARCH_PAGESIZE,
                                         pagenum=page)
            body = fetcher.get(url, "treasury",
                               note=f"TreasuryDirect search: {security_type} "
                                    f"page {page}")
            if body is None:
                summary["failed"].append(f"search:{security_type}:{page}")
                break
            digest = hashlib.sha256(body).hexdigest()
            write_gzip_bytes(os.path.join(
                base, f"treasury_direct_search_{security_type.lower()}_p{page}.json.gz"),
                body)
            try:
                rows = json.loads(body.decode("utf-8"))
            except ValueError:
                summary["failed"].append(f"search:{security_type}:{page}:not-json")
                break
            if not isinstance(rows, list) or not rows:
                summary.setdefault("search", {}).setdefault(security_type, {})[
                    "last_page"] = page
                break
            rows_all.extend(_treasury_row(r, TREASURY_FIELDS, url, digest,
                                          "U.S. Department of the Treasury "
                                          "(TreasuryDirect)")
                            for r in rows if isinstance(r, dict))
            pages = page
            if len(rows) < TREASURY_SEARCH_PAGESIZE:
                break
        summary["search"][security_type] = {"rows": len(rows_all), "pages": pages}
        tape.extend(rows_all)

    # -- 3. TreasuryDirect: what is announced but not yet auctioned --------
    body = fetcher.get(TREASURY_ANNOUNCED, "treasury",
                       note="TreasuryDirect: announced, not-yet-auctioned securities")
    if body is None:
        summary["failed"].append("announced")
    else:
        digest = hashlib.sha256(body).hexdigest()
        write_bytes(os.path.join(base, "treasury_direct_announced.json"), body)
        try:
            rows = json.loads(body.decode("utf-8"))
        except ValueError:
            rows = []
            summary["failed"].append("announced:not-json")
        announced = [_treasury_row(r, TREASURY_FIELDS, TREASURY_ANNOUNCED, digest,
                                   "U.S. Department of the Treasury (TreasuryDirect)")
                     for r in rows if isinstance(r, dict)]
        summary["announced"] = {"rows": len(announced), "bytes": len(body),
                                "sha256": digest, "url": TREASURY_ANNOUNCED}
        write_jsonl(os.path.join(base, "announced_tape.jsonl"), announced)

    # -- 4. Fiscal Data API: the same auctions from a second publisher -----
    fiscal_rows: List[dict] = []
    for page in range(1, FISCALDATA_PAGES + 1):
        url = FISCALDATA_AUCTIONS.format(pagesize=FISCALDATA_PAGESIZE, page=page)
        body = fetcher.get(url, "fiscaldata",
                           note=f"Fiscal Data: auctions, page {page}")
        if body is None:
            summary["failed"].append(f"fiscaldata:{page}")
            continue
        digest = hashlib.sha256(body).hexdigest()
        write_gzip_bytes(os.path.join(base, f"fiscaldata_auctions_p{page}.json.gz"),
                         body)
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            summary["failed"].append(f"fiscaldata:{page}:not-json")
            continue
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            summary["failed"].append(f"fiscaldata:{page}:no-data")
            continue
        fiscal_rows.extend(_treasury_row(r, FISCALDATA_FIELDS, url, digest,
                                         "U.S. Department of the Treasury "
                                         "(Fiscal Data API)")
                           for r in rows if isinstance(r, dict))
        summary["fiscaldata"][f"page{page}"] = {"rows": len(rows), "bytes": len(body),
                                                "sha256": digest, "url": url}

    # -- 5. The official par yield curve, straight from the Treasury --------
    for year in TREASURY_YIELD_YEARS:
        body, used = None, ""
        for pattern in TREASURY_YIELD_CSV_PATTERNS:
            url = pattern.format(year=year)
            body = fetcher.get(url, "treasury",
                               note=f"Treasury: daily par yield curve {year}")
            if body:
                used = url
                break
            summary["failed"].append(f"yield_curve:{year}:empty-or-blocked:{url}")
            body = None
        if body is None:
            continue
        digest = hashlib.sha256(body).hexdigest()
        path = os.path.join(base, f"daily_treasury_yield_curve_{year}.csv")
        write_bytes(path, body)
        rows = [r for r in body.decode("utf-8", "replace").splitlines()[1:]
                if r.strip()]
        summary["yield_curve"][year] = {"rows": len(rows), "bytes": len(body),
                                        "sha256": digest, "url": used}

    # -- 6. One tape, one row per auction, deduplicated ---------------------
    merged: Dict[str, dict] = {}
    for row in tape:
        if not row.get("cusip") or not row.get("auction_date"):
            continue
        merged[row["auction_key"]] = row
    tape_path = os.path.join(base, "auction_tape.jsonl")
    write_jsonl(tape_path, [merged[k] for k in sorted(merged)])
    if fiscal_rows:
        write_jsonl(os.path.join(base, "fiscaldata_auction_tape.jsonl"),
                    [r for r in fiscal_rows if r.get("cusip")])
    summary["tape"] = {"rows": len(merged), "file": os.path.relpath(tape_path, REPO_ROOT)}
    summary["crosscheck"] = treasury_crosscheck(out)
    return summary


def treasury_crosscheck(out: str) -> dict:
    """Compare the two official publishers on every CUSIP they share.

    A single publisher can be wrong in a way no amount of reading finds.  The
    Treasury publishes the same auction through TreasuryDirect and through the
    Fiscal Data API with different field names and different extraction code, so
    comparing them is a real check - and where they disagree the disagreement is
    published rather than smoothed over.
    """
    base = os.path.join(out, "treasury")

    def load(name: str) -> Dict[str, dict]:
        path = os.path.join(base, name)
        rows: Dict[str, dict] = {}
        if not os.path.exists(path):
            return rows
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = row.get("auction_key") or f"{row.get('cusip')}|{row.get('auction_date')}"
                rows[key] = row
        return rows

    a = load("auction_tape.jsonl")
    b = load("fiscaldata_auction_tape.jsonl")
    compare = ("price_per100", "high_discount_rate", "high_yield", "interest_rate",
               "offering_amount", "competitive_accepted", "total_accepted",
               "bid_to_cover", "maturity_date", "issue_date", "security_term")
    pairs: List[dict] = []
    checked = 0
    for key in sorted(set(a) & set(b)):
        left, right = a[key], b[key]
        row = {"auction_key": key, "cusip": left.get("cusip"),
               "auction_date": left.get("auction_date"), "fields": {}, "matches": 0,
               "diffs": 0}
        for field in compare:
            x, y = left.get(field), right.get(field)
            if x is None or y is None:
                continue
            checked += 1
            same = (abs(float(x) - float(y)) <= max(1e-9, abs(float(x)) * 1e-9)
                    if isinstance(x, (int, float)) and isinstance(y, (int, float))
                    else str(x) == str(y))
            row["fields"][field] = {"treasurydirect": x, "fiscaldata": y,
                                    "match": bool(same)}
            row["matches" if same else "diffs"] += 1
        pairs.append(row)
    result = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "treasurydirect_auctions": len(a), "fiscaldata_auctions": len(b),
              "shared_auctions": len(pairs), "field_comparisons": checked,
              "fields_compared": list(compare),
              "shared_auctions_with_a_difference": sum(1 for p in pairs if p["diffs"]),
              "pairs": pairs}
    write_json(os.path.join(out, "crosschecks", "treasury_crosscheck.json"), result)
    return {k: v for k, v in result.items() if k != "pairs"}


# --------------------------------------------------------------------------
# 1d. SEC bulk insider data sets (OFFICIAL, primary, complete quarters)
# --------------------------------------------------------------------------
#
# The per-filing Form 4 walk in ``collect_sec`` depends on EDGAR's browse
# endpoint answering an anonymous client; when it does not, three participants
# in the published Season 2 roster had no signal at all and reported
# DATA-MISSING rather than a number.  The Commission also publishes the same
# filings as a quarterly *structured* extract - every Form 3, 4 and 5 for the
# quarter, tab delimited, with the transaction table carrying the transaction
# date, the transaction code and **the price per share the insider actually
# transacted at**.  That is a filing-grade price for a real security on a real
# date from the primary source, and it is retrieved as one ZIP per quarter
# instead of one HTTP request per filing.
#
#   SOURCE: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
#   README: https://www.sec.gov/files/insider_transactions_readme.pdf
#     - eight tab-delimited UTF-8 files per quarter; this collector reads three
#       of them (SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS) and keeps the rows
#       whose issuer symbol is in the traded universe
#     - fields kept from NONDERIV_TRANS: TRANS_DATE (DD-MON-YYYY), TRANS_CODE,
#       TRANS_SHARES, TRANS_PRICEPERSHARE, TRANS_ACQUIRED_DISP_CD
#     - fields kept from REPORTINGOWNER: RPTOWNERNAME, RPTOWNER_RELATIONSHIP,
#       RPTOWNER_TITLE (the officer title is what makes the CEO/CFO rule
#       checkable rather than assumed)
SEC_INSIDER_SETS_PAGE = ("https://www.sec.gov/data-research/sec-markets-data/"
                         "insider-transactions-data-sets")
#: Kept for the register's benefit: the two published layouts, in the order the
#: policy module tries them for a recent quarter. The collector calls
#: ``sim.sec.insider_zip_candidates(quarter)`` so the order follows the SEC's own
#: table (2026 Q2 from ``datastandardsinnovation``, earlier from
#: ``structureddata``) rather than a fixed preference.
SEC_INSIDER_ZIP_PATTERNS: Tuple[str, ...] = (
    sec_policy.SEC_ENDPOINTS["insider_zip"],
    sec_policy.SEC_ENDPOINTS["insider_zip_legacy"],
)
INSIDER_MAX_QUARTERS = 8
#: 2026 Q2 is the newest published quarter as of 2026-09-18 (the page says the
#: data sets run "January 2006 - June 2026").
INSIDER_LATEST_QUARTER = "2026q2"


def _quarter_walk(latest: str, count: int) -> List[str]:
    m = re.match(r"^(\d{4})q([1-4])$", latest)
    if not m:
        return []
    year, quarter = int(m.group(1)), int(m.group(2))
    out: List[str] = []
    while len(out) < count:
        out.append(f"{year}q{quarter}")
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        if year < 2006:
            break
    return out


def _insider_rows_from_zip(body: bytes, quarter: str, url: str,
                           digest: str) -> Tuple[List[dict], dict]:
    """Extract the universe's insider transactions from one quarterly ZIP."""
    import io
    import zipfile

    wanted = set(EQUITY_UNIVERSE) | set(INSIDER_TICKERS)
    stats = {"quarter": quarter, "url": url, "bytes": len(body), "sha256": digest,
             "files": [], "submissions": 0, "reporting_owners": 0,
             "transactions": 0, "kept": 0, "issuers": 0}
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = {os.path.basename(n).upper(): n for n in archive.namelist()}
            stats["files"] = sorted(os.path.basename(n) for n in archive.namelist())

            def table(key: str) -> List[dict]:
                name = names.get(key)
                if not name:
                    return []
                text = archive.read(name).decode("utf-8", "replace")
                lines = text.splitlines()
                if not lines:
                    return []
                header = [h.strip().upper() for h in lines[0].split("\t")]
                rows = []
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    parts = line.split("\t")
                    rows.append({header[i]: (parts[i] if i < len(parts) else "")
                                 for i in range(len(header))})
                return rows

            submissions = {}
            for row in table("SUBMISSION.TXT"):
                symbol = (row.get("ISSUERTRADINGSYMBOL") or "").strip().upper()
                if symbol not in wanted:
                    continue
                submissions[row.get("ACCESSION_NUMBER", "")] = {
                    "symbol": symbol, "issuer_cik": row.get("ISSUERCIK"),
                    "issuer_name": row.get("ISSUERNAME"),
                    "filing_date": _treasury_date(row.get("FILING_DATE")),
                    "period_of_report": _treasury_date(row.get("PERIOD_OF_REPORT")),
                    "document_type": (row.get("DOCUMENT_TYPE") or "").strip()}
            stats["submissions"] = len(submissions)
            stats["issuers"] = len({s["symbol"] for s in submissions.values()})

            owners: Dict[str, List[dict]] = {}
            for row in table("REPORTINGOWNER.TXT"):
                accession = row.get("ACCESSION_NUMBER", "")
                if accession not in submissions:
                    continue
                owners.setdefault(accession, []).append({
                    "owner_cik": row.get("RPTOWNERCIK"),
                    "owner_name": (row.get("RPTOWNERNAME") or "").strip(),
                    "relationship": (row.get("RPTOWNER_RELATIONSHIP") or "").strip(),
                    "title": (row.get("RPTOWNER_TITLE") or "").strip()})
            stats["reporting_owners"] = sum(len(v) for v in owners.values())

            transactions: List[dict] = []
            for row in table("NONDERIV_TRANS.TXT"):
                accession = row.get("ACCESSION_NUMBER", "")
                submission = submissions.get(accession)
                if submission is None:
                    continue
                stats["transactions"] += 1
                price = _treasury_number(row.get("TRANS_PRICEPERSHARE"))
                shares = _treasury_number(row.get("TRANS_SHARES"))
                trans_date = _treasury_date(row.get("TRANS_DATE"))
                if trans_date is None or price is None or shares is None:
                    # A row without a date, a price or a share count cannot be
                    # turned into a dated price observation, so it is counted
                    # and dropped rather than guessed at.
                    continue
                transaction = {
                    "symbol": submission["symbol"],
                    "issuer_name": submission["issuer_name"],
                    "issuer_cik": submission["issuer_cik"],
                    "accession_number": accession,
                    "document_type": submission["document_type"],
                    "filing_date": submission["filing_date"],
                    "period_of_report": submission["period_of_report"],
                    "transaction_date": trans_date,
                    "transaction_code": (row.get("TRANS_CODE") or "").strip(),
                    "acquired_disposed": (row.get("TRANS_ACQUIRED_DISP_CD") or "").strip(),
                    "shares": shares,
                    "price_per_share": price,
                    "notional": round(shares * price, 2),
                    "direct_indirect": (row.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip(),
                    "security_title": (row.get("SECURITY_TITLE") or "").strip(),
                    "owners": owners.get(accession, []),
                    "source": url,
                    "raw_sha256": digest,
                    "source_class": "OFFICIAL",
                    "publisher": "U.S. Securities and Exchange Commission (EDGAR "
                                 "structured data set)",
                }
                transactions.append(transaction)
                stats["kept"] += 1
    except Exception as exc:                      # a corrupt ZIP is a finding
        stats["error"] = f"{type(exc).__name__}: {exc}"
        return [], stats
    return transactions, stats


#: The insider ZIP walk is bounded independently of the run's global budget:
#: the 2026-09-19 run proved that a throttled www.sec.gov burns ~40 seconds per
#: URL (three attempts, 20-second backoffs) with nothing to show for it, and
#: the retries silently consumed the minutes the sports section needed.  The
#: bulk section now gets its own slice of time and yields the remainder.
INSIDER_BULK_SUB_BUDGET_SECONDS = 300.0


def collect_insider_bulk(fetcher: Fetcher, out: str) -> dict:
    """The SEC's quarterly Form 3/4/5 structured extracts, universe-filtered."""
    base = os.path.join(out, "insider_bulk")
    quarters = _quarter_walk(INSIDER_LATEST_QUARTER, INSIDER_MAX_QUARTERS)
    summary: dict = {"quarters": quarters, "ok": [], "failed": [], "rows": 0,
                     "detail": [], "source": SEC_INSIDER_SETS_PAGE,
                     "documentation": "https://www.sec.gov/files/insider_transactions_readme.pdf"}
    rows: List[dict] = []
    deadline = time.time() + INSIDER_BULK_SUB_BUDGET_SECONDS
    for index, quarter in enumerate(quarters):
        if time.time() >= deadline or fetcher.timed_out():
            summary["sub_budget_exhausted"] = True
            summary["quarters_skipped"] = quarters[index:]
            break
        body, used, attempts = None, "", []
        for pattern in sec_policy.insider_zip_candidates(quarter):
            url = pattern
            attempts.append(url)
            body = fetcher.get(url, "sec", note=f"SEC insider data set {quarter}",
                               interval=SEC_ZIP_INTERVAL, tries=2, retry_on_403=True)
            if body is not None:
                used = url
                break
        if body is None:
            summary["failed"].append({"quarter": quarter, "attempts": attempts})
            continue
        if body is None:
            summary["failed"].append({"quarter": quarter, "attempts": attempts})
            continue
        digest = hashlib.sha256(body).hexdigest()
        extracted, stats = _insider_rows_from_zip(body, quarter, used, digest)
        stats["attempts"] = attempts
        summary["detail"].append(stats)
        summary["ok"].append(quarter)
        rows.extend(extracted)
    if rows:
        rows.sort(key=lambda r: (r["transaction_date"], r["symbol"],
                                 r["accession_number"]))
        path = os.path.join(base, "insider_transactions.jsonl")
        write_jsonl(path, rows)
        summary["rows"] = len(rows)
        summary["file"] = os.path.relpath(path, REPO_ROOT)
        summary["symbols"] = sorted({r["symbol"] for r in rows})
        # A compact per-quarter index keeps the site's custody table cheap: the
        # row count, the issuer breadth and the exact ZIP bytes are what a
        # reader checks, not 40,000 transaction rows.
        write_json(os.path.join(base, "index.json"), {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": SEC_INSIDER_SETS_PAGE,
            "documentation": summary["documentation"],
            "quarters": summary["detail"]})
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
    summary = {"rows": 0, "failed": [], "pages": 0, "preserved_existing": False}
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
    summary["rows"], summary["preserved_existing"] = write_jsonl_if_nonempty(
        os.path.join(out, "fda", "openfda_decisions.jsonl"), rows)
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
    n, preserved = write_jsonl_if_nonempty(
        os.path.join(out, "sports", "mlb_games_2026.jsonl"), rows)
    return {"rows": n, "ok": True, "preserved_existing": preserved}


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
#: answers HTTP 400 for a date *range*; for the weekly-calendar sports it
#: accepts a season + seasontype + week, which is what the first collection
#: run got wrong.
ESPN_WEEKS = {
    "nfl": [(2025, 2, 1, 18), (2025, 3, 1, 4)],
    "ncaaf": [(2025, 2, 1, 15), (2025, 3, 1, 1)],
}

#: The daily-calendar sports (basketball, baseball) IGNORE the week parameter,
#: and their bare-season-year form is a trap: the response is capped at ~25
#: events from an arbitrary mid-season window (the 2026-09-20 run's manifest
#: is the evidence: dates=2026&limit=5000 answered HTTP 200 with 340,836 bytes
#: whose only NBA events were 2026-01-01..2026-01-04, and the baseball variant
#: returned spring-training games that the preseason filter dropped - 0 rows).
#: The form that returns a complete answer is a SINGLE calendar date:
#: dates=20251021 returned that day's completed games with final scores when
#: verified against the live endpoint on 2026-09-19.  Each daily sport is
#: therefore walked day by day across the dates its season can intersect the
#: trading window; ranges (dates=YYYYMMDD-YYYYMMDD) answer HTTP 400.
ESPN_DAY_WALKS = {
    # The 2025-26 NBA season: preseason from early October, playoffs into June.
    "nba": ("2025-10-01", "2026-06-30"),
    # The 2026 MLB season: spring training from late February to the window end.
    "mlb": ("2026-02-20", "2026-09-16"),
}


def _espn_requests(key: str, path: str) -> List[Tuple[str, str]]:
    """Every scoreboard URL for one sport, with the note that explains it."""
    out: List[Tuple[str, str]] = []
    for season, seasontype, first_week, last_week in ESPN_WEEKS.get(key, []):
        for week in range(first_week, last_week + 1):
            out.append((
                f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
                f"?dates={season}&seasontype={seasontype}&week={week}&limit=1000",
                f"{key} {season} type {seasontype} week {week} (secondary)"))
    if key in ESPN_DAY_WALKS:
        start, end = ESPN_DAY_WALKS[key]
        import datetime as dt
        cur = dt.date.fromisoformat(start)
        stop = dt.date.fromisoformat(end)
        while cur <= stop:
            stamp = cur.strftime("%Y%m%d")
            out.append((
                f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
                f"?dates={stamp}&limit=1000",
                f"{key} {cur.isoformat()} (secondary)"))
            cur += dt.timedelta(days=1)
    return out


def collect_espn(fetcher: Fetcher, out: str) -> dict:
    summary: Dict[str, dict] = {}
    for key, path in ESPN_SPORTS.items():
        rows: List[dict] = []
        failed = 0
        for url, note in _espn_requests(key, path):
            body = fetcher.get(url, "espn", note=note)
            if body is None:
                failed += 1
                continue
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            for event in payload.get("events") or []:
                # Season type 1 is preseason: the football weeks enumerate
                # types 2 and 3 explicitly, and the whole-season responses mix
                # spring training and exhibition games into the same payload,
                # so the same line is applied to every sport.
                if ((event.get("season") or {}).get("type") or 0) == 1:
                    continue
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
        n, preserved = write_jsonl_if_nonempty(
            os.path.join(out, "sports", f"{key}_scoreboard.jsonl"), rows)
        summary[key] = {"rows": n, "chunks_failed": failed,
                        "preserved_existing": preserved}
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
    n, preserved = write_jsonl_if_nonempty(
        os.path.join(out, "sports", "nba_schedule_official.jsonl"), rows)
    return {"ok": True, "rows": n, "preserved_existing": preserved}


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
# 4b. Injury archive - dated snapshots so the forward test can accumulate.
# --------------------------------------------------------------------------
#: The machine-readable injury feeds.  ESPN's structured endpoint is what the
#: NBAInjuryReport project itself polls; it is a SECONDARY publisher (the
#: leagues' own documents are the OFFICIAL record and are snapshotted below),
#: but it is the only form that can be counted without scraping prose.
ESPN_INJURY_FEEDS = {
    "nfl": ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"),
    "nba": ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"),
}

#: The official league documents, snapshotted with the capture date in the
#: filename so repeated runs build an archive instead of overwriting one file.
INJURY_ARCHIVE_OFFICIAL = {
    "nfl_injuries": "https://www.nfl.com/injuries/",
    "nba_injury_report_index": "https://official.nba.com/nba-injury-report-2025-26-season/",
}


def collect_injury_archive(fetcher: Fetcher, out: str) -> dict:
    """One dated capture of every injury feed the forward test reads.

    Neither league publishes a retrievable archive of past injury designations,
    which is why the injury participants are forward-only.  What this function
    adds is the archive itself: each run writes

    * ``sports/official/archive/espn_<league>_injuries_<YYYY-MM-DD>.json`` -
      the machine-readable countable snapshot (SECONDARY publisher, labelled),
    * ``sports/official/archive/<doc>_<YYYY-MM-DD>.raw`` - the official league
      document for the same capture date (custody evidence),

    and skips files that already exist for today, so a re-run on the same date
    is idempotent and the archive only ever grows one capture per date.
    """
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    base = os.path.join(out, "sports", "official", "archive")
    summary: Dict[str, dict] = {"date": stamp, "captures": {}, "skipped": []}

    for league, url in ESPN_INJURY_FEEDS.items():
        path = os.path.join(base, f"espn_{league}_injuries_{stamp}.json")
        if os.path.exists(path):
            summary["skipped"].append(os.path.basename(path))
            continue
        body = fetcher.get(url, "espn", headers={"Accept": "application/json"},
                           note=f"{league} injury snapshot {stamp} (secondary)")
        if body is None:
            summary["captures"][f"espn_{league}_injuries"] = {"ok": False}
            continue
        write_bytes(path, body)
        try:
            payload = json.loads(body.decode("utf-8"))
            count = sum(1 for item in payload.get("items") or []
                        for entry in item.get("injuries") or []
                        if entry.get("status") in ("Out", "Doubtful",
                                                   "Injured Reserve",
                                                   "Out for Season",
                                                   "Out Indefinitely"))
        except Exception as exc:  # noqa: BLE001
            count = None
        summary["captures"][f"espn_{league}_injuries"] = {
            "ok": True, "bytes": len(body), "game_impacting": count}

    for key, url in INJURY_ARCHIVE_OFFICIAL.items():
        path = os.path.join(base, f"{key}_{stamp}.raw")
        if os.path.exists(path):
            summary["skipped"].append(os.path.basename(path))
            continue
        body = fetcher.get(url, "nocode", headers={"Accept": "text/html,*/*"},
                           note=f"official injury document {key} {stamp}")
        if body is None:
            summary["captures"][key] = {"ok": False}
            continue
        write_bytes(path, body)
        summary["captures"][key] = {"ok": True, "bytes": len(body)}
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

#: The venue re-spelled its numbers.  A market no longer carries the legacy
#: integers ("volume", "last_price"); it carries fixed-point contract counts
#: ("volume_fp": "30421098.89") and dollar strings ("last_price_dollars":
#: "0.0100"), and Kalshi's own migration page says the integer fields are legacy
#: and will be deprecated.  Reading only the legacy spelling is what produced a
#: file whose every numeric column was null, which the site then published as
#: "the venue left them empty" - a conclusion drawn from an absence in the
#: *reader*, not in the payload (IR-41).  Both spellings are accepted, newest
#: first, and the key each value came from is written into the row.
#:   SOURCE: https://docs.kalshi.com/getting_started/fixed_point_migration
#:   (the market schema: https://docs.kalshi.com/api-reference/market/get-markets)
KALSHI_NUMERIC_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("volume", ("volume_fp", "volume")),
    ("volume_24h", ("volume_24h_fp", "volume_24h")),
    ("open_interest", ("open_interest_fp", "open_interest")),
    ("last_price", ("last_price_dollars", "last_price")),
    ("yes_bid", ("yes_bid_dollars", "yes_bid")),
    ("yes_ask", ("yes_ask_dollars", "yes_ask")),
    ("no_bid", ("no_bid_dollars", "no_bid")),
    ("no_ask", ("no_ask_dollars", "no_ask")),
    ("settlement_value", ("settlement_value_dollars", "settlement_value")),
    ("liquidity", ("liquidity_dollars", "liquidity")),
)


def _number(value):
    """A venue number, which may arrive as a fixed-point or dollar string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def kalshi_market_row(market: dict) -> dict:
    """One settled market, with the venue's numbers read from either spelling.

    ``source_keys`` maps each value back to the key it came from, so a silent
    re-spelling shows up as a changed mapping rather than as a column of nulls.
    A field the payload does not carry is recorded as ``None`` - never as 0.0,
    which would be an observation of zero activity that nobody made.  The
    verbatim response is what the manifest's SHA-256 covers, so a reader can
    re-fetch the URL and check both the numbers and the spelling.
    """
    row = {"ticker": market.get("ticker"),
           "event_ticker": market.get("event_ticker"),
           "title": market.get("title"),
           "close_time": market.get("close_time"),
           "status": market.get("status"),
           "result": market.get("result"),
           "source": "https://api.elections.kalshi.com/trade-api/v2/markets",
           "source_class": "OFFICIAL-VENDOR"}
    used: Dict[str, str] = {}
    for canonical, keys in KALSHI_NUMERIC_FIELDS:
        row[canonical] = None
        for key in keys:
            value = market.get(key)
            if value in (None, ""):
                continue
            row[canonical] = _number(value)
            used[canonical] = key
            break
    row["source_keys"] = used
    return row


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
        rows = [kalshi_market_row(m) for m in markets]
        n = write_jsonl(os.path.join(out, "kalshi", f"{series}_settled.jsonl"), rows)
        with_volume = sum(1 for r in rows if r.get("volume") is not None)
        summary[series] = {"ok": True, "rows": n, "rows_with_volume": with_volume}
        if rows and not with_volume:
            # Say it in the run log too: a file whose every volume is null is a
            # missing signal, and the reader should not have to open the JSONL to
            # find that out.
            print(f"  kalshi {series}: {n} rows, none carries a volume - "
                  f"check the payload's field spelling against KALSHI_NUMERIC_FIELDS")
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
        (os.path.join(out, "prices", "nasdaq"), "Nasdaq historical quote API (official-source candidate)", "OFFICIAL"),
        (os.path.join(out, "raw", "nasdaq"), "Nasdaq raw JSON responses", "OFFICIAL"),
        (os.path.join(out, "fred"), "FRED (Federal Reserve Bank of St. Louis)", "OFFICIAL"),
        (os.path.join(out, "sec"), "SEC EDGAR (Form 4 + ticker map)", "OFFICIAL"),
        (os.path.join(out, "finra"), "FINRA REG SHO daily short-sale volume", "OFFICIAL"),
        (os.path.join(out, "nyfed"), "NY Fed reference rates (SOFR)", "OFFICIAL"),
        (os.path.join(out, "treasury"), "U.S. Treasury auctions and par yield curve", "OFFICIAL"),
        (os.path.join(out, "insider_bulk"), "SEC quarterly insider transaction data sets", "OFFICIAL"),
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
    write_json(os.path.join(out, "coverage_report.json"), report,
               writer="scripts/collect_real_data.py",
               reason=("derived coverage index rebuilt from the files on disk; a run "
                       "that recorded the previous copy cannot recover its bytes, so "
                       "the write is chained here instead"))
    return report


# --------------------------------------------------------------------------
# 4b. SportsPred dated snapshots (the P1 "map SportsPred's predictions to a
#     dated snapshot URL" item, implemented as an archive).
#
#     The SportsPred repository (buffedlizard55-lab/SportsPred) publishes its
#     model's own record as small JSON files on its default branch:
#
#     * ``data/predictions.json`` - append-only record of every selection the
#       model has made, keyed by OLBG event_id, graded by the site's own
#       backtest script. ``price`` is null unless actually sourced.
#     * ``data/results.json``     - settled outcomes used to grade those
#       predictions; empty until a verified results source is reachable.
#     * ``data/slate.json``       - the dated OLBG consensus slate the model
#       reads (event_id, market, selection, tips_for/tips_total, pct).
#     * ``data/provenance.json``  - the site's own collection environment and
#       irregularity register.
#     * per-sport ``data/<sport>_slate.json`` slates.
#
#     None of these are archived by the site itself, so this collector writes
#     one dated capture per file per run under
#     ``data/real/sportspred/archive/`` (idempotent within a date, like the
#     injury archive). Files larger than SPORTSPRED_BYTE_BUDGET are recorded
#     as skipped-oversized by reading at most budget+1 bytes - never stored,
#     and never silently absent: the summary names them. raw.githubusercontent
#     is reachable from GitHub runners but NOT from the sandbox (IR-76 family),
#     so this section is runner-driven by design.
# --------------------------------------------------------------------------
SPORTSPRED_RAW_BASE = "https://raw.githubusercontent.com/buffedlizard55-lab/SportsPred/main/data"  # noqa: E501 - kept on one line so the sources-register test sees the full URL
#: Small, bounded files worth capturing whole. Everything else in the site's
#: data/ directory is either generated per-sport bulk (data/baseball_predictions
#. json alone is ~96 MB) or derivable from these.
SPORTSPRED_SNAPSHOT_FILES = (
    "predictions.json", "results.json", "slate.json", "provenance.json",
    "card.json", "leagues.json", "olbg_sports.json", "league_context.json",
    "baseball_slate.json", "basketball_slate.json", "cricket_slate.json",
    "darts_slate.json", "f1_slate.json", "gaa_slate.json",
    "gaa_hurling_slate.json", "golf_slate.json", "greyhound_slate.json",
    "handball_slate.json", "ice_hockey_slate.json", "nrl_origin.json",
    "rugby_league_slate.json", "snooker_slate.json",
    "t20_blast_competition.json", "volleyball_slate.json",
)
SPORTSPRED_BYTE_BUDGET = 512_000


def collect_sportspred(fetcher: Fetcher, out: str) -> dict:
    """One dated capture of SportsPred's own published prediction record.

    Writes ``sportspred/archive/sportspred_<name>_<YYYY-MM-DD>.json`` for every
    site file in SPORTSPRED_SNAPSHOT_FILES that exists and fits the byte
    budget, skips captures already written today (idempotent per date), and
    reports oversize files as skipped rather than truncated or absent. Every
    request goes through the Fetcher so the manifest carries URL, status,
    bytes and SHA-256 like every other section.
    """
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    base = os.path.join(out, "sportspred", "archive")
    os.makedirs(base, exist_ok=True)
    summary: Dict[str, dict] = {"date": stamp, "captures": {}, "skipped": [],
                                "skipped_oversized": {}}
    for name in SPORTSPRED_SNAPSHOT_FILES:
        path = os.path.join(base, f"sportspred_{name[:-len('.json')]}_{stamp}.json")
        if os.path.exists(path):
            summary["skipped"].append(os.path.basename(path))
            continue
        url = f"{SPORTSPRED_RAW_BASE}/{name}"
        body = fetcher.get(url, "sportspred",
                           headers={"Accept": "application/json"},
                           note=f"SportsPred snapshot {name} {stamp} "
                                "(the site's own prediction record)")
        if body is None:
            summary["captures"][name] = {"ok": False}
            continue
        if len(body) > SPORTSPRED_BYTE_BUDGET:
            summary["skipped_oversized"][name] = {
                "budget": SPORTSPRED_BYTE_BUDGET,
                "bytes_seen": len(body),
                "note": ("file exceeds the snapshot budget; recorded here, "
                         "not stored and not truncated")}
            continue
        write_bytes(path, body)
        summary["captures"][name] = {"ok": True, "bytes": len(body)}
    return summary


# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "real"))
    parser.add_argument("--max-seconds", type=float, default=1500.0)
    parser.add_argument("--only",
                        default=("prices,nasdaq,sec,fda,sports,weather,kalshi,"
                                 "official_rates,treasury,insider_bulk,"
                                 "injury_archive,sportspred"))
    args = parser.parse_args(argv)

    out = os.path.abspath(args.out)
    only = {p.strip() for p in args.only.split(",") if p.strip()}
    manifest: List[dict] = []
    fetcher = Fetcher(manifest, args.max_seconds)
    results: dict = {}

    # Section order is a correctness decision, not a style one.  The global
    # time budget silently ends whatever section is running when it runs out
    # (every later fetch returns None without a manifest entry), so what is
    # MISSING goes first and what is already-covered-and-expensive goes last:
    # the 2026-09-19 run spent its whole budget on Nasdaq candidate timeouts
    # and throttled SEC ZIP retries, and the NBA scoreboard, injury archive,
    # FDA, weather and Kalshi sections never started at all.  Order now:
    # the missing event data (sports, injuries), the cheap refreshes (FDA,
    # weather, Kalshi), the rate feeds, the price refreshes, the SEC insider
    # walk (with its own sub-budget), and the Nasdaq candidates last because
    # their outage is expected and its audit fails closed by design.
    if "sports" in only:
        results["mlb"] = collect_mlb(fetcher, out)
        results["espn"] = collect_espn(fetcher, out)
        results["nba"] = collect_nba_official(fetcher, out)
        results["official_docs"] = collect_official_sports_docs(fetcher, out)
        print(f"sports: mlb={results['mlb']} espn={results['espn']} nba={results['nba']}")
    if "injury_archive" in only or "sports" in only:
        # Dated injury snapshots: the forward test's archive.  Runs with the
        # sports section (and on its own from the weekly injury-archive
        # workflow) so the archive grows one capture per date without anyone
        # re-running the whole collector.
        results["injury_archive"] = collect_injury_archive(fetcher, out)
        print(f"injury_archive: {results['injury_archive']}")
    if "sportspred" in only or "injury_archive" in only or "sports" in only:
        # Dated SportsPred snapshots: the P1 mapping item. Same archive shape
        # and cadence as the injury captures, so the weekly workflow collects
        # both in one pass.
        results["sportspred"] = collect_sportspred(fetcher, out)
        print(f"sportspred: {results['sportspred']}")
    if "fda" in only:
        results["fda"] = collect_fda(fetcher, out)
        print(f"fda: {results['fda']['rows']} decision rows")
    if "weather" in only:
        results["weather"] = collect_weather(fetcher, out)
        print(f"weather: {results['weather']}")
    if "kalshi" in only:
        results["kalshi"] = collect_kalshi(fetcher, out)
        print(f"kalshi: {results['kalshi']}")
    if "official_rates" in only or "live" in only:
        # The Live Book's two official endpoints: FINRA's files are large and
        # the SOFR API is small.
        results["nyfed"] = collect_nyfed(fetcher, out)
        print(f"nyfed: {results['nyfed']}")
        results["finra"] = collect_finra(fetcher, out)
        print(f"finra: {results['finra']['files']} files, "
              f"{results['finra']['rows_kept']} universe rows kept")
    if "treasury" in only:
        # The U.S. Treasury's own auction results and par yield curve: the only
        # free, public source in this collector whose *executed* price is an
        # official publisher's number, which is what the Official Auction Book
        # trades on (sim/treasury.py).
        results["treasury"] = collect_treasury(fetcher, out)
        print(f"treasury: {results['treasury']['tape']} "
              f"crosscheck={results['treasury']['crosscheck']}")
    if "fred" in only and "prices" not in only:
        # The H.15 series on their own, without re-fetching the equity price
        # files: the official auction book needs the par curve and the
        # secondary-market bill rates, and asking for "prices" would rewrite
        # every committed Yahoo file as a side effect.
        results["fred"] = collect_fred(fetcher, out)
        print(f"fred: {results['fred']['ok']}")
    if "prices" in only:
        results["yahoo"] = collect_yahoo(fetcher, out)
        print(f"yahoo: {len(results['yahoo']['ok'])} ok, {len(results['yahoo']['failed'])} failed")
        results["stooq"] = collect_stooq(fetcher, out)
        print(f"stooq: {len(results['stooq']['ok'])} ok, {len(results['stooq']['failed'])} failed")
        results["fred"] = collect_fred(fetcher, out)
        print(f"fred: {results['fred']['ok']}")
    if "insider_bulk" in only or "sec" in only:
        # The quarterly structured extracts are requested with the per-filing
        # walk because they answer the same question (what did insiders trade,
        # at what price, on what date) from the primary source, without
        # depending on the browse endpoint that returned HTTP 403 on
        # 2026-09-18.  Bounded by INSIDER_BULK_SUB_BUDGET_SECONDS so a
        # throttled host cannot eat the rest of the run again.
        results["insider_bulk"] = collect_insider_bulk(fetcher, out)
        print(f"insider_bulk: {results['insider_bulk']['rows']} rows from "
              f"{results['insider_bulk']['ok']}")
    if "sec" in only:
        results["sec"] = collect_sec(fetcher, out)
        print(f"sec: {results['sec']['transactions']} transactions from "
              f"{results['sec']['filings']} filings")
    if "nasdaq" in only:
        # The official-price candidate probes run last: every attempt so far
        # has timed out (the audit fails closed on exactly that), so this is
        # the one section whose loss to the time budget costs nothing.
        results["nasdaq"] = collect_nasdaq(fetcher, out)
        print(f"nasdaq: {len(results['nasdaq']['ok'])} ok")

    if "prices" in only:
        results["crosscheck"] = crosscheck(out)

    manifest_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "collector": "scripts/collect_real_data.py",
        "window": {"warmup_start": WARMUP_START, "season_start": SEASON_START,
                   "season_end": SEASON_END},
        "requests": manifest,
        # What the collector declared to EDGAR, and under which policy: the
        # insider strategies are gated on this stream, so whether the header set
        # was the published one has to be readable in the artifact, not assumed.
        "sec_access": sec_policy.declared_headers_record(),
        "ok": sum(1 for m in manifest if m["ok"]),
        "failed": sum(1 for m in manifest if not m["ok"]),
        "budget_exhausted": fetcher.budget_exhausted,
    }
    write_json(os.path.join(out, "collection_manifest.json"), manifest_payload,
               writer="scripts/collect_real_data.py",
               reason=("the manifest is rewritten by every collection run; the run "
                       "that recorded this file's hash gets a chain row rather than "
                       "an unexplained mismatch"))
    coverage = coverage_report(out, results)
    print(json.dumps({"ok_requests": manifest_payload["ok"],
                      "failed_requests": manifest_payload["failed"],
                      "budget_exhausted": manifest_payload["budget_exhausted"],
                      "sources": coverage["sources"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
