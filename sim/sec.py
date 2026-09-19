"""SEC EDGAR access rules, implemented once and cited.

The insider strategies (``@InsiderCopycat_Max``, ``@CEO_CFO_Conviction``,
``@InsiderCluster_Live``) read SEC Form 4 filings.  The first EDGAR collection
run was answered **HTTP 403** - the SEC refuses clients that do not declare
themselves the way its published policy asks - so no Form 4 data ever landed and
every insider participant has been idle since.  This module is the fix, and it
is written as a citation rather than a workaround: it implements exactly the
header set, the request rate and the identification the SEC publishes.

    SOURCE (Accessing EDGAR Data, retrieved 2026-09-19):
      https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data

    "Please declare your user agent in request headers:

        Sample Declared Bot Request Headers:
          User-Agent:      Sample Company Name AdminContact@<sample company domain>.com
          Accept-Encoding: gzip, deflate
          Host:            www.sec.gov

     ... Current max request rate: 10 requests/second."

Two consequences the collector has to live with, both quoted from the same page:

* the User-Agent must name the client **and a contact address**, so the declared
  address is configurable - an operator with a real mailbox sets
  ``SPS_SEC_USER_AGENT`` and the collector uses it verbatim;
* the rate limit is a maximum, not a target, so every EDGAR request goes through
  one limiter that keeps the run at or below ``SEC_MAX_REQUESTS_PER_SECOND``.

The module is standard-library only, like the rest of ``sim/``.
"""

from __future__ import annotations

import gzip
import os
import re
import time
import zlib
from typing import Dict, Optional, Tuple

#: The SEC's published maximum request rate, in requests per second.
#: SOURCE: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
SEC_MAX_REQUESTS_PER_SECOND = 10.0

#: The header set the SEC asks an automated client to send, verbatim.
SEC_HEADER_NAMES = ("User-Agent", "Accept-Encoding", "Host")

#: The address the collector declares when the operator has not supplied one.
#: It is the repository's own contact route; a project that wants a mailbox in
#: the header sets ``SPS_SEC_USER_AGENT`` (or ``SEC_USER_AGENT``) instead.
DEFAULT_SEC_CONTACT = "buffedlizard55-lab@users.noreply.github.com"

#: The declared client name, in the shape the SEC's sample shows
#: ("Sample Company Name AdminContact@..."). The shape is deliberately exactly
#: two tokens - a name and a contact address - because that is the sample the
#: SEC prints, and a header that merely contains an address is not the same
#: declaration. The project URL belongs in the policy record, not in the token
#: the SEC parses.
DEFAULT_SEC_AGENT_NAME = "StockPaperSim"

#: Endpoints this collector is allowed to ask for, each one documented by the
#: SEC on the page cited above.
SEC_ENDPOINTS = {
    "ticker_map": "https://www.sec.gov/files/company_tickers.json",
    "submissions": "https://data.sec.gov/submissions/CIK{cik}.json",
    "daily_index": ("https://www.sec.gov/Archives/edgar/daily-index/{year}/"
                    "QTR{quarter}/form.{date}.idx"),
    "browse_edgar": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                     "&CIK={cik}&type={form}&owner=include&count={count}&output=atom"),
    "insider_sets_page": ("https://www.sec.gov/data-research/sec-markets-data/"
                          "insider-transactions-data-sets"),
    # Two directory layouts carry the same quarterly files. The page pins the
    # change: 2026 Q2 (the newest set, 10.97 MB) is served from
    # ``datastandardsinnovation`` and 2026 Q1 and earlier from ``structureddata``.
    # Both are kept, and which is tried first depends on the quarter, because a
    # URL that works today for Q2 is not the URL that works for 2019.
    "insider_zip": ("https://www.sec.gov/files/datastandardsinnovation/data/"
                    "insider-transactions-data-sets/{quarter}_form345.zip"),
    "insider_zip_legacy": ("https://www.sec.gov/files/structureddata/data/"
                           "insider-transactions-data-sets/{quarter}_form345.zip"),
}

#: The quarter from which the SEC's current path replaced the older one.
#: SOURCE: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
INSIDER_ZIP_LAYOUT_CHANGE_QUARTER = (2026, 2)


def insider_zip_candidates(quarter: str) -> Tuple[str, ...]:
    """Both known URLs for a quarter's Form 3/4/5 data set, best guess first.

    The published table names one layout per date range, so the quarter decides
    the order; the other URL is still tried, because a publisher moving a file
    back is cheaper to survive than to diagnose. Every attempt the collector
    makes is recorded with its own URL and status, so a reader can see which one
    answered.
    """
    match = re.match(r"^(\d{4})q([1-4])$", quarter)
    year, q = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    current = SEC_ENDPOINTS["insider_zip"].format(quarter=quarter)
    legacy = SEC_ENDPOINTS["insider_zip_legacy"].format(quarter=quarter)
    return ((current, legacy) if (year, q) >= INSIDER_ZIP_LAYOUT_CHANGE_QUARTER
            else (legacy, current))


def sec_user_agent(contact: Optional[str] = None) -> str:
    """The declared User-Agent, in the SEC's published shape.

    An operator-supplied value wins over everything: the SEC's rule is that the
    header names a real contact, and only the operator knows one.  The format
    ``Name contact@host`` is the shape its sample shows.
    """
    declared = (contact
                or os.environ.get("SPS_SEC_USER_AGENT")
                or os.environ.get("SEC_USER_AGENT") or "").strip()
    if declared:
        return declared
    return f"{DEFAULT_SEC_AGENT_NAME} {DEFAULT_SEC_CONTACT}"


