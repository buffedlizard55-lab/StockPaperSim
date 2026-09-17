"""Season 1 tradable universe and the provenance of every parameter.

THREE KINDS OF NUMBER LIVE IN THIS FILE AND THEY ARE NEVER MIXED UP:

``real``      Transcribed from a vendor/official response bundled under
              ``data/real/``.  The URL, retrieval date and method are recorded
              in the JSON ``_provenance`` block of that file.
``derived``   Computed *in code* from ``real`` inputs (for example the SPY
              average daily volume implied by twelve real monthly volumes).
``scenario``  A declared simulation parameter for the Season 1 replay.  It is
              NOT a claim about the real world and must never be presented as
              one.  Scenario parameters exist because this sandbox has no
              network route to a licensed equity market-data feed; see
              research/IRREGULARITIES.json (IR-03) and the "Data provenance"
              panel of the published site.

Single-name daily OHLCV for Season 1 is therefore *simulated*: the market
factor (S&P 500 daily closes) and the volatility regime (CBOE VIX daily
closes) are real FRED observations, SPY and AAPL are anchored to real start
and end prices, and every other path is generated from the declared scenario
parameters below with a fixed seed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Sector labels follow the Global Industry Classification Standard (GICS),
# maintained by MSCI and S&P Dow Jones Indices.
#   SOURCE: https://www.spglobal.com/spdji/en/index-family/equity/gics/
GICS_SOURCE = "https://www.spglobal.com/spdji/en/index-family/equity/gics/"


@dataclass
class Instrument:
    symbol: str
    name: str
    sector: str
    asset_type: str              # "ETF" | "EQUITY"
    listing_venue: str           # informational
    price_start: float           # scenario unless provenance says "real"
    price_end_anchor: Optional[float] = None   # real end price if available
    beta: float = 1.0
    sigma_idio_annual: float = 0.25
    alpha_annual: float = 0.0
    # ``alpha_annual`` is the declared *excess* log return over beta times the
    # market factor, per year.  Expected total return is therefore
    # beta * market_return + alpha_annual.  It is a scenario parameter.
    adv_shares: float = 5_000_000.0
    shares_outstanding: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    dividends: List[dict] = field(default_factory=list)
    provenance: Dict[str, str] = field(default_factory=dict)
    tradable_by_strategies: bool = True

    @property
    def is_real_anchored(self) -> bool:
        return self.provenance.get("price_start") == "real" or \
            self.provenance.get("price_end_anchor") == "real"

    @property
    def liquidity_tier(self) -> str:
        """Tier used to floor/cap the quoted spread.

        Derived from average daily *dollar* volume, which is the standard
        liquidity screen used by execution desks and by scanner liquidity
        filters (for example Trade Ideas' "Dollar Volume" and "Average
        Volume" columns).
          SOURCE: https://www.trade-ideas.com/stock-trading-competition/
        """
        dv = self.adv_shares * self.price_start
        if dv >= 5_000_000_000:
            return "mega"
        if dv >= 1_000_000_000:
            return "large"
        if dv >= 250_000_000:
            return "mid"
        if dv >= 50_000_000:
            return "small"
        return "micro"


def _spy() -> Instrument:
    """SPY, anchored to real vendor data.

    Real inputs (see data/real/yahoo/SPY_monthly_1y.json):
      * 659.18  - SPY close for the session preceding the 1-year window
                  (2025-09-16), reported by the API as chartPreviousClose.
      * 754.05  - SPY close for 2026-09-16 (September-2026 month-to-date bar).
      * twelve real monthly volumes -> implied average daily volume.
    Cross-check: 659.18 -> 754.05 is +14.39%, against +14.42% for the real
    FRED S&P 500 index over the identical window (6600.35 -> 7551.81).
    Two independent real sources agree to within 3 bp.
    """
    monthly_volumes = [1781815100, 1664668300, 1656188700, 1600537800,
                       1614970000, 2237624600, 1196537500, 945132300,
                       1314920000, 1064946100, 833955700, 463237000]
    # 21 trading days per month is the convention used for the implied ADV.
    adv = sum(monthly_volumes[:11]) / 11 / 21.0
    return Instrument(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        sector="Broad market ETF",
        asset_type="ETF",
        listing_venue="NYSEArca",
        price_start=659.18,
        price_end_anchor=754.05,
        beta=1.0,
        sigma_idio_annual=0.004,     # scenario: ETF tracking error
        alpha_annual=0.0,            # unused: SPY is endpoint-anchored
        adv_shares=adv,
        fifty_two_week_high=779.37,  # real (vendor meta)
        fifty_two_week_low=629.28,   # real (vendor meta)
        provenance={
            "price_start": "real",
            "price_end_anchor": "real",
            "adv_shares": "derived",
            "fifty_two_week_high": "real",
            "fifty_two_week_low": "real",
            "beta": "definition",
            "sigma_idio_annual": "scenario",
        },
    )


def _aapl() -> Instrument:
    """AAPL, anchored to real vendor data.

    Real inputs (see data/real/yahoo/AAPL_snapshot_2026-09-17.json):
      * 236.70 - AAPL close preceding the 1-year window (2025-09-16).
      * 332.41 - AAPL close for 2026-09-16.
      * 344.57 / 236.65 - fifty-two-week high / low.
      * four real cash dividends with ex-dates inside the window.
    """
    return Instrument(
        symbol="AAPL",
        name="Apple Inc.",
        sector="Information Technology",
        asset_type="EQUITY",
        listing_venue="NasdaqGS",
        price_start=236.70,
        price_end_anchor=332.41,
        beta=1.20,                    # scenario
        sigma_idio_annual=0.22,       # scenario
        alpha_annual=0.0,             # unused: AAPL is endpoint-anchored
        adv_shares=42_000_000.0,      # scenario (real 5-day volumes were
        # 18.6M-50.7M shares; see the bundled snapshot)
        fifty_two_week_high=344.57,   # real
        fifty_two_week_low=236.65,    # real
        dividends=[                   # real
            {"ex_date": "2025-11-10", "amount": 0.26},
            {"ex_date": "2026-02-09", "amount": 0.26},
            {"ex_date": "2026-05-11", "amount": 0.27},
            {"ex_date": "2026-08-10", "amount": 0.27},
        ],
        provenance={
            "price_start": "real",
            "price_end_anchor": "real",
            "fifty_two_week_high": "real",
            "fifty_two_week_low": "real",
            "dividends": "real",
            "beta": "scenario",
            "sigma_idio_annual": "scenario",
            "adv_shares": "scenario",
        },
    )


def _scenario(symbol: str, name: str, sector: str, venue: str, price: float,
              beta: float, sigma: float, alpha: float, adv: float) -> Instrument:
    """Build an instrument whose every numeric field is a scenario parameter."""
    return Instrument(
        symbol=symbol, name=name, sector=sector, asset_type="EQUITY",
        listing_venue=venue, price_start=price, price_end_anchor=None,
        beta=beta, sigma_idio_annual=sigma, alpha_annual=alpha, adv_shares=adv,
        provenance={
            "price_start": "scenario", "beta": "scenario",
            "sigma_idio_annual": "scenario", "alpha_annual": "scenario",
            "adv_shares": "scenario",
        },
    )


def build_universe() -> List[Instrument]:
    """The Season 1 universe: 17 instruments across 9 sectors.

    Scenario parameters below are declared design inputs for the replay.
    They were chosen to span a realistic cross-section of beta, volatility,
    liquidity and drift so that the strategy set is exercised across
    regimes - they are not vendor statistics.
    """
    uni = [
        _spy(),
        _aapl(),
        Instrument(
            symbol="QQQ", name="Invesco QQQ Trust (Nasdaq-100)",
            sector="Broad market ETF", asset_type="ETF", listing_venue="NasdaqGM",
            price_start=585.00, beta=1.15, sigma_idio_annual=0.006,
            alpha_annual=0.02, adv_shares=35_000_000.0,
            provenance={"price_start": "scenario", "beta": "scenario",
                        "sigma_idio_annual": "scenario", "alpha_annual": "scenario",
                        "adv_shares": "scenario"},
        ),
        Instrument(
            symbol="IWM", name="iShares Russell 2000 ETF",
            sector="Small-cap ETF", asset_type="ETF", listing_venue="NYSEArca",
            price_start=242.00, beta=1.10, sigma_idio_annual=0.010,
            alpha_annual=-0.06, adv_shares=26_000_000.0,
            provenance={"price_start": "scenario", "beta": "scenario",
                        "sigma_idio_annual": "scenario", "alpha_annual": "scenario",
                        "adv_shares": "scenario"},
        ),
        _scenario("MSFT", "Microsoft Corp.", "Information Technology", "NasdaqGS",
                  520.00, 1.05, 0.20, 0.03, 22_000_000),
        _scenario("NVDA", "NVIDIA Corp.", "Information Technology", "NasdaqGS",
                  185.00, 1.75, 0.45, 0.22, 165_000_000),
        _scenario("GOOGL", "Alphabet Inc. Class A", "Communication Services", "NasdaqGS",
                  245.00, 1.10, 0.26, 0.06, 28_000_000),
        _scenario("AMZN", "Amazon.com Inc.", "Consumer Discretionary", "NasdaqGS",
                  228.00, 1.20, 0.28, 0.05, 42_000_000),
        _scenario("META", "Meta Platforms Inc. Class A", "Communication Services", "NasdaqGS",
                  720.00, 1.30, 0.32, 0.02, 14_000_000),
        _scenario("TSLA", "Tesla Inc.", "Consumer Discretionary", "NasdaqGS",
                  340.00, 2.05, 0.55, -0.05, 95_000_000),
        _scenario("JPM", "JPMorgan Chase & Co.", "Financials", "NYSE",
                  285.00, 1.05, 0.18, 0.04, 9_500_000),
        _scenario("XOM", "Exxon Mobil Corp.", "Energy", "NYSE",
                  118.00, 0.85, 0.22, -0.05, 17_000_000),
        _scenario("UNH", "UnitedHealth Group Inc.", "Health Care", "NYSE",
                  315.00, 0.70, 0.34, -0.28, 8_500_000),
        _scenario("CAT", "Caterpillar Inc.", "Industrials", "NYSE",
                  405.00, 1.10, 0.24, 0.02, 3_200_000),
        _scenario("WMT", "Walmart Inc.", "Consumer Staples", "NYSE",
                  96.00, 0.55, 0.16, 0.00, 22_000_000),
        _scenario("RIVN", "Rivian Automotive Inc. Class A", "Consumer Discretionary", "NasdaqGS",
                  16.50, 2.30, 0.75, -0.40, 32_000_000),
        _scenario("CVNA", "Carvana Co. Class A", "Consumer Discretionary", "NYSE",
                  285.00, 2.60, 0.85, 0.18, 4_200_000),
    ]
    return _attach_declared_dividends(uni)


# --------------------------------------------------------------------------
# Declared cash-dividend schedules
# --------------------------------------------------------------------------
# Only AAPL carries a *real* dividend schedule in this repo (four ex-dates and
# amounts captured from the Yahoo Finance v8 chart API on 2026-09-17).
# The other payers below get DECLARED schedules: a yield and a quarterly
# ex-month pattern approximating the issuer's real cycle.  They are scenario
# parameters, not vendor data.  Flagged as IR-09 in research/IRREGULARITIES.json.
#
# Why they exist at all: without them the Buy-and-Hold control would be
# systematically understated by roughly one dividend yield per year, and that
# bias would leak into every relative comparison on the leaderboard.  The
# replay generator compensates by subtracting the declared window dividend
# yield from the *price* drift (see sim/marketdata.build_replay), so
# declared total return = price path + cash dividends, with no double count.
_DECLARED_DIVIDENDS: Dict[str, Tuple[float, Tuple[int, ...]]] = {
    # symbol: (declared annual yield, ex-months)
    "SPY": (0.0115, (3, 6, 9, 12)),     # index ETF, quarterly
    "QQQ": (0.0050, (3, 6, 9, 12)),
    "IWM": (0.0120, (3, 6, 9, 12)),
    "MSFT": (0.0065, (2, 5, 8, 11)),
    "GOOGL": (0.0040, (3, 6, 9, 12)),
    "META": (0.0030, (3, 6, 9, 12)),
    "NVDA": (0.0002, (3, 6, 9, 12)),
    "JPM": (0.0200, (1, 4, 7, 10)),
    "XOM": (0.0330, (2, 5, 8, 11)),
    "UNH": (0.0140, (3, 6, 9, 12)),
    "CAT": (0.0200, (1, 4, 7, 10)),
    "WMT": (0.0095, (3, 6, 9, 12)),
    # AMZN, TSLA, RIVN, CVNA pay no cash dividend -> nothing declared.
}
# Day-of-month anchors used for the declared ex-dates (approximate real cycle).
_DECLARED_EX_DAY: Dict[str, Tuple[int, int, int, int]] = {
    "SPY": (20, 19, 18, 19), "QQQ": (22, 22, 21, 22), "IWM": (20, 19, 18, 19),
    "MSFT": (20, 21, 20, 20), "GOOGL": (15, 16, 15, 15), "META": (15, 16, 15, 15),
    "NVDA": (12, 11, 11, 12), "JPM": (6, 6, 6, 6), "XOM": (13, 13, 13, 12),
    "UNH": (16, 17, 16, 15), "CAT": (21, 21, 21, 21),
    "WMT": (10, 10, 10, 10),
}


def _snap_to_business_day(year: int, month: int, day: int) -> str:
    """Declared ex-date, rolled back to the previous business day."""
    import datetime as _dt
    d = _dt.date(year, month, day)
    while d.weekday() >= 5:            # Saturday/Sunday -> Friday
        d -= _dt.timedelta(days=1)
    return d.isoformat()


def _attach_declared_dividends(instruments: List[Instrument]) -> List[Instrument]:
    for inst in instruments:
        decl = _DECLARED_DIVIDENDS.get(inst.symbol)
        if not decl or inst.dividends:      # keep AAPL's real schedule intact
            continue
        yld, months = decl
        days = _DECLARED_EX_DAY[inst.symbol]
        per_share = round(inst.price_start * yld / 4.0, 4)
        events = []
        for year in (2025, 2026):
            for month, day in zip(months, days):
                ex = _snap_to_business_day(year, month, day)
                if "2025-09-17" <= ex <= "2026-09-16":
                    events.append({"ex_date": ex, "amount": per_share})
        if events:
            inst.dividends = events
            inst.provenance["dividends"] = (
                "scenario-declared (yield and quarterly cycle approximated; "
                "NOT vendor data) - IR-09")
    return instruments


def universe_by_symbol() -> Dict[str, Instrument]:
    return {i.symbol: i for i in build_universe()}


def provenance_table() -> List[dict]:
    """Flat, site-ready provenance rows: one per instrument x field."""
    rows = []
    for inst in build_universe():
        for field_name, status in sorted(inst.provenance.items()):
            rows.append({
                "symbol": inst.symbol,
                "name": inst.name,
                "field": field_name,
                "status": status,
                "value": getattr(inst, field_name, None),
            })
    return rows
