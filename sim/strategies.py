"""The Season 1 participant roster: 20 strategies, 20 usernames.

Every participant is deliberately built to **maximise return**, not to manage
risk.  That is a competition design decision requested for this project and it
is the reason several participants use 2x gross leverage, concentrate into a
handful of names, and hold high-beta or high-idiosyncratic-volatility names.
Position sizing rules exist (a competition cannot run without them) but there
are no stop-loss budgets, no value-at-risk limits, no volatility targeting and
no drawdown brakes anywhere in this roster - except where a strategy's *edge*
is itself a volatility signal.

Each strategy carries:
  * a unique ``username`` (the competition handle),
  * a machine-readable ``StrategySpec`` (thesis, entry/exit rules, sizing,
    cadence, academic basis with links, and known failure modes),
  * an ``on_day`` implementation that may only look at information available
    at the open of the current session (enforced by :class:`Context`, which
    exposes prior-session history and today's opening quote only).

Post-mortem narratives ("why did it work / why did it not") are generated
after the run from the realised numbers in :mod:`sim.analytics`, never from
these priors.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .microstructure import BUY, SELL, LIMIT, MARKET, STOP, Order


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------

@dataclass
class StrategySpec:
    username: str
    display_name: str
    archetype: str
    thesis: str
    entry_rules: List[str]
    exit_rules: List[str]
    sizing: str
    leverage: str
    cadence: str
    horizon: str
    academic_basis: List[dict] = field(default_factory=list)
    known_failure_modes: List[str] = field(default_factory=list)
    aggression: int = 3
    why_return_seeking: str = ""

    @property
    def factor_exposure(self) -> Dict[str, float]:
        """Declared directional loading on each measurable style factor.

        Used by :func:`sim.analytics.build_narrative` to compare what the
        strategy was built to harvest against the factor premium that was
        actually realised in the traded window.  The mapping lives in one
        reviewable table (:data:`ARCHETYPE_FACTORS`) rather than being
        scattered across twenty classes.
        """
        return dict(ARCHETYPE_FACTORS.get(self.archetype, {}))


# --------------------------------------------------------------------------
# Declared factor loadings, by archetype
# --------------------------------------------------------------------------
# Sign convention matches sim/analytics.factor_report, where each factor is a
# long/short portfolio:
#   momentum  = long recent winners, short recent losers (63-session, skip 5)
#   reversal  = long recent losers, short recent winners (5-session)
#   beta      = long high-beta names, short low-beta names
#   low_vol   = long low realised-volatility names, short high-volatility names
#   liquidity = long illiquid names, short liquid names
#   market    = the index itself
# A strategy that buys strength therefore has reversal exposure of -1, not +1.
ARCHETYPE_FACTORS: Dict[str, Dict[str, float]] = {
    "cross-sectional momentum": {"momentum": 1.0, "reversal": -1.0, "beta": 0.5},
    "intraday momentum": {"momentum": 0.5, "reversal": -1.0, "beta": 0.5,
                          "liquidity": -0.5},
    "time-series trend": {"momentum": 1.0, "beta": 0.5},
    "short-term reversal": {"reversal": 1.0, "momentum": -0.5},
    "channel breakout": {"momentum": 0.5, "reversal": -1.0},
    "post-event drift": {"momentum": 1.0},
    "liquidity squeeze": {"momentum": 0.5, "liquidity": -1.0},
    "levered beta": {"beta": 1.0, "market": 1.0},
    "sector rotation": {"momentum": 0.5, "beta": 0.5},
    "statistical arbitrage": {"reversal": 0.5, "low_vol": 0.5},
    "market making": {"low_vol": 0.5},
    "volatility risk premium": {"low_vol": 0.5, "beta": 0.5},
    "volatility regime timing": {"beta": 1.0, "low_vol": 0.5},
    "contrarian reversal": {"reversal": 1.0, "momentum": -1.0},
    "liquidity-constrained aggression": {"liquidity": -1.0, "beta": 1.0},
    "buy and hold (control)": {"beta": 1.0, "market": 1.0},
    "concentration": {"beta": 1.0, "momentum": 0.5},
    "overnight premium": {"beta": 0.5},
    "signal stacking": {"momentum": 0.5, "beta": 0.5, "reversal": -0.5},
    "long-horizon reversal": {"reversal": 1.0, "momentum": -1.0, "low_vol": 0.5},
    # Season 2 archetypes.  Declared here rather than in the Season 2 module so
    # that the factor attribution in sim/analytics.py reads one table.
    "informed-flow following": {"momentum": 0.5, "beta": 0.3},
    "event attention proxy": {"momentum": 0.5, "beta": 0.8},
    "regime rotation": {"momentum": 0.3, "beta": 0.6},
    # Live-book archetypes.  Declared in the same table for the same reason:
    # one reviewable place that says what each participant is built to harvest.
    "volatility regime timing (official index)": {"beta": 1.0, "low_vol": 0.5},
    "curve/carry rotation": {"beta": 0.6, "low_vol": 0.3},
    "commodity-macro proxy": {"beta": 0.7, "momentum": 0.4},
}


# --------------------------------------------------------------------------
# Decision context (no look-ahead by construction)
# --------------------------------------------------------------------------

class Context:
    """Everything a strategy may know at the open of session ``t``."""

    def __init__(self, md, t: int, account, cfg: config.CompetitionConfig,
                 quotes: Dict[str, dict], seed: int) -> None:
        self.md = md
        self.t = t
        self.date = md.dates[t]
        self.account = account
        self.cfg = cfg
        self.quotes = quotes
        self.seed = seed
        self.rng = random.Random(f"{seed}:{account.participant}:{self.date}")
        self.symbols = [s for s in md.symbols if md.instruments[s].tradable_by_strategies]
        self._marks = {s: self.open_price(s) for s in md.symbols}
        # Minute-bar lane: the engine re-issues the context at each intraday
        # decision point and stamps which interval the decision lands on
        # (0 = the open, the venue path's K = the closing cross).  Season 1's
        # one-decision-per-session semantics leave it at 0.
        self.interval = 0

    # -- prices ------------------------------------------------------------
    def open_price(self, symbol: str) -> float:
        return self.md.bar(symbol, self.t).open

    def prior_close(self, symbol: str) -> float:
        return self.md.bar(symbol, self.t - 1).close if self.t else \
            self.md.instruments[symbol].price_start

    def bid(self, symbol: str) -> float:
        return self.quotes.get(symbol, {}).get("bid", self.open_price(symbol))

    def ask(self, symbol: str) -> float:
        return self.quotes.get(symbol, {}).get("ask", self.open_price(symbol))

    def mid(self, symbol: str) -> float:
        return self.quotes.get(symbol, {}).get("mid", self.open_price(symbol))

    def spread_bps(self, symbol: str) -> float:
        mid = self.mid(symbol)
        if mid <= 0:
            return 0.0
        return 10_000.0 * self.quotes.get(symbol, {}).get("spread", 0.0) / mid

    def marks(self) -> Dict[str, float]:
        return dict(self._marks)

    # -- history -----------------------------------------------------------
    def closes(self, symbol: str, n: Optional[int] = None) -> List[float]:
        return self.md.history_closes(symbol, self.t, n)

    def returns(self, symbol: str, n: Optional[int] = None) -> List[float]:
        return self.md.history_returns(symbol, self.t, n)

    def sigma(self, symbol: str, n: int = 21) -> float:
        return self.md.realised_sigma_daily(symbol, self.t, n)

    def sigma_annual(self, symbol: str, n: int = 21) -> float:
        return self.sigma(symbol, n) * math.sqrt(config.TRADING_DAYS_PER_YEAR)

    def adv(self, symbol: str, window: int = 63) -> float:
        return self.md.adv(symbol, self.t, window)

    def dollar_volume(self, symbol: str) -> float:
        return self.adv(symbol) * self.prior_close(symbol)

    def momentum(self, symbol: str, lookback: int, skip: int = 0) -> Optional[float]:
        closes = self.closes(symbol, lookback + skip + 1)
        if len(closes) < lookback + skip + 1:
            return None
        if skip:
            return closes[-1 - skip] / closes[0] - 1.0
        return closes[-1] / closes[0] - 1.0

    def vix(self) -> float:
        return self.md.vix[self.t - 1] if self.t else self.md.vix[0]

    def vix_ma(self, n: int = 21) -> float:
        vals = self.md.vix[max(0, self.t - n):self.t] or self.md.vix[:1]
        return sum(vals) / len(vals)

    def market_momentum(self, lookback: int) -> float:
        i = self.t
        if i - lookback < 0:
            lookback = i
        if lookback <= 0:
            return 0.0
        return self.md.spx[i - 1] / self.md.spx[i - 1 - lookback] - 1.0

    # -- account -----------------------------------------------------------
    @property
    def equity(self) -> float:
        return self.account.equity(self._marks)

    @property
    def cash(self) -> float:
        return self.account.cash

    @property
    def buying_power(self) -> float:
        return self.account.buying_power(self._marks)

    def position(self, symbol: str) -> int:
        return self.account.quantity(symbol)

    def weights(self) -> Dict[str, float]:
        eq = self.equity
        if eq <= 0:
            return {s: 0.0 for s in self.md.symbols}
        return {s: self.position(s) * self._marks[s] / eq for s in self.md.symbols}

    # -- order construction -------------------------------------------------
    def orders_to_targets(self, targets: Dict[str, float], reason: str,
                          order_type: str = MARKET, limit_offset_ticks: int = 0,
                          min_trade_notional: float = 1_000.0,
                          at_close: bool = False) -> List[Order]:
        """Convert target weights into tradable orders, respecting margin.

        `at_close` routes the order to the session's final interval instead of
        its first, which is what makes a documented "market on close" exit mean
        what it says.  Sizing and the margin check then use the closing mark, not
        the open, because sizing an order against a price it will never see is
        how a target weight silently becomes a different position.
        """
        eq = self.equity
        if eq <= 0:
            return []
        current = self.weights()
        orders: List[Order] = []
        for symbol, target in targets.items():
            if symbol not in self.md.instruments:
                continue
            price = (self._marks.get(symbol) or 0.0) if at_close \
                else self.open_price(symbol)
            if price <= 0:
                continue
            delta_notional = (target - current.get(symbol, 0.0)) * eq
            if abs(delta_notional) < min_trade_notional:
                continue
            qty = int(abs(delta_notional) // price)
            if qty <= 0:
                continue
            side = BUY if delta_notional > 0 else SELL
            ok, why = self.account.can_increase(symbol, side, qty, price, self._marks)
            if not ok:
                continue
            if order_type == LIMIT:
                tick = config.minimum_tick(price)
                if side == BUY:
                    lp = self.bid(symbol) + limit_offset_ticks * tick
                else:
                    lp = self.ask(symbol) - limit_offset_ticks * tick
                lp = max(tick, round(round(lp / tick) * tick, 6))
                orders.append(Order(symbol=symbol, side=side, quantity=qty,
                                    order_type=LIMIT, limit_price=lp,
                                    participant=self.account.participant,
                                    reason=reason, at_close=at_close))
            else:
                orders.append(Order(symbol=symbol, side=side, quantity=qty,
                                    order_type=MARKET,
                                    participant=self.account.participant,
                                    reason=reason, at_close=at_close))
        # Sell first so proceeds free up buying power for the buys.
        orders.sort(key=lambda o: 0 if o.side == SELL else 1)
        return orders

    def flatten(self, reason: str, symbols: Optional[Sequence[str]] = None,
                at_close: bool = False) -> List[Order]:
        return self.orders_to_targets({s: 0.0 for s in (symbols or self.md.symbols)
                                       if self.position(s)}, reason,
                                      at_close=at_close)


# --------------------------------------------------------------------------
# Indicator helpers (pure functions over prior-session data)
# --------------------------------------------------------------------------

def ema(values: Sequence[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    k = 2.0 / (span + 1.0)
    e = sum(values[:span]) / span
    for v in values[span:]:
        e = v * k + e * (1 - k)
    return e


def zscore(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n + 1:
        return None
    window = values[-n:]
    mu = sum(window) / len(window)
    var = sum((x - mu) ** 2 for x in window) / max(1, len(window) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return (values[-1] - mu) / sd


def donchian(values: Sequence[float], n: int) -> Optional[Tuple[float, float]]:
    if len(values) < n:
        return None
    window = values[-n:]
    return max(window), min(window)


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 10:
        return 0.0
    a, b = list(a)[-n:], list(b)[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


# --------------------------------------------------------------------------
# Participants
# --------------------------------------------------------------------------

class Strategy:
    spec: StrategySpec

    def on_day(self, ctx: Context) -> List[Order]:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def username(self) -> str:
        return self.spec.username


class MomentumMax(Strategy):
    """Cross-sectional momentum, top quartile, 2x gross, monthly rebalance."""

    spec = StrategySpec(
        username="@MomentumMax_12x1",
        display_name="Momentum Max",
        archetype="cross-sectional momentum",
        thesis=("Rank the universe by trailing 63-session return with the most "
                "recent 5 sessions skipped, buy the top quartile and hold for a "
                "month. Winners keep winning because information diffuses "
                "slowly and investors under-react then herd."),
        entry_rules=["At the first session of each month, rank all 17 instruments by 63-session return, skipping the last 5 sessions",
                     "Buy the top 4 names in equal weight",
                     "Skip any name whose 63-session average dollar volume is below $100m (untradeable size)"],
        exit_rules=["Rebalance to the new top 4 at the next month turn",
                     "No stop loss: the position is only exited at the rebalance"],
        sizing="equal weight, 50% of equity per name = 200% gross",
        leverage="2.0x gross (Reg T initial margin 50%)",
        cadence="monthly", horizon="1 month",
        academic_basis=[
            {"claim": "Buying stocks based on a 12-month formation period and holding 3 months earns significant positive returns",
             "url": "https://doi.org/10.1111/j.1540-6261.1993.tb04681.x",
             "ref": "Jegadeesh & Titman (1993), Journal of Finance 48(1):65-91", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Momentum profits are partly explained by behavioural over/under-reaction and persist across markets",
             "url": "https://doi.org/10.1111/0022-1082.00184",
             "ref": "Daniel, Hirshleifer & Subrahmanyam (1998)", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Momentum crashes when a beaten-down market rebounds sharply (high beta to the losing side)",
                             "Monthly rebalancing means the signal is stale for up to 20 sessions",
                             "2x gross leverage doubles the drawdown in a trend reversal"],
        aggression=4,
        why_return_seeking=("Momentum has one of the largest documented premia "
                            "in equities; levered and concentrated it is the "
                            "highest-expected-return sleeve we can build from a "
                            "single signal."))

    def on_day(self, ctx: Context) -> List[Order]:
        first_session_of_month = ctx.t == ctx.md.first_competition_index or \
            ctx.date[:7] != ctx.md.dates[ctx.t - 1][:7]
        if not first_session_of_month:
            return []
        scored = []
        for s in ctx.symbols:
            mom = ctx.momentum(s, 63, skip=5)
            if mom is None:
                continue
            if ctx.dollar_volume(s) < 1e8:
                continue
            scored.append((mom, s))
        scored.sort(reverse=True)
        picks = [s for _, s in scored[:4]]
        if not picks:
            return ctx.flatten("momentum: no qualifying names")
        return ctx.orders_to_targets({s: 0.5 for s in picks},
                                     "momentum: monthly top-quartile rebalance")


class GapAndGo(Strategy):
    """Overnight gap continuation, flat by the close."""

    spec = StrategySpec(
        username="@GapAndGo_YOLO",
        display_name="Gap And Go",
        archetype="intraday momentum",
        thesis=("A stock that gaps up on the open has absorbed news or "
                "imbalanced overnight order flow; continuation in the first "
                "hours is common because retail and institutional demand "
                "arrives through the session. Take the continuation and be "
                "flat before the close so nothing is held overnight."),
        entry_rules=["At the open, compute gap = open / prior close - 1",
                     "Buy any name gapping up more than +1.5%",
                     "Skip names whose quoted spread is wider than 30 bp (costs eat the edge)"],
        exit_rules=["Exit every position at the same session's close, as a market-on-close order routed to the venue's final interval",
                     "No stop loss intraday",
                     "Any exit that fails to fill at the close is flattened at the next open"],
        sizing="equal 40% of equity across qualifying names, capped at 4 names = up to 160% gross",
        leverage="up to 1.6x gross", cadence="daily", horizon="intraday",
        academic_basis=[
            {"claim": "Overnight and intraday returns behave differently and overnight returns carry a distinct risk/return profile",
             "url": "https://doi.org/10.1016/j.jfineco.2019.03.011",
             "ref": "Lou, Polk & Skouras (2019), 'A tug of war', Journal of Financial Economics", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Intraday momentum: the return of the first half hour predicts the return of the last half hour",
             "url": "https://doi.org/10.1016/j.jfineco.2014.04.004",
             "ref": "Gao, Han, Li & Zhou (2014), Journal of Financial Economics", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Gap-and-fade days: the open print is the high of day",
                             "Pays the spread twice per day, so transaction costs compound at ~250 sessions per year",
                             "Cannot see intraday volume at decision time in a daily-bar simulation (flagged limitation)"],
        aggression=5,
        why_return_seeking=("Turns the whole account over every day, so a small "
                            "per-trade edge compounds ~250 times a year."))

    def on_day(self, ctx: Context) -> List[Order]:
        # Yesterday's leftovers, if any close failed, go out at the open; today's
        # entries go out at today's CLOSE, which is what this strategy has always
        # claimed to do and, until the at_close ticket existed, could not
        # (IR-34).  Sizing the exit against the entry quantity rather than the
        # current position is the point: the position to be closed does not exist
        # yet at decision time.
        orders = ctx.flatten("gap: close out yesterday's unfilled exit at the open")
        picks = []
        for s in ctx.symbols:
            pc = ctx.prior_close(s)
            if pc <= 0:
                continue
            gap = ctx.open_price(s) / pc - 1.0
            if gap > 0.015 and ctx.spread_bps(s) < 30.0:
                picks.append((gap, s))
        picks.sort(reverse=True)
        picks = [s for _, s in picks[:4]]
        if picks:
            entries = ctx.orders_to_targets({s: 0.40 for s in picks},
                                            "gap: overnight gap-up continuation")
            orders += entries
            orders += [Order(symbol=o.symbol, side=SELL, quantity=o.quantity,
                             order_type=MARKET, participant=ctx.account.participant,
                             reason="gap: market-on-close exit, flat by the bell",
                             at_close=True)
                       for o in entries if o.side == BUY]
        return orders


class TrendSurfer(Strategy):
    """Dual moving-average trend following with pyramiding."""

    spec = StrategySpec(
        username="@TrendSurfer_GoldenX",
        display_name="Trend Surfer",
        archetype="time-series trend",
        thesis=("Prices trend. A 20-session EMA above a 50-session EMA means "
                "the intermediate trend is up; stay long and add to winners "
                "(pyramid) until the trend breaks."),
        entry_rules=["Long when EMA20 > EMA50 on the prior session's closes",
                     "Add a second half position when price is also above the 50-session high",
                     "Maximum 5 concurrent names, chosen by strongest EMA20-EMA50 spread normalised by volatility"],
        exit_rules=["Exit when EMA20 crosses below EMA50",
                     "No trailing stop and no profit target"],
        sizing="25% of equity initial, +25% on the pyramid = 50% per name",
        leverage="up to 2.0x gross", cadence="daily check, monthly-ish turnover",
        horizon="weeks to months",
        academic_basis=[
            {"claim": "Time-series momentum: each instrument's own past 12-month return predicts its next month",
             "url": "https://doi.org/10.1016/j.jfineco.2012.02.023",
             "ref": "Moskowitz, Ooi & Pedersen (2012), Journal of Financial Economics 104(2):228-250", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Moving-average rules applied to the Dow earn significant returns in-sample across 1897-1986",
             "url": "https://doi.org/10.1111/0022-1082.00108",
             "ref": "Brock, Lakonishok & LeBaron (1992), Journal of Finance", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Whipsaw in range-bound markets: repeated small losses",
                             "Moving averages lag, so the exit gives back a meaningful part of every rally",
                             "Pyramiding concentrates the account into whatever already ran"],
        aggression=4,
        why_return_seeking=("Riding a full trend with leverage captures the fat "
                            "right tail of price moves instead of clipping it."))

    def on_day(self, ctx: Context) -> List[Order]:
        signals = []
        for s in ctx.symbols:
            closes = ctx.closes(s, 60)
            e20, e50 = ema(closes, 20), ema(closes, 50)
            if e20 is None or e50 is None:
                continue
            vol = ctx.sigma(s, 21) or 1e-6
            strength = (e20 - e50) / (vol * closes[-1])
            hi, _ = donchian(closes, 50)
            pyramid = closes[-1] >= hi * 0.999
            signals.append((strength, s, e20 > e50, pyramid))
        longs = sorted([x for x in signals if x[2]], reverse=True)[:5]
        targets = {}
        for strength, s, up, pyramid in longs:
            targets[s] = 0.50 if pyramid else 0.25
        if not targets:
            return ctx.flatten("trend: no EMA20>EMA50 names")
        return ctx.orders_to_targets(targets, "trend: EMA20/EMA50 with pyramid")


class MeanRevZ(Strategy):
    """Short-horizon reversal: buy 2-sigma dips, exit at the mean."""

    spec = StrategySpec(
        username="@MeanRev_Z2Sigma",
        display_name="Mean Rev Z2",
        archetype="short-term reversal",
        thesis=("Liquidity provision is paid for. A large-cap that trades two "
                "standard deviations below its own 20-session mean in a single "
                "session has usually been hit by transient order flow, and it "
                "snaps back within days."),
        entry_rules=["Compute the z-score of yesterday's close against its 20-session mean",
                     "Buy names with z <= -2.0",
                     "Rank by most negative z, take up to 6 names",
                     "Skip names whose quoted spread exceeds 50 bp"],
        exit_rules=["Exit when z returns to >= -0.25 (back at the mean)",
                     "Exit unconditionally after 10 sessions"],
        sizing="equal 30% of equity per name = up to 180% gross",
        leverage="up to 1.8x gross", cadence="daily", horizon="1-10 sessions",
        academic_basis=[
            {"claim": "Stocks with extreme poor performance over short horizons earn abnormal returns in the following days/weeks",
             "url": "https://doi.org/10.1111/j.1540-6261.1990.tb05110.x",
             "ref": "Jegadeesh (1990), 'Evidence of Predictable Behavior of Security Returns', Journal of Finance", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Short-term reversal is compensation for providing liquidity",
             "url": "https://doi.org/10.1093/rfs/hhs106",
             "ref": "Da, Liu & Schaumburg (2014), Review of Financial Studies", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Catching a falling knife: a genuine fundamental break keeps falling",
                             "The 2-sigma filter fires most often exactly when volatility is rising",
                             "Exits are limit orders at the mean, so the best days can fail to fill"],
        aggression=4,
        why_return_seeking=("Buys the panic and is paid the liquidity premium, "
                            "with turnover high enough to compound the edge."))

    def on_day(self, ctx: Context) -> List[Order]:
        exits: List[str] = []
        held = [s for s in ctx.symbols if ctx.position(s) > 0]
        for s in held:
            closes = ctx.closes(s, 25)
            z = zscore(closes, 20)
            age = ctx.t - ctx.account._entry_day.get(s, ctx.t)
            if z is None or z >= -0.25 or age >= 10:
                exits.append(s)
        orders = ctx.orders_to_targets({s: 0.0 for s in exits},
                                       "meanrev: back to the mean or 10-session timeout")
        candidates = []
        for s in ctx.symbols:
            if s in held or s in exits:
                continue
            closes = ctx.closes(s, 25)
            z = zscore(closes, 20)
            if z is None or z > -2.0:
                continue
            if ctx.spread_bps(s) >= 50.0:
                continue
            candidates.append((z, s))
        candidates.sort()
        targets = {s: 0.30 for _, s in candidates[:6]}
        orders += ctx.orders_to_targets(targets, "meanrev: buy the 2-sigma dip")
        for sym in targets:
            if ctx.position(sym) == 0:
                ctx.account._entry_day[sym] = ctx.t
        return orders


class DonchianBreakout(Strategy):
    """20-session high breakout, exit on 10-session low (turtle style)."""

    spec = StrategySpec(
        username="@DonchianBreakout_20",
        display_name="Donchian Breakout",
        archetype="channel breakout",
        thesis=("A close above the 20-session high means the market has "
                "accepted a price nobody was willing to sell at for a month. "
                "Breakouts from consolidation continue often enough, and when "
                "they do they run far, to pay for the many false ones."),
        entry_rules=["Enter long when the prior close is above the prior 20-session high",
                     "Enter short when the prior close is below the prior 20-session low",
                     "Maximum 4 concurrent positions"],
        exit_rules=["Exit longs on a close below the 10-session low; exit shorts on a close above the 10-session high"],
        sizing="25% of equity per position, up to 100% gross long plus 100% gross short",
        leverage="up to 2.0x gross", cadence="daily", horizon="days to weeks",
        academic_basis=[
            {"claim": "Trend-following systems earn positive risk-adjusted returns across asset classes and decades",
             "url": "https://doi.org/10.1111/j.1468-036X.2006.00278.x",
             "ref": "Fung & Hsieh (1999)/subsequent trend-following literature", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["False breakouts in choppy markets produce a run of small losses",
                             "Entry at the breakout means paying the widest spread of the day",
                             "Two-sided entries can be whipsawed in both directions in the same week"],
        aggression=4,
        why_return_seeking=("Cuts losers mechanically and lets a genuine "
                            "breakout run without a profit cap."))

    def on_day(self, ctx: Context) -> List[Order]:
        targets = dict(ctx.weights())
        for s in list(targets):
            if abs(targets[s]) < 0.01:
                targets.pop(s, None)
        for s in ctx.symbols:
            closes = ctx.closes(s, 31)
            if len(closes) < 31:
                continue
            hi20, lo20 = donchian(closes[:-1], 20)
            hi10, lo10 = donchian(closes[:-1], 10)
            last = closes[-1]
            if s in targets and targets[s] > 0 and last <= lo10:
                targets.pop(s, None)
            elif s in targets and targets[s] < 0 and last >= hi10:
                targets.pop(s, None)
            elif s not in targets and len(targets) < 4:
                if last > hi20:
                    targets[s] = 0.25
                elif last < lo20:
                    targets[s] = -0.25
        return ctx.orders_to_targets(targets, "donchian: 20-session breakout, 10-session exit")


class EventDriftRider(Strategy):
    """Post-event drift, proxied by a large move on heavy volume."""

    spec = StrategySpec(
        username="@DriftRider_PEAD",
        display_name="Drift Rider",
        archetype="post-event drift",
        thesis=("Markets digest a large information shock over days, not "
                "seconds. When a name moves more than 8% in five sessions on "
                "at least twice its normal volume, the move continues in the "
                "same direction for weeks - the drift that earnings "
                "announcement research documents."),
        entry_rules=["5-session absolute return > 8%",
                     "20-session average volume on the event day >= 2.0x the 63-session average",
                     "Go with the direction of the move, up to 5 names, ranked by |move|"],
        exit_rules=["Hold 15 sessions then exit",
                     "Exit early if the move retraces more than 60%"],
        sizing="equal 35% of equity per name = up to 175% gross",
        leverage="up to 1.75x gross", cadence="daily scan", horizon="15 sessions",
        academic_basis=[
            {"claim": "Stock prices drift after earnings announcements in the direction of the surprise",
             "url": "https://doi.org/10.2307/2491062",
             "ref": "Bernard & Thomas (1989), Journal of Accounting Research 27(Suppl):1-36", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "The first documentation of the drift: after the earnings announcement month, cumulative abnormal returns continued to move in the direction of the earnings surprise",
             "url": "https://doi.org/10.2307/2490232",
             "ref": "Ball & Brown (1968), 'An Empirical Evaluation of Accounting Income Numbers', Journal of Accounting Research 6(2):159-178",
             "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["PROXY DATA LIMITATION: no earnings calendar or EPS surprise series is available offline, so the entry uses a price/volume proxy rather than a real announcement (flagged as IR-07)",
                             "A large move on volume is often a reversal candidate, the exact opposite of drift",
                             "Holding 15 sessions through a reversal is expensive without a stop"],
        aggression=4,
        why_return_seeking=("Concentrates into the strongest information event "
                            "in the universe and refuses to take profit early."))

    def on_day(self, ctx: Context) -> List[Order]:
        held = {s for s in ctx.symbols if ctx.position(s)}
        exits = []
        # Iterate the universe order, never the set.  Set iteration order
        # depends on PYTHONHASHSEED, and the order in which exits are submitted
        # changes the cash and margin available to the entries that follow - so
        # the same seed and config produced different seasons in different
        # processes.  Found by the CI reproducibility gate; see IR-30.
        for s in ctx.symbols:
            if s not in held:
                continue
            age = ctx.t - ctx.account._entry_day.get(s, ctx.t)
            pos = ctx.account.positions[s]
            entry_px = pos.avg_cost
            now = ctx.prior_close(s)
            sign = 1.0 if pos.quantity > 0 else -1.0
            pnl_frac = sign * (now / entry_px - 1.0) if entry_px else 0.0
            if age >= 15 or pnl_frac <= -0.12:
                exits.append(s)
        orders = ctx.orders_to_targets({s: 0.0 for s in exits}, "drift: 15-session or retrace exit")
        candidates = []
        for s in ctx.symbols:
            if s in held and s not in exits:
                continue
            rets = ctx.returns(s, 20)
            vols = ctx.md.history_volume(s, ctx.t, 20)
            # history_volume(s, t, 20) returns at most 20 bars, so a "< 21"
            # guard would silently skip every candidate on every session.
            if len(rets) < 6 or len(vols) < 6:
                continue
            move = ctx.prior_close(s) / ctx.closes(s, 6)[0] - 1.0
            base_adv = sum(vols[:-1]) / max(1, len(vols) - 1)
            if abs(move) > 0.08 and base_adv > 0 and vols[-1] >= 2.0 * ctx.adv(s, 63):
                candidates.append((abs(move), s, move))
        candidates.sort(reverse=True)
        targets = {}
        for _, s, move in candidates[:5]:
            targets[s] = 0.35 if move > 0 else -0.35
        orders += ctx.orders_to_targets(targets, "drift: event proxy entry")
        for s in targets:
            if ctx.position(s) == 0:
                ctx.account._entry_day[s] = ctx.t
        return orders


class SqueezeHunter(Strategy):
    """Turnover-driven squeeze in the tightest-float names."""

    spec = StrategySpec(
        username="@SqueezeHunter_TF",
        display_name="Squeeze Hunter",
        archetype="liquidity squeeze",
        thesis=("When a name's daily turnover (volume / shares outstanding) "
                "spikes while price is rising, a large fraction of the float "
                "is changing hands at higher prices and short covering "
                "becomes self-reinforcing. Ride the squeeze for five sessions."),
        entry_rules=["Turnover today >= 2.5x its 63-session median",
                     "3-session return > 5%",
                     "Prefer the highest turnover names, up to 3 positions"],
        exit_rules=["Exit after 5 sessions",
                     "Exit immediately if turnover collapses below its median"],
        sizing="equal 50% of equity per name = up to 150% gross",
        leverage="up to 1.5x gross", cadence="daily", horizon="5 sessions",
        academic_basis=[
            {"claim": "Abnormal trading volume predicts subsequent short-horizon returns",
             "url": "https://doi.org/10.1086/261571",
             "ref": "Karpoff (1987), 'The Relation Between Price Changes and Trading Volume', JFQA", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["NO REAL SHORT-INTEREST DATA offline: turnover is a proxy for a squeeze, not a measurement of one (IR-07)",
                             "The most squeezed name is also the one with the widest spread and the deepest impact",
                             "Five-session holds through a reversal with no stop"],
        aggression=5,
        why_return_seeking=("Squeezes produce the largest single-day moves in "
                            "the whole equity universe; concentration is the point."))

    def on_day(self, ctx: Context) -> List[Order]:
        held = {s for s in ctx.symbols if ctx.position(s)}
        exits = []
        # Iterate the universe order, never the set.  Set iteration order
        # depends on PYTHONHASHSEED, and the order in which exits are submitted
        # changes the cash and margin available to the entries that follow - so
        # the same seed and config produced different seasons in different
        # processes.  Found by the CI reproducibility gate; see IR-30.
        for s in ctx.symbols:
            if s not in held:
                continue
            age = ctx.t - ctx.account._entry_day.get(s, ctx.t)
            vols = ctx.md.history_volume(s, ctx.t, 63)
            med = sorted(vols)[len(vols) // 2] if vols else 0
            if age >= 5 or (med and vols and vols[-1] < med):
                exits.append(s)
        orders = ctx.orders_to_targets({s: 0.0 for s in exits}, "squeeze: 5-session or volume-collapse exit")
        cands = []
        for s in ctx.symbols:
            if s in held and s not in exits:
                continue
            vols = ctx.md.history_volume(s, ctx.t, 63)
            if len(vols) < 21:
                continue
            med = sorted(vols[:-1])[len(vols[:-1]) // 2]
            if med <= 0:
                continue
            turnover = vols[-1] / med
            mom3 = ctx.momentum(s, 3) or 0.0
            if turnover >= 2.5 and mom3 > 0.05:
                cands.append((turnover, s))
        cands.sort(reverse=True)
        targets = {s: 0.50 for _, s in cands[:3]}
        orders += ctx.orders_to_targets(targets, "squeeze: turnover spike entry")
        for s in targets:
            if ctx.position(s) == 0:
                ctx.account._entry_day[s] = ctx.t
        return orders


class BetaChaser(Strategy):
    """Perpetually levered into the highest-beta names."""

    spec = StrategySpec(
        username="@BetaChaser_3xProxy",
        display_name="Beta Chaser",
        archetype="levered beta",
        thesis=("In a rising market the highest-beta names deliver the highest "
                "return. This participant is the human equivalent of a 3x "
                "levered ETF: hold the top-beta basket at maximum permitted "
                "leverage and rebalance weekly."),
        entry_rules=["Rank the universe by realised beta to the S&P 500 over 63 sessions",
                     "Hold the top 5 beta names equally weighted at 2.0x gross",
                     "Re-rank weekly"],
        exit_rules=["Only at the weekly rebalance; never de-risks on a drawdown"],
        sizing="40% of equity per name = 200% gross",
        leverage="2.0x gross, always invested", cadence="weekly", horizon="all season",
        academic_basis=[
            {"claim": "High-beta (and levered) portfolios underperform on a risk-adjusted basis - the betting-against-beta anomaly",
             "url": "https://doi.org/10.1016/j.jfineco.2014.02.004",
             "ref": "Frazzini & Pedersen (2014), 'Betting against beta', Journal of Financial Economics", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Levered ETF-style daily rebalancing creates volatility decay in choppy markets",
             "url": "https://www.sec.gov/investor/alerts/leveraged-etf-alert.pdf",
             "ref": "SEC/FINRA investor alert on leveraged and inverse ETFs", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Volatility decay: repeated rebalancing to a fixed leverage in a choppy market bleeds value",
                             "Beta is estimated on trailing data, so the basket chases whatever was volatile last quarter",
                             "A 2x gross book in a 10% market drawdown is a 20% account drawdown with no brake"],
        aggression=5,
        why_return_seeking=("Maximum market exposure at all times: if the "
                            "market goes up, this participant should win."))

    def on_day(self, ctx: Context) -> List[Order]:
        weekly = (ctx.t - ctx.md.first_competition_index) % 5 == 0
        if not weekly and ctx.position("SPY") == 0 and not any(ctx.position(s) for s in ctx.symbols):
            weekly = True
        if not weekly:
            return []
        betas = []
        mkt = ctx.md.market_ret[max(1, ctx.t - 63):ctx.t]
        for s in ctx.symbols:
            r = ctx.returns(s, 63)
            n = min(len(r), len(mkt))
            if n < 30:
                continue
            betas.append((_beta(r[-n:], mkt[-n:]), s))
        betas.sort(reverse=True)
        picks = [s for _, s in betas[:5]]
        if not picks:
            return []
        return ctx.orders_to_targets({s: 0.40 for s in picks}, "beta: weekly top-5 beta at 2x")


def _beta(r: Sequence[float], m: Sequence[float]) -> float:
    n = min(len(r), len(m))
    if n < 5:
        return 0.0
    mr = sum(r) / n
    mm = sum(m) / n
    cov = sum((a - mr) * (b - mm) for a, b in zip(r, m))
    var = sum((b - mm) ** 2 for b in m)
    return cov / var if var else 0.0


class SectorRotator(Strategy):
    """Sector momentum rotation, all-in on the two strongest sectors."""

    spec = StrategySpec(
        username="@SectorRotator_AlphaX",
        display_name="Sector Rotator",
        archetype="sector rotation",
        thesis=("Capital rotates between sectors in multi-week waves. Buy the "
                "two sectors with the strongest 21-session relative return and "
                "hold until the ranking changes."),
        entry_rules=["Compute each sector's equal-weighted 21-session return from its constituents",
                     "Buy the top 2 sectors, split 100% of gross exposure between them",
                     "Use 2.0x gross leverage across the sector baskets"],
        exit_rules=["Rotate at the weekly re-rank; no defensive sleeve and no cash drag by design"],
        sizing="1.0x of equity per selected sector, spread equally across its constituents",
        leverage="2.0x gross", cadence="weekly", horizon="weeks",
        academic_basis=[
            {"claim": "Industry momentum: buying past-winning industries earns significant returns",
             "url": "https://doi.org/10.1086/250090",
             "ref": "Moskowitz & Grinblatt (1999), Journal of Business 72(4):419-445", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Sector labels here come from the declared universe table, not from a licensed GICS feed",
                             "Rotation whipsaws at turning points, buying the top of a sector wave",
                             "Only 9 sectors in a 17-name universe, so the ranking is noisy"],
        aggression=4,
        why_return_seeking=("Concentrates the whole account into the strongest "
                            "two sleeves of the market instead of diversifying."))

    def on_day(self, ctx: Context) -> List[Order]:
        weekly = (ctx.t - ctx.md.first_competition_index) % 5 == 0
        if not weekly:
            return []
        sector_returns: Dict[str, List[float]] = {}
        for s in ctx.symbols:
            sector = ctx.md.instruments[s].sector
            m = ctx.momentum(s, 21)
            if m is None:
                continue
            sector_returns.setdefault(sector, []).append(m)
        ranked = sorted(((sum(v) / len(v), k) for k, v in sector_returns.items()), reverse=True)
        top = [k for _, k in ranked[:2]]
        members = [s for s in ctx.symbols if ctx.md.instruments[s].sector in top]
        if not members:
            return ctx.flatten("sector: no ranking available")
        w = 2.0 / len(members)
        return ctx.orders_to_targets({s: w for s in members}, f"sector: rotate into {', '.join(top)}")


class PairsStatArb(Strategy):
    """Cointegration-free pairs trade on the highest-correlation pair."""

    spec = StrategySpec(
        username="@PairsArb_ZScore2",
        display_name="Pairs Stat Arb",
        archetype="statistical arbitrage",
        thesis=("Two names in the same sector move together. When their price "
                "ratio stretches two standard deviations from its 60-session "
                "mean, short the relative winner and buy the relative loser "
                "and wait for convergence."),
        entry_rules=["Find the pair with the highest 60-session return correlation inside the same sector",
                     "Compute the z-score of log(price_A) - beta * log(price_B) over 60 sessions",
                     "Enter when |z| >= 2.0, dollar-neutral, 1.0x gross each leg"],
        exit_rules=["Exit at |z| <= 0.25",
                     "Exit unconditionally after 20 sessions"],
        sizing="dollar neutral, 100% long + 100% short = 200% gross",
        leverage="2.0x gross, ~0 net", cadence="daily", horizon="1-20 sessions",
        academic_basis=[
            {"claim": "Pairs trading earned significant abnormal returns in the 1960s-1990s and has since decayed",
             "url": "https://doi.org/10.1093/rfs/hhj020",
             "ref": "Gatev, Goetzmann & Rouwenhorst (2006), Review of Financial Studies 19(3):797-827", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Correlation is not cointegration: the ratio can trend forever",
                             "Pays borrow fees on the short leg every session",
                             "A market-neutral book still has idiosyncratic event risk on both legs"],
        aggression=3,
        why_return_seeking=("Leverage, not diversification: a market-neutral "
                            "book at 2x gross can out-return a long-only book "
                            "if convergence is fast."))

    def _best_pair(self, ctx: Context) -> Optional[Tuple[str, str, float]]:
        best = None
        by_sector: Dict[str, List[str]] = {}
        for s in ctx.symbols:
            by_sector.setdefault(ctx.md.instruments[s].sector, []).append(s)
        for sector, members in by_sector.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    ra, rb = ctx.returns(a, 60), ctx.returns(b, 60)
                    c = correlation(ra, rb)
                    if best is None or c > best[2]:
                        best = (a, b, c)
        return best

    def on_day(self, ctx: Context) -> List[Order]:
        pair = self._best_pair(ctx)
        if pair is None:
            return []
        a, b, corr = pair
        if corr < 0.5:
            return ctx.flatten("pairs: no pair correlated enough")
        ca, cb = ctx.closes(a, 61), ctx.closes(b, 61)
        if len(ca) < 61 or len(cb) < 61:
            return []
        ra, rb = ctx.returns(a, 60), ctx.returns(b, 60)
        beta_ab = _beta(ra, rb) or 1.0
        spread = [math.log(x) - beta_ab * math.log(y) for x, y in zip(ca[-60:], cb[-60:])]
        mu = sum(spread) / len(spread)
        sd = math.sqrt(sum((x - mu) ** 2 for x in spread) / (len(spread) - 1)) or 1e-9
        z = (spread[-1] - mu) / sd
        held = ctx.position(a) or ctx.position(b)
        if held:
            age = ctx.t - ctx.account._entry_day.get(a, ctx.t)
            if abs(z) <= 0.25 or age >= 20:
                return ctx.flatten("pairs: convergence or timeout exit")
            return []
        if abs(z) < 2.0:
            return []
        if z > 0:   # A rich vs B -> short A, long B
            targets = {a: -1.0, b: 1.0}
        else:
            targets = {a: 1.0, b: -1.0}
        ctx.account._entry_day[a] = ctx.t
        return ctx.orders_to_targets(targets, f"pairs: {a}/{b} z={z:+.2f} corr={corr:.2f}")


class SpreadHarvester(Strategy):
    """Passive market making: rest limit orders on both sides of the book."""

    spec = StrategySpec(
        username="@SpreadHarvester_MM",
        display_name="Spread Harvester",
        archetype="market making",
        thesis=("Provide liquidity instead of demanding it. Resting limit "
                "orders on both sides of the three most liquid names collect "
                "the quoted spread and the maker rebate, and pay for "
                "themselves as long as inventory does not run away."),
        entry_rules=["Each session, rest a buy limit one tick below the displayed bid and a sell limit one tick above the displayed offer",
                     "Trade only the three highest dollar-volume instruments",
                     "Size each quote to 10% of the session's expected volume, capped so gross inventory stays under 60% of equity per side"],
        exit_rules=["Quotes expire at the end of the session (TIF=DAY)",
                     "If net inventory in a name exceeds the cap, cross the spread with a market order to get back inside it"],
        sizing="quote size from the liquidity cap, inventory cap 60% of equity per side",
        leverage="typically < 1.0x gross; inventory is the exposure",
        cadence="daily quoting", horizon="intraday to a few days",
        academic_basis=[
            {"claim": "Optimal quotes around an inventory-adjusted reservation price maximise market-making P&L",
             "url": "https://doi.org/10.1080/14697680701381228",
             "ref": "Avellaneda & Stoikov (2008), Quantitative Finance 8(3):217-224", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Maker-taker fees pay rebates to resting liquidity providers",
             "url": "https://www.sec.gov/market-structure/research",
             "ref": "SEC market structure research on maker-taker pricing", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Adverse selection: the quotes fill precisely when the price is about to move against the book",
                             "DAILY DECISION GRANULARITY: a real market maker requotes in milliseconds; this participant requotes once per session, which understates both its earnings and its risk (flagged limitation)",
                             "Inventory builds in trending markets and the participant ends up long the losers"],
        aggression=2,
        why_return_seeking=("Earns the spread and the rebate every session "
                            "instead of paying them, and reinvests the accrual."))

    def on_day(self, ctx: Context) -> List[Order]:
        ranked = sorted(ctx.symbols, key=lambda s: ctx.dollar_volume(s), reverse=True)[:3]
        # Declared exit rule: if net inventory in a name exceeds the cap, cross
        # the spread with a market order to get back inside it.  Without this
        # the participant only ever accumulates inventory and becomes a
        # directionally levered bet on whichever names moved against its quotes.
        hedge = {}
        for s in ranked:
            price = ctx.open_price(s)
            cap = 0.60 * ctx.equity
            inv = ctx.position(s) * price
            if abs(inv) > cap:
                hedge[s] = 0.30 * (1.0 if inv > 0 else -1.0)
        if hedge:
            ctx.orders_to_targets(hedge, "mm: inventory cap breached - cross to hedge")
        orders: List[Order] = []
        for s in ranked:
            price = ctx.open_price(s)
            tick = config.minimum_tick(price)
            inv = ctx.position(s) * price
            cap = 0.60 * ctx.equity
            # Size = the smaller of 10% of expected session volume (a normal
            # quoting-size heuristic) and the inventory cap in this strategy's
            # own spec.  Without the second term the quote size is set by ADV
            # alone, which on SPY is tens of millions of shares - orders of
            # magnitude beyond what a 100,000 USD account may hold.  The
            # broker-side pre-trade check in sim/engine.py would clip it
            # anyway; a strategy should still size itself.
            size = min(int(0.10 * ctx.adv(s, 21)), int(cap / max(price, 1e-9)))
            if size < 1:
                continue
            if s in hedge:
                continue          # this session is spent getting back inside the cap
            if inv < cap:
                bid = max(tick, round(round((ctx.bid(s) - tick) / tick) * tick, 6))
                orders.append(Order(symbol=s, side=BUY, quantity=size, order_type=LIMIT,
                                    limit_price=bid, participant=ctx.account.participant,
                                    reason="mm: rest buy one tick inside"))
            if -inv < cap:
                ask = round((ctx.ask(s) + tick) / tick) * tick
                orders.append(Order(symbol=s, side=SELL, quantity=size, order_type=LIMIT,
                                    limit_price=round(ask, 6), participant=ctx.account.participant,
                                    reason="mm: rest sell one tick inside"))
        return orders


class VolCarryLowVol(Strategy):
    """Dollar-neutral long-low-vol / short-high-vol, levered."""

    spec = StrategySpec(
        username="@VolCarry_LowHigh",
        display_name="Vol Carry",
        archetype="volatility risk premium",
        thesis=("Low-volatility stocks have historically out-returned "
                "high-volatility stocks because investors overpay for lottery "
                "characteristics. Go long the three lowest-volatility names "
                "and short the three highest, dollar-neutral, at 2x gross."),
        entry_rules=["Rank the universe by 63-session realised volatility",
                     "Long the bottom 3, short the top 3, equal dollar weights",
                     "Rebalance monthly"],
        exit_rules=["Monthly rebalance only"],
        sizing="33% of equity per leg = 100% long + 100% short = 200% gross",
        leverage="2.0x gross, ~0 net", cadence="monthly", horizon="1 month",
        academic_basis=[
            {"claim": "The cross-section of stock returns is negatively related to idiosyncratic volatility",
             "url": "https://doi.org/10.1111/j.1540-6261.2006.00836.x",
             "ref": "Ang, Hodrick, Xing & Zhang (2006), Journal of Finance 61(4):1637-1672", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["In a melt-up the high-volatility short leg explodes upward",
                             "Borrow fees on the short leg accrue daily and are highest exactly on the high-vol names",
                             "The anomaly is weak in a single 12-month window; it is a multi-decade effect"],
        aggression=3,
        why_return_seeking=("Uses leverage on a market-neutral spread to "
                            "convert a small annual edge into a large one."))

    def on_day(self, ctx: Context) -> List[Order]:
        monthly = ctx.t == ctx.md.first_competition_index or \
            ctx.date[:7] != ctx.md.dates[ctx.t - 1][:7]
        if not monthly:
            return []
        ranked = sorted(((ctx.sigma_annual(s, 63), s) for s in ctx.symbols))
        if len(ranked) < 6:
            return []
        low = [s for _, s in ranked[:3]]
        high = [s for _, s in ranked[-3:]]
        targets = {s: 1.0 / 3 for s in low}
        targets.update({s: -1.0 / 3 for s in high})
        return ctx.orders_to_targets(targets, "volcarry: long low-vol / short high-vol")


class VIXRegimeTimer(Strategy):
    """Leverage the index according to the *real* VIX regime."""

    spec = StrategySpec(
        username="@VIXRegime_Timer",
        display_name="VIX Regime Timer",
        archetype="volatility regime timing",
        thesis=("This participant trades the one genuinely real exogenous "
                "series in Season 1: the daily CBOE VIX close from FRED. "
                "Equity returns cluster with volatility regimes, so hold the "
                "index ETF at maximum leverage when realised fear is high and "
                "the market is falling, and cut leverage when the regime is "
                "quiet and extended."),
        entry_rules=["VIX >= 24 and the index is below its 20-session mean: 2.0x long SPY (distress buying)",
                     "VIX between 18 and 24: 1.5x long SPY",
                     "VIX < 18 and the index is above its 20-session mean: 1.0x long SPY",
                     "VIX < 15 and 21-session index return > 6%: 0.5x (extended, quiet)"],
        exit_rules=["Regime change only; no stop loss"],
        sizing="leverage set by the regime table", leverage="0.5x - 2.0x gross",
        cadence="daily", horizon="all season",
        academic_basis=[
            {"claim": "VIX is computed from SPX option prices and measures expected 30-day volatility",
             "url": "https://www.cboe.com/us/indices/dashboard/VIX/",
             "ref": "Cboe VIX Index methodology", "status": "KNOWN-NOT-FETCHED"},
            {"claim": "Daily VIX close observations (real input to this strategy)",
             "url": "https://fred.stlouisfed.org/series/VIXCLS",
             "ref": "FRED series VIXCLS, Federal Reserve Bank of St. Louis", "status": "FETCHED-2026-09-17"}],
        known_failure_modes=["High VIX often marks a falling knife that keeps falling",
                             "Regime thresholds (24 / 18 / 15) are hand-set and could be over-fitted to this window",
                             "Only trades the index ETF, so it forgoes all single-name alpha"],
        aggression=3,
        why_return_seeking=("Concentrates the entire account into the index "
                            "and varies leverage 4x between regimes."))

    def on_day(self, ctx: Context) -> List[Order]:
        vix = ctx.vix()
        closes = ctx.closes("SPY", 25)
        if len(closes) < 21:
            target = 1.0
        else:
            ma20 = sum(closes[-20:]) / 20
            r21 = closes[-1] / closes[-21] - 1.0 if len(closes) >= 21 else 0.0
            below = closes[-1] < ma20
            if vix >= 24 and below:
                target = 2.0
            elif vix >= 18:
                target = 1.5
            elif vix < 15 and r21 > 0.06:
                target = 0.5
            else:
                target = 1.0
        return ctx.orders_to_targets({"SPY": target},
                                     f"vix: regime vix={vix:.2f} -> {target:.1f}x SPY")


class OverreactionFade(Strategy):
    """Buy the biggest single-session losers, hold three sessions."""

    spec = StrategySpec(
        username="@OverreactionFade_LT",
        display_name="Overreaction Fade",
        archetype="contrarian reversal",
        thesis=("Investors over-react to bad news. A large-cap that falls more "
                "than 5% in one session without a trend behind it tends to "
                "recover part of the move within days."),
        entry_rules=["Prior session return <= -5%",
                     "The 63-session trend must still be positive (avoids buying structural decliners)",
                     "Up to 4 names, equal weight"],
        exit_rules=["Exit after 3 sessions",
                     "Exit if the position is up more than 8% (take the snap-back)"],
        sizing="equal 40% of equity per name = up to 160% gross",
        leverage="up to 1.6x gross", cadence="daily", horizon="3 sessions",
        academic_basis=[
            {"claim": "Stocks that have performed extremely poorly over 3-5 years subsequently outperform (long-horizon overreaction)",
             "url": "https://doi.org/10.1111/j.1540-6261.1985.tb05004.x",
             "ref": "De Bondt & Thaler (1985), Journal of Finance 40(3):793-805", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["A 5% one-day drop is frequently justified and continues",
                             "Three-session holds are too short for the documented multi-year overreaction effect (scale mismatch, flagged)",
                             "Buys into the widest spreads of the day"],
        aggression=4,
        why_return_seeking=("Buys the day everyone else is forced to sell and "
                            "holds through the noise with leverage."))

    def on_day(self, ctx: Context) -> List[Order]:
        held = {s for s in ctx.symbols if ctx.position(s)}
        exits = []
        # Iterate the universe order, never the set.  Set iteration order
        # depends on PYTHONHASHSEED, and the order in which exits are submitted
        # changes the cash and margin available to the entries that follow - so
        # the same seed and config produced different seasons in different
        # processes.  Found by the CI reproducibility gate; see IR-30.
        for s in ctx.symbols:
            if s not in held:
                continue
            age = ctx.t - ctx.account._entry_day.get(s, ctx.t)
            cost = ctx.account.positions[s].avg_cost
            gain = (ctx.prior_close(s) / cost - 1.0) if cost else 0.0
            if age >= 3 or gain > 0.08:
                exits.append(s)
        orders = ctx.orders_to_targets({s: 0.0 for s in exits}, "fade: 3-session or +8% exit")
        cands = []
        for s in ctx.symbols:
            if s in held and s not in exits:
                continue
            r = ctx.returns(s, 1)
            trend = ctx.momentum(s, 63)
            if r and r[-1] <= -0.05 and (trend or 0) > 0:
                cands.append((r[-1], s))
        cands.sort()
        targets = {s: 0.40 for _, s in cands[:4]}
        orders += ctx.orders_to_targets(targets, "fade: buy the 5% one-day loser")
        for s in targets:
            if ctx.position(s) == 0:
                ctx.account._entry_day[s] = ctx.t
        return orders


class IlliquidRocket(Strategy):
    """Maximum aggression in the least liquid names - on purpose."""

    spec = StrategySpec(
        username="@IlliquidRocket_Degen",
        display_name="Illiquid Rocket",
        archetype="liquidity-constrained aggression",
        thesis=("The highest theoretical returns live in the least liquid "
                "names. This participant deliberately trades the two smallest "
                "dollar-volume names in the universe at maximum leverage, "
                "chasing 5-session momentum. It exists to measure exactly how "
                "much return the microstructure model takes away."),
        entry_rules=["Rank the universe by dollar volume, take the two smallest",
                     "Go 2x gross into whichever of the two has the better 5-session momentum",
                     "Rebalance every 5 sessions"],
        exit_rules=["Weekly rotation only"],
        sizing="200% of equity in a single name", leverage="2.0x gross, single name",
        cadence="weekly", horizon="5 sessions",
        academic_basis=[
            {"claim": "Illiquid stocks earn a return premium, but transaction costs consume a large share of it",
             "url": "https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf",
             "ref": "Amihud (2002), 'Illiquidity and stock returns', Journal of Financial Markets 5(1):31-56",
             "status": "FETCHED-VIA-SEARCH"},
            {"claim": "Market impact grows with the square root of the participation rate",
             "url": "https://doi.org/10.21314/JOR.2001.041",
             "ref": "Almgren & Chriss (2001), Journal of Risk 3(2):21-40",
             "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Pays the widest spread and the largest square-root impact of any participant",
                             "Hits the session participation cap and leaves orders unfilled",
                             "Single-name concentration at 2x means one bad week can halve the account"],
        aggression=5,
        why_return_seeking=("Concentrates everything into the highest-volatility "
                            "names the competition allows."))

    def on_day(self, ctx: Context) -> List[Order]:
        if (ctx.t - ctx.md.first_competition_index) % 5 != 0 and \
                any(ctx.position(s) for s in ctx.symbols):
            return []
        ranked = sorted(ctx.symbols, key=lambda s: ctx.dollar_volume(s))
        small = ranked[:2]
        if not small:
            return []
        best = max(small, key=lambda s: ctx.momentum(s, 5) or -9.0)
        return ctx.orders_to_targets({s: 0.0 for s in ctx.symbols if s != best},
                                     "rocket: exit everything else") + \
            ctx.orders_to_targets({best: 2.0}, f"rocket: 2x into {best}")


class BuyHoldMaxBeta(Strategy):
    """Buy-and-hold control: fully invested from session one."""

    spec = StrategySpec(
        username="@BuyHold_MaxBeta",
        display_name="Buy And Hold Max Beta",
        archetype="buy and hold (control)",
        thesis=("The control participant. Buy the three highest-beta names on "
                "the first session at 1.5x gross and never trade again. "
                "Everything else on the leaderboard is measured against what "
                "doing nothing but holding beta would have produced."),
        entry_rules=["On the first session, buy the top 3 names by declared beta at 50% of equity each"],
        exit_rules=["Never (except the mandatory liquidation at the end of the competition period)"],
        sizing="50% of equity per name = 150% gross", leverage="1.5x gross, static",
        cadence="once", horizon="all season",
        academic_basis=[
            {"claim": "Equity indices return roughly 6-8% real per year over the long run; a single year is dominated by the market draw",
             "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Pages/dataarchived.html",
             "ref": "Damodaran historical returns data archive", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Zero turnover means zero transaction cost, which is its only structural advantage",
                             "No ability to avoid a drawdown: the account takes the full market move times beta",
                             "Concentration in three declared-high-beta names"],
        aggression=3,
        why_return_seeking=("Static high-beta exposure is the honest benchmark "
                            "for 'highest return with no skill'."))

    def on_day(self, ctx: Context) -> List[Order]:
        if ctx.t != ctx.md.first_competition_index:
            return []
        ranked = sorted(ctx.symbols, key=lambda s: ctx.md.instruments[s].beta, reverse=True)
        picks = ranked[:3]
        return ctx.orders_to_targets({s: 0.5 for s in picks}, "buyhold: enter top-3 beta, never exit")


class ConcentratedQuality(Strategy):
    """All-in on the single strongest trend, switched at most monthly."""

    spec = StrategySpec(
        username="@OneBigBet_Concentra",
        display_name="One Big Bet",
        archetype="concentration",
        thesis=("Diversification dilutes return. Put the entire account, at "
                "maximum leverage, into the single name with the best "
                "risk-scaled 63-session momentum, and switch only when "
                "another name is materially better."),
        entry_rules=["Score every name by 63-session momentum divided by realised volatility",
                     "Hold only the top name at 2.0x gross",
                     "Switch only if the runner-up's score exceeds the incumbent's by more than 0.5"],
        exit_rules=["Switch, or the mandatory end-of-season liquidation"],
        sizing="200% of equity in one name", leverage="2.0x gross, one position",
        cadence="daily check, rare switches", horizon="weeks to months",
        academic_basis=[
            {"claim": "Concentrated portfolios have higher expected return and much higher variance than diversified ones",
             "url": "https://doi.org/10.1111/j.1540-6261.1993.tb04681.x",
             "ref": "Jegadeesh & Titman (1993) - momentum magnitude increases with portfolio concentration", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["A single name can halve; at 2x gross the account can approach a margin call",
                             "The switching threshold creates hysteresis, so it holds losers too long",
                             "No diversification means the outcome is almost entirely one idiosyncratic draw"],
        aggression=5,
        why_return_seeking=("The mathematically highest-variance legal book in "
                            "the competition, which is what a "
                            "highest-return-only mandate implies."))

    def on_day(self, ctx: Context) -> List[Order]:
        scores = []
        for s in ctx.symbols:
            m = ctx.momentum(s, 63, skip=5)
            if m is None:
                continue
            vol = ctx.sigma_annual(s, 63) or 1e-6
            scores.append((m / vol, s))
        if not scores:
            return []
        scores.sort(reverse=True)
        best_score, best = scores[0]
        held = [s for s in ctx.symbols if ctx.position(s)]
        if held:
            incumbent = held[0]
            inc_score = dict((s, sc) for sc, s in scores).get(incumbent, -9e9)
            if best_score - inc_score < 0.5:
                return []
            orders = ctx.orders_to_targets({s: 0.0 for s in held}, f"concentra: switch out of {incumbent}")
        else:
            orders = []
        return orders + ctx.orders_to_targets({best: 2.0}, f"concentra: 2x into {best} score={best_score:.2f}")


class OvernightCarry(Strategy):
    """Long overnight, flat intraday - the documented overnight premium."""

    spec = StrategySpec(
        username="@OvernightCarry_NO",
        display_name="Overnight Carry",
        archetype="overnight premium",
        thesis=("A large share of the market's long-run return arrives "
                "overnight rather than during the session. This participant "
                "buys the index ETF at the close of every session and sells it "
                "at the open of the next, collecting the overnight leg only."),
        entry_rules=["Buy SPY at the close of every session, worked on the venue's final interval (at_close), so the fill is the closing print and the overnight gap is captured rather than paid away",
                     "Hold only the index ETF; no single-name risk"],
        exit_rules=["Sell at the next session's open"],
        sizing="2.0x gross overnight, flat during the session",
        leverage="2.0x gross overnight", cadence="daily", horizon="overnight",
        academic_basis=[
            {"claim": "Overnight and intraday returns have different distributions and premia",
             "url": "https://doi.org/10.1016/j.jfineco.2019.03.011",
             "ref": "Lou, Polk & Skouras (2019), Journal of Financial Economics", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["TIMING / INTERVAL GRANULARITY: the entry is worked on the session's final interval, so it captures the overnight leg rather than the intraday one, but the venue's closing print is the last of K synthetic intervals rather than a real 16:00 tape print, and the exit pays the spread again at the next open. Both are cost drags a live implementation of this trade would also carry; neither is a reason to read the season's number as an estimate of the real premium.",
                             "Pays the spread twice per session, ~500 times per year",
                             "No intraday exposure means it forfeits the whole trend"],
        aggression=3,
        why_return_seeking=("Applies 2x leverage to the leg of the return "
                            "distribution that the literature says is the "
                            "paid one."))

    def on_day(self, ctx: Context) -> List[Order]:
        # Sell at the opening bell, buy back at the closing bell: the position is
        # held overnight and flat through the session, which is the trade the
        # literature describes. Before the at_close ticket existed the entry had
        # to be worked at the open, so the strategy paid the intraday leg it was
        # trying to avoid and was structurally short-changed (IR-08); that is
        # now fixed rather than merely documented.
        orders = []
        if ctx.position("SPY"):
            orders += ctx.orders_to_targets({"SPY": 0.0}, "overnight: sell at the open")
        orders += ctx.orders_to_targets({"SPY": 2.0}, "overnight: buy at the close",
                                        at_close=True)
        return orders


class KitchenSink(Strategy):
    """Signal stacking: every bullish signal at once, maximum size."""

    spec = StrategySpec(
        username="@KitchenSink_AllIn",
        display_name="Kitchen Sink",
        archetype="signal stacking",
        thesis=("Stack the signals instead of choosing between them. A name "
                "that is simultaneously above its 50-session EMA, inside 5% of "
                "its 63-session high, positive on 21-session momentum and in a "
                "rising-market regime gets the full 2x allocation. Names that "
                "fail any test get nothing."),
        entry_rules=["EMA20 > EMA50 on prior closes",
                     "Close within 5% of the 63-session high",
                     "21-session momentum > 0",
                     "Index above its 50-session mean (regime filter)",
                     "Equal weight across all qualifying names, scaled to 2.0x gross"],
        exit_rules=["A name is dropped the moment it fails any test",
                     "The whole book goes to cash if the regime filter fails"],
        sizing="2.0x gross divided equally across qualifying names",
        leverage="2.0x gross or 0x", cadence="daily", horizon="days to weeks",
        academic_basis=[
            {"claim": "Combining weakly-correlated return predictors improves out-of-sample performance relative to any single predictor",
             "url": "https://doi.org/10.1093/rfs/hhv044",
             "ref": "Rapach, Strauss & Zhou (2013)/related forecast-combination literature", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["Four filters means it is often flat and misses the move entirely",
                             "The regime filter is a lagging index average, so the book goes to cash after the drop, not before",
                             "Stacking correlated signals is not diversification: they all fail on the same days"],
        aggression=4,
        why_return_seeking=("Refuses to hold anything that is not confirming on "
                            "every axis, then sizes the survivors to the cap."))

    def on_day(self, ctx: Context) -> List[Order]:
        spx_closes = ctx.closes("SPY", 55)
        regime_ok = len(spx_closes) >= 51 and \
            spx_closes[-1] > sum(spx_closes[-50:]) / 50
        if not regime_ok:
            return ctx.flatten("sink: regime filter off -> all cash")
        picks = []
        for s in ctx.symbols:
            closes = ctx.closes(s, 70)
            if len(closes) < 65:
                continue
            e20, e50 = ema(closes, 20), ema(closes, 50)
            if e20 is None or e50 is None or e20 <= e50:
                continue
            hi, _ = donchian(closes[:-1], 63)
            if closes[-1] < hi * 0.95:
                continue
            if (ctx.momentum(s, 21) or -1) <= 0:
                continue
            picks.append(s)
        if not picks:
            return ctx.flatten("sink: no name passes all four filters")
        w = 2.0 / len(picks)
        return ctx.orders_to_targets({s: w for s in picks}, f"sink: {len(picks)} names pass all filters")


class ContrarianValue(Strategy):
    """Buy the worst performers of the year, levered, and hold."""

    spec = StrategySpec(
        username="@Contrarian_DeepValue",
        display_name="Contrarian Deep Value",
        archetype="long-horizon reversal",
        thesis=("The market over-reacts to sustained bad news. Buy the three "
                "worst 126-session performers at 2x gross and hold them, "
                "betting on the long-horizon reversal that De Bondt and Thaler "
                "documented."),
        entry_rules=["Rank the universe by trailing 126-session return (or all available history)",
                     "Buy the bottom 3 at 66% of equity each",
                     "Re-rank quarterly"],
        exit_rules=["Quarterly re-rank only"],
        sizing="66% of equity per name = 200% gross", leverage="2.0x gross",
        cadence="quarterly", horizon="3-12 months",
        academic_basis=[
            {"claim": "Long-horizon losers outperform long-horizon winners over 3-5 year horizons",
             "url": "https://doi.org/10.1111/j.1540-6261.1985.tb05004.x",
             "ref": "De Bondt & Thaler (1985), Journal of Finance", "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=["A 12-month window is far shorter than the 3-5 years the effect is documented over",
                             "The worst performers are often worst for fundamental reasons (value traps)",
                             "2x leverage on falling knives accelerates the drawdown"],
        aggression=4,
        why_return_seeking=("Buys the most hated names at double leverage "
                            "because that is where the reversal payoff is largest."))

    def on_day(self, ctx: Context) -> List[Order]:
        quarterly = ctx.t == ctx.md.first_competition_index or \
            (ctx.t - ctx.md.first_competition_index) % 63 == 0
        if not quarterly:
            return []
        ranked = []
        for s in ctx.symbols:
            closes = ctx.closes(s, 127)
            if len(closes) < 30:
                continue
            ranked.append((closes[-1] / closes[0] - 1.0, s))
        ranked.sort()
        picks = [s for _, s in ranked[:3]]
        if not picks:
            return []
        return ctx.orders_to_targets({s: 2.0 / 3 for s in picks}, "value: bottom-3 126-session performers")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

STRATEGY_CLASSES = [
    MomentumMax, GapAndGo, TrendSurfer, MeanRevZ, DonchianBreakout,
    EventDriftRider, SqueezeHunter, BetaChaser, SectorRotator, PairsStatArb,
    SpreadHarvester, VolCarryLowVol, VIXRegimeTimer, OverreactionFade,
    IlliquidRocket, BuyHoldMaxBeta, ConcentratedQuality, OvernightCarry,
    KitchenSink, ContrarianValue,
]


def build_roster() -> List[Strategy]:
    """Instantiate every participant, checking that usernames are unique."""
    roster = [cls() for cls in STRATEGY_CLASSES]
    handles = [s.username for s in roster]
    if len(set(handles)) != len(handles):
        dupes = {h for h in handles if handles.count(h) > 1}
        raise ValueError(f"duplicate usernames in the roster: {sorted(dupes)}")
    return roster


def roster_specs() -> List[dict]:
    """Site-ready serialisation of the roster."""
    out = []
    for s in build_roster():
        spec = s.spec
        out.append({
            "username": spec.username,
            "display_name": spec.display_name,
            "archetype": spec.archetype,
            "thesis": spec.thesis,
            "entry_rules": spec.entry_rules,
            "exit_rules": spec.exit_rules,
            "sizing": spec.sizing,
            "leverage": spec.leverage,
            "cadence": spec.cadence,
            "horizon": spec.horizon,
            "academic_basis": spec.academic_basis,
            "known_failure_modes": spec.known_failure_modes,
            "aggression": spec.aggression,
            "why_return_seeking": spec.why_return_seeking,
            # asdict() would drop this: factor_exposure is a property derived
            # from ARCHETYPE_FACTORS, and the strategy pages need it to show
            # what each participant was built to harvest.
            "factor_exposure": spec.factor_exposure,
        })
    return out
