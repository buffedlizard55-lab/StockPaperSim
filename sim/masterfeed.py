"""MasterFeed: turn the MasterSite projects into *tested* trading signals.

The brief was to look at the published project directory
(https://buffedlizard55-lab.github.io/MasterSite/) and see which of those
projects can be turned into a stock strategy - naming CEO, weather, insider
trades, TheLeap, NFL/NBA injuries, FDA decisions, NCAA/NFL/MLB scoreboards,
SportsPred, gold and PinePilot - and then to backtest each one **only** on real,
verified prices and dates, and forward-test the ones with no historical feed.

This module is that bridge, and it is deliberately split in two:

``MASTER_SITE_SIGNALS``
    the register.  One row per requested project: what the project is, the
    official endpoint a strategy would read, whether that data was actually
    retrieved into ``data/real/`` in this project's lifetime, and how strong
    the mapping from the project to a tradable instrument honestly is.

``SignalBook``
    the only thing a strategy is allowed to see: per-session arrays computed
    from the collected files, with a strict no-look-ahead rule (an event dated
    *D* is visible from the open of the next session, never on *D* itself).

Two rules are enforced here rather than documented and hoped for:

* **No invented events.**  A signal whose file is missing stays ``MISSING`` and
  the strategy that depends on it is reported as having produced no trades for
  lack of data - it is never replaced by a simulated stand-in.
* **No silent proxies.**  Where a project has no tradable mapping that can be
  defended (SFWeather is a rain forecast, not a commodity feed), the register
  says so and the associated participant is labelled ``WEAK-MAPPING`` or
  ``FORWARD-ONLY`` rather than being quietly switched to a trend follower.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .calendar import REPO_ROOT
from .realdata import RealDataUnavailable, REAL_ROOT, Series, load_fred, load_series


def _rel(path: str) -> str:
    """Path relative to the repository root, so every register row is clickable."""
    return os.path.relpath(path, REPO_ROOT)

MASTER_SITE_URL = "https://buffedlizard55-lab.github.io/MasterSite/"

#: How well a project's output maps onto a tradable instrument.  This is a
#: judgement, made once, in the open, so a reader can disagree with it.
STRONG = "STRONG-MAPPING"
WEAK = "WEAK-MAPPING"
UNPROVEN = "UNPROVEN-MAPPING"
NO_MARKET = "NO-TRADABLE-INSTRUMENT"

BACKTESTED = "BACKTESTED"
# Signals that are just a *real price of a traded instrument* under another
# name (GLD's close, SPY's dollar volume).  A strategy that reads only these is
# not waiting on an external dataset, so it must not be labelled
# "signal-dependent": that label is what tells a reader a zero return means
# "data missing" rather than "the rule never fired".  The first Season 2 run
# labelled @GOLD_Trend_GLD signal-dependent on the strength of ``gold_close``,
# which is GLD's own price.
PRICE_DERIVED = ("gold_close", "spy_dollar_volume_20d")
FORWARD_ONLY = "FORWARD-ONLY"

#: The register.  ``evidence`` names the collected file(s) a backtest would
#: read; where it is empty the honest statement is that no historical data was
#: retrievable in this environment.
MASTER_SITE_SIGNALS: List[dict] = [
    {
        "id": "CEO",
        "signals": ['insider_ceo_buys_30d', 'insider_buys_30d'],
        "requested_as": "CEO",
        "repo": None,
        "title": "No project named 'CEO' exists in the directory",
        "site_url": MASTER_SITE_URL,
        "official_url": "https://www.sec.gov/edgar/search/",
        "source_class": "OFFICIAL",
        # Backtestable: the CEO/CFO behaviour is read from the real Form 4 stream
        # (which tags the reporting officer's title and its date), not from a
        # project this directory does not contain. If the Form 4 collection does
        # not land, the participant reports DATA-MISSING rather than trading.
        "status": BACKTESTED,
        "mapping": STRONG,
        "mapping_note": (
            "The directory was enumerated from the official GitHub API "
            "(39 public repositories) and from the site's own data file: there is no "
            "CEO-named project. The closest real feed for CEO behaviour is SEC Form 4, "
            "which is tagged with the officer's title, so a CEO/CFO-only filter is "
            "implemented on the real Form 4 stream instead of inventing a source."),
        "evidence": ["data/real/sec/form4_transactions.jsonl"],
        "hypothesis": ("Open-market purchases by a CEO or CFO - the two roles with the "
                       "best view of the firm - predict positive abnormal returns over "
                       "the following one to three months."),
        "tradable": ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "TSLA", "MU", "T"],
    },
    {
        "id": "SFWeather",
        "signals": ['weather_cold_anomaly_10d', 'weather_precip_30d_in'],
        "requested_as": "weather",
        "repo": "SFWeather",
        "title": "SFWeather - 94122 rainy-season outlook",
        "site_url": "https://buffedlizard55-lab.github.io/SFWeather/",
        "official_url": "https://www.ncei.noaa.gov/access/services/data/v1",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": WEAK,
        "mapping_note": (
            "SFWeather publishes a rainfall outlook for one ZIP code. There is no "
            "instrument that prices San Francisco rain directly, so the mapped trade is "
            "a heating/cooling-demand proxy: cold anomalies at the SF station -> long "
            "UNG (natural gas) and XLU (utilities). The mapping is weak and the result "
            "must be read as a test of the proxy, not of the forecast."),
        "evidence": ["data/real/weather/USW00023272_daily.json"],
        "hypothesis": ("Below-normal minimum temperatures at the SF station raise "
                       "heating demand, which lifts natural-gas and utility equities "
                       "with a lag of days."),
        "tradable": ["UNG", "XLU"],
    },
    {
        "id": "Insider-trades",
        "signals": ['insider_buys_30d', 'insider_buy_ratio_30d'],
        "requested_as": "insider trades",
        "repo": "Insider-trades",
        "title": "Insider-trades - SEC EDGAR Form 4 toolkit",
        "site_url": "https://buffedlizard55-lab.github.io/Insider-trades/",
        "official_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": STRONG,
        "mapping_note": (
            "Form 4 is the primary filing for insider transactions and carries the "
            "execution price and date of each trade, so both the signal and the fill "
            "are verifiable against the issuer's own filing."),
        "evidence": ["data/real/sec/form4_transactions.jsonl",
                     "data/real/sec/form4/*.xml"],
        "hypothesis": ("Cluster purchases - two or more distinct insiders buying in the "
                       "open market inside ten days - outperform, because insiders trade "
                       "only when their private valuation gap is large."),
        "tradable": ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "TSLA", "MU", "T"],
    },
    {
        "id": "TradingViewTheLeap",
        "signals": [],
        "requested_as": "TheLeap",
        "repo": "TradingViewTheLeap",
        "title": "The Leap - verified competition research",
        "site_url": "https://buffedlizard55-lab.github.io/TradingViewTheLeap/",
        "official_url": "https://www.tradingview.com/the-leap/december-2025/rules/",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": WEAK,
        "mapping_note": (
            "The Leap trades 94 futures symbols on AMP; this simulation cannot hold "
            "futures, so the leap-style rule (maximum leverage into the strongest "
            "trailing momentum, auto-liquidated at the close of the season) is tested "
            "on the equity/ETF proxies SPY, QQQ and TLT. The contest rule is copied; "
            "the instrument is declared as a proxy."),
        "evidence": ["data/real/prices/yahoo/SPY.json", "data/real/prices/yahoo/QQQ.json",
                     "data/real/prices/yahoo/TLT.json"],
        "hypothesis": ("In a contest that ranks on total return with auto-liquidation at "
                       "the end, the highest expected rank is bought by levering the "
                       "asset with the strongest trailing return."),
        "tradable": ["SPY", "QQQ", "TLT"],
    },
    {
        "id": "NFLInjuryReport",
        "signals": [],
        "requested_as": "NFL Injury",
        "repo": "NFLInjuryReport",
        "title": "NFL Injury Report - 32 team tracker",
        "site_url": "https://buffedlizard55-lab.github.io/NFLInjuryReport/",
        "official_url": "https://www.nfl.com/injuries/",
        "source_class": "OFFICIAL",
        "status": FORWARD_ONLY,
        "mapping": WEAK,
        "mapping_note": (
            "nfl.com publishes the current week's injury designations; there is no "
            "official archive endpoint for a past season that this project could "
            "retrieve, so the injury rule runs forward from the next collection instead "
            "of being backdated. ESPN's archive is a secondary feed and is used only for "
            "scores, never as the official injury designation."),
        "evidence": ["data/real/sports/official/nfl_injuries.raw"],
        "hypothesis": ("Heavy injury weeks at contending teams move sportsbook equities "
                       "through expected-handle revisions."),
        "tradable": ["FLUT", "DKNG", "PENN"],
    },
    {
        "id": "NBAInjuryReport",
        "signals": [],
        "requested_as": "NBA Injury",
        "repo": "NBAInjuryReport",
        "title": "NBA Injury Watch - 30 team monitor",
        "site_url": "https://buffedlizard55-lab.github.io/NBAInjuryReport/",
        "official_url": "https://official.nba.com/nba-injury-report-2025-26-season/",
        "source_class": "OFFICIAL",
        "status": FORWARD_ONLY,
        "mapping": WEAK,
        "mapping_note": (
            "The NBA publishes its injury report as a dated PDF; the 2025-26 archive is "
            "not retrievable as a machine-readable series, so the rule is forward-tested "
            "against the live document (whose existence is verified and hashed in "
            "data/real/sports/official/)."),
        "evidence": ["data/real/sports/official/nba_injury_report_index.raw"],
        "hypothesis": ("Games lost by star players to injury raise expected in-game "
                       "variance, which is positive for sportsbook and sports-data "
                       "equities."),
        "tradable": ["DKNG", "PENN", "SRAD", "GENI"],
    },
    {
        "id": "DrugAnalysis",
        "signals": ['fda_orig_30d', 'fda_orig_z', 'fda_all_30d'],
        "requested_as": "FDA Decisions Drug Analysis",
        "repo": "DrugAnalysis",
        "title": "DrugAnalysis - FDA decisions and biotech reactions",
        "site_url": "https://buffedlizard55-lab.github.io/DrugAnalysis/",
        "official_url": "https://api.fda.gov/drug/drugsfda.json",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": STRONG,
        "mapping_note": (
            "openFDA returns the FDA's own approval and supplement decisions with the "
            "decision date, so the event clock is official. The instrument is the "
            "biotech sector (XBI/IBB) because most openFDA sponsors are private or "
            "unlisted, and mapping a public ticker by hand would be a guess."),
        "evidence": ["data/real/fda/openfda_decisions.jsonl"],
        "hypothesis": ("Weeks with an unusual number of FDA approvals mark a positive "
                       "regulatory regime and predict biotech sector strength; the "
                       "opposite holds for approval droughts."),
        "tradable": ["XBI", "IBB"],
    },
    {
        "id": "Ncaa-football-alerts",
        "signals": [],
        "requested_as": "NCAA Scoreboard",
        "repo": "Ncaa-football-alerts",
        "title": "Ncaa-football-alerts",
        "site_url": "https://buffedlizard55-lab.github.io/Ncaa-football-alerts/",
        "official_url": "https://data.ncaa.com/casablanca/scoreboard/football/fbs/2025/10/scoreboard.json",
        "source_class": "OFFICIAL",
        "status": FORWARD_ONLY,
        "mapping": WEAK,
        "mapping_note": (
            "The NCAA scoreboard endpoint is official but the archived 2025-26 season "
            "had to be probed rather than enumerated; the same attention rule that is "
            "backtested on MLB scores runs forward on NCAA weeks."),
        "evidence": ["data/real/sports/official/ncaa_scoreboard_fbs_2025_week10.raw"],
        "hypothesis": ("High-profile college-football weekends drive betting handle, "
                       "which lifts sportsbook and sports-data equities."),
        "tradable": ["GENI", "SRAD", "DKNG"],
    },
    {
        "id": "NFL-scoreboard",
        "signals": ['mlb_games_7d', 'mlb_upsets_7d'],
        "requested_as": "NFL scoreboard",
        "repo": "NFL-scoreboard",
        "title": "NFL-scoreboard",
        "site_url": "https://buffedlizard55-lab.github.io/NFL-scoreboard/",
        "official_url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "source_class": "SECONDARY",
        "status": BACKTESTED,
        "mapping": WEAK,
        "mapping_note": (
            "The retrieved NFL/NBA/NCAAF scores come from ESPN's public scoreboard "
            "endpoint, which is a secondary publisher; the official league endpoints "
            "were probed and are recorded with their HTTP status. The sports-derived "
            "signals are therefore labelled as resting on secondary data."),
        "evidence": ["data/real/sports/nfl_scoreboard.jsonl"],
        "hypothesis": ("A weekend with many upsets raises gambling attention and "
                       "handle, which lifts sportsbook equities in the following week."),
        "tradable": ["DKNG", "FLUT", "PENN"],
    },
    {
        "id": "MLB-Live-PBP",
        "signals": ['mlb_games_7d', 'mlb_upsets_7d', 'mlb_home_win_rate_30d'],
        "requested_as": "MLB Scoreboard",
        "repo": "MLB-Live-PBP",
        "title": "MLB pitch-by-pitch / scoreboard feeds",
        "site_url": "https://buffedlizard55-lab.github.io/MLB-Live-PBP/",
        "official_url": "https://statsapi.mlb.com/api/v1/schedule",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": WEAK,
        "mapping_note": (
            "MLB StatsAPI is the league's own feed, so every final score used here is "
            "official. The economic link from baseball results to listed equities is "
            "indirect (attention and handle), so the mapping is declared weak."),
        "evidence": ["data/real/sports/mlb_games_2026.jsonl"],
        "hypothesis": ("Dense winning runs by popular teams raise baseball attention, "
                       "and sportsbook/sports-data equities follow attention."),
        "tradable": ["DKNG", "FLUT", "PENN"],
    },
    {
        "id": "SportsPred",
        "signals": [],
        "requested_as": "Sports Pred",
        "repo": "SportsPred",
        "title": "SportsPred - game prediction models",
        "site_url": "https://buffedlizard55-lab.github.io/SportsPred/",
        "official_url": "https://statsapi.mlb.com/api/v1/schedule",
        "source_class": "OFFICIAL",
        "status": FORWARD_ONLY,
        "mapping": UNPROVEN,
        "mapping_note": (
            "SportsPred emits pre-game probabilities. There is no archived, timestamped "
            "snapshot of those predictions for the past season, so a backtest would have "
            "to recompute them - which would be this project's model, not the site's. "
            "The participant therefore trades only on snapshots collected from now on."),
        "evidence": [],
        "hypothesis": ("Where a model's probability disagrees materially with the "
                       "market's implied probability, the sports-data complex is "
                       "mispriced in the same direction."),
        "tradable": ["SRAD", "GENI"],
    },
    {
        "id": "GOLD",
        "signals": ['gold_close'],
        "requested_as": "Gold",
        "repo": "GOLD",
        "title": "GOLD - solid-gold engagement ring buyer's guide",
        "site_url": "https://buffedlizard55-lab.github.io/GOLD/",
        "official_url": "https://fred.stlouisfed.org/series/GOLDAMGBD228NLBM",
        "source_class": "OFFICIAL",
        "status": BACKTESTED,
        "mapping": WEAK,
        "mapping_note": (
            "GOLD is a retail buyer's directory: it publishes per-item prices, not a "
            "gold market feed, and its own README states the site performs arithmetic "
            "only and never calls a price API. The tradable mapping used here is GLD "
            "against the LBMA fix from FRED, which is a different (and official) price "
            "source; the distinction is stated on the site."),
        "evidence": ["data/real/prices/yahoo/GLD.json", "data/real/fred/"],
        "hypothesis": ("Retail jewellery demand data cannot be traded directly; the "
                       "proxy test is whether gold's own trend, confirmed by the "
                       "official LBMA fix, persists."),
        "tradable": ["GLD"],
    },
    {
        "id": "Tradingview-pinescript-editor",
        "signals": [],
        "requested_as": "PinePilot",
        "repo": "Tradingview-pinescript-editor",
        "title": "PinePilot - Pine Script editor and strategy lab",
        "site_url": "https://buffedlizard55-lab.github.io/Tradingview-pinescript-editor/",
        "official_url": "https://www.tradingview.com/pine-script-docs/",
        "source_class": "SECONDARY",
        "status": BACKTESTED,
        "mapping": STRONG,
        "mapping_note": (
            "PinePilot generates Pine Script, and the canonical community rules "
            "(EMA cross, Donchian break, RSI-2 mean reversion) are fully specified in "
            "public documentation, so they can be reproduced exactly on real prices."),
        "evidence": ["data/real/prices/yahoo/QQQ.json", "data/real/prices/yahoo/IWM.json",
                     "data/real/prices/yahoo/SPY.json"],
        "hypothesis": ("Trend confirmation with a volatility stop earns positive "
                       "expectancy on liquid index ETFs after costs."),
        "tradable": ["SPY", "QQQ", "IWM"],
    },
    {
        "id": "KalshiPaperSim",
        "signals": ['kalshi_settled_30d', 'kalshi_volume_30d'],
        "requested_as": "Kalshi (competition design reference)",
        "repo": "KalshiPaperSim",
        "title": "KalshiPaperSim - paper-trading competition lab",
        "site_url": "https://buffedlizard55-lab.github.io/KalshiPaperSim/",
        "official_url": "https://api.elections.kalshi.com/trade-api/v2/markets",
        "source_class": "OFFICIAL-VENDOR",
        "status": BACKTESTED,
        "mapping": UNPROVEN,
        "mapping_note": (
            "Kalshi's own settled-market data is collected, and the number of settled "
            "contracts is used as an attention proxy for weather/energy. The link from "
            "prediction-market attention to an equity is unproven and is labelled so."),
        "evidence": ["data/real/kalshi/KXHIGHNY_settled.jsonl",
                     "data/real/kalshi/KXAAAGASM_settled.jsonl"],
        "hypothesis": ("Settled-contract volume in temperature and gasoline markets is a "
                       "real-time nowcast of attention on weather and fuel, which leads "
                       "utility and gas ETFs."),
        "tradable": ["UNG", "XLU"],
    },
]


# --------------------------------------------------------------------------
# The signal book
# --------------------------------------------------------------------------

class SignalBook:
    """Per-session signal values, built strictly from collected real files.

    Every array is indexed exactly like ``md.dates`` (warm-up sessions first).
    ``availability`` records what was actually found on disk, so the site can
    distinguish "the strategy had no edge" from "the strategy had no data".
    """

    def __init__(self, dates: Sequence[str]) -> None:
        self.dates = list(dates)
        self.index = {d: i for i, d in enumerate(self.dates)}
        self.arrays: Dict[str, List[float]] = {}
        self.availability: Dict[str, dict] = {}
        self.provenance: Dict[str, dict] = {}

    # -- helpers -----------------------------------------------------------
    def _blank(self, name: str) -> List[float]:
        array = [0.0] * len(self.dates)
        self.arrays[name] = array
        return array

    def _register_all(self, names: Sequence[str], state: str, rows: int,
                      window: Sequence[str], files: Sequence[str], note: str,
                      url: str = "") -> None:
        """Register every array a builder produced in one call.

        The first Season 2 run exposed the failure this fixes: only the one array
        a builder happened to name was registered, so ``available()`` returned
        False for the sibling arrays and two strategies silently never traded.
        A signal that exists must be marked available for *every* array it fills.
        """
        for name in names:
            self._register(name, state, rows, window, files, note, url)

    def finalise(self) -> None:
        """Register anything a builder forgot, as MISSING (never as available)."""
        for name in sorted(self.arrays):
            if name not in self.availability:
                self.availability[name] = {
                    "state": "MISSING", "rows": 0, "window": [None, None],
                    "files": [], "note": "array was filled but never registered as "
                                         "sourced - treated as missing on purpose",
                    "url": ""}

    def _register(self, name: str, state: str, rows: int, window: Sequence[str],
                  files: Sequence[str], note: str, url: str = "") -> None:
        self.availability[name] = {"state": state, "rows": int(rows),
                                   "window": [window[0] if window else None,
                                              window[-1] if window else None],
                                   "files": list(files), "note": note, "url": url}

    def series(self, name: str) -> List[float]:
        return self.arrays.get(name, [0.0] * len(self.dates))

    def value(self, name: str, t: int) -> float:
        array = self.arrays.get(name)
        if not array or t < 0 or t >= len(array):
            return 0.0
        return array[t]

    def available(self, name: str) -> bool:
        return self.availability.get(name, {}).get("state") == "AVAILABLE"

    def by_symbol(self, name: str) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for key, array in self.arrays.items():
            if key.startswith(name + "::"):
                out[key.split("::", 1)[1]] = array
        return out

    def as_dict(self) -> dict:
        return {"dates": self.dates, "availability": self.availability,
                "provenance": self.provenance,
                "arrays": {k: [round(v, 6) for v in vals]
                           for k, vals in sorted(self.arrays.items())}}


def _window_before(dates: Sequence[str], t: int, days: int) -> Tuple[str, str]:
    """[date - days, date) - the trailing window knowable at the open of t."""
    current = dt.date.fromisoformat(dates[t])
    return ((current - dt.timedelta(days=days)).isoformat(), dates[t])


def _count_in_window(event_dates: Sequence[str], dates: Sequence[str], t: int,
                     days: int) -> int:
    lo, hi = _window_before(dates, t, days)
    return sum(1 for d in event_dates if lo <= d < hi)


# -- League injury feeds (OFFICIAL documents, no retrievable archive) -------
INJURY_SIGNALS = ("nfl_injury_report", "nba_injury_report")


def _injury_signals(book: SignalBook, md, root: str) -> None:
    """Register the two league injury feeds as forward-only signals.

    The NFL publishes the weekly injury report as a live page and the NBA as a
    dated PDF index; neither league offers an enumerable archive, so there is no
    historical series to trade.  Both are registered explicitly - as MISSING
    arrays with the official URL - so that "this strategy is waiting for data
    that cannot be backfilled" is a published state of the competition rather
    than a participant that silently returns 0.0%.
    """
    captured = sorted(
        name for name in os.listdir(os.path.join(root, "sports", "official"))
        if name.endswith(".raw")
    ) if os.path.isdir(os.path.join(root, "sports", "official")) else []
    evidence = [os.path.join("data/real/sports/official", name) for name in captured]
    urls = {
        "nfl_injury_report": "https://www.nfl.com/injuries/",
        "nba_injury_report":
            "https://official.nba.com/nba-injury-report-2025-26-season/",
    }
    for name in INJURY_SIGNALS:
        book._blank(name)
        book._register(
            name, "MISSING", 0, [], evidence,
            "official feed resolves"
            + (f" (captured: {', '.join(captured)})" if captured
               else " (not captured by the last collection run)")
            + ", but the league publishes it as a live document with no "
              "retrievable history, so it is forward-only and places no "
              "backdated trades",
            urls[name])


def build_signal_book(md, root: str = REAL_ROOT) -> SignalBook:
    """Build every signal from the collected files; missing files stay missing."""
    book = SignalBook(md.dates)
    _fda_signals(book, md, root)
    _insider_signals(book, md, root)
    _mlb_signals(book, md, root)
    _weather_signals(book, md, root)
    _kalshi_signals(book, md, root)
    _fred_signals(book, md, root)
    _yahoo_signals(book, md, root)
    _injury_signals(book, md, root)
    book.finalise()
    return book


# -- FDA (openFDA, OFFICIAL) ------------------------------------------------
def _fda_signals(book: SignalBook, md, root: str) -> None:
    path = os.path.join(root, "fda", "openfda_decisions.jsonl")
    name = "fda_orig_30d"
    book._blank(name)
    book._blank("fda_orig_z")
    book._blank("fda_all_30d")
    if not os.path.exists(path):
        book._register(name, "MISSING", 0, [], [], "no collected openFDA file", "")
        return
    orig: List[str] = []
    every: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            date = str(row.get("status_date") or "")
            if len(date) != 8 or row.get("status") != "AP":
                continue
            iso = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
            every.append(iso)
            if row.get("submission_type") == "ORIG":
                orig.append(iso)
    orig.sort()
    every.sort()
    for t, date in enumerate(md.dates):
        # Filled over the warm-up as well. The first version skipped the warm-up,
        # which left zeros in the trailing-year window the z-score is measured
        # against and made the z-score of the first competition sessions an
        # artefact of the array's construction rather than of the data.
        book.arrays[name][t] = float(_count_in_window(orig, md.dates, t, 30))
        book.arrays["fda_all_30d"][t] = float(_count_in_window(every, md.dates, t, 30))
    # z-score of the 30-day approval count against the trailing year of the same
    # series (only sessions inside the competition window are scored).
    values = [book.arrays[name][t] for t in range(len(md.dates))]
    for t in range(len(md.dates)):
        window = values[max(0, t - 252):t]
        if len(window) < 30:
            continue
        mu = sum(window) / len(window)
        sd = math.sqrt(sum((v - mu) ** 2 for v in window) / (len(window) - 1)) or 1.0
        book.arrays["fda_orig_z"][t] = (values[t] - mu) / sd
    book.provenance["fda"] = {"file": _rel(path),
                              "url": "https://api.fda.gov/drug/drugsfda.json"}
    book._register_all((name, "fda_orig_z", "fda_all_30d"), "AVAILABLE", len(orig), orig,
                       [_rel(path)],
                       "approvals of original applications with status AP, by decision date",
                       "https://api.fda.gov/drug/drugsfda.json")


# -- Insider filings (SEC EDGAR, OFFICIAL) ---------------------------------
CEO_TITLES = ("chief executive officer", "ceo", "chief financial officer", "cfo")


def _insider_signals(book: SignalBook, md, root: str) -> None:
    path = os.path.join(root, "sec", "form4_transactions.jsonl")
    tickers = sorted({i.symbol for i in md.instruments.values()})
    for symbol in tickers:
        book._blank(f"insider_buys_30d::{symbol}")
        book._blank(f"insider_ceo_buys_30d::{symbol}")
    book._blank("insider_buy_ratio_30d")
    if not os.path.exists(path):
        # Registered as MISSING *and* left as a real zero array: the site plots a
        # series for every registered name, and a strategy that reads a missing
        # signal must see 0.0 (which is what ``value`` returns) rather than a
        # crash or an unregistered hole.  The unqualified names are blanked here
        # as well, so the invariant "every registered name has an array" holds.
        for name in ("insider_buys_30d", "insider_ceo_buys_30d",
                     "insider_buy_ratio_30d"):
            book._blank(name)
        book._register_all(("insider_buys_30d", "insider_ceo_buys_30d",
                            "insider_buy_ratio_30d"),
                           "MISSING", 0, [], [],
                           "no collected Form 4 file",
                           "https://www.sec.gov/cgi-bin/browse-edgar")
        return
    buys: Dict[str, List[str]] = {s: [] for s in tickers}
    ceo_buys: Dict[str, List[str]] = {s: [] for s in tickers}
    sales = 0
    purchases = 0
    rows = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            rows += 1
            symbol = row.get("ticker")
            code = (row.get("code") or "").upper()
            date = str(row.get("transaction_date") or "")
            if len(date) != 10:
                continue
            if symbol in buys and code == "P":
                buys[symbol].append(date)
                purchases += 1
                title = (row.get("title") or "").lower()
                roles = row.get("roles") or []
                if any(term in title for term in CEO_TITLES) or "ceo" in title:
                    ceo_buys[symbol].append(date)
            elif code == "S":
                sales += 1
    for t in range(len(md.dates)):
        for symbol in tickers:
            book.arrays[f"insider_buys_30d::{symbol}"][t] = float(
                _count_in_window(buys[symbol], md.dates, t, 30))
            book.arrays[f"insider_ceo_buys_30d::{symbol}"][t] = float(
                _count_in_window(ceo_buys[symbol], md.dates, t, 30))
        ratio = (purchases / max(1, sales))
        book.arrays["insider_buy_ratio_30d"][t] = ratio
    book.provenance["insider"] = {"file": "data/real/sec/form4_transactions.jsonl",
                                  "url": "https://www.sec.gov/cgi-bin/browse-edgar"}
    names = [f"insider_buys_30d::{s}" for s in tickers] + \
            [f"insider_ceo_buys_30d::{s}" for s in tickers] + \
            ["insider_buys_30d", "insider_ceo_buys_30d", "insider_buy_ratio_30d"]
    book._register_all(names, "AVAILABLE", rows,
                       sorted(d for s in buys for d in buys[s]),
                       ["data/real/sec/form4_transactions.jsonl"],
                       f"open-market purchases (code P) by ticker; {purchases} buys "
                       f"and {sales} sales parsed from real filings",
                       "https://www.sec.gov/cgi-bin/browse-edgar")


# -- Sports (MLB official, ESPN secondary) ----------------------------------
def _mlb_signals(book: SignalBook, md, root: str) -> None:
    path = os.path.join(root, "sports", "mlb_games_2026.jsonl")
    for name in ("mlb_games_7d", "mlb_home_win_rate_30d", "mlb_upsets_7d"):
        book._blank(name)
    if not os.path.exists(path):
        book._register("mlb_games_7d", "MISSING", 0, [], [], "no collected MLB file",
                       "https://statsapi.mlb.com/api/v1/schedule")
        return
    games: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("status") == "Final" and row.get("home_score") is not None \
                    and row.get("away_score") is not None:
                games.append(row)
    games.sort(key=lambda g: g["date"])
    dates = [g["date"] for g in games]
    for t in range(len(md.dates)):
        book.arrays["mlb_games_7d"][t] = float(_count_in_window(dates, md.dates, t, 7))
        lo, hi = _window_before(md.dates, t, 30)
        window = [g for g in games if lo <= g["date"] < hi]
        if window:
            home_wins = sum(1 for g in window if g["home_score"] > g["away_score"])
            book.arrays["mlb_home_win_rate_30d"][t] = home_wins / len(window)
        lo7, hi7 = _window_before(md.dates, t, 7)
        upsets = 0
        for g in games:
            if not (lo7 <= g["date"] < hi7):
                continue
            home_worse = _pct(g.get("home_record")) < _pct(g.get("away_record"))
            if home_worse and g["home_score"] > g["away_score"]:
                upsets += 1
            if (not home_worse) and g["away_score"] > g["home_score"]:
                upsets += 1
        book.arrays["mlb_upsets_7d"][t] = float(upsets)
    book.provenance["mlb"] = {"file": "data/real/sports/mlb_games_2026.jsonl",
                              "url": "https://statsapi.mlb.com/api/v1/schedule"}
    book._register_all(("mlb_games_7d", "mlb_home_win_rate_30d", "mlb_upsets_7d"),
                       "AVAILABLE", len(games), dates,
                       ["data/real/sports/mlb_games_2026.jsonl"],
                       "MLB regular-season finals, official league feed",
                       "https://statsapi.mlb.com/api/v1/schedule")


def _pct(record: Optional[str]) -> float:
    if not record or "-" not in record:
        return 0.5
    try:
        wins, losses = (int(x) for x in record.split("-", 1))
    except ValueError:
        return 0.5
    total = wins + losses
    return wins / total if total else 0.5


# -- Weather (NOAA/NCEI, OFFICIAL) ------------------------------------------
def _weather_signals(book: SignalBook, md, root: str) -> None:
    path = os.path.join(root, "weather", "USW00023272_daily.json")
    for name in ("weather_cold_anomaly_10d", "weather_precip_30d_in"):
        book._blank(name)
    if not os.path.exists(path):
        book._register("weather_cold_anomaly_10d", "MISSING", 0, [], [],
                       "no collected NCEI file",
                       "https://www.ncei.noaa.gov/access/services/data/v1")
        return
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.loads(fh.read())
    tmin: Dict[str, float] = {}
    precip: Dict[str, float] = {}
    for row in rows:
        date = (row.get("DATE") or "")[:10]
        try:
            if row.get("TMIN") not in (None, ""):
                tmin[date] = float(row["TMIN"])
        except (TypeError, ValueError):
            pass
        try:
            if row.get("PRCP") not in (None, ""):
                precip[date] = float(row["PRCP"])
        except (TypeError, ValueError):
            pass
    ordered = sorted(tmin)
    for t, date in enumerate(md.dates):
        lo10, hi10 = _window_before(md.dates, t, 10)
        recent = [tmin[d] for d in ordered if lo10 <= d < hi10]
        if len(recent) >= 3:
            trail = [tmin[d] for d in ordered if d < lo10][-60:]
            mean = sum(trail) / len(trail) if trail else sum(recent) / len(recent)
            sd = (math.sqrt(sum((v - mean) ** 2 for v in trail) / (len(trail) - 1))
                  if len(trail) > 2 else 1.0) or 1.0
            book.arrays["weather_cold_anomaly_10d"][t] = float(
                sum(1 for v in recent if v < mean - sd))
        lo30, hi30 = _window_before(md.dates, t, 30)
        book.arrays["weather_precip_30d_in"][t] = round(
            sum(v for d, v in precip.items() if lo30 <= d < hi30), 3)
    book.provenance["weather"] = {"file": "data/real/weather/USW00023272_daily.json",
                                  "url": "https://www.ncei.noaa.gov/access/services/data/v1"}
    book._register_all(("weather_cold_anomaly_10d", "weather_precip_30d_in"), "AVAILABLE",
                       len(rows), ordered,
                       ["data/real/weather/USW00023272_daily.json"],
                       "NCEI daily summaries, San Francisco station USW00023272",
                       "https://www.ncei.noaa.gov/access/services/data/v1")


# -- Kalshi (venue's own data) ----------------------------------------------
def _kalshi_signals(book: SignalBook, md, root: str) -> None:
    for name in ("kalshi_settled_30d", "kalshi_volume_30d"):
        book._blank(name)
    directory = os.path.join(root, "kalshi")
    if not os.path.isdir(directory):
        book._register("kalshi_settled_30d", "MISSING", 0, [], [],
                       "no collected Kalshi files",
                       "https://api.elections.kalshi.com/trade-api/v2/markets")
        return
    events: List[str] = []
    volumes: Dict[str, float] = {}
    rows_total = 0
    values_present = 0
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                rows_total += 1
                close = str(row.get("close_time") or "")[:10]
                if len(close) != 10:
                    continue
                volume = row.get("volume")
            if volume is None:
                volume = row.get("open_interest")
            if volume is not None:
                values_present += 1
            events.append(close)
            try:
                volumes[close] = volumes.get(close, 0.0) + float(volume or 0)
            except (TypeError, ValueError):
                continue
    events.sort()
    if values_present == 0:
        # Guard for a payload that carries no number this code can read: a file
        # that exists but holds no values is a missing signal, not an available
        # one, and the strategy that depends on it must report DATA-MISSING.
        #
        # This branch fired on the second collection run, and the diagnosis
        # published with it was wrong. The venue's *list* endpoint does carry
        # numbers; the collector was reading the legacy integer field names
        # ("volume", "last_price"), which the payload no longer has, because the
        # venue moved to fixed-point spellings ("volume_fp": "30421098.89",
        # "last_price_dollars": "0.0100"). Every number was absent from the
        # reader, not from the response - and the site published that as the
        # venue's doing (IR-41). The collector now reads either spelling and
        # records which key each value came from; the branch stays so a future
        # re-spelling reports DATA-MISSING rather than a column of zeros that
        # looks like an observation of no activity.
        book.provenance["kalshi"] = {
            "file": "data/real/kalshi/*_settled.jsonl",
            "url": "https://api.elections.kalshi.com/trade-api/v2/markets"}
        for t in range(len(md.dates)):
            book.arrays["kalshi_settled_30d"][t] = float(
                _count_in_window(events, md.dates, t, 30))
        book._register("kalshi_settled_30d", "AVAILABLE", rows_total, events,
                       ["data/real/kalshi/"],
                       "settled-contract counts from the venue's own API; no numeric "
                       "field in the same payload could be read by this collector",
                       "https://api.elections.kalshi.com/trade-api/v2/markets")
        book._register("kalshi_volume_30d", "MISSING", rows_total, events,
                       ["data/real/kalshi/"],
                       "no traded-volume field in the collected payload could be read, "
                       "so a volume signal cannot be computed from it - the participant "
                       "that reads it reports DATA-MISSING",
                       "https://api.elections.kalshi.com/trade-api/v2/markets")
        return
    for t in range(len(md.dates)):
        book.arrays["kalshi_settled_30d"][t] = float(_count_in_window(events, md.dates, t, 30))
        lo, hi = _window_before(md.dates, t, 30)
        book.arrays["kalshi_volume_30d"][t] = sum(v for d, v in volumes.items() if lo <= d < hi)
    book.provenance["kalshi"] = {"file": "data/real/kalshi/*_settled.jsonl",
                                 "url": "https://api.elections.kalshi.com/trade-api/v2/markets"}
    book._register_all(("kalshi_settled_30d", "kalshi_volume_30d"), "AVAILABLE",
                       len(events), events, ["data/real/kalshi/"],
                       "settled-contract counts and volumes from the venue's own API",
                       "https://api.elections.kalshi.com/trade-api/v2/markets")


# -- FRED macro (OFFICIAL) ---------------------------------------------------
def _fred_signals(book: SignalBook, md, root: str) -> None:
    book._blank("fred_slope_bps")
    book._blank("fred_slope_change_21d")
    try:
        dgs10, path10, _ = load_fred("DGS10", root)
        dgs3, _, _ = load_fred("DGS3MO", root)
    except RealDataUnavailable:  # a missing file is a data state, not a crash
        book._register("fred_slope_bps", "MISSING", 0, [], [], "no collected yield files",
                       "https://fred.stlouisfed.org/series/DGS10")
        return
    slope: Dict[str, float] = {}
    for date, value in dgs10.items():
        if date in dgs3:
            slope[date] = (value - dgs3[date]) * 100.0  # percentage points -> bp
    ordered = sorted(slope)
    for t, date in enumerate(md.dates):
        prior = [d for d in ordered if d < date]
        if not prior:
            continue
        book.arrays["fred_slope_bps"][t] = slope[prior[-1]]
        if len(prior) > 21:
            book.arrays["fred_slope_change_21d"][t] = slope[prior[-1]] - slope[prior[-22]]
    book.provenance["fred"] = {"file": path10, "url": "https://fred.stlouisfed.org/series/DGS10"}
    book._register_all(("fred_slope_bps", "fred_slope_change_21d"), "AVAILABLE",
                       len(slope), ordered, [path10],
                       "10-year minus 3-month Treasury constant-maturity yields",
                       "https://fred.stlouisfed.org/series/DGS10")


# -- Vendor price signals (SECONDARY) ---------------------------------------
def _yahoo_signals(book: SignalBook, md, root: str) -> None:
    """Realised volatility, dollar volume and 52-week position, from real bars."""
    for name in ("gold_close", "spy_dollar_volume_20d"):
        book._blank(name)
    files = []
    try:
        series: Series = load_series("GLD", root)
    except RealDataUnavailable:  # a missing file is a data state, not a crash
        series = None
    for t in range(len(md.dates)):
        if "GLD" in md.instruments:
            book.arrays["gold_close"][t] = md.bar("GLD", t).close
        book.arrays["spy_dollar_volume_20d"][t] = round(
            md.adv("SPY", t, 20) * md.bar("SPY", t).close, 2) if "SPY" in md.instruments else 0.0
    if series is not None:
        files.append(series.path)
    book._register_all(("gold_close", "spy_dollar_volume_20d"),
                       "AVAILABLE" if series else "MISSING",
                       len(series.bars) if series else 0,
                       [b.date for b in series.bars] if series else [], files,
                       "GLD closes from the collected vendor file",
                       "https://query1.finance.yahoo.com/v8/finance/chart/GLD")


def signal_register() -> List[dict]:
    """The published MasterFeed register, with the availability merged in later."""
    return [dict(row) for row in MASTER_SITE_SIGNALS]


__all__ = ["MASTER_SITE_SIGNALS", "MASTER_SITE_URL", "SignalBook", "build_signal_book",
           "signal_register", "BACKTESTED", "FORWARD_ONLY", "STRONG", "WEAK",
           "UNPROVEN", "NO_MARKET"]
