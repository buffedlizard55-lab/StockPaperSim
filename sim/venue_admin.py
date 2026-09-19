"""Venue administration: official calendars, halts, corporate actions, fees.

This module answers the questions a scheduler must answer *before* an order is
submitted: is the market open that day, does cash actually settle on the
expected day, is the symbol halted, is there a sourced corporate action that a
price series cannot silently absorb, and what regulator fees apply to a sale.

Every date set below was read from the publisher's own page during the
2026-09-19 verification pass, and each constant carries its URL and the day it
was checked.  The rule is the same fail-closed rule used everywhere else in
this repository: outside the documented horizon the functions raise rather
than extrapolate a weekday pattern, because a guessed calendar is exactly how
paper-trading projects start trading on days the real market was closed.

VERIFIED ON 2026-09-19 (documented in research/VERIFICATION_LOG.md):

* NYSE 2026 holidays and the two 1:00pm ET early closes, from the NYSE's own
  "Holidays & Trading Hours" page:
    SOURCE: https://www.nyse.com/trade/hours-calendars
* Federal Reserve Banks 2026 holiday schedule (the days Fedwire funds and
  securities services close, which is what governs whether cash moves), from
  FRBservices.  The old path https://www.frbservices.org/resources/holidays
  now 404s and the live path is /about/holiday-schedules - that rot is logged
  as IR-72 and the cite-hygiene row type it belongs to is IR-33's.
    SOURCE: https://www.frbservices.org/about/holiday-schedules
* SEC Section 31 and FINRA TAF rates are validated in sim/config.py and were
  re-checked against FINRA's 2026 fee-adjustment schedule and the FY2026
  Section 31 advisory on 2026-09-19.

Halts are not embedded at build time - they move minute to minute.  The parser
below reads the Nasdaq Trader halts RSS document (an official Nasdaq
publication) and the collector stores the raw bytes so every parsed row keeps
its checksum provenance.  Parsing an empty document yields zero halts, not a
hallucinated "all clear from authority": row provenance is the document's own
``ndq:now`` timestamp.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional

UTC = dt.timezone.utc

# --------------------------------------------------------------------------
# Trading calendar (permissions to *trade*), sourced, fail-closed at the edges
# --------------------------------------------------------------------------

# NYSE holidays for 2026 - the whole U.S. cash-equity complex (NYSE, NYSE
# American, NYSE Arca, NYSE National, NYSE Texas, Nasdaq's own calendar
# matches) closes.  Read 2026-09-19 from the NYSE table; Good Friday is on the
# MARKET calendar but NOT on the Federal Reserve calendar (banks are open), a
# distinction the settlement logic below depends on.
#   SOURCE: https://www.nyse.com/trade/hours-calendars  (checked 2026-09-19)
MARKET_HOLIDAYS_2026: Dict[str, str] = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
}
MARKET_CALENDAR_SOURCE = "https://www.nyse.com/trade/hours-calendars"
MARKET_CALENDAR_CHECKED = "2026-09-19"

# Early closes at 1:00 p.m. ET for 2026, per the footnotes on the same NYSE
# page: the day after Thanksgiving (2026-11-27) and Christmas Eve (2026-12-24).
MARKET_EARLY_CLOSES_2026: Dict[str, str] = {
    "2026-11-27": "day after Thanksgiving",
    "2026-12-24": "Christmas Eve",
}
EARLY_CLOSE_TIME_NY = "13:00"  # HH:MM America/New_York, per the same page

# Federal Reserve Banks holidays for 2026: when Fedwire (and therefore cash
# settlement) is closed.  Read 2026-09-19 from the FRBservices schedule table.
# Notes on the page: when a holiday falls on a Saturday the Banks are open the
# preceding Friday (so 2026-07-04, a Saturday, leaves 2026-07-03 OPEN for
# settlement even though the exchange calendar is closed that day); when it
# falls on a Sunday the Banks close the following Monday.  Good Friday is not
# a Federal holiday, so settlement runs on 2026-04-03 while trading does not.
#   SOURCE: https://www.frbservices.org/about/holiday-schedules (checked 2026-09-19)
FED_HOLIDAYS_2026: Dict[str, str] = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-04": "Independence Day (Saturday; Banks open 2026-07-03)",
    "2026-09-07": "Labor Day",
    "2026-10-12": "Columbus Day",
    "2026-11-11": "Veterans Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
}
FED_CALENDAR_SOURCE = "https://www.frbservices.org/about/holiday-schedules"
FED_CALENDAR_CHECKED = "2026-09-19"

_CALENDARS = {
    "2026": {
        "market": MARKET_HOLIDAYS_2026,
        "early_close": MARKET_EARLY_CLOSES_2026,
        "fed": FED_HOLIDAYS_2026,
    }
}


def _calendar(year: int) -> Dict[str, Dict[str, str]]:
    cal = _CALENDARS.get(str(year))
    if cal is None:
        raise ValueError(
            f"CALENDAR_NOT_DOCUMENTED:{year} - this repository only documents "
            f"{sorted(_CALENDARS)}; guessing a weekday pattern is how paper "
            f"books invent sessions."
        )
    return cal


def _iso(day: dt.date) -> str:
    return day.isoformat()


def is_market_open(day: dt.date) -> bool:
    cal = _calendar(day.year)
    return day.weekday() < 5 and _iso(day) not in cal["market"]


def is_early_close(day: dt.date) -> bool:
    return _iso(day) in _calendar(day.year)["early_close"]


def is_fed_open(day: dt.date) -> bool:
    cal = _calendar(day.year)
    return day.weekday() < 5 and _iso(day) not in cal["fed"]


def next_market_day(day: dt.date) -> dt.date:
    cur = day + dt.timedelta(days=1)
    for _ in range(40):
        if cur.year != day.year:
            raise ValueError(f"CALENDAR_NOT_DOCUMENTED:{cur.year}")
        if is_market_open(cur):
            return cur
        cur += dt.timedelta(days=1)
    raise ValueError("CALENDAR_NOT_DOCUMENTED:40-day search exhausted")


def next_fed_day(day: dt.date) -> dt.date:
    cur = day + dt.timedelta(days=1)
    for _ in range(40):
        if cur.year != day.year:
            raise ValueError(f"CALENDAR_NOT_DOCUMENTED:{cur.year}")
        if is_fed_open(cur):
            return cur
        cur += dt.timedelta(days=1)
    raise ValueError("CALENDAR_NOT_DOCUMENTED:40-day search exhausted")


def settlement_date(trade_date: dt.date) -> dt.date:
    """Regular-way T+1 against the FED calendar, never a weekday guess.

    SOURCE for the T+1 convention: SEC, "New T+1 Settlement Cycle" investor
    bulletin, https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin
    """
    return next_fed_day(trade_date)


def trading_sessions(start: dt.date, end: dt.date) -> List[dt.date]:
    """All documented market sessions in [start, end]; fails closed at edges."""
    out = []
    cur = start
    while cur <= end:
        if is_market_open(cur):
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def session_record(day: dt.date) -> dict:
    """A strict-desk-compatible schedule record for one documented session."""
    if not is_market_open(day):
        raise ValueError(f"MARKET_CLOSED:{_iso(day)}")
    close_ny = "13:00" if is_early_close(day) else "16:00"
    # New York is UTC-4 (EDT) in the 2026 Sep-early Nov window and UTC-5 (EST)
    # after 2026-11-01; compute with zoneinfo instead of hard-coding.
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    hh, mm = (int(x) for x in close_ny.split(":"))
    close_local = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=ny)
    open_local = dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=ny)
    settle = settlement_date(day)
    return {
        "date": _iso(day),
        "open": open_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "close": close_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "early_close": is_early_close(day),
        "settlement_date": _iso(settle),
        "source_url": MARKET_CALENDAR_SOURCE,
        "settlement_source_url": FED_CALENDAR_SOURCE,
        "checked_on": MARKET_CALENDAR_CHECKED,
    }


# --------------------------------------------------------------------------
# Trading halts (official Nasdaq Trader publication, parsed offline)
# --------------------------------------------------------------------------

# The official halts feed is the Nasdaq Trader "Trade Halts - Current" RSS
# document.  It is published by Nasdaq for public consumption; the collector
# stores the raw response with its SHA-256 before anything is parsed.
#   SOURCE: https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts
#   PAGE:   https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts
HALTS_FEED_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
HALTS_PAGE_URL = "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts"

# Fixed column order of the halt rows, verbatim from the feed's own table
# header (fetched 2026-09-19).  The feed publishes this header inside every
# item's description CDATA; we parse the cells in this exact order.
HALT_COLUMNS = (
    "halt_date",
    "halt_time",
    "issue_symbol",
    "issue_name",
    "market",
    "reason_code",
    "pause_threshold_price",
    "resumption_date",
    "resumption_quote_time",
    "resumption_trade_time",
)


def _td_values(html_fragment: str) -> List[str]:
    import re

    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", html_fragment, re.S)
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]


def parse_halts_feed(xml_bytes: bytes) -> List[dict]:
    """Parse the official halts RSS document into rows; never invent a row.

    The document is RSS 2.0: one ``<item>`` per current halt, ``<title>`` the
    issue symbol, ``<pubDate>`` the publication stamp, and a CDATA-embedded
    HTML table in ``<description>`` whose header row names the columns in
    :data:`HALT_COLUMNS` order (verified against the live feed on 2026-09-19).

    Returns ``[]`` for a well-formed empty feed.  Raises ValueError for bytes
    that are not the halts document, so a collector proxy error page cannot be
    silently read as "no halts today".
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"HALTS_FEED_UNREADABLE:{exc}") from exc
    if root.tag.lower() != "rss":
        raise ValueError(f"HALTS_FEED_WRONG_TYPE:{root.tag!r}")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("HALTS_FEED_NO_CHANNEL")
    titles = [ (t.text or "") for t in channel.findall("title")]
    if not any("Trade Halts" in t for t in titles):
        raise ValueError("HALTS_FEED_WRONG_DOCUMENT")
    rows: List[dict] = []
    for item in channel.findall("item"):
        symbol = (item.findtext("title") or "").strip()
        description = item.findtext("description") or ""
        cells = _td_values(description)
        # The first cell(s) may include the header row; find the data row by
        # locating the first cell that parses as a MM/DD/YYYY date.
        import re as _re

        start = next(
            (i for i, c in enumerate(cells) if _re.fullmatch(r"\d{2}/\d{2}/\d{4}", c)),
            None,
        )
        if start is None:
            raise ValueError(f"HALTS_ROW_UNPARSEABLE:{symbol!r}")
        values = cells[start:start + len(HALT_COLUMNS)]
        values += [""] * (len(HALT_COLUMNS) - len(values))
        record = dict(zip(HALT_COLUMNS, values))
        if record["issue_symbol"] and record["issue_symbol"] != symbol:
            raise ValueError(
                f"HALTS_ROW_SYMBOL_MISMATCH:title={symbol!r} row={record['issue_symbol']!r}"
            )
        rows.append(
            {
                "symbol": symbol,
                "issue_name": record["issue_name"],
                "halt_date": record["halt_date"],
                "halt_time": record["halt_time"],
                "resume_time": record["resumption_trade_time"] or None,
                "resume_date": record["resumption_date"] or None,
                "reason_code": record["reason_code"],
                "pause_threshold_price": record["pause_threshold_price"] or None,
                "market": record["market"],
                "pub_date": (item.findtext("pubDate") or "").strip(),
                "source_url": HALTS_FEED_URL,
            }
        )
        if not rows[-1]["symbol"]:
            raise ValueError("HALTS_ROW_MISSING_SYMBOL")
    return rows