def sec_headers(contact: Optional[str] = None) -> Dict[str, str]:
    """The full declared header set, quoted from the SEC's published sample."""
    return {
        "User-Agent": sec_user_agent(contact),
        # The SEC's own sample value, kept exactly: this is the one place the
        # project may not "improve" on a documented requirement.
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
        "Accept": "*/*",
    }


def declared_headers_record(contact: Optional[str] = None) -> dict:
    """A JSON-serialisable record of what is sent, for the collection manifest."""
    headers = sec_headers(contact)
    return {
        "headers": headers,
        "policy": {
            "source": "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
            "max_requests_per_second": SEC_MAX_REQUESTS_PER_SECOND,
            "contact_source": ("SPS_SEC_USER_AGENT" if os.environ.get("SPS_SEC_USER_AGENT")
                               else "SEC_USER_AGENT" if os.environ.get("SEC_USER_AGENT")
                               else "repository default"),
        },
    }


def decode_body(body: bytes, content_encoding: str = "") -> bytes:
    """Undo the ``Accept-Encoding: gzip, deflate`` the SEC was asked to use.

    The declared header set asks for compression, so the responses arrive
    compressed; a client that declares the header and does not implement it
    stores bytes it cannot read.  Handled defensively: a body that is not
    actually compressed is returned unchanged, because several CDN edges ignore
    the header entirely.
    """
    encoding = (content_encoding or "").lower()
    if "gzip" in encoding or body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    if "deflate" in encoding:
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                return body
    return body


class SecRateLimiter:
    """Keep EDGAR requests at or below the SEC's published maximum rate.

    ``min_interval`` is derived from the published limit rather than hard-coded
    twice, so a change to the constant cannot silently leave the limiter behind.
    ``0.9`` leaves a margin under the ceiling: the point is never to be the
    client that made the SEC tighten it.
    """

    def __init__(self, max_per_second: float = SEC_MAX_REQUESTS_PER_SECOND,
                 clock=time.monotonic, sleep=time.sleep, margin: float = 0.9):
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self.max_per_second = max_per_second
        self.min_interval = 1.0 / (max_per_second * margin)
        self._clock = clock
        self._sleep = sleep
        self._last: Optional[float] = None
        self.requests = 0

    def wait(self) -> float:
        """Block until one more request may be sent; return the seconds waited."""
        now = self._clock()
        waited = 0.0
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self.min_interval:
                waited = self.min_interval - elapsed
                self._sleep(waited)
        self._last = self._clock()
        self.requests += 1
        return waited

    def record(self) -> dict:
        return {"requests": self.requests, "max_requests_per_second": self.max_per_second,
                "min_interval_seconds": round(self.min_interval, 4),
                "effective_max_requests_per_second": round(1.0 / self.min_interval, 3)}


#: The spacing a caller must leave between EDGAR requests to stay under the
#: published ceiling. Derived here, from the one constant, so a collector that
#: imports this file cannot drift from the rule it cites.
SEC_MIN_INTERVAL_EFFECTIVE = SecRateLimiter().min_interval


def edgar_request(url: str, limiter: Optional[SecRateLimiter] = None,
                  contact: Optional[str] = None, timeout: float = 30.0,
                  opener=None) -> Tuple[int, bytes, dict]:
    """One rate-limited EDGAR GET, with the declared headers and decompression.

    ``opener`` is an injected ``urllib.request.urlopen``-compatible callable so
    the whole path is testable without a network.
    """
    if opener is None:                                      # pragma: no cover
        import urllib.request
        opener = urllib.request.urlopen
    limiter = limiter or SecRateLimiter()
    waited = limiter.wait()
    request_headers = sec_headers(contact)
    request = None
    try:
        import urllib.request
        request = urllib.request.Request(url, headers=request_headers)
        with opener(request, timeout=timeout) as response:  # type: ignore[arg-type]
            body = response.read()
            status = int(getattr(response, "status", 200))
            encoding = ""
            if hasattr(response, "headers"):
                encoding = response.headers.get("Content-Encoding", "") or ""
    except Exception as exc:                                # pragma: no cover
        status, body, encoding = getattr(exc, "code", 0) or 0, b"", ""
        record = {"url": url, "status": status, "error": f"{type(exc).__name__}: {exc}",
                  "headers": request_headers, "waited_seconds": round(waited, 4)}
        return status, body, record
    body = decode_body(body, encoding)
    record = {"url": url, "status": status, "bytes": len(body),
              "content_encoding": encoding or "identity",
              "headers": request_headers, "waited_seconds": round(waited, 4)}
    return status, body, record


__all__ = ["SEC_MAX_REQUESTS_PER_SECOND", "SEC_MIN_INTERVAL_EFFECTIVE",
           "SEC_ENDPOINTS", "INSIDER_ZIP_LAYOUT_CHANGE_QUARTER",
           "insider_zip_candidates", "sec_user_agent", "sec_headers",
           "SEC_HEADER_NAMES", "DEFAULT_SEC_CONTACT", "DEFAULT_SEC_AGENT_NAME",
           "declared_headers_record", "decode_body", "SecRateLimiter", "edgar_request"]
