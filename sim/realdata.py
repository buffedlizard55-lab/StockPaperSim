"""Season 2 market data: real, collected, citable prices only.

Season 1 traded a *calibrated* venue: real FRED index and VIX path, with the
fifteen single names synthesised from declared parameters (see the README and
``research/IRREGULARITIES.json`` IR-07..IR-09).  Season 2 removes that
simulation from the price path entirely.  Every open, high, low, close and
volume that a Season 2 strategy can see is read from a file that

* was downloaded by ``scripts/collect_real_data.py`` from a recorded URL,
* is committed verbatim under ``data/real/``,
* carries the HTTP status, byte count and SHA-256 of its bytes in
  ``data/real/collection_manifest.json``.

The data is downloaded on a GitHub Actions runner because the development
sandbox has no outbound network (documented in the workflow and in
``research/LIMITATIONS.json``).

WHAT IS STILL SIMULATED IN SEASON 2 (and why the site must say so)
------------------------------------------------------------------
* **Intraday path.** Yahoo publishes daily bars, not ticks. Each real daily
  bar is expanded into an intraday path by the same calibrated model Season 1
  used (``sim/microstructure.py``), constrained to visit that day's real
  open/high/low/close. Fill prices inside a session are therefore modelled,
  not observed. IR-40 flags this.
* **Venue.** Depth, spreads, market-making and impact are modelled from real
  volume and real volatility, and FINRA/SEC fee schedules are the dated real
  ones. The liquidity *levels* are modelled; the *returns the prices imply over
  days* are real.
* **Corporate actions.** Dividends and splits come from the vendor's event feed
  (Yahoo ``events=div,split``) - real dates and real amounts, but a secondary
  vendor rather than the issuer's own filing. IR-42.

Every one of those is a declared, flagged deviation, not a silent one.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .calendar import TradingCalendar, REPO_ROOT
from .marketdata import Bar
from .universe import Instrument

REAL_ROOT = os.path.join(REPO_ROOT, "data", "real")
YAHOO_DIR = os.path.join(REAL_ROOT, "prices", "yahoo")
FRED_DIR = os.path.join(REAL_ROOT, "fred")

SEASON2_WARMUP_START = "2024-09-16"
SEASON2_START = "2025-09-17"
SEASON2_END = "2026-09-16"

#: Declared metadata for the traded universe.  ``sector`` and ``name`` are
#: descriptive labels only - they are not used by the venue model except for the
#: sector-rotation strategy's grouping, which is declared here so a reader can
#: disagree with it.
UNIVERSE_META: Dict[str, dict] = {
    "SPY":  {"name": "SPDR S&P 500 ETF Trust", "sector": "Broad market ETF", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "The Leap / market factor",
             "master_site": "TradingViewTheLeap"},
    "QQQ":  {"name": "Invesco QQQ Trust (Nasdaq-100)", "sector": "Broad market ETF",
             "asset_type": "ETF", "venue": "Nasdaq", "signal_source": "PinePilot technical rules",
             "master_site": "Tradingview-pinescript-editor"},
    "IWM":  {"name": "iShares Russell 2000 ETF", "sector": "Broad market ETF", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "PinePilot technical rules",
             "master_site": "Tradingview-pinescript-editor"},
    "GLD":  {"name": "SPDR Gold Shares", "sector": "Gold", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "GOLD (buyer's reference directory)",
             "master_site": "GOLD"},
    "UNG":  {"name": "United States Natural Gas Fund", "sector": "Energy", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "SFWeather / NOAA heating demand",
             "master_site": "SFWeather"},
    "XLU":  {"name": "Utilities Select Sector SPDR", "sector": "Utilities", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "SFWeather / NOAA weather",
             "master_site": "SFWeather"},
    "XBI":  {"name": "SPDR S&P Biotech ETF", "sector": "Biotechnology", "asset_type": "ETF",
             "venue": "NYSEArca", "signal_source": "FDA decision intensity (openFDA)",
             "master_site": "DrugAnalysis"},
    "IBB":  {"name": "iShares Biotechnology ETF", "sector": "Biotechnology", "asset_type": "ETF",
             "venue": "Nasdaq", "signal_source": "FDA decision intensity (openFDA)",
             "master_site": "DrugAnalysis"},
    "TLT":  {"name": "iShares 20+ Year Treasury Bond ETF", "sector": "Rates", "asset_type": "ETF",
             "venue": "Nasdaq", "signal_source": "FRED yield curve", "master_site": "TheLeap-adjacent"},
    "DKNG": {"name": "DraftKings Inc.", "sector": "Sports betting", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "NFL/NBA/MLB/NCAA scoreboards",
             "master_site": "NFL-scoreboard"},
    "FLUT": {"name": "Flutter Entertainment plc", "sector": "Sports betting", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "Sports injury + scoreboard feeds",
             "master_site": "NFLInjuryReport"},
    "PENN": {"name": "PENN Entertainment, Inc.", "sector": "Sports betting", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "Sports scoreboards + injuries",
             "master_site": "NBAInjuryReport"},
    "SRAD": {"name": "Sportradar Group AG", "sector": "Sports data", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "SportsPred model vs market",
             "master_site": "SportsPred"},
    "GENI": {"name": "Genius Sports Limited", "sector": "Sports data", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "Ncaa-football-alerts feed",
             "master_site": "Ncaa-football-alerts"},
    "AAPL": {"name": "Apple Inc.", "sector": "Information Technology", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "MSFT": {"name": "Microsoft Corporation", "sector": "Information Technology", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "NVDA": {"name": "NVIDIA Corporation", "sector": "Information Technology", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "JPM":  {"name": "JPMorgan Chase & Co.", "sector": "Financials", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "XOM":  {"name": "Exxon Mobil Corporation", "sector": "Energy", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "JNJ":  {"name": "Johnson & Johnson", "sector": "Health Care", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "PG":   {"name": "The Procter & Gamble Company", "sector": "Consumer Staples",
             "asset_type": "EQUITY", "venue": "NYSE", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "TSLA": {"name": "Tesla, Inc.", "sector": "Consumer Discretionary", "asset_type": "EQUITY",
             "venue": "Nasdaq", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "MU":   {"name": "Micron Technology, Inc.", "sector": "Information Technology",
             "asset_type": "EQUITY", "venue": "Nasdaq", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
    "T":    {"name": "AT&T Inc.", "sector": "Communication Services", "asset_type": "EQUITY",
             "venue": "NYSE", "signal_source": "SEC Form 4 insider filings",
             "master_site": "Insider-trades"},
}

#: Symbols the Season 2 roster is allowed to trade.
TRADED_SYMBOLS: Tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "GLD", "UNG", "XLU", "XBI", "IBB", "TLT",
    "DKNG", "FLUT", "PENN", "SRAD", "GENI",
    "AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "TSLA", "MU", "T",
)

#: Symbols that must resolve to the market factor and are never traded.
REFERENCE_SYMBOLS: Tuple[str, ...] = ("^GSPC", "^VIX")


class RealDataUnavailable(RuntimeError):
    """Raised when a required collected file is missing."""


@dataclass
class Series:
    symbol: str
    provider: str
    source_class: str
    bars: List[Bar]
    dividends: List[dict]
    splits: List[dict]
    sha256: str
    path: str
    exchange: str = ""

    def closes(self) -> Dict[str, float]:
        return {b.date: b.close for b in self.bars}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_series(root: str = REAL_ROOT, symbol: str = "SPY") -> Series:
    path = os.path.join(root, "prices", "yahoo", f"{symbol.lstrip('^')}.json"
                        if not symbol.startswith("^") else f"{symbol[1:]}.json")
    if symbol.startswith("^"):
        path = os.path.join(root, "prices", "yahoo", f"{symbol[1:]}.json")
    if not os.path.exists(path):
        raise RealDataUnavailable(
            f"missing collected series for {symbol}: {path}. Run the "
            f"'Collect real market data' workflow (scripts/collect_real_data.py) "
            f"on a machine with internet access.")
    payload = _read_json(path)
    bars = [Bar(b["date"], b["open"], b["high"], b["low"], b["close"], int(b["volume"]))
            for b in payload["bars"] if b.get("close") is not None]
    bars.sort(key=lambda b: b.date)
    return Series(symbol=symbol, provider=payload.get("provider", "yahoo"),
                  source_class=payload.get("source_class", "SECONDARY"), bars=bars,
                  dividends=payload.get("dividends") or [], splits=payload.get("splits") or [],
                  sha256=_sha256_file(path), path=os.path.relpath(path, REPO_ROOT),
                  exchange=payload.get("exchange") or "")


def _read_fred_file(path: str) -> Dict[str, float]:
    values: Dict[str, float] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip():
                try:
                    values[parts[0].strip()] = float(parts[1])
                except ValueError:
                    continue
    return values


def load_fred(root: str = REAL_ROOT, series: str = "SP500") -> Tuple[Dict[str, float], str, str]:
    """Return (date->value, relative path, sha256) for a collected FRED series.

    Several collection windows can coexist for one series (Season 1 collected the
    competition window; Season 2 collects a year of warm-up as well), so the file
    that is read is the one with the **widest observed date range**, not simply
    the last file name in sorted order. Reading the wrong one silently truncated
    Season 2's warm-up to zero sessions, which is exactly the kind of defect the
    run manifest now reports.
    """
    directory = os.path.join(root, "fred")
    hits = sorted(f for f in os.listdir(directory) if f.startswith(series + "_"))
    if not hits:
        raise RealDataUnavailable(f"no collected FRED file for {series} in {directory}")
    best_path, best_values, best_span = None, {}, (-1, "")
    for name in hits:
        path = os.path.join(directory, name)
        values = _read_fred_file(path)
        if not values:
            continue
        span = (len(values), sorted(values)[0])
        if best_path is None or span[0] > best_span[0]:
            best_path, best_values, best_span = path, values, span
    if best_path is None:
        raise RealDataUnavailable(f"collected FRED files for {series} are empty")
    return best_values, os.path.relpath(best_path, REPO_ROOT), _sha256_file(best_path)


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _ols_beta(y: Sequence[float], x: Sequence[float]) -> float:
    n = min(len(x), len(y))
    if n < 30:
        return 1.0
    mx, my = sum(x[:n]) / n, sum(y[:n]) / n
    vxx = sum((v - mx) ** 2 for v in x[:n])
    if vxx <= 0:
        return 1.0
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / vxx


class RealMarketData:
    """Duck-typed replacement for :class:`sim.marketdata.MarketData`.

    The engine only needs the attribute surface below, so Season 2 does not
    touch the replay code at all: it hands the same engine a different, real
    market.
    """

    def __init__(self, calendar: TradingCalendar, instruments: Sequence[Instrument],
                 bars: Dict[str, List[Bar]], spx: Sequence[float], vix: Sequence[float],
                 diagnostics: dict, source: str, warmup_days: int,
                 warmup_dates: Sequence[str], series_meta: Optional[Dict[str, Series]] = None,
                 gaps: Optional[Dict[str, List[str]]] = None) -> None:
        self.calendar = calendar
        self.instruments = {i.symbol: i for i in instruments}
        self.symbols = [i.symbol for i in instruments]
        self.bars = bars
        self.spx = list(spx)
        self.vix = list(vix)
        self.diagnostics = diagnostics
        self.source = source
        self.warmup_days = warmup_days
        self.warmup_dates = list(warmup_dates)
        self.dates = list(warmup_dates) + list(calendar.dates)
        self.first_competition_index = warmup_days
        self.series_meta = series_meta or {}
        self.gaps = gaps or {}
        self._closes = {s: [b.close for b in bars[s]] for s in self.symbols}
        self._vols = {s: [b.volume for b in bars[s]] for s in self.symbols}
        self._rets: Dict[str, List[float]] = {}
        for s in self.symbols:
            c = self._closes[s]
            self._rets[s] = [0.0] + [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))]
        self.market_ret = [0.0] + [spx[i] / spx[i - 1] - 1.0 for i in range(1, len(spx))]
        if len(self.dates) != len(self.spx) or len(self.dates) != len(self.vix):
            raise AssertionError("Season 2 market data arrays are misaligned")
        for sym, rows in self.bars.items():
            if len(rows) != len(self.dates):
                raise AssertionError(f"{sym}: {len(rows)} bars vs {len(self.dates)} dates")
            for i, bar in enumerate(rows):
                if bar.date != self.dates[i]:
                    raise AssertionError(f"{sym}: bar {i} is {bar.date}, expected {self.dates[i]}")

    # -- identical accessors to MarketData, deliberately -------------------
    def history_closes(self, symbol: str, t: int, n: Optional[int] = None) -> List[float]:
        start = 0 if n is None else max(0, t - n)
        return self._closes[symbol][start:t]

    def history_returns(self, symbol: str, t: int, n: Optional[int] = None) -> List[float]:
        start = 1 if n is None else max(1, t - n)
        return self._rets[symbol][start:t]

    def history_volume(self, symbol: str, t: int, n: Optional[int] = None) -> List[int]:
        start = 0 if n is None else max(0, t - n)
        return self._vols[symbol][start:t]

    def adv(self, symbol: str, t: int, window: int = 63) -> float:
        vols = self.history_volume(symbol, t, window)
        if not vols:
            return float(self.instruments[symbol].adv_shares)
        return sum(vols) / len(vols)

    def realised_sigma_daily(self, symbol: str, t: int, window: int = 21) -> float:
        rets = self.history_returns(symbol, t, window)
        if len(rets) < 5:
            rets = self.history_returns(symbol, t)
        if len(rets) < 2:
            return self.instruments[symbol].sigma_idio_annual / math.sqrt(252.0)
        return max(_stdev(rets), 1e-5)

    def market_sigma_daily(self, t: int, window: int = 21) -> float:
        rets = self.market_ret[max(1, t - window):t]
        if len(rets) < 2:
            rets = self.market_ret[1:t] or [0.01]
        return max(_stdev(rets), 1e-5)

    def bar(self, symbol: str, t: int) -> Bar:
        return self.bars[symbol][t]

    def vix_regime_factor(self, t: int) -> float:
        mean = sum(self.vix) / len(self.vix)
        return max(0.25, min(4.0, self.vix[t] / mean))

    def __len__(self) -> int:
        return len(self.dates)


def _align(bars: List[Bar], dates: Sequence[str]) -> Tuple[List[Bar], List[str]]:
    """Snap a real series onto the session calendar by forward-filling.

    A missing session for a listed instrument means no print reached the vendor
    feed for that symbol on that day.  Forward-filling keeps the array aligned
    and *records* every filled date, which is what the run manifest and the
    site's data-coverage panel then report. No price is invented: the last real
    close is carried, and the venue sees zero volume for that session.
    """
    by_date = {b.date: b for b in bars}
    out: List[Bar] = []
    gapped: List[str] = []
    last: Optional[Bar] = None
    for date in dates:
        bar = by_date.get(date)
        if bar is not None:
            last = bar
            out.append(bar)
            continue
        if last is None:
            raise RealDataUnavailable(
                f"no real observation at or before {date}; the collected series "
                f"starts at {bars[0].date if bars else 'n/a'}")
        gapped.append(date)
        out.append(Bar(date=date, open=last.close, high=last.close, low=last.close,
                       close=last.close, volume=0))
    return out, gapped


def build_real_market_data(root: str = REAL_ROOT, symbols: Sequence[str] = TRADED_SYMBOLS,
                           warmup_start: str = SEASON2_WARMUP_START,
                           start: str = SEASON2_START,
                           end: str = SEASON2_END) -> RealMarketData:
    """Assemble Season 2's market from the collected, hashed real data files."""
    spx_map, spx_path, spx_sha = load_fred(root, "SP500")
    vix_map, vix_path, vix_sha = load_fred(root, "VIXCLS")

    all_dates = sorted(d for d in spx_map if warmup_start <= d <= end)
    competition_dates = [d for d in all_dates if start <= d <= end]
    warmup_dates = [d for d in all_dates if d < start]
    if not competition_dates:
        raise RealDataUnavailable("no FRED sessions inside the Season 2 window")

    # The calendar the venue reads (session minutes, early closes) is built from
    # the same FRED files, so it cannot disagree with the price data.
    calendar = TradingCalendar(start, end, fred_dir=os.path.join(root, "fred"))
    if calendar.dates != competition_dates:
        raise AssertionError(
            f"calendar ({len(calendar.dates)} sessions) disagrees with the FRED "
            f"path used for Season 2 ({len(competition_dates)} sessions)")

    dates = list(warmup_dates) + list(competition_dates)
    series_meta: Dict[str, Series] = {}
    gaps: Dict[str, List[str]] = {}
    bars: Dict[str, List[Bar]] = {}
    for symbol in symbols:
        series = load_series(root, symbol)
        series_meta[symbol] = series
        aligned, gapped = _align(series.bars, dates)
        bars[symbol] = aligned
        gaps[symbol] = gapped

    spx = [spx_map[d] for d in dates]
    vix = [vix_map.get(d) for d in dates]
    last_vix = None
    for i, value in enumerate(vix):
        if value is None:
            vix[i] = last_vix
        else:
            last_vix = value
    if any(v is None for v in vix):
        raise RealDataUnavailable("VIX series does not start before the Season 2 window")

    # Instruments are *measured* from the real data, never declared: beta and
    # idiosyncratic volatility come from the real return series, ADV from the
    # real traded volume, dividends from the vendor's real event feed.
    instruments: List[Instrument] = []
    for symbol in symbols:
        meta = UNIVERSE_META.get(symbol, {"name": symbol, "sector": "Unclassified",
                                          "asset_type": "EQUITY", "venue": "UNKNOWN"})
        closes = [b.close for b in bars[symbol]]
        squashed = closes[0]
        returns: List[float] = []
        idx_returns: List[float] = []
        for i in range(1, len(dates)):
            if spx[i - 1] and dates[i] in spx_map and dates[i - 1] in spx_map:
                returns.append(closes[i] / closes[i - 1] - 1.0)
                idx_returns.append(spx[i] / spx[i - 1] - 1.0)
        beta = _ols_beta(returns, idx_returns)
        resid = [returns[i] - beta * idx_returns[i] for i in range(len(returns))]
        sigma_idio = _stdev(resid) * math.sqrt(252.0)
        warm_vols = [bars[symbol][i].volume for i in range(len(warmup_dates)) if bars[symbol][i].volume > 0]
        adv = sum(warm_vols) / len(warm_vols) if warm_vols else 1.0
        window = [bars[symbol][i] for i in range(len(dates))]
        high = max(b.high for b in window)
        low = min(b.low for b in window)
        dividends = [d for d in series_meta[symbol].dividends if start <= d["date"] <= end]
        instruments.append(Instrument(
            symbol=symbol, name=meta["name"], sector=meta["sector"],
            asset_type=meta["asset_type"], listing_venue=meta.get("venue", ""),
            price_start=squashed, price_end_anchor=closes[-1],
            beta=round(beta, 4), sigma_idio_annual=round(sigma_idio, 4),
            alpha_annual=0.0, adv_shares=round(adv, 1),
            fifty_two_week_high=round(high, 4), fifty_two_week_low=round(low, 4),
            dividends=[{"ex_date": d["date"], "amount": float(d["amount"])} for d in dividends],
            provenance={
                "price_start": "real-collected", "price_end_anchor": "real-collected",
                "beta": "measured on the collected real series (OLS vs FRED SP500)",
                "sigma_idio_annual": "measured residual volatility of the real series",
                "adv_shares": "mean real traded volume over the real warm-up window",
                "alpha_annual": "not declared - real prices carry their own returns",
                "dividends": "real ex-dates and amounts from the vendor event feed",
                "source_class": series_meta[symbol].source_class,
                "file": series_meta[symbol].path, "sha256": series_meta[symbol].sha256,
            }))

    diagnostics = {
        "season": "Season 2 (real collected prices)",
        "window": {"warmup_start": warmup_start, "start": start, "end": end},
        "sessions": {"warmup": len(warmup_dates), "competition": len(competition_dates)},
        "market_series": {
            "spx": {"file": spx_path, "sha256": spx_sha, "source": "FRED SP500"},
            "vix": {"file": vix_path, "sha256": vix_sha, "source": "FRED VIXCLS"},
        },
        "price_series": {
            s: {"file": series_meta[s].path, "sha256": series_meta[s].sha256,
                "provider": series_meta[s].provider,
                "source_class": series_meta[s].source_class,
                "first_bar": series_meta[s].bars[0].date,
                "last_bar": series_meta[s].bars[-1].date,
                "filled_sessions": len(gaps[s]),
                "dividends_in_window": len([d for d in series_meta[s].dividends
                                            if start <= d["date"] <= end])}
            for s in symbols},
        "spx_start": spx[0], "spx_end": spx[-1],
        "spx_total_return_pct_real": 100.0 * (spx[-1] / spx[len(warmup_dates) - 1] - 1.0),
        "vix_mean_real": round(sum(vix) / len(vix), 4),
        "vix_max_real": round(max(vix), 4),
    }
    return RealMarketData(calendar=calendar, instruments=instruments, bars=bars,
                          spx=spx, vix=vix, diagnostics=diagnostics,
                          source="real-collected", warmup_days=len(warmup_dates),
                          warmup_dates=warmup_dates, series_meta=series_meta, gaps=gaps)


def data_inventory(root: str = REAL_ROOT) -> dict:
    """A hash inventory of every collected file, for the data page and audits."""
    rows: List[dict] = []
    for dirpath, _, filenames in os.walk(root):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rows.append({"path": os.path.relpath(path, REPO_ROOT),
                         "bytes": os.path.getsize(path),
                         "sha256": _sha256_file(path)})
    rows.sort(key=lambda r: r["path"])
    return {"files": rows, "count": len(rows),
            "total_bytes": sum(r["bytes"] for r in rows)}


__all__ = [
    "REAL_ROOT", "SEASON2_START", "SEASON2_END", "SEASON2_WARMUP_START",
    "TRADED_SYMBOLS", "UNIVERSE_META", "RealDataUnavailable", "RealMarketData",
    "Series", "build_real_market_data", "data_inventory", "load_fred", "load_series",
    "config",
]
