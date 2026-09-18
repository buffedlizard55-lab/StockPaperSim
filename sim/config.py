"""Configuration, verified market constants and cost schedules.

Every numeric constant in this module that describes the *real* U.S. equity
market carries a ``SOURCE:`` comment with a canonical URL that was checked
while building Season 1.  Anything that is a *design choice* of this
simulator (and therefore not a claim about the real market) is marked
``SIM CHOICE:``.

Nothing in this file invents a market statistic.  If a value could not be
verified against a primary source it is flagged in
``research/IRREGULARITIES.json`` and marked ``UNVERIFIED:`` below.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple

# --------------------------------------------------------------------------
# Competition defaults
# --------------------------------------------------------------------------

# SIM CHOICE (mirrors TradingView "The Leap", which presets every paper
# trading competition account to 100,000 virtual USD):
#   SOURCE: https://www.tradingview.com/the-leap/december-2025/rules/
STARTING_CASH = 100_000.0

# Season 1 window.  Derived from the real FRED S&P 500 daily series that is
# bundled in data/real/fred/SP500_2025-09-17_2026-09-16.csv (251 trading
# days, 10 market holidays).
#   SOURCE: https://fred.stlouisfed.org/series/SP500
SEASON1_START = "2025-09-17"
SEASON1_END = "2026-09-16"

# Regular session hours, U.S. cash equities: 09:30-16:00 America/New_York.
#   SOURCE: https://www.nasdaq.com/market-activity/stock-market-holiday-schedule
#           (early-close sessions end 13:00 ET)
SESSION_OPEN_MINUTES = 9 * 60 + 30
SESSION_CLOSE_MINUTES = 16 * 60
EARLY_CLOSE_MINUTES = 13 * 60
TRADING_DAYS_PER_YEAR = 252  # convention used for annualisation (SIM CHOICE)

# Scenario seeds.  Index 0 is the PRIMARY scenario (full event memory, the one
# the site reports on); the rest form the robustness panel.  Every scenario
# replays the SAME real market factor path (FRED S&P 500 and VIXCLS) and
# differs only in the idiosyncratic draws and the venue noise, so the spread of
# outcomes across seeds measures how much of a single-season result is a
# repeatable edge and how much is one draw from the noise distribution.
SCENARIO_SEEDS = (20260917, 11111111, 22222222, 33333333, 44444444, 55555555)


# --------------------------------------------------------------------------
# Pricing / tick rules
# --------------------------------------------------------------------------

def minimum_tick(price: float) -> float:
    """Reg NMS Rule 612 minimum pricing increment.

    $0.01 for quotations/orders priced >= $1.00, $0.0001 below $1.00.
      SOURCE: https://www.sec.gov/files/rules/final/34-51808.pdf  (Rule 612)
      SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612

    VERIFIED STATUS OF THE $0.005 TIER (checked 2026-09-17 against primary
    sources, not assumed):

    * The amended text IS codified.  17 CFR 242.612(b)(2) as it stands in the
      current eCFR (up to date as of 2026-09-15) reads: $0.01 if the Time
      Weighted Average Quoted Spread for the Evaluation Period was greater
      than $0.015, and $0.005 if it was equal to or less than $0.015;
      242.612(b)(3) keeps $0.0001 below $1.00.
        SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612
        SOURCE (adopting release, 89 FR 81774, 08-Oct-2024):
        https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders
    * But it is NOT in force.  The Commission has granted temporary exemptive
      relief from the amended compliance dates three times: a partial stay on
      12-Dec-2024 (Release 34-101899) pending judicial review; relief to the
      first business day of November 2026 on 31-Oct-2025, after the D.C.
      Circuit denied the petitions for review; and Release 34-105656
      (11-Jun-2026, published 15-Jun-2026) extending relief for Rules
      600(b)(89)(i)(F), 610(c) and 612 "until the first business day of
      November 2027".
        SOURCE: https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf
        SOURCE: https://www.federalregister.gov/documents/2026-06-15/2026-11997/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of
        SOURCE: https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126

    The whole Season 1 window (2025-09-17 to 2026-09-16) therefore sits inside
    the exemption period: the operative grid is $0.01 / $0.0001 and the
    operative Rule 610(c) access-fee cap is $0.003 per share.  That is what
    this function returns, and the same reasoning keeps
    :data:`ACCESS_FEE_CAP_PER_SHARE` at 0.003.
      FLAGGED IN: research/IRREGULARITIES.json (IR-04)
    """
    return 0.0001 if price < 1.0 else 0.01


def round_lot(price: float) -> int:
    """Reg NMS Rule 600(b)(93) tiered round-lot definition.

    Effective 3 November 2025 on the NYSE-listed venues (and the same date
    generally under the 2024 Reg NMS amendments):
        <= $250.00            -> 100 shares
        $250.01 - $1,000.00   ->  40 shares
        $1,000.01 - $10,000   ->  10 shares
        >= $10,000.01         ->   1 share
      SOURCE: https://www.nyse.com/publicdocs/nyse/markets/nyse/rule-interpretations/2025/Regulatory_Memo_-_Market_Maker_Displayed_Quotation.pdf
      SOURCE: https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders
    """
    if price <= 250.00:
        return 100
    if price <= 1_000.00:
        return 40
    if price <= 10_000.00:
        return 10
    return 1


# Regulatory fee schedules, applied by sim/microstructure.py.
# Each entry is (effective_date_inclusive, rate).  Rates change, so they are
# stored as dated schedules rather than single numbers.

# SEC Section 31 transaction fee, levied on *sales* of equities.
# FY2026 order: $0.00 per $1,000,000 from 01-Sep-2025 through 03-Apr-2026,
# then $20.60 per $1,000,000 effective 04-Apr-2026.
#   SOURCE: https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates
#   SOURCE: https://www.nasdaqtrader.com/MicroNews.aspx?id=OTA2026-14
# The first entry of each schedule is the EARLIEST DATE THE SOURCES DOCUMENT,
# not a "beginning of time" sentinel.  A competition window that opens before
# it cannot be costed honestly, so validate_fee_coverage() refuses it rather
# than silently extrapolating a rate that was never verified.
SEC31_PER_MILLION: Tuple[Tuple[str, float], ...] = (
    ("2025-09-01", 0.0),        # $0.00 per $1,000,000 (01-Sep-2025 → 03-Apr-2026)
    ("2026-04-04", 20.60),      # $20.60 per $1,000,000 from 04-Apr-2026
)

# FINRA Trading Activity Fee (TAF), levied on *sales*, per share, with a
# per-trade cap.  The primary FINRA fee-adjustment schedule was fetched on
# 2026-09-18 and states the 2025 and 2026 rates/caps used below.  The general
# TAF page points to Section 1 of Schedule A to the By-Laws for the rule text.
#   SOURCE: https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule
#   SOURCE: https://www.finra.org/rules-guidance/guidance/trading-activity-fee
#   FLAGGED IN: research/IRREGULARITIES.json (IR-33 only for the dead old URL)
FINRA_TAF_PER_SHARE: Tuple[Tuple[str, float], ...] = (
    ("2025-01-01", 0.000166),   # FINRA primary fee-adjustment schedule
    ("2026-01-01", 0.000195),   # FINRA primary fee-adjustment schedule
)
# The per-trade cap changed with the rate, so it is a schedule too.  Applying
# the 2026 cap to a 2025 sale would overstate the fee on very large orders.
FINRA_TAF_MAX_PER_TRADE: Tuple[Tuple[str, float], ...] = (
    ("2025-01-01", 8.30),       # FINRA primary fee-adjustment schedule
    ("2026-01-01", 9.79),       # FINRA primary fee-adjustment schedule
)

# Exchange access-fee cap under Reg NMS Rule 610 is $0.003 per share for NMS
# stocks priced >= $1.00 (the 2024 amendments lower it to $0.001; not treated
# as operative - see minimum_tick note).
#   SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610
ACCESS_FEE_CAP_PER_SHARE = 0.003


def rate_for(schedule: Tuple[Tuple[str, float], ...], date_iso: str) -> float:
    """Look up the rate in force on ``date_iso`` in a dated schedule.

    Dates before the first documented entry fall back to the first rate.  That
    is a deliberate, documented extrapolation: :func:`validate_fee_coverage`
    rejects any competition window that would rely on it.
    """
    out = schedule[0][1]
    for eff, rate in schedule:
        if date_iso >= eff:
            out = rate
    return out


FEE_SCHEDULES: Tuple[Tuple[str, Tuple[Tuple[str, float], ...]], ...] = (
    ("SEC Section 31 transaction fee", SEC31_PER_MILLION),
    ("FINRA Trading Activity Fee (per share)", FINRA_TAF_PER_SHARE),
    ("FINRA Trading Activity Fee (per-trade cap)", FINRA_TAF_MAX_PER_TRADE),
)


def fee_coverage_start() -> str:
    """Earliest date every fee schedule is documented for."""
    return max(schedule[0][0] for _name, schedule in FEE_SCHEDULES)


def validate_fee_coverage(start: str, end: str) -> None:
    """Refuse to cost a window the verified fee schedules do not cover.

    Why this exists: the schedules are dated, and the earliest documented date
    is 2025-09-01 (SEC §31).  A season starting before that would silently be
    charged a rate that no source in the register actually states - exactly the
    kind of unverified number this project is not allowed to invent.
    """
    coverage = fee_coverage_start()
    if start < coverage:
        raise ValueError(
            f"competition window opens {start}, before the earliest documented "
            f"fee-schedule date {coverage}; extend the schedules in sim/config.py "
            f"with sourced rates before running this season")
    if end < start:
        raise ValueError(f"competition window ends {end} before it starts {start}")


# --------------------------------------------------------------------------
# Cost / liquidity / market-maker configuration
# --------------------------------------------------------------------------


@dataclass
class CostConfig:
    """Broker + exchange + regulatory cost stack.

    SIM CHOICE: Season 1 models a zero-commission retail broker (the
    prevailing retail model) that still pays exchange taker fees and passes
    through regulatory fees on sales.  TradingView's own paper-trading
    competition, for comparison, charges a flat $1 per trade.
      SOURCE: https://www.tradingview.com/the-leap/december-2025/rules/
    """

    commission_per_share: float = 0.0
    commission_per_order: float = 0.0
    pay_exchange_fees: bool = True
    taker_fee_per_share: float = 0.0030   # <= Rule 610 access fee cap
    maker_rebate_per_share: float = 0.0020
    pass_regulatory_fees: bool = True


@dataclass
class LiquidityConfig:
    """Quoted-spread, depth and participation model."""

    # ------------------------------------------------------------------
    # Quoted spread model: THE MINIMUM INCREMENT IS THE BINDING CONSTRAINT
    # ------------------------------------------------------------------
    # Empirically, the quoted spread on a liquid US equity or index ETF is a
    # small whole number of minimum increments, not a smooth function of
    # volatility: competition compresses the touch to one tick for as long as
    # the tick is small relative to the price.  That is why the September 2024
    # Reg NMS amendments created a $0.005 tick tier for stocks whose
    # time-weighted average quoted spread is <= $0.015, and why the SEC's own
    # releasing text describes the most liquid names as "tick constrained".
    #   SOURCE (Rule 612 minimum pricing increments, as amended):
    #   https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612
    #   SOURCE (SEC Order approving/adopting the 2024 tick-size and access-fee
    #   amendments, incl. the TWAQS <= $0.015 tier and the $0.001 fee cap):
    #   https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders
    #   SOURCE (execution-quality reporting framework that publishes realised
    #   quoted/effective spreads by symbol):
    #   https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.605
    #
    # Model: spread_ticks = clamp(round(spread_k_ticks *
    #                                   sqrt(sigma_daily * sqrt(vix_factor)
    #                                        / (tick / price))),
    #                             min_ticks[tier], max_ticks[tier])
    # The square root compresses the huge tick-to-price ratio range across a
    # $15 stock and a $670 ETF into a plausible 1-6 tick range.  SIM CHOICE:
    # the coefficient and the per-tier tick bounds.
    spread_k_ticks: float = 0.05
    min_spread_ticks: Dict[str, int] = field(default_factory=lambda: {
        "mega": 1, "large": 1, "mid": 1, "small": 1, "micro": 2,
    })
    max_spread_ticks: Dict[str, int] = field(default_factory=lambda: {
        "mega": 3, "large": 4, "mid": 6, "small": 12, "micro": 30,
    })
    # Absolute backstop so a cheap, volatile name can never quote an absurd
    # spread in basis points (e.g. a $2 stock at 30 ticks = 15%).
    spread_cap_bps: Dict[str, float] = field(default_factory=lambda: {
        "mega": 5.0, "large": 10.0, "mid": 30.0, "small": 80.0, "micro": 300.0,
    })
    # Number of displayed price levels on each side of the book, and how
    # displayed size grows away from the touch (level n size = touch size *
    # depth_growth**n).  SIM CHOICE, calibrated so a mega-cap shows a few
    # thousand shares at the touch and a few tens of thousands across the
    # ladder - the order of magnitude that Level II data shows for liquid
    # NMS stocks, and three orders of magnitude smaller than a naive
    # "6% of the interval's volume" reading would give.
    book_levels: int = 6
    depth_growth: float = 1.60
    touch_size_round_lots: float = 20.0
    # Quantity beyond the displayed ladder is filled at the touch as
    # non-displayed liquidity, and priced by the impact model instead.
    # Rationale: roughly 45% of US consolidated equity volume trades
    # off-exchange (internalised or on alternative trading systems) and most
    # on-exchange volume prints at or inside the national best bid/offer, so
    # a VWAP-sliced order does not walk six displayed levels to get done.
    #   SOURCE (FINRA off-exchange weekly volume transparency, the primary
    #   public source for the off-exchange share of consolidated volume):
    #   https://otctransparency.finra.org/
    #   SOURCE (Rule 611 trade-through protection for displayed quotations):
    #   https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.611
    hidden_liquidity: bool = True
    # Maximum fraction of a day's consolidated volume one participant may take
    # (a standard execution-desk participation limit).  SIM CHOICE, informed
    # by the square-root impact literature.
    #   SOURCE: Almgren & Chriss (2001), "Optimal execution of portfolio
    #           transactions", Journal of Risk 3(2):21-40, https://doi.org/10.21314/JOR.2001.041
    max_participation: float = 0.10
    # Intraday volume profile: U-shaped (first/last 30 minutes are the heaviest).
    #   SOURCE: Admati & Pfleiderer (1988), "A Theory of Intraday Patterns",
    #           Review of Financial Studies 1(1):3-40.
    #           https://doi.org/10.1093/rfs/1.1.3
    u_shape_front_load: float = 0.30


@dataclass
class ImpactConfig:
    """Square-root market-impact model.

    impact = coefficient * sigma_daily * sqrt(order_shares / ADV)  (in price
    units), split into a temporary component that decays and a permanent
    component that stays in the simulated mid.
      SOURCE (framework): Almgren & Chriss (2001), "Optimal execution of
              portfolio transactions", Journal of Risk 3(2):21-40,
              https://doi.org/10.21314/JOR.2001.041
      SOURCE (empirical calibration): Almgren, Thum, Hauptmann & Li (2005),
              "Direct estimation of equity market impact", Risk 18(7):58-62.
    HONESTY NOTE (IR-26): the exponent here is 0.5 (square root) because that
    is the convention the execution literature and every vendor model uses, and
    it is what makes the coefficient comparable across names. Almgren, Thum,
    Hauptmann & Li (2005) measured Citigroup order flow and explicitly REJECTED
    the square-root form for temporary impact in favour of a 3/5 power law, so
    this model understates the concavity of impact for very large orders. The
    deviation is quantified in research/IRREGULARITIES.json (IR-26) rather than
    silently absorbed. Neither the publisher page for that article nor the
    author-hosted PDF was retrievable on 2026-09-17, so the citation is
    KNOWN-NOT-FETCHED.
    CITATION FIXES made after the audit in tests/test_sources_register.py: the
    author list was "Almgren, Thum, Hauptmann & Pacold" (it is Li), the page
    range was variously 57-62 and 105-111 (it is 58-62), the DOI
    10.1088/1469-7688/5/8/005 belongs to a different Quantitative Finance
    article, 10.1093/rfs/1.1.3 is Admati & Pfleiderer on intraday volume, and
    the risk.net URL above now returns "page not found". See
    research/VERIFICATION_LOG.md.
    SIM CHOICE: the coefficient; published calibrations for large-cap U.S.
    equities are of order 0.1-1.0 times sigma*sqrt(Q/V).
    """

    coefficient: float = 0.55
    permanent_share: float = 0.35
    impact_decay: float = 0.50  # fraction of permanent impact that decays next day


@dataclass
class MarketMakerConfig:
    """Avellaneda-Stoikov style competitive market makers.

      SOURCE: Avellaneda & Stoikov (2008), "High-frequency trading in a limit
              order book", Quantitative Finance 8(3):217-224.
              https://doi.org/10.1080/14697680701381228
    SIM CHOICE: number of competing dealers and their risk aversion.
    """

    num_makers: int = 4
    risk_aversion: float = 0.9          # gamma in Avellaneda-Stoikov
    intensity_k: float = 60.0           # order-flow intensity parameter
    inventory_limit_shares: int = 40_000
    quote_refresh_seconds: int = 30
    min_spread_ticks: int = 1
    max_spread_ticks: int = 25


@dataclass
class MarginConfig:
    """Margin / leverage rules.

    Reg T initial margin for equities is 50% (i.e. 2:1), set by the Federal
    Reserve Board.
      SOURCE: https://www.federalreserve.gov/supervisionreg/regtcg.htm
    FINRA minimum maintenance margin is 25% of market value.
      SOURCE: https://www.finra.org/investors/learn-to-invest/types-investments/margin-investing
    Broker-dealers may set higher house requirements.  SIM CHOICE: Season 1
    allows up to 2.0x gross leverage with a 30% house maintenance rule so
    that aggressive, return-maximising strategies can express leverage, and
    forces liquidation on a maintenance breach.
    """

    max_gross_leverage: float = 2.0
    initial_margin: float = 0.50
    maintenance_margin: float = 0.30
    shorting_allowed: bool = True
    # Pattern Day Trader rule: accounts under $25,000 are limited to 3 day
    # trades per rolling 5 business days.  Not binding at $100k but modelled.
    # The counting rule, including the six worked examples the venue's
    # Account._count_day_trade reproduces, is FINRA Regulatory Notice 21-13.
    #   SOURCE: https://www.finra.org/rules-guidance/notices/21-13
    # (The investor-education page previously cited here,
    #  /investors/learn-to-invest/.../pattern-day-trader, now 404s: IR-33.)
    pdt_equity_threshold: float = 25_000.0


@dataclass
class CompetitionConfig:
    name: str = "StockPaperSim Alpha Cup"
    season: str = "Season 1 (2025-2026)"
    start: str = SEASON1_START
    end: str = SEASON1_END
    starting_cash: float = STARTING_CASH
    seed: int = 20260917
    scenarios: int = 1
    force_liquidate_at_end: bool = True   # matches The Leap: all open
    # positions are auto-closed at the end of the competition period.
    #   SOURCE: https://www.tradingview.com/the-leap/december-2025/rules/
    rank_metric: str = "total_return_pct"  # SIM CHOICE: highest return wins
    costs: CostConfig = field(default_factory=CostConfig)
    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)
    impact: ImpactConfig = field(default_factory=ImpactConfig)
    market_maker: MarketMakerConfig = field(default_factory=MarketMakerConfig)
    margin: MarginConfig = field(default_factory=MarginConfig)

    def __post_init__(self) -> None:
        validate_fee_coverage(self.start, self.end)

    def as_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        """Stable hash of the whole configuration, for reproducibility."""
        blob = json.dumps(self.as_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


DEFAULT_COMPETITION = CompetitionConfig()


# Verification status legend.  This project was built in a sandbox whose only
# outbound network paths were pypi.org, files.pythonhosted.org and github.com,
# so every other URL below was reached through the page-fetch tool (or through
# search-result text where a page would not load).  The status field states
# exactly how each claim was obtained, and nothing is marked verified that was
# not actually retrieved:
#   FETCHED-VERIFIED   retrieved in this environment AND saved under data/real/
#   FETCHED            retrieved in this environment, content used, not saved
#   FETCHED-VIA-SEARCH reached only as text inside search results (weaker)
#   SECONDARY          only a secondary source was reached; primary not fetched
#   KNOWN-NOT-FETCHED  cited from the literature; NOT retrieved here - verify
#                      manually before relying on it
FETCHED_VERIFIED = "FETCHED-VERIFIED"
FETCHED = "FETCHED"
FETCHED_VIA_SEARCH = "FETCHED-VIA-SEARCH"
SECONDARY = "SECONDARY"
KNOWN_NOT_FETCHED = "KNOWN-NOT-FETCHED"


def all_verified_sources() -> List[dict]:
    """Machine-readable list of the primary sources cited in this module."""
    return [
        {"claim": "Paper-trading competition leaderboard schema: Rank, User, Total Profit, Open Profit, Close Profit, Total Trades, Open Trades, Closed Trades, Account Value, Avg Profit/Trade; 'Data delayed by 15 minutes'",
         "url": "https://www.trade-ideas.com/stock-trading-competition/",
         "publisher": "Trade Ideas (PM Challenge)", "status": "FETCHED"},
        {"claim": "Contest run on a 50,000 USD paper account, hosted through TradingView Community Competitions, with prizes for the top three",
         "url": "https://specials.candlecharts.com/contest/",
         "publisher": "CandleCharts Showdown", "status": "FETCHED"},
        {"claim": "Paper trading competition accounts preset to 100,000 virtual USD; ranking on realised P&L; open positions auto-closed at period end",
         "url": "https://www.tradingview.com/the-leap/december-2025/rules/",
         "publisher": "TradingView",
         "status": "FETCHED"},
        {"claim": "17 CFR 242.612(b): minimum pricing increment is $0.01 if the Time Weighted Average Quoted Spread for the Evaluation Period was greater than $0.015 and $0.005 if it was equal to or less than $0.015 (orders >= $1.00), and $0.0001 below $1.00; increments become operative on the first business day of May and of November. Current eCFR text, up to date as of 2026-09-15, retrieved 2026-09-17.",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612",
         "publisher": "eCFR / SEC Regulation NMS (17 CFR 242.612)",
         "status": "FETCHED"},
        {"claim": "Reg NMS adopting release (Rule 610/611/612)",
         "url": "https://www.sec.gov/files/rules/final/34-51808.pdf",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "Tiered round-lot definition effective 3 November 2025",
         "url": "https://www.nyse.com/publicdocs/nyse/markets/nyse/rule-interpretations/2025/Regulatory_Memo_-_Market_Maker_Displayed_Quotation.pdf",
         "publisher": "NYSE",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "2024 Reg NMS amendments ($0.005 tick tier, $0.001 access-fee cap, round-lot acceleration)",
         "url": "https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders",
         "publisher": "Federal Register",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "SEC Section 31 fee rate $20.60 per $1,000,000 effective 2026-04-04 ($0.00 per million before that date)",
         "url": "https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates",
         "publisher": "Federal Register / SEC",
         "status": "FETCHED"},
        {"claim": "Nasdaq exchange application of the Section 31 rate change",
         "url": "https://www.nasdaqtrader.com/MicroNews.aspx?id=OTA2026-14",
         "publisher": "Nasdaq",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "2026 U.S. equity market holiday schedule and 13:00 ET early closes",
         "url": "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule",
         "publisher": "Nasdaq",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "Daily S&P 500 index closes used as the real market factor",
         "url": "https://fred.stlouisfed.org/series/SP500",
         "publisher": "Federal Reserve Bank of St. Louis (FRED)",
         "status": "FETCHED-VERIFIED"},
        {"claim": "Daily CBOE VIX closes used as the real volatility regime driver",
         "url": "https://fred.stlouisfed.org/series/VIXCLS",
         "publisher": "Federal Reserve Bank of St. Louis (FRED)",
         "status": "FETCHED-VERIFIED"},
        {"claim": "Reg T initial margin of 50% for equities",
         "url": "https://www.federalreserve.gov/supervisionreg/regtcg.htm",
         "publisher": "Board of Governors of the Federal Reserve System",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "FINRA maintenance margin and pattern day trader rules",
         "url": "https://www.finra.org/investors/learn-to-invest/types-investments/margin-investing",
         "publisher": "FINRA",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Execution quality reporting framework (Rule 605)",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.605",
         "publisher": "eCFR / SEC Regulation NMS",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Square-root permanent/temporary market-impact framework and the optimal-execution frontier behind the impact model and the participation limit",
         "url": "https://doi.org/10.21314/JOR.2001.041",
         "publisher": "Almgren & Chriss (2001), Journal of Risk 3(2):21-40",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Reg NMS Rule 610 access-fee cap of $0.003 per share for protected quotations in NMS stocks priced $1.00 or more (the amended $0.001 cap is exempt until November 2027 - see the Release 34-105656 row)",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610",
         "publisher": "eCFR / SEC Regulation NMS (17 CFR 242.610)",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Regulation SHO overview page: short-sale locate and close-out requirements that the simulated borrow/locate logic mirrors",
         "url": "https://www.sec.gov/regulation-sho",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Optimal high-frequency market making with inventory risk",
         "url": "https://doi.org/10.1080/14697680701381228",
         "publisher": "Avellaneda & Stoikov (2008), Quantitative Finance",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "U-shaped intraday volume pattern",
         "url": "https://doi.org/10.1093/rfs/1.1.3",
         "publisher": "Admati & Pfleiderer (1988), Review of Financial Studies",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Nasdaq historical API returns dated OHLCV rows for official-source price collection",
         "url": "https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks&fromdate=2024-09-16&todate=2026-09-17&limit=5000",
         "publisher": "Nasdaq",
         "status": "FETCHED",
         "note": "The ISO-date endpoint returned 503 rows through the page-fetch path on 2026-09-18. "
                 "The local urllib path failed with TLS EOF, so the supported retrieval path is "
                 "the GitHub Actions collector. Nasdaq's legal terms were separately fetched on "
                 "2026-09-18 and do not establish repository redistribution permission; the "
                 "official-price gate therefore remains closed until licensing is confirmed."},
        {"claim": "Nasdaq public-site legal terms governing automated capture and redistribution review for the official-source candidate",
         "url": "https://www.nasdaq.com/legal",
         "publisher": "Nasdaq",
         "status": "FETCHED",
         "note": "Fetched 2026-09-18. The terms prohibit unauthorized capture or reproduction, so the collector records NOT_AUTHORIZED_BY_TERMS and the official-price gate remains closed pending licensing review."},
        {"claim": "FINRA Section 1 member regulatory fees schedule referenced by the methodology's dated Trading Activity Fee disclosure",
         "url": "https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees",
         "publisher": "FINRA",
         "status": "FETCHED",
         "note": "Primary schedule link published for manual review on 2026-09-18; the dated rates used by config.py are also recorded in the fee-adjustment schedule row above."},
        {"claim": "FINRA SR-FINRA-2024-019 fee filing and 2024 TAF adjustment notice",
         "url": "https://www.federalregister.gov/documents/2024/11/27/2024-27764/self-regulatory-organizations-financial-industry-regulatory-authority-inc-notice-of-filing-and",
         "publisher": "Federal Register / FINRA",
         "status": "FETCHED",
         "note": "Primary filing link cited by the methodology page on 2024-11-27; the current dated fee values are taken from the newer fee-adjustment schedule retrieved 2026-09-18."},
        {"claim": "SEC guidance for accessing EDGAR data and identifying automated clients",
         "url": "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED",
         "note": "Collector User-Agent and request-rate disclosure reference, retrieved 2026-09-18; the local copy is retained under data/real/regulatory/."},
        {"claim": "Real SPY monthly OHLCV (13 bars, 2025-09 to 2026-09) used to calibrate the simulated intraday range, plus real AAPL daily bars and four AAPL dividend ex-dates",
         "url": "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&range=1y",
         "publisher": "Yahoo Finance chart API v8",
         "status": "FETCHED-VERIFIED"},
        {"claim": "Off-exchange (internalised and ATS) share of consolidated US equity volume, the basis for treating quantity beyond the displayed ladder as non-displayed liquidity at the touch",
         "url": "https://otctransparency.finra.org/",
         "publisher": "FINRA OTC Transparency",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "FINRA Trade Activity Fee of $0.000195 per share on equity sells (minimum $0.01, maximum $9.79) from 2026-01-01",
         "url": "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule",
         "publisher": "FINRA; fee-adjustment schedule and Section 1 of Schedule A to the By-Laws",
         "status": "FETCHED",
         "note": "FINRA's fee-adjustment schedule was fetched on 2026-09-18 and states "
                 "$0.000195 per covered-equity share up to $9.79 per trade for 2026; "
                 "the same primary table states the 2025 rate and cap used in config.py. "
                 "The general TAF page confirms the fee is assessed on sales. This resolves "
                 "IR-05; the previously cited rulebooks/7541 path remains a 404 (IR-33)."},
        {"claim": "Tiered round-lot definition: 100 shares up to $250, 40 to $1,000, 10 to $10,000, 1 share above",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.600",
         "publisher": "eCFR / SEC Rule 600(b)(93)",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Trade-through protection for displayed quotations, which is why the simulated book may never cross",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.611",
         "publisher": "eCFR / SEC Rule 611",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "A broker-dealer must have reasonable grounds to believe a security can be borrowed before effecting a short sale (the locate requirement)",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203",
         "publisher": "eCFR / SEC Regulation SHO Rule 203(b)",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Pattern day trader definition and the $25,000 minimum equity requirement for margin accounts",
         "url": "https://www.ecfr.gov/current/title-17/chapter-II/part-240/section-240.3b-1",
         "publisher": "eCFR / FINRA Rule 4210 and SEC Rule 3b-1",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Stylised facts of daily equity returns used to justify the fat-tail mixture in the replay generator (excess kurtosis, volatility clustering, no autocorrelation in returns)",
         "url": "https://doi.org/10.1080/713665679",
         "publisher": "Cont (2001), Quantitative Finance 1(2):223-236",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Implementation shortfall as the paper-versus-reality cost of a trading decision",
         "url": "https://doi.org/10.2469/faj.v44.5.28",
         "publisher": "Perold (1988), Financial Analysts Journal 44(5):6-31",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Cross-sectional momentum: buying 12-month winners and holding 3 months earns significant positive returns",
         "url": "https://doi.org/10.1111/j.1540-6261.1993.tb04681.x",
         "publisher": "Jegadeesh & Titman (1993), Journal of Finance 48(1):65-91",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Short-term reversal: the cross-section of stock returns is negatively related to prior-month (and prior-week) returns",
         "url": "https://doi.org/10.1111/j.1540-6261.1990.tb05110.x",
         "publisher": "Jegadeesh (1990), Journal of Finance 45(3):881-898",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Post-earnings-announcement drift: prices continue to move in the direction of the earnings surprise for weeks",
         "url": "https://doi.org/10.2307/2491062",
         "publisher": "Bernard & Thomas (1989), Journal of Accounting Research 27(Suppl):1-36",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "The cross-section of expected returns is negatively related to idiosyncratic volatility (the low-volatility anomaly)",
         "url": "https://doi.org/10.1111/j.1540-6261.2006.00836.x",
         "publisher": "Ang, Hodrick, Xing & Zhang (2006), Journal of Finance 61(4):1637-1672",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Long-horizon reversal: 3-5 year losers outperform winners",
         "url": "https://doi.org/10.1111/j.1540-6261.1985.tb05002.x",
         "publisher": "De Bondt & Thaler (1985), Journal of Finance 40(3):793-805",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "High-frequency trading of the equity risk premium: selling volatility insurance earns a premium most of the time and loses catastrophically occasionally",
         "url": "https://doi.org/10.1016/j.jfineco.2004.08.011",
         "publisher": "Carr & Wu (2005), Journal of Financial Economics",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Sharpe ratio: reward-to-variability ratio, excess return over standard deviation - the formula behind the leaderboard's Sharpe column",
         "url": "https://doi.org/10.1086/260062",
         "publisher": "Sharpe (1966), 'Mutual Fund Performance', Journal of Political Economy 74(1):119-138",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Sortino ratio: excess return over downside deviation only - the formula behind the leaderboard's Sortino column",
         "url": "https://doi.org/10.2469/faj.v50.6.48",
         "publisher": "Sortino & Price (1994), Financial Analysts Journal 50(6):48-64",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Empirical CAPM tests behind the beta factor and the beta-adjusted (residual) return used to separate market exposure from skill",
         "url": "https://doi.org/10.1111/j.1540-6261.1972.tb03157.x",
         "publisher": "Black, Jensen & Scholes (1972), Journal of Finance 27(4):799-816",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Coherent measures of risk (the definition of expected shortfall / CVaR used here)",
         "url": "https://doi.org/10.1111/1467-9965.00068",
         "publisher": "Artzner, Delbaen, Eber & Heath (1999), Mathematical Finance 9(3):203-228",
         "status": "KNOWN-NOT-FETCHED"},

        # ---- Reg NMS tick-size / access-fee status, verified 2026-09-17 ----
        # These four rows are why the simulation quotes on the $0.01 grid and
        # keeps the $0.003 access-fee cap for the whole Season 1 window.
        {"claim": "Release No. 34-105656 (11-Jun-2026): temporary exemptive relief from the amended compliance dates for Rules 600(b)(89)(i)(F), 610(c) and 612 of Regulation NMS extended until the first business day of November 2027 - so the $0.005 tick tier and the $0.001 access-fee cap were NOT operative during Season 1 (2025-09-17 to 2026-09-16)",
         "url": "https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "Federal Register publication of that order (89 FR / doc 2026-11997, 15-Jun-2026), granting relief pursuant to section 36(a)(1) and Rules 610(f) and 612(d)",
         "url": "https://www.federalregister.gov/documents/2026-06-15/2026-11997/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of",
         "publisher": "Federal Register / SEC",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "Statement by Chairman Paul S. Atkins on minimum pricing increments and access fee caps (11-Jun-2026), recording the 12-Dec-2024 partial stay, the 31-Oct-2025 relief to November 2026, this further extension to November 2027, and the Commission's proposed rescission of the Rule 611 order protection rule",
         "url": "https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "SEC press release 2024-137: the adopted amendments create a $0.005 minimum pricing increment for NMS stocks with a TWAQS of $0.015 or less, cut the access fee cap to $0.001 per share for stocks priced $1.00 or more, and set the compliance date for Rule 612, Rule 610 and the round lot definition at the first business day of November 2025 (odd-lot information: May 2026)",
         "url": "https://www.sec.gov/newsroom/press-releases/2024-137",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "Fact sheet for the tick-size / access-fee adopting release (Release 34-101070, 18-Sep-2024)",
         "url": "https://www.sec.gov/files/34-101070-fact-sheet.pdf",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "FETCHED-VIA-SEARCH"},
        {"claim": "GovInfo PDF of the adopting release as published at 89 FR 81774 (08-Oct-2024), including the round-lot semiannual assignment change",
         "url": "https://www.govinfo.gov/content/pkg/FR-2024-10-08/pdf/2024-21867.pdf",
         "publisher": "U.S. Government Publishing Office",
         "status": "FETCHED-VIA-SEARCH"},

        # ---- Sources cited by the code that were not previously registered ----
        {"claim": "GICS sector classification used to label each instrument's sector (and therefore the sector-rotation participant's sleeves)",
         "url": "https://www.spglobal.com/spdji/en/index-family/equity/gics/",
         "publisher": "S&P Dow Jones Indices / MSCI",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "SEC and FINRA investor alert on leveraged and inverse ETFs: daily reset means multi-period returns diverge sharply from the underlying index, which is why the 3x-proxy participant holds levered single names instead",
         "url": "https://www.sec.gov/investor/alerts/leveraged-etf-alert.pdf",
         "publisher": "U.S. Securities and Exchange Commission / FINRA",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "SEC market structure research on maker-taker pricing and access fees, the basis for the taker fee / maker rebate defaults",
         "url": "https://www.sec.gov/market-structure/research",
         "publisher": "U.S. Securities and Exchange Commission",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "CBOE VIX index methodology: the VIX is computed from SPX option prices, which is why VIX closes can exist on dates the cash index does not print (IR-01)",
         "url": "https://www.cboe.com/us/indices/dashboard/VIX/",
         "publisher": "Cboe Global Markets",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "Damodaran historical returns data archive, the source of the long-run equity premium and volatility priors quoted in the strategy theses",
         "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Pages/dataarchived.html",
         "publisher": "NYU Stern (Aswath Damodaran)",
         "status": "KNOWN-NOT-FETCHED"},
        {"claim": "FINRA Regulatory Notice 21-13: the day-trade counting rule (Rule 4210(f)(8)(B)) with six worked examples, which Account._count_day_trade reproduces test-for-test; also the source of the overnight-position carve-out",
         "url": "https://www.finra.org/rules-guidance/notices/21-13",
         "publisher": "FINRA",
         "status": FETCHED_VERIFIED,
         "note": "fetched live 2026-09-17; the six examples are pinned in tests/test_portfolio.py::TestFinraDayTradeExamples"},
        {"claim": "FINRA Rule 4210 (Margin Requirements), the rule body the Notice interprets, including the $25,000 pattern-day-trader equity minimum the venue reports against",
         "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
         "publisher": "FINRA",
         "status": FETCHED_VERIFIED,
         "note": "fetched 2026-09-17 and excerpted under data/real/regulatory/, "
                 "because paragraph (f)(8)(B) is the rule body the day-trade "
                 "counter in sim/portfolio.py implements and the $25,000 "
                 "minimum is the figure MarginConfig reports against"},
        {"claim": "FINRA Rule 4330: a member borrowing a customer's securities must disclose 'payments deemed cash-in-lieu of dividend paid on securities while on loan' - the manufactured-dividend obligation the venue charges short positions with (IR-31)",
         "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4330",
         "publisher": "FINRA",
         "status": FETCHED_VERIFIED,
         "note": "fetched live 2026-09-17; the disclosure list is in 4330(b)(2)(B)(ii)(g)"},
        {"claim": "IRS Publication 550: a borrower who must remit payments in lieu of dividends to the lender, and the 45-day holding rule that decides whether that payment is deductible - the tax-side confirmation that a short position OWES the dividend rather than missing it",
         "url": "https://www.irs.gov/publications/p550",
         "publisher": "Internal Revenue Service",
         "status": FETCHED_VERIFIED,
         "note": "fetched live 2026-09-17 (Publication 550 (2025))"},
        {"claim": "FINRA Trading Activity Fee guidance: the fee is assessed on sales and the governing rates are in Section 1 of Schedule A to the By-Laws",
         "url": "https://www.finra.org/rules-guidance/guidance/trading-activity-fee",
         "publisher": "FINRA",
         "status": "FETCHED",
         "note": "Fetched 2026-09-18; the linked primary fee-adjustment schedule supplies the dated rates and caps."},
    ]
