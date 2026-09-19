"""Live Season: a forward-tested paper-trading book on verified prices.

WHAT THIS ADDS THAT SEASONS 1 AND 2 DO NOT
------------------------------------------
Season 1 and Season 2 both decide *and* execute inside the same session: a
strategy reads the history strictly before session ``t`` and its orders fill at
session ``t``'s open.  That is already look-ahead free, but it is still not what
a person can actually do, because nobody can place an order at today's opening
print using information that only exists after today's close.

The live book closes that gap.  A participant **plans after the close of session
``T``** and submits *intents* for a **future session** ``T+k``.  The intent is
written to an append-only ledger the moment it is created, with every input the
rule read (series name, observation date, value, file, SHA-256) attached to it.
Nothing about the intent can change afterwards.  When session ``T+k`` finally has
a verified bar, the intent is settled through the same venue model the
competition uses - dated tick grid, quoted spread, displayed depth, market-maker
quotes, square-root impact, participation cap, dated regulatory fees - and the
fill is recorded next to the reference bar it executed against.

So the live book answers the question the brief actually asks: *can these
strategies place upcoming trades and simulate a real trading experience?*  It
also makes the honest measurement available: a rule that looks good when it
trades on the same bar it reads often looks worse when it has to wait a session,
and the difference between the two is a real cost, not a modelling artefact.

TWO WAYS IT RUNS
----------------
``mode="walkforward"``
    Rehearsal.  The book is stepped session by session over a window whose bars
    are already collected, planning at ``T`` and settling at ``T+k``.  Every
    decision still sees only data dated ``<= T``, so this is a genuine
    walk-forward test - it is not the same as a backtest, because the execution
    date is always after the decision date.

``mode="forward"``
    The real thing.  The book is planned from the last session for which a
    verified bar exists and every intent sits ``PENDING`` until that future bar
    is collected.  Nothing is settled, nothing is claimed, and the published
    status says so.  This is the state a live competition is in on day one.

WHAT IS AND IS NOT OFFICIAL HERE
--------------------------------
Signals, the benchmark, the trading calendar and the financing rate come from
official, free, publicly available series (FRED-republished index and rate data,
see :data:`OFFICIAL_SERIES_REGISTER`).  Executable prices for listed securities
still come from the collected Yahoo research files, which are ``SECONDARY``, so
every fill carries its reference bar's ``source_class`` and the book publishes
the share of settled notional that used an ``OFFICIAL`` price.  Until an
eligible official security-price archive exists this book is not an
official-price competition, and it does not claim to be.

No hallucination is possible in the places it matters, because the book refuses
to invent data:

* an intent whose session has no verified bar stays ``WAITING-DATA`` forever
  rather than filling at a modelled price;
* a strategy whose signal file is missing produces no intents and is reported
  ``DATA-MISSING``, never back-filled with a proxy;
* a future session date is a **projection** from Nasdaq's published holiday
  calendar, labelled as such, and is re-checked against the collected series at
  settlement - if the projection was wrong the intent expires with the reason
  recorded.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config, ledger, microstructure, realdata
from .calendar import REPO_ROOT

# --------------------------------------------------------------------------
# Season identity
# --------------------------------------------------------------------------

LIVE_NAME = "StockPaperSim Live Forward Book"
LIVE_SEASON = ("Live Season (opened 2026-09-18) - intents placed for future "
               "sessions, settled only on collected verified bars")
LIVE_SEED = 20260918

#: Last session for which a verified daily bar exists in ``data/real`` at the
#: time the live book was opened.  The forward book plans from here.
#:   VERIFIED: data/real/prices/yahoo/*.json all end at 2026-09-16 (502 bars).
LIVE_LAST_VERIFIED_SESSION = "2026-09-16"

#: Last session for which an official FRED observation exists.  One session
#: later than the equity files, which is itself recorded as an irregularity: the
#: benchmark and the tradable prices are not aligned to the same date.
LIVE_LAST_OFFICIAL_SESSION = "2026-09-17"

#: Default walk-forward rehearsal window: the same 251 verified sessions Season 2
#: competes over, so the two results are directly comparable.
LIVE_REHEARSAL_START = realdata.SEASON2_START
LIVE_REHEARSAL_END = realdata.SEASON2_END


# --------------------------------------------------------------------------
# Official, free, publicly available series
# --------------------------------------------------------------------------
#
# Every row below was retrieved in this project's lifetime from the URL shown.
# ``fred_copyright_tag`` records what FRED's own series page says about reuse;
# where the tag was not read in-session it says so rather than guessing, because
# the tag is exactly what decides whether a committed copy may be redistributed.
# The FRED download CSV is the publication channel; the ``publisher`` column is
# the agency or index provider FRED names as the source of the observations.

OFFICIAL_SERIES_REGISTER: List[dict] = [
    {
        "sid": "SP500",
        "title": "S&P 500 index, daily close",
        "publisher": "S&P Dow Jones Indices LLC",
        "publisher_verified_from": ("FRED series notes, retrieved by search on "
                                    "2026-09-18: 'S&P 500 (SP500) Source: "
                                    "S&P Dow Jones Indices LLC, Release: "
                                    "Standard & Poors'"),
        "release": "Standard & Poors",
        "units": "Index, Not Seasonally Adjusted",
        "frequency": "Daily, Close",
        "url_series": "https://fred.stlouisfed.org/series/SP500",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION (read the series page before reuse)",
        "role": ("trading calendar (blank rows are the authoritative closed-day "
                 "list), benchmark, market factor"),
        "tradable": False,
    },
    {
        "sid": "NASDAQCOM",
        "title": "NASDAQ Composite Index, daily close",
        "publisher": "Nasdaq, Inc.",
        "publisher_verified_from": ("https://fred.stlouisfed.org/series/NASDAQCOM - "
                                    "FRED notes read 2026-09-18: 'Source: Nasdaq, "
                                    "Inc.; Release: Nasdaq Daily Index Data; Units: "
                                    "Index Feb 5, 1971=100; Frequency: Daily, Close; "
                                    "Copyright (c) 2016, NASDAQ OMX Group, Inc'"),
        "release": "Nasdaq Daily Index Data",
        "units": "Index Feb 5, 1971=100, Not Seasonally Adjusted",
        "frequency": "Daily, Close",
        "url_series": "https://fred.stlouisfed.org/series/NASDAQCOM",
        "url_csv": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM"
                    "&cosd=2024-09-16&coed=2026-09-17"),
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": ("Notes carry 'Copyright (c) 2016, NASDAQ OMX Group, "
                               "Inc'; FRED's reuse tag was not read in-session"),
        "role": ("the Nasdaq-market leg the brief asked for: benchmark and trend "
                 "signal for the Nasdaq-listed sleeve (executed through QQQ)"),
        "tradable": False,
    },
    {
        "sid": "DJIA",
        "title": "Dow Jones Industrial Average, daily close",
        "publisher": "S&P Dow Jones Indices LLC",
        "publisher_verified_from": ("https://fred.stlouisfed.org/series/DJIA - FRED "
                                    "notes and tags read 2026-09-18: 'Source: S&P Dow "
                                    "Jones Indices LLC; Release: Dow Jones Averages; "
                                    "Units: Index; Frequency: Daily, Close'; tags "
                                    "include 'Copyrighted: Pre-Approval Required'"),
        "release": "Dow Jones Averages",
        "units": "Index, Not Seasonally Adjusted",
        "frequency": "Daily, Close",
        "url_series": "https://fred.stlouisfed.org/series/DJIA",
        "url_csv": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DJIA"
                    "&cosd=2024-09-16&coed=2026-09-17"),
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "Copyrighted: Pre-Approval Required",
        "role": ("the NYSE-listed blue-chip leg: benchmark and relative-strength "
                 "signal against the Nasdaq leg (executed through SPY/QQQ)"),
        "tradable": False,
    },
    {
        "sid": "VIXCLS",
        "title": "CBOE Volatility Index: VIX, daily close",
        "publisher": ("Cboe Global Markets (the index administrator); the "
                      "observation is republished by FRED"),
        "publisher_verified_from": ("FRED release table lists 'CBOE Volatility Index: "
                                    "VIX' beside the S&P 500 and NASDAQ Composite "
                                    "release tables (read 2026-09-18)"),
        "release": "CBOE Volatility Index",
        "units": "Index, Not Seasonally Adjusted",
        "frequency": "Daily, Close",
        "url_series": "https://fred.stlouisfed.org/series/VIXCLS",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION (read the series page before reuse)",
        "role": "volatility regime for the venue model and for regime-timed intents",
        "tradable": False,
    },
    {
        "sid": "SOFR",
        "title": "Secured Overnight Financing Rate - broad general collateral rate",
        "publisher": "Federal Reserve Bank of New York",
        "publisher_verified_from": ("cross-checked in-session against the New York "
                                    "Fed's own reference-rate API (see "
                                    "AUXILIARY_OFFICIAL_ENDPOINTS row "
                                    "nyfed_sofr_api), which returned percentRate 3.85 "
                                    "for effectiveDate 2026-09-17 and 3.62 for "
                                    "2026-09-16 - the same two values FRED prints"),
        "release": "Reference Rates",
        "units": "Percent, Not Seasonally Adjusted",
        "frequency": "Daily",
        "url_series": "https://fred.stlouisfed.org/series/SOFR",
        "url_csv": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR"
                    "&cosd=2024-09-16&coed=2026-09-17"),
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL",
        "fred_copyright_tag": ("not read in-session; the Federal Reserve Bank of New "
                               "York publishes SOFR as a public reference rate"),
        "role": ("financing curve: interest credited on idle cash and charged on a "
                 "margin debit, replacing Season 1's zero risk-free rate"),
        "tradable": False,
    },
    {
        "sid": "DGS10",
        "title": "10-year Treasury constant-maturity yield",
        "publisher": "U.S. Department of the Treasury",
        "publisher_verified_from": ("already registered as OFFICIAL in Season 2 "
                                    "(sim/realdata.py COLLECTED_SOURCES row "
                                    "'fred_series')"),
        "release": "Daily Treasury Par Yield Curve Rates",
        "units": "Percent, Not Seasonally Adjusted",
        "frequency": "Daily",
        "url_series": "https://fred.stlouisfed.org/series/DGS10",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION",
        "role": "long-rate signal for the duration and curve-slope participants",
        "tradable": False,
    },
    {
        "sid": "DGS3MO",
        "title": "3-month Treasury constant-maturity yield",
        "publisher": "U.S. Department of the Treasury",
        "publisher_verified_from": "as DGS10",
        "release": "Daily Treasury Par Yield Curve Rates",
        "units": "Percent, Not Seasonally Adjusted",
        "frequency": "Daily",
        "url_series": "https://fred.stlouisfed.org/series/DGS3MO",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION",
        "role": "short-rate leg of the curve-slope signal",
        "tradable": False,
    },
    {
        "sid": "DCOILWTICO",
        "title": "Cushing, OK WTI crude oil spot price",
        "publisher": "U.S. Energy Information Administration",
        "publisher_verified_from": "as DGS10 (registered OFFICIAL in Season 2)",
        "release": "Spot Prices",
        "units": "US Dollars per Barrel, Not Seasonally Adjusted",
        "frequency": "Daily",
        "url_series": "https://fred.stlouisfed.org/series/DCOILWTICO",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION",
        "role": "energy signal for the oil/dollar participant (executed through UNG, XOM)",
        "tradable": False,
    },
    {
        "sid": "DTWEXBGS",
        "title": "Nominal broad US dollar index (goods and services)",
        "publisher": "Board of Governors of the Federal Reserve System",
        "publisher_verified_from": "as DGS10 (registered OFFICIAL in Season 2)",
        "release": "H.10 Foreign Exchange Rates",
        "units": "Index January 2006=100, Not Seasonally Adjusted",
        "frequency": "Daily",
        "url_series": "https://fred.stlouisfed.org/series/DTWEXBGS",
        "url_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS",
        "channel": "Federal Reserve Bank of St. Louis (FRED)",
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "fred_copyright_tag": "NOT-READ-IN-SESSION",
        "role": "dollar-strength signal, the standard inverse driver of commodity exposure",
        "tradable": False,
    },
]


#: Official endpoints the live book cites that are *not* FRED series downloads.
#: They are registered here for the same reason the series are: a reader must be
#: able to follow every claim this module makes back to the publisher's own page.
AUXILIARY_OFFICIAL_ENDPOINTS: List[dict] = [
    {
        "id": "nyfed_sofr_api",
        "label": "Federal Reserve Bank of New York reference-rate API (SOFR)",
        "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/10.json",
        "source_class": "OFFICIAL",
        "role": ("independent second publisher for the SOFR observations the "
                 "financing curve accrues on; the collected FRED file and this API "
                 "agree on both cross-checked dates"),
        "retrieved_utc": "2026-09-18T20:41:00Z",
        "note": ("Retrieved in-session. Returned percentRate 3.85 for 2026-09-17 and "
                 "3.62 for 2026-09-16, matching the collected FRED SOFR file line for "
                 "line on those two dates."),
    },
    {
        "id": "nasdaq_holiday_calendar",
        "label": "Nasdaq Trader - U.S. Equity and Options Markets Holiday Schedule",
        "url": "https://www.nasdaqtrader.com/trader.aspx?id=calendar",
        "source_class": "OFFICIAL",
        "role": ("the forward session projection: sessions after the last collected "
                 "date are projected from this published calendar and labelled "
                 "PROJECTED, never asserted as observations"),
        "retrieved_utc": "2026-09-18T21:05:00Z",
        "note": ("Read in-session. For the rest of 2026 it lists 2026-11-26 and "
                 "2026-12-25 as closures and 2026-11-27 and 2026-12-24 as 1:00 p.m. "
                 "early closes; no 2027 dates are projected because the 2027 schedule "
                 "was not read from the publisher."),
    },
    {
        "id": "nasdaq_holiday_calendar_consumer",
        "label": "Nasdaq - 2026 U.S. stock market holiday schedule (consumer page)",
        "url": "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule",
        "source_class": "OFFICIAL",
        "role": "second publication of the same schedule, used to cross-check the Trader page",
        "retrieved_utc": "2026-09-18T21:05:00Z",
        "note": "Read in-session by search; it agrees with the Trader page row for row.",
    },
]


@dataclass
class OfficialSeries:
    """One official series as loaded from disk, with its provenance attached."""

    sid: str
    title: str
    publisher: str
    source_class: str
    role: str
    tradable: bool
    file: str
    sha256: str
    url_series: str
    url_csv: str
    fred_copyright_tag: str
    dates: List[str]
    values: List[float]

    def last_date(self) -> str:
        return self.dates[-1] if self.dates else ""

    def latest(self) -> Optional[Tuple[str, float]]:
        return (self.dates[-1], self.values[-1]) if self.dates else None

    def truncate(self, as_of: str) -> "OfficialSeries":
        """Copy holding only observations dated ``<= as_of`` (no look-ahead)."""
        keep = [(d, v) for d, v in zip(self.dates, self.values) if d <= as_of]
        return OfficialSeries(
            sid=self.sid, title=self.title, publisher=self.publisher,
            source_class=self.source_class, role=self.role, tradable=self.tradable,
            file=self.file, sha256=self.sha256, url_series=self.url_series,
            url_csv=self.url_csv, fred_copyright_tag=self.fred_copyright_tag,
            dates=[d for d, _ in keep], values=[v for _, v in keep])

    def window(self, n: int) -> List[float]:
        return self.values[-n:] if n > 0 else list(self.values)

    def pct_change(self, n: int) -> Optional[float]:
        """Percentage change over the last ``n`` observations (``n>=1``)."""
        if n < 1 or len(self.values) <= n:
            return None
        older = self.values[-n - 1]
        if not older:
            return None
        return 100.0 * (self.values[-1] / older - 1.0)

    def sma(self, n: int) -> Optional[float]:
        window = self.window(n)
        return sum(window) / len(window) if window else None

    def as_dict(self) -> dict:
        row = asdict(self)
        row["observations"] = len(self.dates)
        row["first_date"] = self.dates[0] if self.dates else None
        row["last_date"] = self.last_date()
        row.pop("dates", None)
        row.pop("values", None)
        return row


class OfficialFeed:
    """The official series this book may read, loaded once and truncated per plan."""

    def __init__(self, root: str = realdata.REAL_ROOT,
                 wanted: Optional[Sequence[str]] = None) -> None:
        self.root = root
        self.register = {r["sid"]: dict(r) for r in OFFICIAL_SERIES_REGISTER}
        self.series: Dict[str, OfficialSeries] = {}
        self.missing: List[dict] = []
        for row in OFFICIAL_SERIES_REGISTER:
            sid = row["sid"]
            if wanted and sid not in wanted:
                continue
            try:
                values, path, sha = realdata.load_fred(sid, root)
            except realdata.RealDataUnavailable as exc:
                self.missing.append({"sid": sid, "reason": str(exc),
                                     "url_series": row["url_series"]})
                continue
            dates = sorted(values)
            self.series[sid] = OfficialSeries(
                sid=sid, title=row["title"], publisher=row["publisher"],
                source_class=row["source_class"], role=row["role"],
                tradable=row["tradable"], file=path, sha256=sha,
                url_series=row["url_series"], url_csv=row["url_csv"],
                fred_copyright_tag=row["fred_copyright_tag"],
                dates=dates, values=[values[d] for d in dates])

    def get(self, sid: str) -> Optional[OfficialSeries]:
        return self.series.get(sid)

    def truncated(self, as_of: str) -> Dict[str, OfficialSeries]:
        return {sid: s.truncate(as_of) for sid, s in self.series.items()}

    def provenance(self) -> List[dict]:
        """One row per loaded series: the register metadata plus what is on disk.

        The register carries the human-facing facts (release, units, frequency,
        the reuse tag) and the loader carries the facts that can only be known
        after reading the file (observation count, first and last date, SHA-256).
        Merging them here means the published sources page never has to join two
        tables by hand - and a field that is missing shows up as missing.
        """
        rows: List[dict] = []
        for sid in sorted(self.series):
            row = dict(self.register.get(sid, {}))
            row.update(self.series[sid].as_dict())
            rows.append(row)
        return rows


# --------------------------------------------------------------------------
# Forward calendar projection
# --------------------------------------------------------------------------
#
# The collected official series end at 2026-09-17, so the sessions after that
# date are not observations.  They are projected from Nasdaq's own published
# trading calendar and labelled as a projection everywhere they appear.  A
# projected session that later turns out to be closed is not silently skipped:
# the intent that targeted it expires with the reason recorded.
#
#   SOURCE (verified 2026-09-18, Nasdaq Trader's published equity/options
#   holiday schedule): https://www.nasdaqtrader.com/trader.aspx?id=calendar
#     2026-11-26 Thanksgiving Day            Closed
#     2026-11-27 Early Close                 1:00 p.m.
#     2026-12-24 Early Close                 1:00 p.m.
#     2026-12-25 Christmas Holiday           Closed
#   SOURCE (same schedule, consumer page):
#     https://www.nasdaq.com/market-activity/stock-market-holiday-schedule
#
# No projection is made past 2026-12-31, because Nasdaq's 2026 calendar is the
# last one read from the publisher in this session and guessing 2027 dates would
# be exactly the invention this module refuses to do.

PROJECTED_CLOSURES = frozenset({"2026-11-26", "2026-12-25"})
PROJECTED_EARLY_CLOSES = frozenset({"2026-11-27", "2026-12-24"})
PROJECTION_LIMIT = "2026-12-31"
PROJECTION_SOURCE = "https://www.nasdaqtrader.com/trader.aspx?id=calendar"


def project_sessions(after: str, count: int) -> List[dict]:
    """The next ``count`` projected sessions strictly after ``after``.

    Returns rows rather than bare dates so the projection status travels with
    the date it applies to.
    """
    day = dt.date.fromisoformat(after)
    out: List[dict] = []
    while len(out) < count:
        day = day + dt.timedelta(days=1)
        iso = day.isoformat()
        if iso > PROJECTION_LIMIT:
            break
        if day.weekday() >= 5:
            continue
        if iso in PROJECTED_CLOSURES:
            continue
        out.append({"date": iso,
                    "status": "PROJECTED",
                    "early_close": iso in PROJECTED_EARLY_CLOSES,
                    "source": PROJECTION_SOURCE})
    return out


# --------------------------------------------------------------------------
# Intents
# --------------------------------------------------------------------------

INTENT_PENDING = "PENDING"
INTENT_WAITING_DATA = "WAITING-DATA"
INTENT_FILLED = "FILLED"
INTENT_PARTIAL = "PARTIAL"
INTENT_REJECTED = "REJECTED"
INTENT_EXPIRED = "EXPIRED"
INTENT_CANCELLED = "CANCELLED"

TERMINAL_INTENT_STATES = frozenset({INTENT_FILLED, INTENT_PARTIAL, INTENT_REJECTED,
                                    INTENT_EXPIRED, INTENT_CANCELLED})


def bar_is_executable(md, symbol: str, session: str) -> Tuple[bool, str]:
    """Can a fill cite a *published* price for ``symbol`` on ``session``?

    Three states are possible and only two of them may carry a fill:

    ``native``
        The collected price file carries a bar for that session (the historical
        window).  Executable.
    ``print``
        ``sim.realdata.apply_forward_prints`` overlaid a row from the print
        ledger, which cites its publisher, URL and checksum.  Executable.
    ``carried``
        Neither: the market holds a previous close with zero volume so the arrays
        line up.  **Not** executable - and the caller is told exactly why, so the
        intent can be published as waiting instead of being filled at a price
        nobody printed.
    """
    provenance = (getattr(md, "bar_provenance", {}) or {}).get((symbol, session))
    if provenance:
        return True, ""
    meta = md.series_meta.get(symbol)
    if meta is not None and session in meta.by_date():
        return True, ""
    gaps = (getattr(md, "gaps", {}) or {}).get(symbol) or []
    if session in gaps:
        return False, (
            f"no publisher printed {symbol} on {session}: the market carries the "
            f"previous session's close forward at zero volume, and a fill may not "
            f"cite a price that was never printed")
    return False, (f"no collected bar for {symbol} on {session}: the intent waits "
                   f"for a published print rather than filling at a modelled price")


@dataclass
class Intent:
    """One upcoming trade, written the moment it is created and never edited.

    ``evidence`` is the no-look-ahead record: every series the rule read, the
    date of the observation it used, the value, the file and the file's SHA-256.
    :func:`verify_live` fails the run if any evidence date is later than
    ``created_on``, or if ``intended_session`` is not strictly after it.
    """

    intent_id: str
    participant: str
    created_on: str
    intended_session: str
    session_status: str              # COLLECTED | PROJECTED
    symbol: str
    side: str                        # buy | sell
    quantity: int
    order_type: str = "market"
    limit_price: Optional[float] = None
    at_close: bool = False
    rationale: str = ""
    rule: str = ""
    evidence: Dict[str, dict] = field(default_factory=dict)
    sizing: Dict[str, object] = field(default_factory=dict)
    status: str = INTENT_PENDING
    settled_on: Optional[str] = None
    settle_note: str = ""
    fill_id: Optional[str] = None

    def to_row(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

class LiveAccount:
    """Cash, positions and the fills that produced them, for one participant.

    Deliberately simpler than :class:`sim.portfolio.Account`: the live book has
    one decision point per session and no intraday margin sequencing, so a flat
    Reg T test at intent-creation time plus a house maintenance test at mark time
    is the whole story.  Both thresholds come from ``config.MarginConfig`` and
    both cite the same rules the competition engine cites.
    """

    def __init__(self, participant: str, starting_cash: float) -> None:
        self.participant = participant
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.positions: Dict[str, int] = {}
        self.fills: List[dict] = []
        self.intents: List[Intent] = []
        self.carry_received = 0.0
        self.carry_paid = 0.0
        self.carry_rows: List[dict] = []
        self.marks: List[dict] = []
        self.margin_events: List[dict] = []
        #: Set when a mark breaches the maintenance floor; cleared by the
        #: forced liquidation that answers the call at the next session.
        self.liquidation_due: bool = False
        #: True once a liquidation left equity below zero - the account is wiped
        #: and can no longer open anything, because it has no buying power.
        self.wiped: bool = False

    # -- trading ----------------------------------------------------------
    def apply_fill(self, row: dict) -> None:
        qty = int(row.get("filled_qty") or 0)
        if qty <= 0:
            self.fills.append(row)
            return
        signed = qty if row["side"] == "buy" else -qty
        price = float(row["avg_price"])
        fees = (float(row.get("commission") or 0.0) + float(row.get("exchange_fee") or 0.0)
                + float(row.get("regulatory_fee") or 0.0) - float(row.get("rebate") or 0.0))
        self.cash -= signed * price
        self.cash -= fees
        self.positions[row["symbol"]] = self.positions.get(row["symbol"], 0) + signed
        if self.positions[row["symbol"]] == 0:
            self.positions.pop(row["symbol"], None)
        self.fills.append(row)

    def gross_exposure(self, prices: Dict[str, float]) -> float:
        return sum(abs(q) * float(prices.get(s, 0.0)) for s, q in self.positions.items())

    def long_value(self, prices: Dict[str, float]) -> float:
        return sum(q * float(prices.get(s, 0.0)) for s, q in self.positions.items() if q > 0)

    def equity(self, prices: Dict[str, float]) -> float:
        return self.cash + sum(q * float(prices.get(s, 0.0))
                               for s, q in self.positions.items())

    def buying_power(self, prices: Dict[str, float],
                     margin: config.MarginConfig) -> float:
        """Cash plus the Reg T loan value of long positions.

        SOURCE (Reg T initial margin 50%): https://www.federalreserve.gov/supervisionreg/regtcg.htm
        SOURCE (FINRA 4210 maintenance): https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210
        """
        return self.cash + margin.initial_margin * max(0.0, self.long_value(prices))

    def maintenance_breach(self, prices: Dict[str, float],
                           margin: config.MarginConfig) -> Optional[dict]:
        equity = self.equity(prices)
        gross = self.gross_exposure(prices)
        if gross <= 0.0:
            return None
        # A negative equity value is the deepest possible breach, not an
        # exemption from the rule.  (An earlier version returned None for
        # equity <= 0, which let a wiped account keep marking a negative balance
        # with no call - caught by the CrowdFade participant printing -118%.)
        required = margin.maintenance_margin * gross
        if equity < required:
            return {"equity": round(equity, 2), "gross_exposure": round(gross, 2),
                    "required": round(required, 2),
                    "shortfall": round(required - equity, 2)}
        return None


# --------------------------------------------------------------------------
# The planning context a strategy is allowed to see
# --------------------------------------------------------------------------

class LiveContext:
    """Read-only view of everything known at ``as_of``, plus ``place``.

    Two rules are enforced here rather than documented:

    * nothing dated after ``as_of`` is reachable - the official series are
      truncated, and every market accessor is bounded by the as-of index;
    * an intent must target a session strictly after ``as_of``.  A strategy that
      tries to trade today's bar raises, because that is a backtest, not a
      forward test.
    """

    def __init__(self, md, official: Dict[str, OfficialSeries], as_of: str,
                 account: LiveAccount, horizon_sessions: Sequence[dict],
                 margin: config.MarginConfig, book: "LiveBook") -> None:
        self.md = md
        self.official = official
        self.as_of = as_of
        self.account = account
        self.margin = margin
        self._book = book
        self._t = md.dates.index(as_of) if as_of in md.dates else len(md.dates) - 1
        self._prices = {s: md.bar(s, self._t).close for s in md.symbols}
        self._horizon = list(horizon_sessions)
        self.placed: List[Intent] = []
        self.rejected_placements: List[dict] = []

    # -- market data, bounded by as_of ------------------------------------
    @property
    def t(self) -> int:
        return self._t

    def close(self, symbol: str) -> Optional[float]:
        """The close of session ``as_of`` - the last executable price known."""
        if symbol not in self.md.instruments:
            return None
        return self._prices.get(symbol)

    def closes(self, symbol: str, n: int) -> List[float]:
        """Up to ``n`` closes ending at ``as_of`` (inclusive)."""
        hist = self.md.history_closes(symbol, self._t + 1, n)
        return list(hist)

    def returns(self, symbol: str, n: int) -> List[float]:
        return list(self.md.history_returns(symbol, self._t + 1, n))

    def adv(self, symbol: str) -> float:
        return float(self.md.adv(symbol, self._t + 1))

    def sigma_daily(self, symbol: str) -> float:
        return float(self.md.realised_sigma_daily(symbol, self._t + 1))

    def session_volume(self, symbol: str) -> int:
        return int(self.md.bar(symbol, self._t).volume)

    # -- official series ---------------------------------------------------
    def series(self, sid: str) -> Optional[OfficialSeries]:
        return self.official.get(sid)

    def evidence_for(self, sid: str) -> Optional[dict]:
        """Provenance row for an official series, for the intent's evidence."""
        s = self.official.get(sid)
        if s is None or not s.dates:
            return None
        return {"series": sid, "observation_date": s.dates[-1],
                "value": s.values[-1], "file": s.file, "sha256": s.sha256,
                "source_class": s.source_class, "url": s.url_series,
                "publisher": s.publisher}

    def evidence_for_symbol(self, symbol: str) -> dict:
        """Provenance row for the instrument's own collected price series."""
        meta = self.md.series_meta.get(symbol)
        bar = self.md.bar(symbol, self._t)
        row = {"series": f"{symbol} daily bar", "observation_date": bar.date,
               "value": bar.close, "source_class": getattr(meta, "source_class", "UNKNOWN"),
               "file": getattr(meta, "path", ""), "sha256": getattr(meta, "sha256", ""),
               "url": getattr(meta, "url", "")}
        return row

    # -- MasterSite signal book (collected event files) --------------------
    def signal(self, name: str) -> float:
        """Value of a collected signal array at ``as_of`` (0.0 if absent)."""
        book = getattr(self.md, "signals", None)
        if book is None:
            return 0.0
        return float(book.value(name, self._t))

    def signal_available(self, name: str) -> bool:
        book = getattr(self.md, "signals", None)
        return bool(book is not None and book.available(name))

    def signal_history(self, name: str, n: int) -> List[float]:
        book = getattr(self.md, "signals", None)
        if book is None:
            return []
        series = book.series(name)
        start = max(0, self._t + 1 - n)
        return [float(v) for v in series[start:self._t + 1]]

    def signal_by_symbol(self, name: str, symbol: str) -> Optional[float]:
        book = getattr(self.md, "signals", None)
        if book is None:
            return None
        array = book.by_symbol(name).get(symbol)
        if not array or self._t >= len(array):
            return None
        return float(array[self._t])

    def signal_evidence(self, name: str) -> Optional[dict]:
        """Provenance row for a collected signal array, for the intent evidence."""
        book = getattr(self.md, "signals", None)
        if book is None:
            return None
        info = book.availability.get(name) or {}
        if not info:
            return None
        window = info.get("window") or [None, None]
        return {"series": f"signal:{name}", "observation_date": self.as_of,
                "value": self.signal(name), "file": "; ".join(info.get("files") or []),
                "source_class": info.get("state", "UNKNOWN"), "url": info.get("url", ""),
                "collection_window": window, "note": info.get("note", "")}

    # -- account state -----------------------------------------------------
    @property
    def cash(self) -> float:
        return self.account.cash

    @property
    def equity(self) -> float:
        return self.account.equity(self._prices)

    def position(self, symbol: str) -> int:
        return int(self.account.positions.get(symbol, 0))

    def pending(self, symbol: Optional[str] = None) -> List[Intent]:
        return [i for i in self.account.intents
                if i.status in (INTENT_PENDING, INTENT_WAITING_DATA)
                and (symbol is None or i.symbol == symbol)]

    # -- the only way to trade ---------------------------------------------
    def next_session(self, offset: int = 1) -> Optional[dict]:
        """The ``offset``-th session after ``as_of`` (1 = the next one)."""
        if offset < 1 or offset > len(self._horizon):
            return None
        return self._horizon[offset - 1]

    def place(self, symbol: str, side: str, *, quantity: Optional[int] = None,
              equity_fraction: Optional[float] = None,
              order_type: str = "market", limit_price: Optional[float] = None,
              at_close: bool = False, session_offset: int = 1,
              rationale: str = "", rule: str = "",
              evidence: Optional[Dict[str, dict]] = None,
              round_lot: bool = True) -> Optional[Intent]:
        """Submit one upcoming trade.  Returns the intent, or None if refused.

        ``quantity`` and ``equity_fraction`` are alternatives: a fraction is
        resolved to whole shares **here**, at plan time, from the equity and the
        reference price known at ``as_of``.  Settlement therefore never has to
        decide how big an order was, which is what keeps it deterministic.
        """
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"bad side {side!r}")
        if symbol not in self.md.instruments:
            self.rejected_placements.append(
                {"symbol": symbol, "reason": "not in the verified tradable universe"})
            return None
        target = self.next_session(session_offset)
        if target is None:
            self.rejected_placements.append(
                {"symbol": symbol, "reason": f"no projected session at offset "
                                             f"{session_offset} from {self.as_of}"})
            return None
        if target["date"] <= self.as_of:
            raise ValueError(
                f"a forward intent must target a session after {self.as_of}, "
                f"got {target['date']} - that would be a backtest, not a forward test")
        ref_price = float(self.close(symbol) or 0.0)
        if ref_price <= 0:
            self.rejected_placements.append(
                {"symbol": symbol, "reason": "no verified close at the plan date"})
            return None

        sizing: Dict[str, object] = {"reference_price": round(ref_price, 6),
                                     "reference_date": self.as_of,
                                     "reference_source_class":
                                         self.evidence_for_symbol(symbol)["source_class"]}
        if quantity is None and equity_fraction is not None:
            if side == "buy":
                budget = self.account.buying_power(self._prices, self.margin)
            else:
                budget = max(0.0, self.account.cash) + \
                    self.margin.initial_margin * self.account.gross_exposure(self._prices)
            budget *= float(equity_fraction)
            raw = int(budget // ref_price)
            lot = config.round_lot(ref_price)
            quantity = (raw // lot) * lot if round_lot and lot > 1 else raw
            sizing.update({"rule": f"equity_fraction={equity_fraction}",
                           "budget_usd": round(budget, 2),
                           "round_lot": lot, "raw_shares": raw})
        elif quantity is None:
            raise ValueError("place() needs quantity= or equity_fraction=")
        sizing.update({"quantity": int(quantity)})
        if int(quantity) <= 0:
            self.rejected_placements.append(
                {"symbol": symbol, "reason": "resolved quantity is zero "
                                             f"(budget {sizing.get('budget_usd')}, "
                                             f"reference price {ref_price})"})
            return None

        # -- Reg T / FINRA 4210 outer bound, applied at plan time -------------
        # The room available to an order is computed exactly, in shares:
        #   * shares that CLOSE an existing position free gross room and are
        #     never trimmed by the gross cap;
        #   * shares that OPEN or INCREASE one need room under the house gross
        #     bound, and a purchase additionally needs Reg T buying power.
        # Trimming rather than refusing keeps the intent honest about what a real
        # margin account would have accepted, and both the intended and the
        # allowed size are recorded in the sizing block.
        #   SOURCE (Reg T initial margin 50%):
        #     https://www.federalreserve.gov/supervisionreg/regtcg.htm
        #   SOURCE (FINRA 4210 house maintenance / leverage):
        #     https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210
        position_before = self.position(symbol)
        closing_shares = (max(0, position_before) if side == "sell"
                          else max(0, -position_before))
        gross_before = self.account.gross_exposure(self._prices)
        allowed_gross = self.margin.max_gross_leverage * max(0.0, self.equity)
        room_notional = max(0.0, allowed_gross - gross_before) + \
            closing_shares * ref_price
        room_shares = closing_shares + int(room_notional // ref_price)
        allowed_qty = min(int(quantity), max(0, room_shares))
        if side == "buy":
            bp = max(0.0, self.account.buying_power(self._prices, self.margin))
            allowed_qty = min(allowed_qty, int(bp // ref_price))
        if allowed_qty < int(quantity):
            lot = config.round_lot(ref_price)
            if lot > 1:
                allowed_qty = (allowed_qty // lot) * lot
            sizing["trimmed_by_margin"] = {
                "requested_quantity": int(quantity),
                "allowed_quantity": int(allowed_qty),
                "closing_shares": int(closing_shares),
                "gross_exposure_usd": round(gross_before, 2),
                "house_gross_limit_usd": round(allowed_gross, 2),
                "max_gross_leverage": self.margin.max_gross_leverage,
                "buying_power_usd": round(
                    self.account.buying_power(self._prices, self.margin), 2),
            }
            if allowed_qty <= 0:
                self.rejected_placements.append({
                    "symbol": symbol, "quantity": int(quantity),
                    "reason": f"no margin room (gross {gross_before:,.2f} USD against a "
                              f"{self.margin.max_gross_leverage}x limit on equity "
                              f"{self.equity:,.2f} USD; buying power "
                              f"{self.account.buying_power(self._prices, self.margin):,.2f} "
                              f"USD)"})
                return None
            quantity = int(allowed_qty)
            sizing["quantity"] = int(quantity)

        adv = self.adv(symbol)
        cap = self._book.cfg.liquidity.max_participation * adv
        if int(quantity) > cap > 0:
            self.rejected_placements.append(
                {"symbol": symbol, "quantity": int(quantity),
                 "reason": f"exceeds the participation cap ({cap:,.0f} shares = "
                           f"{self._book.cfg.liquidity.max_participation:.0%} of "
                           f"{adv:,.0f} ADV); trimmed to the cap instead of submitted",
                 "trimmed_to": int(cap)})
            quantity = int(cap)
            if quantity <= 0:
                return None
            sizing["quantity"] = int(quantity)
            sizing["trimmed_by_participation_cap"] = True

        ev = dict(evidence or {})
        ev.setdefault(f"{symbol}_price", self.evidence_for_symbol(symbol))
        intent = Intent(
            intent_id=self._book.next_intent_id(self.account.participant),
            participant=self.account.participant,
            created_on=self.as_of,
            intended_session=target["date"],
            session_status=target["status"],
            symbol=symbol, side=side, quantity=int(quantity),
            order_type=order_type, limit_price=limit_price, at_close=at_close,
            rationale=rationale, rule=rule, evidence=ev, sizing=sizing)
        self.account.intents.append(intent)
        self.placed.append(intent)
        self._book.intents.append(intent)
        return intent


# --------------------------------------------------------------------------
# The book
# --------------------------------------------------------------------------

class LiveBook:
    """Plan, settle and mark a live forward book.

    ``md`` is the collected market (any backend the eligibility rules allow);
    ``official`` is the truncated-per-plan view of the official series.  The book
    never mutates either.
    """

    def __init__(self, md, official: OfficialFeed, roster: Sequence[object],
                 cfg: Optional[config.CompetitionConfig] = None,
                 seed: int = LIVE_SEED, mode: str = "walkforward") -> None:
        self.md = md
        self.official_feed = official
        self.roster = list(roster)
        self.mode = mode
        self.seed = int(seed)
        self.cfg = cfg or config.CompetitionConfig(
            name=LIVE_NAME, season=LIVE_SEASON, seed=seed, scenarios=1,
            rank_metric="total_return_pct")
        self.cfg_name_space: Sequence[str] = ()
        self.engine = microstructure.ExecutionEngine(self.cfg)
        self.accounts: Dict[str, LiveAccount] = {
            s.username: LiveAccount(s.username, self.cfg.starting_cash)
            for s in self.roster}
        self.intents: List[Intent] = []
        self.sessions_planned: List[str] = []
        self.sessions_settled: List[str] = []
        self.irregularities: List[dict] = []
        self._counters: Dict[str, int] = {}

    # -- identifiers -------------------------------------------------------
    def next_intent_id(self, participant: str) -> str:
        key = participant.lstrip("@")
        self._counters[key] = self._counters.get(key, 0) + 1
        return f"{key}-{self._counters[key]:05d}"

    # -- planning ----------------------------------------------------------
    def plan(self, as_of: str, horizon: int = 1) -> List[Intent]:
        """Every participant plans for the next ``horizon`` session(s)."""
        if as_of not in self.md.dates:
            raise ValueError(f"{as_of} is not a session in the collected market data")
        projected = self._horizon_sessions(as_of, horizon)
        official = self.official_feed.truncated(as_of)
        created: List[Intent] = []
        for strategy in self.roster:
            account = self.accounts[strategy.username]
            ctx = LiveContext(self.md, official, as_of, account, projected,
                              self.cfg.margin, self)
            before = len(account.intents)
            try:
                strategy.plan(ctx)
            except Exception as exc:            # never let one strategy stop the book
                self.irregularities.append({
                    "code": "PLAN-ERROR", "severity": "high",
                    "participant": strategy.username, "session": as_of,
                    "detail": f"{type(exc).__name__}: {exc}"})
                continue
            new = account.intents[before:]
            created.extend(new)
            self._supersede_duplicates(account, new)
            for intent in new:
                self._check_evidence(intent)
        self.sessions_planned.append(as_of)
        return created

    def _horizon_sessions(self, as_of: str, horizon: int) -> List[dict]:
        """The next ``horizon`` sessions after ``as_of``, with their status.

        Inside the collected window the session list is an **observation**: the
        FRED S&P 500 observation dates carry blank rows on every market holiday,
        and those blanks are the authoritative closed-day list.  Only past the
        last collected date does the list become a **projection** from Nasdaq's
        published holiday schedule, and it carries that label.

        This function exists because an earlier version projected weekdays for
        every horizon and therefore scheduled trades on 2025-12-25, 2026-01-01,
        2026-04-03 and six other sessions on which the market was closed.  Those
        intents could never settle and showed up as permanent PENDING rows - a
        defect the live book's own status column made visible.
        """
        calendar = getattr(self.md, "calendar", None)
        ahead = [d for d in self.md.dates if d > as_of][:horizon]
        rows: List[dict] = []
        for date in ahead:
            early = False
            if calendar is not None:
                session = next((s for s in calendar.sessions if s.date == date), None)
                early = bool(session and session.is_early_close)
            rows.append({
                "date": date, "status": "COLLECTED", "early_close": early,
                "source": self.md.diagnostics.get("calendar_source", {}).get(
                    "file", "collected official session list")})
        missing = horizon - len(rows)
        if missing > 0:
            last = self.md.dates[-1] if self.md.dates else as_of
            rows.extend(project_sessions(last, missing))
        return rows

    def is_collected_closure(self, session: str) -> bool:
        """True when the official calendar says this date was a market holiday."""
        calendar = getattr(self.md, "calendar", None)
        if calendar is None:
            return False
        if session in getattr(calendar, "closed_dates", ()):
            return True
        if self.md.dates and self.md.dates[0] <= session <= self.md.dates[-1]:
            return session not in self.md.dates
        return False

    def _supersede_duplicates(self, account: LiveAccount, new: List[Intent]) -> None:
        """One live order per (participant, symbol, session): the newest wins."""
        latest: Dict[Tuple[str, str], Intent] = {}
        for intent in new:
            latest[(intent.symbol, intent.intended_session)] = intent
        for intent in account.intents:
            if intent.status not in (INTENT_PENDING, INTENT_WAITING_DATA):
                continue
            winner = latest.get((intent.symbol, intent.intended_session))
            if winner is not None and winner is not intent:
                intent.status = INTENT_CANCELLED
                intent.settle_note = "superseded by a newer intent for the same session"

    def _check_evidence(self, intent: Intent) -> None:
        """Raise the no-look-ahead alarm at creation, not at audit time."""
        for name, ev in intent.evidence.items():
            date = str(ev.get("observation_date") or "")
            if date and date > intent.created_on:
                self.irregularities.append({
                    "code": "LOOK-AHEAD", "severity": "critical",
                    "participant": intent.participant, "session": intent.created_on,
                    "detail": (f"intent {intent.intent_id} cites {name} dated {date}, "
                               f"after the plan date {intent.created_on}")})

    # -- settlement --------------------------------------------------------
    def settle(self, session: str) -> dict:
        """Settle every live intent whose target session is ``session``."""
        summary = {"session": session, "settled": 0, "waiting": 0, "filled": 0,
                   "partial": 0, "rejected": 0, "expired": 0, "liquidations": 0,
                   "notional": 0.0, "fills": []}
        if self.is_collected_closure(session):
            # The official calendar says the market was shut.  An intent aimed at
            # a closed session is dead, not pending, and it says why.
            for intent in self._live_intents():
                if intent.intended_session == session:
                    intent.status = INTENT_EXPIRED
                    intent.settled_on = session
                    intent.settle_note = ("market closed: the collected official "
                                          "S&P 500 series carries a blank row for this "
                                          "date, so there is no session to execute in")
                    summary["expired"] += 1
            return summary
        self._liquidate_breached(session, summary)
        if session not in self.md.dates:
            # No verified bar for this session: every intent aimed at it waits.
            for intent in self._live_intents():
                if intent.intended_session == session:
                    intent.status = INTENT_WAITING_DATA
                    intent.settle_note = ("no verified bar collected for this session; "
                                          "the intent stays open rather than filling "
                                          "at a modelled price")
                    summary["waiting"] += 1
            return summary
        t = self.md.dates.index(session)
        venues: Dict[str, microstructure.ParticipantVenue] = {}
        for intent in self._live_intents():
            if intent.intended_session != session:
                continue
            account = self.accounts[intent.participant]
            prices = self._mark_prices(t)
            breach = account.maintenance_breach(prices, self.cfg.margin)
            if breach and intent.side == "buy":
                intent.status = INTENT_REJECTED
                intent.settle_note = (f"house maintenance margin breached before entry "
                                      f"(equity {breach['equity']}, required "
                                      f"{breach['required']})")
                account.margin_events.append({"session": session, **breach,
                                              "action": "entry refused",
                                              "intent": intent.intent_id})
                summary["rejected"] += 1
                continue
            executable, why_not = bar_is_executable(self.md, intent.symbol, session)
            if not executable:
                # No publisher printed this symbol on this session.  The market
                # does carry a bar there (a previous close, zero volume) so the
                # arrays stay aligned, and filling against it would put an
                # invented price into a published P&L.  The intent waits instead.
                intent.status = INTENT_WAITING_DATA
                intent.settle_note = why_not
                summary["waiting"] += 1
                continue
            key = (intent.participant, intent.symbol)
            if key not in venues:
                venue_day = self.engine.make_venue_day(self.md, intent.symbol, t,
                                                       self.seed)
                venues[key] = self.engine.make_participant_venue(
                    venue_day, intent.participant, self.seed)
            dm = venues[key]
            order = microstructure.Order(
                symbol=intent.symbol, side=intent.side, quantity=intent.quantity,
                order_type=intent.order_type, limit_price=intent.limit_price,
                participant=intent.participant, reason=intent.rationale[:120],
                at_close=intent.at_close)
            start_interval = (dm.path.K - 1) if intent.at_close else 0
            fill = self.engine.execute(dm, order, self.seed,
                                       start_interval=max(0, start_interval))
            row = fill.to_row()
            row["intent_id"] = intent.intent_id
            row["created_on"] = intent.created_on
            row["sessions_held_before_execution"] = self._sessions_between(
                intent.created_on, session)
            row["session_status"] = intent.session_status
            row = ledger.enrich_fill(row, self.md)
            meta = self.md.series_meta.get(intent.symbol)
            row["reference_source_class"] = getattr(meta, "source_class", "UNKNOWN")
            row["reference_provider"] = getattr(meta, "provider", "")
            row["reference_url"] = getattr(meta, "url", "")
            # A bar that came from the print ledger cites its own publisher, not
            # the vendor file the history happens to live in.
            provenance = (getattr(self.md, "bar_provenance", {}) or {}).get(
                (intent.symbol, session))
            if provenance:
                row["reference_source_class"] = provenance.get("source_class",
                                                               row["reference_source_class"])
                row["reference_provider"] = provenance.get("source", "")
                row["reference_url"] = provenance.get("url", "")
                row["reference_print_file"] = provenance.get("file", "")
                row["reference_print_sha256"] = provenance.get("sha256", "")
                row["reference_redistribution_status"] = provenance.get(
                    "redistribution_status", "")
            session_evidence = (getattr(self.md, "session_evidence", {}) or {}).get(session)
            if session_evidence:
                row["session_verification"] = session_evidence.get("verification", "")
                row["session_official_missing"] = ",".join(
                    session_evidence.get("official_series_missing", []))
            else:
                row["session_verification"] = "COLLECTED-OFFICIAL-CALENDAR"
            account.apply_fill(row)
            intent.fill_id = f"{intent.intent_id}-F1"
            intent.settled_on = session
            if fill.status == "rejected":
                intent.status = INTENT_REJECTED
                intent.settle_note = fill.reject_reason
                summary["rejected"] += 1
            elif fill.filled_qty >= fill.requested_qty:
                intent.status = INTENT_FILLED
                summary["filled"] += 1
            else:
                intent.status = INTENT_PARTIAL
                intent.settle_note = (f"{fill.filled_qty} of {fill.requested_qty} "
                                      f"shares ({fill.status})")
                summary["partial"] += 1
            summary["settled"] += 1
            summary["notional"] += float(row.get("notional") or 0.0)
            summary["fills"].append(row)
        if summary["settled"] or summary["rejected"]:
            self.sessions_settled.append(session)
        return summary

    def _liquidate_breached(self, session: str, summary: dict) -> None:
        """Answer a house maintenance call by closing the book at this session.

        A margin call is not decoration: an account that breaches FINRA 4210's
        maintenance floor is liquidated, and a live book that records the breach
        and then keeps holding the position is publishing a number no broker
        would have let exist.  The liquidation is executed through the same
        venue model as any other order (spread, depth, impact, fees), so the
        cost of being wrong is inside the result.

          SOURCE (FINRA 4210 maintenance margin):
            https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210
        """
        if session not in self.md.dates:
            return
        t = self.md.dates.index(session)
        for account in self.accounts.values():
            if not getattr(account, "liquidation_due", False):
                continue
            account.liquidation_due = False
            if not account.positions:
                continue
            for symbol in sorted(account.positions):
                qty = account.positions.get(symbol, 0)
                if qty == 0:
                    continue
                side = "sell" if qty > 0 else "buy"
                venue_day = self.engine.make_venue_day(self.md, symbol, t, self.seed)
                dm = self.engine.make_participant_venue(venue_day, account.participant,
                                                        self.seed)
                order = microstructure.Order(
                    symbol=symbol, side=side, quantity=abs(qty),
                    participant=account.participant, at_close=False,
                    reason="forced liquidation after a maintenance-margin breach")
                fill = self.engine.execute(dm, order, self.seed)
                row = fill.to_row()
                row["intent_id"] = None
                row["created_on"] = session
                row["forced"] = "maintenance-margin liquidation"
                row["sessions_held_before_execution"] = None
                row["session_status"] = "COLLECTED"
                row = ledger.enrich_fill(row, self.md)
                meta = self.md.series_meta.get(symbol)
                row["reference_source_class"] = getattr(meta, "source_class", "UNKNOWN")
                row["reference_provider"] = getattr(meta, "provider", "")
                row["reference_url"] = getattr(meta, "url", "")
                account.apply_fill(row)
                summary["fills"].append(row)
                summary["notional"] += float(row.get("notional") or 0.0)
                summary["liquidations"] += 1
            account.margin_events.append({
                "session": session, "action": "forced liquidation executed",
                "positions_closed": "all", "orders": summary["liquidations"]})
            # A wiped account stops being able to trade: no equity, no room.
            account.wiped = account.equity(self._mark_prices(t)) < 0.0

    def _live_intents(self) -> Iterable[Intent]:
        return [i for i in self.intents
                if i.status in (INTENT_PENDING, INTENT_WAITING_DATA)]

    def _sessions_between(self, created_on: str, session: str) -> Optional[int]:
        try:
            a = self.md.dates.index(created_on)
            b = self.md.dates.index(session)
        except ValueError:
            return None
        return b - a

    def _mark_prices(self, t: int) -> Dict[str, float]:
        return {s: self.md.bar(s, t).close for s in self.md.symbols}

    # -- carry and marking -------------------------------------------------
    def accrue_carry(self, session: str, previous: Optional[str]) -> None:
        """Credit/debit one session's financing at the official SOFR.

        SIM CHOICE, declared: day-count ACT/360, the convention an overnight
        secured rate is quoted on; idle cash earns SOFR and a margin debit pays
        SOFR plus ``margin_debit_spread``.  Where SOFR has no observation -
        the bond market closes on days the NYSE does not - the last observation
        is carried forward and every such day is counted and published, because
        a financing curve with holes must not silently invent a rate.
        """
        sofr = self.official_feed.get("SOFR")
        days = 1 if previous is None else max(
            1, (dt.date.fromisoformat(session) - dt.date.fromisoformat(previous)).days)
        rate: Optional[float] = None
        rate_date = ""
        carried_forward = False
        if sofr is not None:
            usable = [(d, v) for d, v in zip(sofr.dates, sofr.values) if d <= session]
            if usable:
                rate_date, rate = usable[-1]
                carried_forward = rate_date != session
        for account in self.accounts.values():
            if rate is None:
                account.carry_rows.append({"session": session, "rate_pct": None,
                                           "note": "no official SOFR observation on or "
                                                   "before this session"})
                continue
            annual = rate / 100.0
            amount = account.cash * annual * days / 360.0
            if account.cash >= 0:
                account.cash += amount
                account.carry_received += amount
            else:
                amount = account.cash * (annual + MARGIN_DEBIT_SPREAD) * days / 360.0
                account.cash += amount          # cash<0, amount<0 => debit grows
                account.carry_paid += -amount
            account.carry_rows.append({
                "session": session, "rate_pct": rate, "rate_date": rate_date,
                "rate_carried_forward": carried_forward, "days": days,
                "cash_before": round(account.cash - amount, 2),
                "carry_usd": round(amount, 4)})

    def mark(self, session: str) -> List[dict]:
        if session not in self.md.dates:
            return []
        t = self.md.dates.index(session)
        prices = self._mark_prices(t)
        rows = []
        for account in self.accounts.values():
            equity = account.equity(prices)
            breach = account.maintenance_breach(prices, self.cfg.margin)
            if breach:
                account.liquidation_due = True
                account.margin_events.append({
                    "session": session, **breach,
                    "action": ("maintenance call recorded; the book is liquidated at the "
                               "next session's open")})
            row = {"session": session, "participant": account.participant,
                   "equity": round(equity, 2), "cash": round(account.cash, 2),
                   "positions": dict(sorted(account.positions.items())),
                   "gross_exposure": round(account.gross_exposure(prices), 2),
                   "leverage_x": round(account.gross_exposure(prices) / equity, 4)
                   if equity > 0 else None,
                   "margin_breach": breach,
                   "return_pct": round(100.0 * (equity / account.starting_cash - 1.0), 4)}
            account.marks.append(row)
            rows.append(row)
        return rows

    # -- the whole run -----------------------------------------------------
    def run(self, start: str, end: str, horizon: int = 1) -> dict:
        """Step the book from ``start`` to ``end``, one session at a time."""
        if start not in self.md.dates or end not in self.md.dates:
            raise ValueError("start and end must be sessions in the collected data")
        i0 = self.md.dates.index(start)
        i1 = self.md.dates.index(end)
        marks: List[dict] = []
        settle_log: List[dict] = []
        previous: Optional[str] = None
        for i in range(i0, i1 + 1):
            session = self.md.dates[i]
            if i > i0:
                summary = self.settle(session)
                summary.pop("fills", None)
                settle_log.append(summary)
            self.accrue_carry(session, previous)
            marks.extend(self.mark(session))
            previous = session
            if i + horizon <= i1:
                self.plan(session, horizon=horizon)
        return {"marks": marks, "settlements": settle_log}


#: Declared spread over SOFR charged on a margin debit.  SIM CHOICE: retail
#: margin rates are a spread over a reference rate; the level is a parameter, not
#: an observation, and it is published next to every carry figure that uses it.
MARGIN_DEBIT_SPREAD = 0.035


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

def _max_drawdown_pct(equities: Sequence[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return round(100.0 * worst, 4)


def _sharpe(returns: Sequence[float], risk_free_daily: float = 0.0) -> Optional[float]:
    excess = [r - risk_free_daily for r in returns]
    if len(excess) < 3:
        return None
    mu = sum(excess) / len(excess)
    var = sum((r - mu) ** 2 for r in excess) / (len(excess) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return round(mu / sd * math.sqrt(252.0), 4)


def live_leaderboard(book: LiveBook, benchmark: Optional[dict] = None) -> List[dict]:
    """Rank the live book on total return, the metric the brief asks for."""
    rows: List[dict] = []
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        marks = account.marks
        equities = [m["equity"] for m in marks]
        returns = [(equities[i] / equities[i - 1] - 1.0)
                   for i in range(1, len(equities))] if len(equities) > 1 else []
        final = equities[-1] if equities else account.starting_cash
        filled = [f for f in account.fills if int(f.get("filled_qty") or 0) > 0]
        notional = sum(float(f.get("notional") or 0.0) for f in filled)
        costs = sum(float(f.get("total_cost") or 0.0) for f in filled)
        slip = [float(f.get("slippage_bps") or 0.0) for f in filled]
        part = [f.get("participation_pct_of_session_volume") for f in filled
                if f.get("participation_pct_of_session_volume") is not None]
        official_notional = sum(
            float(f.get("notional") or 0.0) for f in filled
            if f.get("reference_source_class") == "OFFICIAL")
        intents = account.intents
        rows.append({
            "username": strategy.username,
            "display_name": strategy.spec.display_name,
            "archetype": strategy.spec.archetype,
            "data_status": strategy.data_status,
            "starting_cash": account.starting_cash,
            "final_equity": round(final, 2),
            "net_pnl_usd": round(final - account.starting_cash, 2),
            "total_return_pct": round(100.0 * (final / account.starting_cash - 1.0), 4),
            "max_drawdown_pct": _max_drawdown_pct(equities),
            "sharpe": _sharpe(returns),
            "sessions_marked": len(marks),
            "intents_placed": len(intents),
            "intents_filled": sum(1 for i in intents if i.status == INTENT_FILLED),
            "intents_partial": sum(1 for i in intents if i.status == INTENT_PARTIAL),
            "intents_rejected": sum(1 for i in intents if i.status == INTENT_REJECTED),
            "intents_cancelled": sum(1 for i in intents if i.status == INTENT_CANCELLED),
            "intents_pending": sum(1 for i in intents
                                   if i.status in (INTENT_PENDING, INTENT_WAITING_DATA)),
            "fills": len(filled),
            "traded_notional_usd": round(notional, 2),
            "execution_cost_usd": round(costs, 2),
            "execution_cost_pct": round(100.0 * costs / notional, 4) if notional else 0.0,
            "slippage_bps_mean": round(sum(slip) / len(slip), 3) if slip else None,
            "slippage_bps_max": round(max(slip), 3) if slip else None,
            "participation_pct_median": (round(sorted(part)[len(part) // 2], 6)
                                         if part else None),
            "participation_pct_max": round(max(part), 6) if part else None,
            "official_price_notional_usd": round(official_notional, 2),
            "official_price_share_pct": (round(100.0 * official_notional / notional, 3)
                                         if notional else None),
            "carry_received_usd": round(account.carry_received, 2),
            "carry_paid_usd": round(account.carry_paid, 2),
            "margin_events": len(account.margin_events),
        })
    rows.sort(key=lambda r: -r["total_return_pct"])
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
        row["verdict"] = _verdict(row, benchmark)
    return rows


def _verdict(row: dict, benchmark: Optional[dict]) -> str:
    if row["fills"] == 0:
        return f"no trades placed ({row['data_status']})"
    if not benchmark:
        return "traded; no benchmark comparison available"
    diff = row["total_return_pct"] - benchmark["return_pct"]
    if diff > 1.0:
        return f"beat the benchmark by {diff:.1f}pp"
    if diff < -1.0:
        return f"lagged the benchmark by {-diff:.1f}pp"
    return "roughly matched the benchmark"


def benchmark_from_official(feed: OfficialFeed, start: str, end: str,
                            sid: str = "SP500") -> Optional[dict]:
    """Return of one official index over the book's window."""
    series = feed.get(sid)
    if series is None:
        return None
    inside = [(d, v) for d, v in zip(series.dates, series.values) if start <= d <= end]
    if len(inside) < 2:
        return None
    (d0, v0), (d1, v1) = inside[0], inside[-1]
    return {"sid": sid, "title": series.title, "publisher": series.publisher,
            "source_class": series.source_class, "file": series.file,
            "sha256": series.sha256, "url": series.url_series,
            "first_date": d0, "first_value": v0, "last_date": d1, "last_value": v1,
            "return_pct": round(100.0 * (v1 / v0 - 1.0), 4)}


def participant_report(book: LiveBook, username: str,
                       benchmark: Optional[dict] = None) -> dict:
    """One participant's post-mortem: what it did, and why the number is what it is."""
    strategy = next((s for s in book.roster if s.username == username), None)
    if strategy is None:
        raise KeyError(f"no live participant {username!r}")
    row = next(r for r in live_leaderboard(book, benchmark) if r["username"] == username)
    account = book.accounts[username]
    trips = ledger.build_round_trips(account.fills)
    closed = [t for t in trips if t.get("exit_date")]
    wins = [t for t in closed if float(t.get("net_pnl_usd") or 0.0) > 0]
    gross_win = sum(float(t["net_pnl_usd"]) for t in wins)
    gross_loss = -sum(float(t["net_pnl_usd"]) for t in closed
                      if float(t.get("net_pnl_usd") or 0.0) <= 0)
    by_symbol: Dict[str, float] = {}
    for trip in closed:
        by_symbol[trip["symbol"]] = round(
            by_symbol.get(trip["symbol"], 0.0) + float(trip.get("net_pnl_usd") or 0.0), 2)
    symbols = sorted(book.md.symbols)
    stats = {
        "round_trips_closed": len(closed),
        "round_trips_open": len(trips) - len(closed),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "largest_trip_usd": round(max((float(t.get("net_pnl_usd") or 0.0)
                                       for t in closed), default=0.0), 2),
        "worst_trip_usd": round(min((float(t.get("net_pnl_usd") or 0.0)
                                     for t in closed), default=0.0), 2),
        "best_symbol": max(by_symbol, key=by_symbol.get) if by_symbol else None,
        "worst_symbol": min(by_symbol, key=by_symbol.get) if by_symbol else None,
    }
    row = dict(row, **stats)
    narrative = _narrative(strategy, row, account, closed, by_symbol, benchmark)
    return {
        "username": username,
        "spec": asdict(strategy.spec),
        "data_status": strategy.data_status,
        "official_inputs": list(getattr(strategy, "official_inputs", ())),
        "event_inputs": list(getattr(strategy, "event_inputs", ())),
        "signal_note": getattr(strategy, "signal_note", ""),
        "summary": row,
        **stats,
        "pnl_by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: -abs(kv[1]))),
        "best_symbol": max(by_symbol, key=by_symbol.get) if by_symbol else None,
        "worst_symbol": min(by_symbol, key=by_symbol.get) if by_symbol else None,
        "carry": {"received_usd": round(account.carry_received, 2),
                  "paid_usd": round(account.carry_paid, 2),
                  "rate_series": "SOFR",
                  "rate_source_class": (book.official_feed.get("SOFR").source_class
                                        if book.official_feed.get("SOFR") else "MISSING"),
                  "day_count": "ACT/360 (declared)",
                  "margin_debit_spread": MARGIN_DEBIT_SPREAD},
        "margin_events": account.margin_events[:20],
        "intents": [i.to_row() for i in account.intents],
        "trips": closed[:50],
        "narrative": narrative,
        "symbols_traded": sorted({f["symbol"] for f in account.fills
                                  if int(f.get("filled_qty") or 0) > 0}),
        "symbols_available": symbols,
    }


def _narrative(strategy, row: dict, account: LiveAccount, closed: List[dict],
               by_symbol: Dict[str, float], benchmark: Optional[dict]) -> List[str]:
    """Explain the result from this participant's own numbers, not from prose."""
    out: List[str] = []
    out.append(f"{strategy.spec.display_name} ({row['username']}) planned "
               f"{row['intents_placed']} forward intents and saw {row['fills']} of "
               f"them execute; {row['intents_rejected']} were refused by the venue or "
               f"by margin, {row['intents_cancelled']} were superseded by a newer "
               f"intent for the same session.")
    if row["fills"] == 0:
        out.append(f"No trade ever executed, so the {row['total_return_pct']:+.2f}% is "
                   f"cash financing only ({row['carry_received_usd']:,.2f} USD of "
                   f"official SOFR credit). The status is {strategy.data_status}: "
                   f"{getattr(strategy, 'signal_note', '') or 'no verified signal file'}."
                   )
        out.append("This is a *measurement gap*, not a result: the rule was never "
                   "tested, so nothing here says whether it would have worked.")
        return out
    out.append(f"Final equity {row['final_equity']:,.2f} USD on "
               f"{row['starting_cash']:,.0f} starting cash: "
               f"{row['total_return_pct']:+.2f}% with a worst peak-to-trough drawdown "
               f"of {row['max_drawdown_pct']:.2f}% over "
               f"{row['sessions_marked']} marked sessions.")
    if benchmark:
        out.append(f"The benchmark ({benchmark['title']}, "
                   f"{benchmark['source_class']}, {benchmark['first_date']} "
                   f"{benchmark['first_value']} -> {benchmark['last_date']} "
                   f"{benchmark['last_value']}) returned "
                   f"{benchmark['return_pct']:+.2f}% over the same window, so this "
                   f"participant {_verdict(row, benchmark).lower()}.")
    out.append(f"Execution cost was {row['execution_cost_usd']:,.2f} USD, "
               f"{row['execution_cost_pct']:.3f}% of "
               f"{row['traded_notional_usd']:,.0f} USD traded, at a mean slippage of "
               f"{row['slippage_bps_mean']} bps against the decision mid; median "
               f"participation was {row['participation_pct_median']}% of the session's "
               f"real volume (max {row['participation_pct_max']}%).")
    if closed:
        out.append(f"{len(closed)} round trips closed; win rate "
                   f"{row.get('win_rate_pct')}% and profit factor "
                   f"{row.get('profit_factor')}. The largest single trip was "
                   f"{row['largest_trip_usd']:,.2f} USD and the worst "
                   f"{row['worst_trip_usd']:,.2f} USD.")
    if by_symbol:
        best = row["best_symbol"]
        worst = row["worst_symbol"]
        out.append(f"Contribution by instrument: {best} "
                   f"{by_symbol[best]:+,.2f} USD was the biggest positive and "
                   f"{worst} {by_symbol[worst]:+,.2f} USD the biggest negative, out of "
                   f"{len(by_symbol)} instruments traded.")
    out.append("Waiting for execution is the whole point of this book: every intent "
               "here was written after the close of its plan date and settled "
               "against a later verified bar, so the cost of the delay is inside "
               "the number rather than assumed away.")
    if account.margin_events:
        out.append(f"{len(account.margin_events)} margin event(s) were recorded "
                   f"(FINRA 4210 house maintenance), the first on "
                   f"{account.margin_events[0].get('session')}.")
    if row["official_price_share_pct"] is not None:
        out.append(f"{row['official_price_share_pct']}% of traded notional executed "
                   f"against a reference bar from an OFFICIAL source; the rest is "
                   f"SECONDARY (the collected Yahoo research files), which is why this "
                   f"book is not an official-price competition.")
    return out


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def verify_live(book: LiveBook, marks: Sequence[dict]) -> dict:
    """Re-derive the book from its own records and report every disagreement.

    Three families of check, all mechanical:

    1. **No look-ahead.**  Every intent's target session is strictly after its
       plan date, and every evidence observation is dated on or before it.
    2. **Accounting.**  Cash and positions are re-derived from the fill tape by
       :mod:`sim.ledger`, independently of :class:`LiveAccount`, and compared
       with the marked equity.
    3. **Provenance.**  Every fill names a reference bar, a file and a SHA-256,
       and the source class of that file is recorded.
    """
    checks = 0
    failures: List[dict] = []

    def fail(code: str, **detail) -> None:
        failures.append({"code": code, **detail})

    for intent in book.intents:
        checks += 1
        if intent.intended_session <= intent.created_on:
            fail("INTENT-NOT-FORWARD", intent=intent.intent_id,
                 created_on=intent.created_on,
                 intended_session=intent.intended_session)
        for name, ev in intent.evidence.items():
            checks += 1
            date = str(ev.get("observation_date") or "")
            if date and date > intent.created_on:
                fail("EVIDENCE-AFTER-PLAN-DATE", intent=intent.intent_id,
                     evidence=name, observation_date=date,
                     created_on=intent.created_on)
            if not ev.get("file") and not ev.get("url"):
                fail("EVIDENCE-UNSOURCED", intent=intent.intent_id, evidence=name)

    per_participant: List[dict] = []
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        filled = [f for f in account.fills if int(f.get("filled_qty") or 0) > 0]
        # Independent re-derivation of cash and positions from the fill tape.
        cash = account.starting_cash
        positions: Dict[str, int] = {}
        for f in filled:
            signed = f["filled_qty"] if f["side"] == "buy" else -f["filled_qty"]
            fees = (float(f.get("commission") or 0.0) + float(f.get("exchange_fee") or 0.0)
                    + float(f.get("regulatory_fee") or 0.0) - float(f.get("rebate") or 0.0))
            cash -= signed * float(f["avg_price"]) + fees
            positions[f["symbol"]] = positions.get(f["symbol"], 0) + signed
        cash += account.carry_received - account.carry_paid
        checks += 2
        cash_residual = abs(cash - (account.cash))
        if cash_residual > 0.02:
            fail("CASH-RESIDUAL", participant=account.participant,
                 rederived=round(cash, 4), recorded=round(account.cash, 4),
                 residual=round(cash_residual, 4))
        position_mismatch = {s: (q, positions.get(s, 0))
                             for s, q in account.positions.items()
                             if positions.get(s, 0) != q}
        for s, q in positions.items():
            if q != 0 and s not in account.positions:
                position_mismatch[s] = (0, q)
        if position_mismatch:
            fail("POSITION-MISMATCH", participant=account.participant,
                 detail=position_mismatch)

        for f in filled:
            checks += 1
            if not f.get("reference_file") or not f.get("reference_sha256"):
                fail("FILL-WITHOUT-PROVENANCE", participant=account.participant,
                     symbol=f["symbol"], date=f["date"])
            checks += 1
            if not f.get("reference_source_class"):
                fail("FILL-WITHOUT-SOURCE-CLASS", participant=account.participant,
                     symbol=f["symbol"], date=f["date"])

        marks_rows = account.marks
        last = marks_rows[-1] if marks_rows else None
        checks += 1
        if last is not None:
            t = book.md.dates.index(last["session"])
            prices = {s: book.md.bar(s, t).close for s in book.md.symbols}
            recomputed = cash + sum(q * prices.get(s, 0.0)
                                    for s, q in positions.items())
            if abs(recomputed - last["equity"]) > 0.05:
                fail("EQUITY-RESIDUAL", participant=account.participant,
                     rederived=round(recomputed, 4), marked=last["equity"],
                     residual=round(abs(recomputed - last["equity"]), 4))
        trips = ledger.build_round_trips(account.fills)
        trip_pnl = sum(float(t.get("net_pnl_usd") or 0.0) for t in trips
                       if t.get("exit_date"))
        per_participant.append({
            "username": account.participant,
            "fills": len(filled),
            "intents": len(account.intents),
            "rederived_cash": round(cash, 2),
            "recorded_cash": round(account.cash, 2),
            "cash_residual_usd": round(cash_residual, 4),
            "positions_rederived": len(positions),
            "round_trips": len(trips),
            "round_trip_pnl_usd": round(trip_pnl, 2),
            "carry_received_usd": round(account.carry_received, 2),
            "carry_paid_usd": round(account.carry_paid, 2),
            "final_equity": last["equity"] if last else account.starting_cash,
        })

    return {
        "checks": checks,
        "failures": failures,
        "failure_count": len(failures),
        "verdict": "PASS" if not failures else "FAIL",
        "max_abs_cash_residual_usd": round(
            max((abs(p["cash_residual_usd"]) for p in per_participant), default=0.0), 6),
        "per_participant": per_participant,
        "note": ("cash and positions are re-derived here from the fill tape plus the "
                 "carry rows, without asking LiveAccount what it thinks it holds; the "
                 "tolerance is 2 cents, which is the worst case a per-fill rounding "
                 "of fees to four decimals can produce over a few hundred fills."),
    }


# --------------------------------------------------------------------------
# Storage: append-only, gzipped, checksummed, and measured for efficiency
# --------------------------------------------------------------------------

STREAMS = ("intents", "fills", "trips", "equity", "carry", "settlements")


def _write_stream(path: str, rows: Sequence[dict], compress: bool = True) -> dict:
    target = path + ".gz" if compress else path
    os.makedirs(os.path.dirname(target), exist_ok=True)
    opener = (lambda: gzip.open(target, "wt", encoding="utf-8", newline="")) \
        if compress else (lambda: open(target, "w", encoding="utf-8", newline=""))
    raw_bytes = 0
    with opener() as handle:
        for row in rows:
            # sort_keys makes the byte stream reproducible across runs and Python
            # versions, which is what lets CI diff a rebuilt memory tree.
            line = json.dumps(row, sort_keys=True, separators=(",", ":"),
                              default=str)
            raw_bytes += len(line.encode("utf-8")) + 1
            handle.write(line + "\n")
    size = os.path.getsize(target)
    with open(target, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return {"file": os.path.relpath(target, REPO_ROOT), "rows": len(rows),
            "bytes_on_disk": size, "bytes_uncompressed": raw_bytes,
            "bytes_per_row_on_disk": round(size / len(rows), 2) if rows else 0.0,
            "compression_ratio": round(raw_bytes / size, 3) if size else None,
            "sha256": digest, "compressed": compress}


def write_live_run(book: LiveBook, run_dir: str, marks: Sequence[dict],
                   settlements: Sequence[dict], extra: Optional[dict] = None,
                   compress: bool = True) -> dict:
    """Persist the book as one directory of append-only streams plus a manifest."""
    os.makedirs(run_dir, exist_ok=True)
    intents = [i.to_row() for i in book.intents]
    fills: List[dict] = []
    trips: List[dict] = []
    carry: List[dict] = []
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        fills.extend(account.fills)
        trips.extend(dict(t, participant=account.participant)
                     for t in ledger.build_round_trips(account.fills))
        carry.extend(dict(c, participant=account.participant)
                     for c in account.carry_rows)
    streams = {
        "intents": _write_stream(os.path.join(run_dir, "intents.jsonl"), intents, compress),
        "fills": _write_stream(os.path.join(run_dir, "fills.jsonl"), fills, compress),
        "trips": _write_stream(os.path.join(run_dir, "trips.jsonl"), trips, compress),
        "equity": _write_stream(os.path.join(run_dir, "equity.jsonl"), list(marks), compress),
        "carry": _write_stream(os.path.join(run_dir, "carry.jsonl"), carry, compress),
        "settlements": _write_stream(os.path.join(run_dir, "settlements.jsonl"),
                                     list(settlements), compress),
    }
    with open(os.path.join(run_dir, "blotter.csv"), "w", encoding="utf-8",
              newline="") as handle:
        fields = ["intent_id", "participant", "created_on", "intended_session",
                  "session_status", "symbol", "side", "quantity", "order_type",
                  "status", "settled_on", "settle_note", "avg_price",
                  "filled_qty", "notional", "total_cost", "slippage_bps",
                  "participation_pct_of_session_volume", "reference_close",
                  "reference_date", "reference_file", "reference_sha256",
                  "reference_source_class", "rule", "rationale"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        fill_by_intent = {f.get("intent_id"): f for f in fills}
        for intent in intents:
            f = fill_by_intent.get(intent["intent_id"], {})
            row = dict(intent)
            row.update({k: f.get(k) for k in fields if k in f})
            row["reference_date"] = f.get("date")
            writer.writerow(row)
    total_bytes = sum(s["bytes_on_disk"] for s in streams.values())
    total_rows = sum(s["rows"] for s in streams.values())
    manifest = {
        "run_id": os.path.basename(run_dir.rstrip(os.sep)),
        "mode": book.mode,
        "seed": book.seed,
        "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_fingerprint": book.cfg.fingerprint(),
        "competition": {"name": book.cfg.name, "season": book.cfg.season,
                        "starting_cash": book.cfg.starting_cash,
                        "rank_metric": book.cfg.rank_metric},
        "sessions_planned": len(book.sessions_planned),
        "first_plan_session": book.sessions_planned[0] if book.sessions_planned else None,
        "last_plan_session": book.sessions_planned[-1] if book.sessions_planned else None,
        "sessions_settled": len(book.sessions_settled),
        "participants": [s.username for s in book.roster],
        "counts": {"intents": len(intents), "fills": len(fills),
                   "round_trips": len(trips), "marks": len(marks),
                   "carry_rows": len(carry), "settlements": len(settlements)},
        "storage": {"streams": streams, "total_bytes_on_disk": total_bytes,
                    "total_rows": total_rows,
                    "bytes_per_row_average": round(total_bytes / total_rows, 2)
                    if total_rows else 0.0,
                    "format": ("gzip JSON Lines, one object per line, keys sorted, "
                               "separators ',' and ':' - the reference bar is stored "
                               "once per fill because the fill is the unit a reader "
                               "queries, and the file SHA-256 is stored in the "
                               "provenance document rather than repeated per row"),
                    "csv_export": "blotter.csv"},
        "official_series": book.official_feed.provenance(),
        "official_series_missing": book.official_feed.missing,
        "price_provenance": {
            s: {"file": m.path, "sha256": m.sha256, "provider": m.provider,
                "source_class": m.source_class, "url": m.url,
                "first_bar": m.bars[0].date, "last_bar": m.bars[-1].date,
                "redistribution_status": m.redistribution_status}
            for s, m in sorted(book.md.series_meta.items())},
        "irregularities": book.irregularities,
    }
    if extra:
        manifest.update(extra)
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    return manifest


def read_live_run(run_dir: str) -> Dict[str, List[dict]]:
    """Read a persisted live run back, compressed or not."""
    out: Dict[str, List[dict]] = {}
    for name in STREAMS:
        rows: List[dict] = []
        for suffix in (".jsonl.gz", ".jsonl"):
            path = os.path.join(run_dir, name + suffix)
            if not os.path.exists(path):
                continue
            opener = gzip.open(path, "rt", encoding="utf-8") if suffix.endswith(".gz") \
                else open(path, "r", encoding="utf-8")
            with opener as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            break
        out[name] = rows
    return out


__all__ = [
    "LIVE_NAME", "LIVE_SEASON", "LIVE_SEED", "LIVE_LAST_VERIFIED_SESSION",
    "LIVE_LAST_OFFICIAL_SESSION", "LIVE_REHEARSAL_START", "LIVE_REHEARSAL_END",
    "OFFICIAL_SERIES_REGISTER", "AUXILIARY_OFFICIAL_ENDPOINTS",
    "OfficialSeries", "OfficialFeed",
    "PROJECTED_CLOSURES", "PROJECTED_EARLY_CLOSES", "PROJECTION_LIMIT",
    "PROJECTION_SOURCE", "project_sessions", "Intent", "LiveAccount",
    "LiveContext", "LiveBook", "MARGIN_DEBIT_SPREAD", "live_leaderboard",
    "benchmark_from_official", "participant_report", "verify_live",
    "write_live_run", "read_live_run", "STREAMS",
    "INTENT_PENDING", "INTENT_WAITING_DATA", "INTENT_FILLED", "INTENT_PARTIAL",
    "INTENT_REJECTED", "INTENT_EXPIRED", "INTENT_CANCELLED",
]