def halted_symbols(rows: Iterable[dict], at: Optional[dt.datetime] = None) -> List[str]:
    """Symbols whose halt row has no resume time (still halted)."""
    out = []
    for row in rows:
        if row.get("halt_time") and not row.get("resume_time"):
            out.append(row["symbol"])
    return out


# --------------------------------------------------------------------------
# Corporate actions: rows only exist with a cited primary source
# --------------------------------------------------------------------------

_ACTION_TYPES = {"DIVIDEND", "SPLIT", "MERGER", "DELISTING", "SPINOFF", "SYMBOL_CHANGE"}


@dataclass
class CorporateAction:
    symbol: str
    action_type: str           # one of _ACTION_TYPES
    ex_date: str               # ISO date of the ex-date (or effective date)
    description: str
    source_url: str            # primary-source URL (issuer filing / exchange)
    checked_on: str            # ISO date the URL was read

    def validate(self) -> None:
        if not re_full(self.symbol, r"^[A-Z][A-Z0-9.\-]{0,11}$"):
            raise ValueError(f"CORP_ACTION_SYMBOL:{self.symbol!r}")
        if self.action_type not in _ACTION_TYPES:
            raise ValueError(f"CORP_ACTION_TYPE:{self.action_type!r}")
        dt.date.fromisoformat(self.ex_date)  # raises on malformed
        if not self.source_url.startswith("https://"):
            raise ValueError("CORP_ACTION_SOURCE_REQUIRED")
        dt.date.fromisoformat(self.checked_on)


