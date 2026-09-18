"""Season 2 market data: real, collected daily bars.

Season 1 traded a *calibrated replay* anchored on real FRED index closes. Season 2
trades the real thing: every open, high, low, close and volume in this module is
read from a file that was fetched from a publisher and committed to the
repository, and every bar carries the file name and SHA-256 it came from so a
reader can re-derive the run without trusting this code.

What is real here
-----------------
* **Prices.** Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart/
  <symbol>`) daily bars, 26 symbols, 24 used in the competition, for a one-year
  warm-up plus the one-year competition window. ``source_class`` is SECONDARY:
  Yahoo is an aggregator, not the exchange. The primary record is the exchange's
  own consolidated tape, which is not freely redistributable; the aggregator's
  numbers are therefore cross-checked against an independent publisher (Nasdaq)
  and against the FRED index series where a proxy exists, and the comparison is
  published (``data/real/crosschecks/price_crosscheck.json``).
* **Volumes.** The vendor's share volume for the same sessions. These drive the
  participation model: a fill's participation rate is its quantity divided by
  the real session volume.
* **Index and volatility path.** FRED ``SP500`` and ``VIXCLS`` daily closes.
* **Dividends and splits.** Vendor event feeds, used for cash dividends, dividend
  compensation on shorts, and share adjustment. Flagged as vendor data because
  the issuer's own announcement is the primary record.
* **Corporate actions and calends.** Sessions come from the real trading
  calendar built off FRED's observation dates.

What is modelled
----------------
The intraday path *inside* each real daily bar, the venue's quoted spread and
displayed depth, the impact model, borrow fees and the participation cap. Those
are the same models Season 1 used, they are declared in the irregularity
register, and nothing in this module invents a price: the modelled part only
decides *where inside a real day's range* an order filled.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .calendar import REPO_ROOT, TradingCalendar
from .marketdata import Bar, MarketData
from .universe import Instrument

REAL_ROOT = os.path.join(REPO_ROOT, "data", "real")
PRICES_DIR = os.path.join(REAL_ROOT, "prices", "yahoo")
FRED_DIR = os.path.join(REAL_ROOT, "fred")

SEASON2_WARMUP_START = "2024-09-16"
SEASON2_START = "2025-09-17"
SEASON2_END = "2026-09-16"

#: Instruments traded in Season 2. Every one of them was collected; a symbol
#: whose file is missing is dropped with a recorded reason rather than filled
#: with a placeholder price.
UNIVERSE: Tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "GLD", "UNG", "XLU", "XBI", "IBB", "TLT",
    "DKNG", "FLUT", "PENN", "SRAD", "GENI", "AAPL", "MSFT", "NVDA",
    "JPM", "XOM", "JNJ", "PG", "TSLA", "MU", "T",
)

#: Instruments collected but *not* traded: index and volatility proxies, which
#: are used as signals rather than as tradable legs.
NON_TRADABLE: Tuple[str, ...] = ("^GSPC", "^VIX")

_NAME = {
    "SPY": ("SPDR S&P 500 ETF Trust", "broad-index", "ETF"),
    "QQQ": ("Invesco QQQ Trust", "broad-index", "ETF"),
    "IWM": ("iShares Russell 2000 ETF", "small-cap", "ETF"),
    "GLD": ("SPDR Gold Shares", "commodity", "ETF"),
    "UNG": ("United States Natural Gas Fund", "commodity", "ETF"),
    "XLU": ("Utilities Select Sector SPDR", "utilities", "ETF"),
    "XBI": ("SPDR S&P Biotech ETF", "biotech", "ETF"),
    "IBB": ("iShares Biotechnology ETF", "biotech", "ETF"),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", "rates", "ETF"),
    "DKNG": ("DraftKings Inc", "sports-betting", "EQUITY"),
    "FLUT": ("Flutter Entertainment plc", "sports-betting", "EQUITY"),
    "PENN": ("PENN Entertainment Inc", "sports-betting", "EQUITY"),
    "SRAD": ("Sportradar Group AG", "sports-data", "EQUITY"),
    "GENI": ("Genius Sports Ltd", "sports-data", "EQUITY"),
    "AAPL": ("Apple Inc", "technology", "EQUITY"),
    "MSFT": ("Microsoft Corp", "technology", "EQUITY"),
    "NVDA": ("NVIDIA Corp", "semiconductors", "EQUITY"),
    "JPM": ("JPMorgan Chase & Co", "financials", "EQUITY"),
    "XOM": ("Exxon Mobil Corp", "energy", "EQUITY"),
    "JNJ": ("Johnson & Johnson", "healthcare", "EQUITY"),
    "PG": ("Procter & Gamble Co", "staples", "EQUITY"),
    "TSLA": ("Tesla Inc", "automotive", "EQUITY"),
    "MU": ("Micron Technology Inc", "semiconductors", "EQUITY"),
    "T": ("AT&T Inc", "telecom", "EQUITY"),
}


#: Every remote source Season 2 reads, with the endpoint, what it provides, how
#: it is classed and whether the collection run actually retrieved it.  This is
#: the list a reviewer needs to check the season by hand: each row names the URL
#: that was called and the file it produced under ``data/real/``.  The register
#: is also what keeps a URL quoted anywhere in the code followable from the site
#: (tests/test_sources_register.py enforces that).
COLLECTED_SOURCES: Tuple[dict, ...] = (
    {"id": "yahoo", "label": "Yahoo Finance chart API (daily bars, dividends, splits)",
     "url": "https://query1.finance.yahoo.com/v8/finance/chart/SPY",
     "source_class": "SECONDARY", "path": "data/real/prices/yahoo/",
     "provides": "daily OHLCV, dividend and split events for 26 symbols, "
                 "2024-09-16 to 2026-09-16",
     "note": "An aggregator, not the consolidated tape: classed SECONDARY and "
             "cross-checked against FRED's index series and the Nasdaq quote API."},
    {"id": "fred", "label": "Federal Reserve Bank of St. Louis (FRED) CSV downloads",
     "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
     "source_class": "OFFICIAL", "path": "data/real/fred/",
     "provides": "SP500, VIXCLS, DGS10, DGS3MO, DCOILWTICO, DTWEXBGS daily series",
     "note": "Official reserve-bank publication. SP500 supplies the session "
             "calendar, so holidays are the real ones."},
    {"id": "sec", "label": "SEC EDGAR full-text and submissions APIs",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4",
     "source_class": "OFFICIAL", "path": "data/real/sec/",
     "provides": "Form 4 insider transactions (issuer, insider, officer title, "
                 "transaction code, shares, price, filing date)",
     "note": "The primary record for insider activity: the filing itself."},
    {"id": "openfda", "label": "openFDA drug approval endpoint",
     "url": "https://api.fda.gov/drug/drugsfda.json",
     "source_class": "OFFICIAL", "path": "data/real/fda/",
     "provides": "application decisions with status and status date, sponsor, "
                 "submission type",
     "note": "FDA's own API. PDUFA action dates are NOT in it: only actual "
             "decisions, which is what the strategy keys on."},
    {"id": "mlb", "label": "MLB StatsAPI schedule",
     "url": "https://statsapi.mlb.com/api/v1/schedule",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "regular-season finals with dates, teams, scores and records",
     "note": "The league's own feed, used as the sports-attention clock."},
    {"id": "espn", "label": "ESPN scoreboard endpoints (NFL, NBA, NCAAF)",
     "url": "https://site.api.espn.com/apis/site/v2/sports",
     "source_class": "SECONDARY", "path": "data/real/sports/",
     "provides": "dated NFL/NBA/NCAAF results for the attention signals",
     "note": "Secondary: the official league sites publish the same scores but "
             "their public endpoints are not stable or enumerable."},
    {"id": "nba", "label": "NBA official injury report and stats endpoints",
     "url": "https://official.nba.com/nba-injury-report-2025-26-season/",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "the league's own injury report documents",
     "note": "A live document with no retrievable archive of past seasons, so "
             "the injury strategy is forward-only."},
    {"id": "nfl", "label": "NFL official injury report",
     "url": "https://www.nfl.com/injuries/",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "the league's own weekly injury document",
     "note": "Same limitation as the NBA report: live only."},
    {"id": "ncei", "label": "NOAA National Centers for Environmental Information",
     "url": "https://www.ncei.noaa.gov/access/services/data/v1",
     "source_class": "OFFICIAL", "path": "data/real/weather/",
     "provides": "daily station summaries (TMIN/TMAX/PRCP) for two US stations",
     "note": "The station record behind the weather signal."},
    {"id": "kalshi", "label": "Kalshi settled-markets API",
     "url": "https://api.elections.kalshi.com/trade-api/v2/markets",
     "source_class": "OFFICIAL-VENDOR", "path": "data/real/kalshi/",
     "provides": "settled-contract listings with close times",
     "note": "The venue's own API. The payload's price and volume fields came "
             "back null, which is recorded rather than smoothed over."},
    {"id": "nasdaq", "label": "Nasdaq quote API (independent price cross-check)",
     "url": "https://api.nasdaq.com/api/quote",
     "source_class": "SECONDARY", "path": "data/real/crosschecks/",
     "provides": "an independent publisher's daily closes for cross-checking the "
                 "price files used as reference",
     "note": "Two independent publishers disagreeing by more than a tick would be "
             "a red flag on the price file; the comparison is published."},
    {"id": "masterfeed", "label": "MasterFeed register (this project's signal catalogue)",
     "url": "https://buffedlizard55-lab.github.io/MasterSite/",
     "source_class": "ASSERTED", "path": "sim/masterfeed.py",
     "provides": "the mapping from each MasterSite project to a tradable signal",
     "note": "The mapping itself is this project's judgement, not a source claim; "
             "the register labels each one STRONG, WEAK or UNPROVEN."},
    {"id": "sec_data", "label": "SEC structured data APIs (ticker map, submissions, filing archive)",
     "url": "https://www.sec.gov/files/company_tickers.json",
     "source_class": "OFFICIAL", "path": "data/real/sec/",
     "provides": "the official ticker->CIK map, per-issuer submission indexes and the "
                 "filing archive directory tree",
     "note": "The ticker map answered HTTP 403 to an anonymous User-Agent from the "
             "collection runner; it now sends a contact address in the UA as SEC's "
             "own webmaster FAQ requires, and the outcome is recorded either way."},
    {"id": "sec_submissions", "label": "SEC submissions API",
     "url": "https://data.sec.gov/submissions/CIK",
     "source_class": "OFFICIAL", "path": "data/real/sec/",
     "provides": "an issuer's filing history, one JSON document per CIK",
     "note": "Same 10-requests-per-second courtesy limit as the rest of EDGAR, so the "
             "collector throttles to one request a second (SEC_MIN_INTERVAL)."},
    {"id": "sec_archive", "label": "SEC EDGAR filing archive",
     "url": "https://www.sec.gov/Archives/edgar/data",
     "source_class": "OFFICIAL", "path": "data/real/sec/",
     "provides": "the raw Form 4 XML documents the insider signals are parsed from",
     "note": "The filing text itself, not a summary: the entry states the officer "
             "title and transaction code, which is what the CEO/CFO filter needs."},
    {"id": "sec_faq", "label": "SEC webmaster FAQ (automated-access policy)",
     "url": "https://www.sec.gov/about/webmaster-frequently-asked-questions",
     "source_class": "OFFICIAL", "path": "sim/realdata.py",
     "provides": "the published rule that automated EDGAR access must declare a "
                 "User-Agent with contact details",
     "note": "Cited because the collector's header changed to comply with it."},
    {"id": "fred_series", "label": "FRED series pages",
     "url": "https://fred.stlouisfed.org/series/DGS10",
     "source_class": "OFFICIAL", "path": "data/real/fred/",
     "provides": "the human-readable page (units, revision policy) for the series "
                 "the CSV downloads supply",
     "note": "DGS10/DGS3MO supply the yield-curve signal; GOLDPMGBD228NLBM was "
             "discontinued (404) and GOLDAMGBD228NLBM is collected beside it."},
    {"id": "stooq", "label": "Stooq daily CSV (attempted second publisher)",
     "url": "https://stooq.com/q/d/l",
     "source_class": "SECONDARY", "path": "data/real/prices/stooq/",
     "provides": "nothing: every request was refused",
     "note": "Eight of eight requests came back \"Access denied\" from the collection "
             "runner, so this publisher contributes no price file at all; the Nasdaq "
             "quote API was added as the independent cross-check instead."},
    {"id": "nba_static", "label": "NBA CDN schedule feed (attempted)",
     "url": "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "nothing: HTTP 403 from the collection runner",
     "note": "Recorded as attempted rather than quietly dropped; the NBA injury "
             "signal is forward-only regardless, because no archive exists."},
    {"id": "nba_stats", "label": "NBA stats scoreboard endpoint (attempted)",
     "url": "https://stats.nba.com/stats/scoreboardv3",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "nothing: HTTP 403 from the collection runner",
     "note": "The league's stats host rejects datacentre clients; the official "
             "injury-report documents remain the citation for the forward probe."},
    {"id": "nba_site", "label": "NBA official site (injury report index host)",
     "url": "https://www.nba.com",
     "source_class": "OFFICIAL", "path": "data/real/sports/",
     "provides": "the league's own publication channel for the daily injury report",
     "note": "Cited at the host because the injury-report path moves between "
             "seasons; the stable season URL is registered beside it."},
    {"id": "github", "label": "This repository (self-citation for provenance notes)",
     "url": "https://github.com/buffedlizard55-lab/StockPaperSim",
     "source_class": "ASSERTED", "path": ".",
     "provides": "the commit and branch the collector records next to each file it "
                 "writes",
     "note": "Every data file is committed here with its SHA-256, so a reader can "
             "check the season against the same bytes the run used."},
)


def collected_sources() -> List[dict]:
    """The data-source register as plain dicts (used by the site and the tests)."""
    return [dict(row) for row in COLLECTED_SOURCES]


class RealDataUnavailable(RuntimeError):
    """A collected file the season needs is missing or unusable."""


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Series:
    """One collected price series, with its provenance attached."""

    symbol: str
    path: str
    sha256: str
    provider: str
    source_class: str
    bars: List[Bar]
    dividends: List[dict] = field(default_factory=list)
    splits: List[dict] = field(default_factory=list)
    url: str = ""

    def by_date(self) -> Dict[str, Bar]:
        return {b.date: b for b in self.bars}


def _reject_swapped_arguments(series: str, root: str) -> None:
    """Catch ``load_x(root, "SPY")`` - the argument order that once silently
    switched every collected signal in Season 2 to MISSING.

    Both loaders take ``(series, root)``.  A caller who passes them the other way
    round used to get an exception that the signal builders swallowed as "no
    data", which is the worst possible outcome: the site reported a missing
    source and nothing said the file was sitting right there.  Raise a
    ``TypeError`` - a programming error is not a data state.
    """
    looks_like_path = os.sep in str(series) or str(series) in (".", "..")
    if looks_like_path:
        raise TypeError(
            f"argument order: {series!r} looks like a directory, so this call "
            f"passed (root, series) instead of (series, root)")


def load_series(symbol: str, root: str = REAL_ROOT) -> Series:
    """Read one collected vendor file. Raises if it is absent or empty."""
    _reject_swapped_arguments(symbol, root)
    slug = symbol.replace("^", "_")
    path = os.path.join(root, "prices", "yahoo", f"{slug}.json")
    if not os.path.exists(path):
        raise RealDataUnavailable(f"no collected price file for {symbol} at {path}")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    bars = [Bar(b["date"], float(b["open"]), float(b["high"]), float(b["low"]),
                float(b["close"]), int(b.get("volume") or 0))
            for b in payload.get("bars", [])]
    if not bars:
        raise RealDataUnavailable(f"{path} contains no bars")
    return Series(symbol=symbol, path=os.path.relpath(path, REPO_ROOT),
                  sha256=_sha256_file(path),
                  provider=payload.get("provider", "unknown"),
                  source_class=payload.get("source_class", "UNKNOWN"),
                  bars=bars, dividends=payload.get("dividends", []),
                  splits=payload.get("splits", []),
                  url=payload.get("source", "") or payload.get("url", ""))


def load_fred(series: str, root: str = REAL_ROOT) -> Tuple[Dict[str, float], str, str]:
    """Read the collected FRED CSV with the longest coverage for ``series``.

    More than one window can be on disk (the first collection run fetched the
    competition window, the second fetched a year of warm-up as well), and the
    file names sort in an order that has nothing to do with coverage, so the
    choice is made on the number of observations. Getting this wrong silently
    shortens the warm-up, which is exactly the kind of defect the run manifest
    is supposed to expose, so the choice is also recorded in the diagnostics.
    """
    _reject_swapped_arguments(series, root)
    directory = os.path.join(root, "fred")
    if not os.path.isdir(directory):
        raise RealDataUnavailable(f"no collected FRED directory at {directory}")
    hits = sorted(f for f in os.listdir(directory) if f.startswith(series + "_"))
    if not hits:
        raise RealDataUnavailable(f"no collected FRED file for {series}")
    best: Optional[Tuple[int, str, Dict[str, float]]] = None
    for name in hits:
        path = os.path.join(directory, name)
        values: Dict[str, float] = {}
        with open(path, "r", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if len(row) < 2 or row[0].strip() in ("observation_date", "DATE"):
                    continue
                try:
                    values[row[0].strip()] = float(row[1])
                except ValueError:
                    continue
        if not values:
            continue
        if best is None or len(values) > best[0]:
            best = (len(values), path, values)
    if best is None:
        raise RealDataUnavailable(f"collected FRED files for {series} are empty")
    _, path, values = best
    return values, os.path.relpath(path, REPO_ROOT), _sha256_file(path)


def _ols_beta(asset: Sequence[float], market: Sequence[float]) -> float:
    if len(asset) != len(market) or len(asset) < 20:
        return 1.0
    n = len(asset)
    ma, mm = sum(asset) / n, sum(market) / n
    cov = sum((a - ma) * (m - mm) for a, m in zip(asset, market))
    var = sum((m - mm) ** 2 for m in market)
    return cov / var if var else 1.0


def _annualised_sigma(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def build_real_market_data(root: str = REAL_ROOT,
                           symbols: Optional[Sequence[str]] = None,
                           verbose: bool = False) -> RealMarketData:
    """Assemble the Season 2 market from the collected files.

    The trading sessions are FRED's S&P 500 observation dates (the real US
    equity calendar). A symbol with no bar on a session is forward-filled at its
    previous close with zero volume, and *every* such session is listed under
    ``gaps`` and published in the run diagnostics, because a forward-filled bar
    is a missing observation, not a real print.
    """
    calendar = TradingCalendar(SEASON2_WARMUP_START, SEASON2_END,
                               fred_dir=os.path.join(root, "fred"))
    dates = [d for d in sorted(calendar.spx) if SEASON2_WARMUP_START <= d <= SEASON2_END]
    if not dates:
        raise RealDataUnavailable("no FRED sessions in the Season 2 window")
    if dates[0] != SEASON2_WARMUP_START:
        raise RealDataUnavailable(
            f"collected FRED SP500 starts at {dates[0]}, expected {SEASON2_WARMUP_START} "
            f"(warm-up would be truncated)")
    spx_values, spx_path, spx_sha = load_fred("SP500", root)
    vix_values, vix_path, vix_sha = load_fred("VIXCLS", root)
    spx = [spx_values.get(d, 0.0) for d in dates]
    vix = [vix_values.get(d, 0.0) for d in dates]
    missing_spx = [d for d in dates if d not in spx_values]
    if missing_spx:
        raise RealDataUnavailable(f"FRED SP500 has no observation for {missing_spx[:5]}")

    first_competition = dates.index(SEASON2_START)
    warmup_days = first_competition

    wanted = tuple(symbols or UNIVERSE)
    instruments: Dict[str, Instrument] = {}
    bars: Dict[str, List[Bar]] = {}
    series_meta: Dict[str, Series] = {}
    gaps: Dict[str, List[str]] = {}
    dropped: List[dict] = []
    price_series_diag: Dict[str, dict] = {}

    spx_returns = [spx[t] / spx[t - 1] - 1.0 for t in range(1, len(spx))]

    for symbol in wanted:
        try:
            series = load_series(symbol, root)
        except RealDataUnavailable as exc:
            dropped.append({"symbol": symbol, "reason": str(exc)})
            continue
        by_date = series.by_date()
        filled: List[str] = []
        rows: List[Bar] = []
        for date in dates:
            bar = by_date.get(date)
            if bar is None:
                if not rows:
                    filled.append(date)
                    previous = next((b for b in reversed(series.bars) if b.date < date), None)
                    if previous is None:
                        dropped.append({"symbol": symbol,
                                        "reason": f"no bar on or before {date}"})
                        break
                    rows.append(Bar(date, previous.close, previous.close, previous.close,
                                    previous.close, 0))
                    continue
                filled.append(date)
                previous = rows[-1]
                rows.append(Bar(date, previous.close, previous.close, previous.close,
                                previous.close, 0))
            else:
                rows.append(bar)
        if len(rows) != len(dates):
            continue

        # Baseline statistics are measured on the real warm-up window only, the
        # same way a desk would size a name before the competition starts. No
        # statistic in this file is a scenario parameter.
        warm = rows[:warmup_days] or rows[:min(60, len(rows))]
        closes = [b.close for b in warm if b.close > 0]
        returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        vols = [b.volume for b in warm if b.volume > 0]
        # The market leg must be the S&P 500 return over the *same sessions* as
        # the asset return. Slicing the last N entries of two series that start
        # at different indices silently compares different dates (an earlier
        # version of this file did exactly that and produced a negative beta for
        # XBI), so both legs are indexed off the session index here.
        warm_end = len(warm)
        market_leg = [spx[i] / spx[i - 1] - 1.0 for i in range(1, warm_end)]
        pair = list(zip(returns, market_leg))
        beta = _ols_beta([p[0] for p in pair], [p[1] for p in pair])
        idio = _annualised_sigma([a - beta * m for a, m in pair])
        adv = float(sorted(vols)[len(vols) // 2]) if vols else 0.0
        all_closes = [b.close for b in rows]
        volume_in_competition = [b.volume for b in rows[first_competition:]]
        name, sector_name, asset_type = _NAME.get(
            symbol, (f"{symbol} (collected)", "unclassified", "EQUITY"))
        # ``ex_date`` is the key the engine and the season-1 market both read
        # (``engine._build_dividend_map``), so the collected vendor field
        # ``date`` is renamed here rather than leaking a second spelling.  An
        # earlier revision of this loader wrote "date" and every subsequent
        # Season 2 run died with KeyError: 'ex_date' - the trades that survived
        # it are the reason the register now carries a "why" for each rename.
        dividends = [{"ex_date": d.get("date"), "amount": d.get("amount"),
                      "provenance": "vendor event feed (secondary)"}
                     for d in series.dividends if d.get("date") in set(dates)]
        instruments[symbol] = Instrument(
            symbol=symbol, name=name, sector=sector_name, asset_type=asset_type,
            listing_venue="US", price_start=rows[first_competition - 1].close,
            price_end_anchor=rows[-1].close, beta=round(beta, 4),
            sigma_idio_annual=round(idio, 4), alpha_annual=0.0, adv_shares=round(adv),
            shares_outstanding=None,
            fifty_two_week_high=max(all_closes), fifty_two_week_low=min(all_closes),
            dividends=dividends,
            provenance={
                "price_start": "real",
                "price_end_anchor": "real",
                "beta": "estimated from the real warm-up returns of this series "
                        "against the real FRED S&P 500 (no scenario prior)",
                "sigma_idio_annual": "residual volatility of the real warm-up returns",
                "adv_shares": "median real share volume over the warm-up window",
                "fifty_two_week_high_low": "real collected bars",
                "dividends": "vendor event feed, not the issuer's own announcement"
                             if dividends else "no dividend paid in the window",
                "source_class": series.source_class,
                "file": series.path,
                "sha256": series.sha256,
                "splits_in_window": str(len(series.splits)),
            })
        bars[symbol] = rows
        series_meta[symbol] = series
        gaps[symbol] = filled
        price_series_diag[symbol] = {
            "file": series.path, "sha256": series.sha256, "provider": series.provider,
            "source_class": series.source_class, "url": series.url,
            "first_bar": series.bars[0].date, "last_bar": series.bars[-1].date,
            "bars_in_window": len(rows),
            "sessions_forward_filled": len(filled),
            "forward_filled_dates": filled[:20],
            "dividends_in_window": len(dividends),
            "splits_in_window": len(series.splits),
            "median_volume_warmup": int(adv),
            "beta_vs_fred_spx": round(beta, 4),
            "sigma_idio_annual_estimated": round(idio, 4),
            "first_close": rows[0].close, "last_close": rows[-1].close,
        }

    if not bars:
        raise RealDataUnavailable("not one collected price series could be used")

    # Season 2 never needs a synthetic index: the real FRED S&P 500 series *is*
    # the market factor, and the real VIX series is the volatility factor.
    diagnostics = {
            "season": "Season 2 (real collected prices)",
            "window": {"warmup_start": dates[0], "start": SEASON2_START,
                       "end": dates[-1]},
            "sessions": {"warmup": warmup_days,
                         "competition": len(dates) - warmup_days},
            "calendar_source": {
                "series": "FRED SP500 observation dates",
                "file": spx_path, "sha256": spx_sha,
                "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"},
            "volatility_source": {
                "series": "FRED VIXCLS", "file": vix_path, "sha256": vix_sha,
                "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"},
            "price_series": price_series_diag,
            "dropped_series": dropped,
            "sessions_forward_filled_total": sum(len(v) for v in gaps.values()),
            "volume_in_competition_min": {
                s: (min((b.volume for b in rows[first_competition:]), default=0))
                for s, rows in sorted(bars.items())},
            "modelled_not_real": [
                "the intraday path inside each real daily bar",
                "the venue's quoted spread and displayed depth",
                "the square-root impact model and the participation cap",
                "borrow fees and market-maker behaviour",
            ],
            "spx_total_return_pct_real": (
                round(100.0 * (spx[-1] / spx[first_competition - 1] - 1.0), 4)
                if first_competition else None),
        }
    market = MarketData(calendar=calendar, instruments=[instruments[s] for s in bars],
                        bars=bars, spx=spx, vix=vix, diagnostics=diagnostics,
                        source="real-collected: Yahoo Finance v8 chart daily bars, "
                               "FRED SP500/VIXCLS, vendor dividend events",
                        warmup_days=warmup_days, warmup_dates=[])
    # Extra, Season-2-only attributes. They are plain attributes because
    # MarketData is the interface the engine and every strategy already program
    # against; keeping the subclass out of the way means a Season 2 run exercises
    # exactly the same engine code path as Season 1.
    market.series_meta = series_meta
    market.gaps = gaps
    market.real = True
    market.dropped = dropped
    if verbose:
        print(f"  real data: {len(bars)} symbols · {len(dates)} sessions "
              f"({warmup_days} warm-up + {len(dates) - warmup_days} competition)")
        for name in sorted(gaps):
            if gaps[name]:
                print(f"    {name}: {len(gaps[name])} forward-filled session(s)")
        for row in dropped:
            print(f"    DROPPED {row['symbol']}: {row['reason']}")
    return market


def data_inventory(root: str = REAL_ROOT, digest: bool = True) -> dict:
    """Every collected file, its size, and (optionally) its SHA-256.

    This is the artefact that makes "verified pricing" a checkable claim: the
    independent audit recomputes these hashes and compares them with the run's
    provenance record, so a price that changed after publication is visible.
    """
    files: List[dict] = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            size = os.path.getsize(path)
            total += size
            row = {"path": os.path.relpath(path, REPO_ROOT), "bytes": size}
            if digest:
                row["sha256"] = _sha256_file(path)
            files.append(row)
    files.sort(key=lambda r: r["path"])
    return {"root": os.path.relpath(root, REPO_ROOT), "file_count": len(files),
            "bytes": total, "files": files}


def crosscheck_against_fred(symbol: str = "SPY", root: str = REAL_ROOT) -> dict:
    """Compare a collected ETF series with the real FRED index on the same days.

    SPY tracks the S&P 500 with a small tracking difference, so this is not an
    equality test: it is a *sanity* test that the two independently collected
    sources describe the same market. Both the return correlation and the
    worst daily difference are published.
    """
    spx_values, spx_path, _ = load_fred("SP500", root)
    series = load_series(symbol, root)
    common = [(b.date, b.close, spx_values[b.date]) for b in series.bars
              if b.date in spx_values]
    if len(common) < 30:
        return {"ok": False, "reason": "not enough common sessions",
                "sessions": len(common)}
    asset_returns = [common[i][1] / common[i - 1][1] - 1.0 for i in range(1, len(common))]
    index_returns = [common[i][2] / common[i - 1][2] - 1.0 for i in range(1, len(common))]
    n = len(asset_returns)
    ma, mi = sum(asset_returns) / n, sum(index_returns) / n
    cov = sum((a - ma) * (b - mi) for a, b in zip(asset_returns, index_returns))
    va = math.sqrt(sum((a - ma) ** 2 for a in asset_returns))
    vi = math.sqrt(sum((b - mi) ** 2 for b in index_returns))
    corr = cov / (va * vi) if va and vi else 0.0
    diffs = [abs(a - b) for a, b in zip(asset_returns, index_returns)]
    return {
        "ok": True, "symbol": symbol, "index": "FRED SP500", "sessions": len(common),
        "return_correlation": round(corr, 6),
        "max_abs_daily_return_diff": round(max(diffs), 6),
        "median_abs_daily_return_diff": round(sorted(diffs)[len(diffs) // 2], 6),
        "fred_file": spx_path,
        "level_ratio_mean": round(sum(c[1] / c[2] for c in common) / len(common), 6),
        "note": "SPY is an ETF and the index is not: a small tracking difference is "
                "expected. The test is for a broken series, not for equality.",
    }