def re_full(value: str, pattern: str) -> bool:
    import re

    return re.fullmatch(pattern, value) is not None


class CorporateActionTable:
    """An explicit, sourced table.  Absence of a row is NOT an assertion that
    no corporate action exists - :meth:`coverage_note` says what was checked."""

    def __init__(self, rows: Iterable[CorporateAction], coverage_note: str):
        self.rows = []
        for row in rows:
            row.validate()
            self.rows.append(row)
        if not coverage_note.strip():
            raise ValueError("CORP_ACTIONS_COVERAGE_NOTE_REQUIRED")
        self.coverage_note = coverage_note

    def actions_for(self, symbol: str, start: dt.date, end: dt.date) -> List[CorporateAction]:
        out = [
            r
            for r in self.rows
            if r.symbol == symbol and start <= dt.date.fromisoformat(r.ex_date) <= end
        ]
        return sorted(out, key=lambda r: r.ex_date)

    def to_json(self) -> dict:
        return {
            "coverage_note": self.coverage_note,
            "rows": [asdict(r) for r in self.rows],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "CorporateActionTable":
        return cls(
            (CorporateAction(**r) for r in payload.get("rows", [])),
            payload.get("coverage_note", ""),
        )


# --------------------------------------------------------------------------
# Verified regulator fees for an equity sale (re-exports of sim/config)
# --------------------------------------------------------------------------

def regulator_fees(sell_date: str, shares: int, price: float) -> dict:
    """SEC Section 31 + FINRA TAF on a *sale* of covered equity.

    Rates come from sim/config.py's dated schedules, re-verified 2026-09-19:
      SEC §31: $0.00/1e6 to 2026-04-03, $20.60/1e6 from 2026-04-04
        SOURCE: FINRA Information Notice 20260317 and SEC FY2026 Fee Rate
        Advisory, via https://www.finra.org/rules-guidance/notices/information-notice-20260317
      FINRA TAF (2026): $0.000195/share, max $9.79/trade; no fee if the per-
        share execution price is below the rate.  There is NO $0.01 minimum for
        covered equity (that floor applies only to security-futures round
        turns - see IR-72).
        SOURCE: https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule
    """
    from .config import SEC31_PER_MILLION, FINRA_TAF_PER_SHARE, FINRA_TAF_MAX_PER_TRADE, rate_for

    proceeds = shares * price
    sec31 = proceeds * rate_for(SEC31_PER_MILLION, sell_date) / 1_000_000.0
    taf_rate = rate_for(FINRA_TAF_PER_SHARE, sell_date)
    if price < taf_rate:
        taf = 0.0
    else:
        taf = min(shares * taf_rate, rate_for(FINRA_TAF_MAX_PER_TRADE, sell_date))
    return {
        "date": sell_date,
        "shares": shares,
        "price": price,
        "sec31_fee": round(sec31, 6),
        "finra_taf": round(taf, 6),
        "total": round(sec31 + taf, 6),
        "currency": "USD",
        "sources": [
            "https://www.finra.org/rules-guidance/notices/information-notice-20260317",
            "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule",
        ],
    }


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
