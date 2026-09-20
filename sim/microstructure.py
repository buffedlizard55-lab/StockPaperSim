"""Market microstructure: liquidity, market making, pricing and execution.

What is modelled
----------------
* **Quoted spread** - a tick-floored, volatility-scaled two-sided quote whose
  floor/cap per liquidity tier comes from :class:`sim.config.LiquidityConfig`.
  Reg NMS Rule 612 fixes the minimum pricing increment
  (https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612).
* **Depth** - a ladder of displayed levels whose size is a share of the
  session's tradable volume, with depth growing away from the touch.
* **Market makers** - competing dealers quoting around an inventory-adjusted
  reservation price using the Avellaneda-Stoikov (2008) formulas
  (https://doi.org/10.1080/14697680701381228).  Dealer inventory is the net
  of taker flow, so aggressive buying widens and lifts the offer - i.e. the
  price responds to order flow instead of being a free lunch.
* **Market impact** - the square-root law, impact_return = Y * sigma *
  sqrt(Q / ADV), split into permanent and temporary parts (Almgren, Thum,
  Hauptmann & Li 2005, "Direct estimation of equity market impact",
  Risk 18(7):58-62; framework from Almgren & Chriss 2001, Journal of Risk
  3(2):21-40, https://doi.org/10.21314/JOR.2001.041).
* **Participation limits** - no order may take more than a configured share of
  the day's volume; the remainder expires unfilled (TIF=DAY).
* **Intraday path** - an OHLC-consistent path with a U-shaped volume profile
  (Admati & Pfleiderer 1988, https://doi.org/10.1093/rfs/1.1.3) used to decide
  whether limit and stop orders would have been filled, and at what VWAP.
* **Cost stack** - broker commission, exchange taker fee / maker rebate
  (capped by Reg NMS Rule 610), SEC Section 31 fee on sales at the *dated*
  statutory rate, and the FINRA Trading Activity Fee on sales.
* **Implementation shortfall** - every fill records the decision price (the
  mid when the order was created) so gross alpha can be separated from
  execution cost (Perold 1988, "The Implementation Shortfall: Paper versus
  Reality", Financial Analysts Journal 44(5):6-31,
  https://doi.org/10.2469/faj.v44.5.28).

  NOTE: this line previously carried https://doi.org/10.2307/2328616, which is
  Roll (1984), "A Simple Implicit Measure of the Effective Bid-Ask Spread in an
  Efficient Market" - a different paper. Caught by the citation audit in
  tests/test_sources_register.py; see research/VERIFICATION_LOG.md.

Everything random is seeded from ``(scenario seed, symbol, date, participant)``
so a run is bit-for-bit reproducible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config
from .universe import Instrument

# --------------------------------------------------------------------------
# Orders and fills
# --------------------------------------------------------------------------

MARKET = "market"
LIMIT = "limit"
STOP = "stop"
# Closing-auction order types (the minute-bar lane, 2026-09-20).  A market-on-
# close order participates in the closing auction only: it meets the book at
# the session's final interval and fills at the closing print's walked price.
# A limit-on-close order adds price protection: it fills at the close only if
# the close is marketable against its limit, otherwise it expires at the bell.
# The exchanges operate exactly these orders in their closing crosses.
#   SOURCE: https://www.nasdaqtrader.com/Trader.aspx?id=CloseCross  (MOC/LOC
#   order types and their cancellation deadlines are the exchange's published
#   closing-cross mechanics; the 3:50 p.m. ET deadline itself is not modelled -
#   see research/LIMITATIONS.json L-04 for the time-of-day gap).
MOC = "moc"
LOC = "loc"
BUY = "buy"
SELL = "sell"


@dataclass
class Order:
    symbol: str
    side: str                 # BUY | SELL
    quantity: int
    order_type: str = MARKET  # MARKET | LIMIT | STOP | MOC | LOC
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: str = "DAY"
    participant: str = ""
    reason: str = ""          # free-text strategy rationale, stored in memory
    # Execute against the closing interval instead of the opening one.  A
    # strategy decides once per session, so without this flag every order it
    # can write is an at-the-open order, and a rule like "exit at the close"
    # (which two Season 1 strategies document) is not expressible at all.  The
    # venue already matched orders at the final interval for the end-of-season
    # forced liquidation, so this reuses that mechanism rather than adding one;
    # see sim/engine.py::Competition._submit and IR-34.
    at_close: bool = False

    def __post_init__(self) -> None:
        if self.side not in (BUY, SELL):
            raise ValueError(f"bad side {self.side!r}")
        if self.order_type not in (MARKET, LIMIT, STOP, MOC, LOC):
            raise ValueError(f"bad order type {self.order_type!r}")
        if self.order_type == LIMIT and self.limit_price is None:
            raise ValueError("limit order needs limit_price")
        if self.order_type == STOP and self.stop_price is None:
            raise ValueError("stop order needs stop_price")
        if self.order_type == LOC and self.limit_price is None:
            raise ValueError("limit-on-close order needs limit_price")
        if self.order_type == MOC and (self.limit_price or self.stop_price):
            raise ValueError("market-on-close takes no price; use LOC to protect one")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive; use side to express direction")


@dataclass
class Fill:
    order: Order
    date: str
    filled_qty: int
    requested_qty: int
    avg_price: float
    decision_price: float          # mid at order creation
    spread_cost: float             # $ paid crossing the quoted spread
    depth_cost: float              # $ paid walking the book
    impact_cost: float             # $ paid in square-root impact
    commission: float
    exchange_fee: float
    regulatory_fee: float
    rebate: float
    quote_at_fill: Dict[str, float] = field(default_factory=dict)
    status: str = "filled"         # filled | partial | expired | rejected
    reject_reason: str = ""
    interval: int = 0
    slippage_bps: float = 0.0

    @property
    def notional(self) -> float:
        return self.filled_qty * self.avg_price

    @property
    def total_cost(self) -> float:
        """All-in execution cost in dollars (positive = cost to the trader)."""
        return (self.spread_cost + self.depth_cost + self.impact_cost
                + self.commission + self.exchange_fee + self.regulatory_fee
                - self.rebate)

    @property
    def execution_cost_bps(self) -> float:
        """All-in friction cost in bp of executed notional (always >= ~0).

        This is the number comparable to a Rule 605 "effective spread" plus
        fees statistic, and deliberately excludes intraday drift so that a
        favourable price move cannot make execution look free.
        """
        n = self.notional
        return 10_000.0 * self.total_cost / n if n else 0.0

    @property
    def price_difference(self) -> float:
        """$ of price paid/received vs the decision mid (signed, + = worse)."""
        sign = 1.0 if self.order.side == BUY else -1.0
        return sign * (self.avg_price - self.decision_price) * self.filled_qty

    @property
    def drift_cost(self) -> float:
        """Part of the price difference NOT explained by spread, depth or impact.

        This is the intraday timing term: the mid itself moved between the
        decision and the execution slices.  It can be negative (favourable),
        which is why it is reported separately rather than folded into
        ``total_cost``.  Adding ``spread_cost + depth_cost + impact_cost +
        drift_cost`` reproduces ``price_difference`` exactly, so the
        decomposition cannot double count.
        """
        return self.price_difference - (self.spread_cost + self.depth_cost
                                        + self.impact_cost)

    @property
    def implementation_shortfall(self) -> float:
        """$ vs the decision mid, all-in including fees (Perold 1988)."""
        return self.price_difference + (self.commission + self.exchange_fee
                                        + self.regulatory_fee - self.rebate)

    def to_row(self) -> dict:
        return {
            "participant": self.order.participant,
            "date": self.date,
            "symbol": self.order.symbol,
            "side": self.order.side,
            "order_type": self.order.order_type,
            "requested_qty": self.requested_qty,
            "filled_qty": self.filled_qty,
            "avg_price": round(self.avg_price, 6),
            "decision_price": round(self.decision_price, 6),
            "notional": round(self.notional, 2),
            "spread_cost": round(self.spread_cost, 4),
            "depth_cost": round(self.depth_cost, 4),
            "impact_cost": round(self.impact_cost, 4),
            "commission": round(self.commission, 4),
            "exchange_fee": round(self.exchange_fee, 4),
            "regulatory_fee": round(self.regulatory_fee, 4),
            "rebate": round(self.rebate, 4),
            "total_cost": round(self.total_cost, 4),
            "price_difference": round(self.price_difference, 4),
            "drift_cost": round(self.drift_cost, 4),
            "implementation_shortfall": round(self.implementation_shortfall, 4),
            "slippage_bps": round(self.slippage_bps, 3),
            "execution_cost_bps": round(self.execution_cost_bps, 3),
            "status": self.status,
            "reject_reason": self.reject_reason,
            "reason": self.order.reason,
            "limit_price": self.order.limit_price,
            "stop_price": self.order.stop_price,
            "at_close": bool(getattr(self.order, "at_close", False)),
            "bid": self.quote_at_fill.get("bid"),
            "ask": self.quote_at_fill.get("ask"),
            "mid": self.quote_at_fill.get("mid"),
            "interval": self.interval,
        }


# --------------------------------------------------------------------------
# Borrow terms for short selling (Reg SHO locate/borrow requirement)
# --------------------------------------------------------------------------
# SIM CHOICE / scenario: declared borrow availability and annualised fee.
# Reg SHO requires a broker-dealer to have reasonable grounds to believe a
# security can be borrowed before effecting a short sale ("locate").
#   SOURCE: https://www.sec.gov/regulation-sho  (Regulation SHO)
#   SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203
BORROW_TERMS: Dict[str, Dict[str, object]] = {
    "default": {"shortable": True, "fee_annual": 0.005},
    "RIVN": {"shortable": True, "fee_annual": 0.045},
    "CVNA": {"shortable": True, "fee_annual": 0.060},
    "TSLA": {"shortable": True, "fee_annual": 0.012},
    "NVDA": {"shortable": True, "fee_annual": 0.010},
    "SMCI": {"shortable": True, "fee_annual": 0.090},
}


def borrow_terms(symbol: str) -> Dict[str, object]:
    return BORROW_TERMS.get(symbol, BORROW_TERMS["default"])


# --------------------------------------------------------------------------
# Intraday path
# --------------------------------------------------------------------------

class IntradayPath:
    """OHLC-consistent intraday price/volume path for one symbol-day.

    The session is split into ``K`` equal intervals.  Volume follows a
    U-shaped profile (heaviest at the open and close).  Price follows a path
    that visits the day's high and low in a seeded random order and lands
    exactly on the bar's close, so every statement the simulation makes about
    "would this limit order have filled?" is consistent with the bar.
    """

    def __init__(self, bar, sigma_daily: float, minutes: int, rng: random.Random,
                 intervals: int = 13) -> None:
        self.bar = bar
        self.sigma_daily = max(sigma_daily, 1e-6)
        self.minutes = minutes
        self.K = intervals
        # U-shaped volume weights.
        w = []
        for k in range(intervals):
            x = (k + 0.5) / intervals
            w.append(0.55 + 2.6 * (x - 0.5) ** 2)
        total = sum(w)
        self.vol_weights = [wi / total for wi in w]
        self.cum_vol = []
        acc = 0.0
        for wi in self.vol_weights:
            acc += wi
            self.cum_vol.append(acc)
        self.prices = self._build(rng)

    def _build(self, rng: random.Random) -> List[float]:
        o, h, l, c = self.bar.open, self.bar.high, self.bar.low, self.bar.close
        K = self.K
        high_at = rng.randint(1, K - 1)
        low_at = rng.randint(1, K - 1)
        if high_at == low_at:
            low_at = max(1, high_at - 1) if high_at > 1 else min(K - 1, high_at + 1)
        pts = [0.0] * (K + 1)
        pts[0] = o
        pts[K] = c
        pts[high_at] = h
        pts[low_at] = l
        anchors = sorted({0, high_at, low_at, K})
        for a, b in zip(anchors, anchors[1:]):
            span = b - a
            if span <= 1:
                continue
            for j in range(1, span):
                t = j / span
                base = pts[a] * (1 - t) + pts[b] * t
                wiggle = rng.gauss(0.0, 0.25 * self.sigma_daily * math.sqrt(t * (1 - t)))
                pts[a + j] = max(l, min(h, base + wiggle * base))
        # Guarantee containment of the bar's extremes.
        for i in range(K + 1):
            pts[i] = max(l, min(h, pts[i]))
        pts[0], pts[K] = o, c
        return pts

    def price(self, k: int) -> float:
        return self.prices[max(0, min(self.K, k))]

    def interval_volume(self, k: int) -> float:
        return self.bar.volume * self.vol_weights[max(0, min(self.K - 1, k))]

    def remaining_volume_share(self, k: int) -> float:
        """Share of the day's volume still to come after interval ``k``."""
        k = max(0, min(self.K - 1, k))
        return 1.0 - self.cum_vol[k]

    def vwap_between(self, k0: int, k1: int) -> float:
        num = den = 0.0
        for k in range(max(0, k0), min(self.K, k1) + 1):
            v = self.interval_volume(k)
            num += v * self.price(k)
            den += v
        return num / den if den else self.price(k0)

    def crossed(self, price: float, side: str,
                from_interval: int = 0) -> Tuple[bool, int]:
        """Did the path trade through ``price``?  Returns (crossed, interval).

        ``from_interval`` bounds the search to intervals at or after the
        order's arrival: a limit that starts working mid-session must not
        fill on a trade-through that happened before it existed (that would
        be lookahead).  Interval 0 is the opening print, so the scan starts
        at interval 1 unless the caller says otherwise.
        """
        for k in range(max(1, from_interval), self.K + 1):
            p = self.price(k)
            if side == BUY and p <= price:
                return True, k
            if side == SELL and p >= price:
                return True, k
        return False, self.K

    def path_min(self) -> float:
        return min(self.prices)

    def path_max(self) -> float:
        return max(self.prices)


# --------------------------------------------------------------------------
# Liquidity and impact
# --------------------------------------------------------------------------

class LiquidityModel:
    """Quoted spread and displayed depth.

    The spread is expressed in whole minimum increments (Rule 612), because
    that is what actually binds for liquid US equities.  See the long comment
    on :class:`sim.config.LiquidityConfig` for the sources.
    """

    def __init__(self, cfg: config.LiquidityConfig) -> None:
        self.cfg = cfg

    def spread_ticks(self, inst: Instrument, price: float, sigma_daily: float,
                     vix_factor: float = 1.0) -> int:
        tick = config.minimum_tick(price)
        tick_pct = tick / max(price, 1e-9)
        tier = inst.liquidity_tier
        stress = max(sigma_daily, 1e-5) * max(vix_factor, 0.25) ** 0.5
        raw = self.cfg.spread_k_ticks * math.sqrt(stress / max(tick_pct, 1e-9))
        ticks = int(round(raw))
        lo = self.cfg.min_spread_ticks.get(tier, 1)
        hi = self.cfg.max_spread_ticks.get(tier, 6)
        return max(lo, min(hi, ticks))

    def quoted_spread(self, inst: Instrument, price: float, sigma_daily: float,
                      vix_factor: float = 1.0) -> float:
        """Absolute quoted spread in dollars: a whole number of ticks."""
        tick = config.minimum_tick(price)
        spread = self.spread_ticks(inst, price, sigma_daily, vix_factor) * tick
        cap = price * self.cfg.spread_cap_bps.get(inst.liquidity_tier, 30.0) / 10_000.0
        spread = min(spread, cap)
        # Floor to whole ticks *after* capping: rounding here instead of
        # flooring pushed the quoted spread up to half a tick above the cap
        # (RIVN quoted $0.05 against an $0.0495 cap), which made the cap a
        # statement the model did not actually honour.  One tick stays the
        # floor, so the cap can only bind on names where it is above a tick.
        steps = max(1, int(math.floor(spread / tick + 1e-9)))
        return steps * tick

    def touch_size(self, price: float) -> float:
        """Displayed shares at the best bid/offer (a multiple of the round lot).

        Round-lot sizes follow the tiered definition in Rule 600(b)(93):
        100 shares up to $250, 40 shares to $1,000, 10 shares to $10,000 and
        1 share above that.
          SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.600
        """
        return max(config.round_lot(price),
                   config.round_lot(price) * self.cfg.touch_size_round_lots)

    def book_depth(self, price: float, level: int) -> float:
        """Displayed size at ladder depth ``level`` (0 = the touch)."""
        return self.touch_size(price) * (self.cfg.depth_growth ** level)


class ImpactModel:
    """Market impact with permanent and temporary components.

    ``exponent`` is 0.5 (square root) by default, the convention the execution
    literature and vendor models use.  Almgren, Thum, Hauptmann & Li (2005)
    measured a 3/5 power instead and rejected the square root for temporary
    impact, which is recorded as IR-26; the sensitivity harness moves this
    exponent and publishes what it does to the ranking rather than leaving the
    choice as an unmeasured assumption.
    """

    def __init__(self, cfg: config.ImpactConfig, *, exponent: float = 0.5) -> None:
        self.cfg = cfg
        self.exponent = exponent

    def impact_return(self, qty: int, adv: float, sigma_daily: float) -> float:
        if adv <= 0 or qty <= 0:
            return 0.0
        participation = qty / adv
        return self.cfg.coefficient * sigma_daily * (participation ** self.exponent)

    def split(self, impact_return: float) -> Tuple[float, float]:
        permanent = impact_return * self.cfg.permanent_share
        return permanent, impact_return - permanent


# --------------------------------------------------------------------------
# Market makers
# --------------------------------------------------------------------------

@dataclass
class _Dealer:
    dealer_id: int
    inventory: int = 0
    gamma: float = 0.9
    kappa: float = 60.0


class MarketMakerPool:
    """Competing Avellaneda-Stoikov dealers around a reference mid.

    The Avellaneda-Stoikov reservation price and optimal half-spread are

        r = s - q * gamma * sigma^2 * (T - t)
        delta = gamma * sigma^2 * (T - t) / 2 + (1/gamma) * ln(1 + gamma/kappa)

    expressed here in *relative* units with dealer inventory ``q`` measured as
    a fraction of average daily volume, so that r and delta scale with price.
    That normalisation is a SIM CHOICE documented here; the functional form is
    the published one.
      SOURCE: Avellaneda & Stoikov (2008), "High-frequency trading in a limit
              order book", Quantitative Finance 8(3):217-224,
              https://doi.org/10.1080/14697680701381228
    """

    def __init__(self, cfg: config.MarketMakerConfig, seed_key: str) -> None:
        self.cfg = cfg
        self.rng = random.Random(seed_key)
        self.dealers = [_Dealer(i, gamma=cfg.risk_aversion * (0.75 + 0.5 * self.rng.random()),
                                kappa=cfg.intensity_k * (0.75 + 0.5 * self.rng.random()))
                        for i in range(cfg.num_makers)]

    @property
    def net_inventory(self) -> int:
        return sum(d.inventory for d in self.dealers)

    def take_inventory(self, side: str, qty: int) -> None:
        """Dealers absorb taker flow: a buy lifts offers and adds short inventory."""
        sign = -1 if side == BUY else 1
        remaining = qty
        order = sorted(self.dealers, key=lambda d: self.rng.random())
        for d in order:
            if remaining <= 0:
                break
            room = self.cfg.inventory_limit_shares - abs(d.inventory)
            if room <= 0:
                continue
            take = min(remaining, room)
            d.inventory += sign * take
            remaining -= take
        # Whatever the dealers could not absorb is assumed taken by the wider
        # market; it still moves the mid through the impact model.

    def quotes(self, mid: float, sigma_daily: float, time_remaining_frac: float,
               interval_volume: float, touch_spread: float,
               touch_size: float) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """Return (bids, asks) as lists of (price, size), best first.

        The *level* of the touch spread comes from :class:`LiquidityModel`
        (i.e. from the tick-size structure of the name), because that is what
        determines quoted spreads in liquid US equities.  What the dealers add
        is the Avellaneda-Stoikov behaviour: an inventory-dependent
        reservation price that skews the whole ladder away from flow, and
        dealer-to-dealer heterogeneity in gamma/kappa.
          SOURCE (reservation price and optimal spread): Avellaneda &
          Stoikov (2008), Quantitative Finance 8(3):217-224,
          https://doi.org/10.1080/14697680701381228
        """
        tick = config.minimum_tick(mid)
        n_ticks = max(1, int(round(touch_spread / tick)))
        low_ticks = n_ticks // 2
        high_ticks = n_ticks - low_ticks
        tau = max(time_remaining_frac, 1e-4)
        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []
        for d in self.dealers:
            q_frac = d.inventory / max(interval_volume * 8.0, 1.0)
            # Inventory skew in relative terms, bounded by the inventory limit.
            skew = q_frac * d.gamma * sigma_daily ** 2 * tau * 250.0
            skew = max(-0.01, min(0.01, skew))
            reservation = mid * (1.0 - skew)
            # Every dealer centres its two-sided quote on the same tick grid
            # and quotes exactly n_ticks wide, so the touch spread equals the
            # structural spread instead of drifting wider through independent
            # floor/ceil rounding - or crossing to zero through per-dealer
            # jitter.  Dealer heterogeneity lives in the inventory skew and in
            # gamma/kappa, which is where Avellaneda-Stoikov puts it.
            self.rng.random()      # consume a draw: keeps streams stable
            grid = round(reservation / tick) * tick
            bid = round(grid - low_ticks * tick, 6)
            ask = round(grid + high_ticks * tick, 6)
            if bid <= 0:
                bid = tick
            if ask <= bid:
                ask = round(bid + tick, 6)
            bids.append((bid, float(touch_size)))
            asks.append((ask, float(touch_size)))
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        return bids, asks


class OrderBook:
    """Aggregated displayed book built from the dealer pool."""

    def __init__(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]],
                 tick: float, liquidity: LiquidityModel, reference_price: float) -> None:
        self.tick = tick
        self.liquidity = liquidity
        self.reference_price = reference_price
        self._bids = self._aggregate(bids, side=BUY)
        self._asks = self._aggregate(asks, side=SELL)
        self._uncross()

    def _uncross(self) -> None:
        """Defensive: a displayed book may never cross (Rule 610/611 quotes)."""
        if not self._bids or not self._asks:
            return
        if self._asks[0][0] <= self._bids[0][0]:
            grid = round((self._bids[0][0] + self._asks[0][0]) / 2.0 / self.tick) * self.tick
            self._bids = [(round(grid - self.tick * (i + 1), 6), sz)
                          for i, (_, sz) in enumerate(self._bids)]
            self._asks = [(round(grid + self.tick * (i + 1), 6), sz)
                          for i, (_, sz) in enumerate(self._asks)]

    def _aggregate(self, quotes: List[Tuple[float, float]], side: str) -> List[Tuple[float, float]]:
        """Build a full displayed ladder.

        Dealers usually cluster on one or two prices, so the ladder is extended
        out to ``book_levels`` price levels at successive minimum increments,
        with displayed size growing away from the touch (the shape Level II
        data shows for liquid names).
        """
        buckets: Dict[float, float] = {}
        for price, size in quotes:
            buckets[round(price, 6)] = buckets.get(round(price, 6), 0.0) + size
        items = sorted(buckets.items(), key=lambda kv: kv[0], reverse=(side == BUY))
        out: List[Tuple[float, float]] = []
        for i, (price, size) in enumerate(items[:self.liquidity.cfg.book_levels]):
            out.append((price, min(size, self.liquidity.book_depth(self.reference_price, i))))
        # Extend to a full ladder at successive ticks.
        while len(out) < self.liquidity.cfg.book_levels and out:
            last_price = out[-1][0]
            step = self.tick * (1 if side == SELL else -1)
            price = round(last_price - step, 6) if side == BUY else round(last_price + step, 6)
            if price <= 0:
                break
            out.append((price, self.liquidity.book_depth(self.reference_price, len(out))))
        return out

    @property
    def best_bid(self) -> float:
        return self._bids[0][0] if self._bids else 0.0

    @property
    def best_ask(self) -> float:
        return self._asks[0][0] if self._asks else 0.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return max(0.0, self.best_ask - self.best_bid)

    def walk(self, side: str, qty: float) -> Tuple[float, float, float]:
        """Consume the book.  Returns (filled_qty, vwap, depth_cost_per_share)."""
        ladder = self._asks if side == BUY else self._bids
        if not ladder:
            return 0.0, 0.0, 0.0
        remaining = qty
        cost = 0.0
        filled = 0.0
        best = ladder[0][0]
        for price, size in ladder:
            if remaining <= 0:
                break
            take = min(remaining, size)
            cost += take * price
            filled += take
            remaining -= take
        if filled <= 0:
            return 0.0, 0.0, 0.0
        # Non-displayed liquidity: the residual is filled at the touch and
        # priced by the impact model rather than by walking a deeper ladder.
        # See the ``hidden_liquidity`` note in sim/config.LiquidityConfig.
        if remaining > 0 and self.liquidity.cfg.hidden_liquidity:
            cost += remaining * best
            filled += remaining
            remaining = 0.0
        vwap = cost / filled
        depth_cost = (vwap - best) if side == BUY else (best - vwap)
        return filled, vwap, max(0.0, depth_cost)

    def snapshot(self) -> dict:
        return {"bid": round(self.best_bid, 6), "ask": round(self.best_ask, 6),
                "mid": round(self.mid, 6), "spread": round(self.spread, 6),
                "bid_size": round(self._bids[0][1]) if self._bids else 0,
                "ask_size": round(self._asks[0][1]) if self._asks else 0,
                "levels": len(self._bids) + len(self._asks)}


# --------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------

class CostModel:
    """Broker, exchange and regulatory costs, applied per fill."""

    def __init__(self, cfg: config.CostConfig) -> None:
        self.cfg = cfg

    def commission(self, qty: int, price: float) -> float:
        c = qty * self.cfg.commission_per_share + self.cfg.commission_per_order
        return max(0.0, c)

    def regulatory(self, side: str, qty: int, price: float, date: str) -> float:
        if not self.cfg.pass_regulatory_fees or side != SELL:
            return 0.0
        notional = qty * price
        sec31 = notional * config.rate_for(config.SEC31_PER_MILLION, date) / 1_000_000.0
        taf = min(qty * config.rate_for(config.FINRA_TAF_PER_SHARE, date),
                  config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, date))
        return sec31 + taf

    def taker_fee(self, qty: int) -> float:
        return qty * self.cfg.taker_fee_per_share if self.cfg.pay_exchange_fees else 0.0

    def maker_rebate(self, qty: int) -> float:
        return qty * self.cfg.maker_rebate_per_share if self.cfg.pay_exchange_fees else 0.0


# --------------------------------------------------------------------------
# Execution engine
# --------------------------------------------------------------------------

@dataclass
class VenueDay:
    """Shared, participant-independent state of one symbol for one session.

    Built once per (symbol, session) and reused by every participant, because
    the intraday path, realised volatility, ADV and VIX regime factor do not
    depend on who is trading.
    """

    symbol: str
    date: str
    instrument: Instrument
    bar: object                      # Bar
    path: IntradayPath
    sigma_daily: float
    adv: float
    vix_factor: float


@dataclass
class ParticipantVenue:
    """One participant's replica of the venue for one symbol-day.

    SIM CHOICE (documented): each participant trades against an *independent*
    replica of the venue, so one participant's impact and dealer inventory
    cannot change another participant's fills.  That keeps the leaderboard
    independent of participant ordering and makes every run exactly
    reproducible.  It also means the participants' combined flow does not
    clear a single shared book - a real market would aggregate them.  Flagged
    as a limitation in research/LIMITATIONS.json (L-04).
    """

    venue: VenueDay
    makers: MarketMakerPool
    permanent_impact: float = 0.0    # cumulative relative shift of the mid
    volume_taken: int = 0            # shares already taken by this participant

    @property
    def symbol(self) -> str:
        return self.venue.symbol

    @property
    def date(self) -> str:
        return self.venue.date

    @property
    def bar(self):
        return self.venue.bar

    @property
    def path(self) -> IntradayPath:
        return self.venue.path

    @property
    def sigma_daily(self) -> float:
        return self.venue.sigma_daily

    @property
    def adv(self) -> float:
        return self.venue.adv

    @property
    def vix_factor(self) -> float:
        return self.venue.vix_factor


class ExecutionEngine:
    """Turns participant orders into costed fills against the simulated venue.

    ``snap_quotes_to_tick`` and ``impact_exponent`` are model-form switches that
    exist so the sensitivity harness (sim/sensitivity.py, and IR-29/IR-26) can
    measure what the two documented modelling choices are worth: without tick
    snapping the re-centred ladder publishes prices no exchange could display,
    and a 3/5 exponent is the form Almgren, Thum, Hauptmann & Li (2005) measured
    instead of the square root.  Both default to the shipped behaviour, and
    neither is a CompetitionConfig field - the configuration fingerprint, and
    therefore the replay the published season was generated from, must not move
    because a sensitivity knob exists.
    """

    def __init__(self, cfg: config.CompetitionConfig, *,
                 snap_quotes_to_tick: bool = True,
                 impact_exponent: float = 0.5) -> None:
        self.cfg = cfg
        self.snap_quotes_to_tick = snap_quotes_to_tick
        self.liquidity = LiquidityModel(cfg.liquidity)
        self.impact = ImpactModel(cfg.impact, exponent=impact_exponent)
        self.costs = CostModel(cfg.costs)

    # -- venue construction ------------------------------------------------
    def make_venue_day(self, md, symbol: str, t: int, seed: int,
                       session_index: Optional[int] = None) -> VenueDay:
        """Build the shared venue state for one symbol-session."""
        inst = md.instruments[symbol]
        bar = md.bar(symbol, t)
        sigma = md.realised_sigma_daily(symbol, t)
        adv = md.adv(symbol, t)
        rng = random.Random(f"{seed}:{symbol}:{bar.date}:path")
        si = t - md.first_competition_index if session_index is None else session_index
        minutes = md.calendar.sessions[max(0, si)].minutes if 0 <= si < len(md.calendar.sessions) \
            else config.SESSION_CLOSE_MINUTES - config.SESSION_OPEN_MINUTES
        path = IntradayPath(bar, sigma, minutes, rng)
        return VenueDay(symbol=symbol, date=bar.date, instrument=inst, bar=bar,
                        path=path, sigma_daily=sigma, adv=adv,
                        vix_factor=md.vix_regime_factor(t))

    def make_participant_venue(self, venue: VenueDay, participant: str,
                               seed: int) -> ParticipantVenue:
        makers = MarketMakerPool(
            self.cfg.market_maker,
            f"{seed}:{venue.symbol}:{venue.date}:mm:{participant}")
        return ParticipantVenue(venue=venue, makers=makers)

    def quote_at(self, dm: ParticipantVenue, k: int) -> Tuple[OrderBook, float]:
        """Book for interval ``k`` including any permanent impact so far."""
        base = dm.path.price(k)
        mid = base * (1.0 + dm.permanent_impact)
        tau = dm.path.remaining_volume_share(k)
        ivol = dm.path.interval_volume(k)
        touch_spread = self.liquidity.quoted_spread(
            dm.venue.instrument, mid, dm.sigma_daily, dm.vix_factor)
        touch_size = self.liquidity.touch_size(mid)
        bids, asks = dm.makers.quotes(mid, dm.sigma_daily * dm.vix_factor, tau, ivol,
                                      touch_spread, touch_size)
        # Re-centre the ladder on the impacted mid so quotes track the path.
        #
        # The shift is snapped to a whole number of minimum increments.  A
        # displayed quotation must be priced in a legal increment - 17 CFR
        # 242.612(b) - so moving the ladder by an arbitrary fraction (which is
        # what an unsnapped ``mid - raw_mid`` does) would publish quotes no
        # exchange could display, e.g. a bid of 655.4003 on SPY.  Snapping keeps
        # the ladder within half a tick of the impacted mid, leaves the quoted
        # spread at exactly ``n_ticks`` wide, and cannot cross the book, because
        # every level moves by the same amount.
        #   SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612
        #   (see research/IRREGULARITIES.json IR-28: found and fixed here)
        tick = config.minimum_tick(mid)
        raw_mid = (bids[0][0] + asks[0][0]) / 2.0 if bids and asks else mid
        shift = (round((mid - raw_mid) / tick) * tick if self.snap_quotes_to_tick
                 else mid - raw_mid)
        bids = [(round(p + shift, 6), s) for p, s in bids]
        asks = [(round(p + shift, 6), s) for p, s in asks]
        book = OrderBook(bids, asks, tick, self.liquidity, mid)
        return book, mid

    # -- execution --------------------------------------------------------
    def execute(self, dm: ParticipantVenue, order: Order, seed: int,
                start_interval: int = 0) -> Fill:
        """Execute one order against the participant's venue replica.

        ``start_interval`` is the intraday interval at which the order arrives.
        Strategies always trade at the open (interval 0).  The end-of-competition
        forced liquidation uses the *last* interval so that closing prints pay a
        realistic spread and impact rather than being waved through at the
        close price - the rule copied from The Leap ("all open positions are
        closed at the end of the competition"), but costed.
        """
        rng = random.Random(
            f"{seed}:{order.participant}:{order.symbol}:{dm.date}:{order.side}:"
            f"{order.quantity}:{start_interval}:{order.reason[:8]}")
        # Closing-auction orders only meet the book at the closing cross, no
        # matter when the decision that wrote them was made.  Forcing the
        # interval here (rather than trusting the caller) is what makes MOC/LOC
        # semantically different from ``at_close=True``: the ticket itself
        # carries the execution window.
        if order.order_type in (MOC, LOC):
            start_interval = dm.path.K
        decision_book, decision_mid = self.quote_at(dm, start_interval)
        decision_price = decision_mid

        borrow = borrow_terms(order.symbol)
        if order.side == SELL and not borrow.get("shortable", True):
            return self._reject(order, dm, decision_price, decision_book,
                                "not borrowable (Reg SHO locate unavailable)")

        # Participation limit: cap total demand against today's volume.
        cap = self.cfg.liquidity.max_participation * float(dm.bar.volume)
        room = max(0.0, cap - dm.volume_taken)
        if room <= 0:
            return self._reject(order, dm, decision_price, decision_book,
                                "venue participation limit exhausted for the session")
        target_qty = int(min(order.quantity, room))
        if target_qty <= 0:
            return self._reject(order, dm, decision_price, decision_book,
                                "no executable volume inside participation limit")

        if order.order_type == LIMIT:
            return self._execute_limit(dm, order, target_qty, decision_price,
                                       decision_book, rng, start_interval)
        if order.order_type == STOP:
            return self._execute_stop(dm, order, target_qty, decision_price,
                                      decision_book, rng, start_interval)
        if order.order_type == LOC:
            # Limit-on-close: the closing cross is the only print that can
            # fill it, and only if the close is marketable against the limit.
            book, _mid = self.quote_at(dm, start_interval)
            limit = float(order.limit_price or 0.0)
            marketable = ((order.side == BUY and limit >= book.best_ask) or
                          (order.side == SELL and limit <= book.best_bid
                           and book.best_bid > 0))
            if not marketable:
                return self._reject(order, dm, decision_price, decision_book,
                                    "limit-on-close not marketable at the closing cross",
                                    status="expired")
            return self._execute_market(dm, order, target_qty, decision_price,
                                        decision_book, rng,
                                        start_interval=start_interval)
        if order.order_type == MOC:
            return self._execute_market(dm, order, target_qty, decision_price,
                                        decision_book, rng,
                                        start_interval=start_interval)
        return self._execute_market(dm, order, target_qty, decision_price,
                                    decision_book, rng, start_interval=start_interval)

    # -- order types ------------------------------------------------------
    def _execute_market(self, dm: ParticipantVenue, order: Order, qty: int,
                        decision_price: float, decision_book: OrderBook,
                        rng: random.Random, start_interval: int = 0) -> Fill:
        slices = self._slice(qty, dm, start_interval)
        filled = 0
        notional = 0.0
        spread_cost = depth_cost = impact_cost = 0.0
        last_quote: Dict[str, float] = {}
        for k, slice_qty in slices:
            if slice_qty <= 0:
                continue
            book, mid = self.quote_at(dm, k)
            half_spread = book.spread / 2.0
            book_filled, vwap, dcost = book.walk(order.side, slice_qty)
            take = int(min(book_filled, slice_qty))
            if take <= 0:
                break
            imp_ret = self.impact.impact_return(take, max(dm.adv, 1.0), dm.sigma_daily)
            perm, temp = self.impact.split(imp_ret)
            ref = book.best_ask if order.side == BUY else book.best_bid
            fill_price = ref + (dcost if order.side == BUY else -dcost)
            fill_price += (1 if order.side == BUY else -1) * temp * mid
            fill_price = max(config.minimum_tick(fill_price), fill_price)
            tick = config.minimum_tick(fill_price)
            fill_price = round(round(fill_price / tick) * tick, 6)
            filled += take
            notional += take * fill_price
            spread_cost += take * half_spread
            depth_cost += take * dcost
            impact_cost += take * temp * mid
            dm.permanent_impact += (perm if order.side == BUY else -perm)
            dm.volume_taken += take
            dm.makers.take_inventory(order.side, take)
            last_quote = book.snapshot()
        return self._settle(order, dm, filled, qty, notional, decision_price,
                            spread_cost, depth_cost, impact_cost, last_quote,
                            start_interval)

    def _execute_limit(self, dm: ParticipantVenue, order: Order, qty: int,
                       decision_price: float, decision_book: OrderBook,
                       rng: random.Random, start_interval: int = 0) -> Fill:
        limit = float(order.limit_price or 0.0)
        tick = config.minimum_tick(limit)
        if abs(round(limit / tick) * tick - limit) > 1e-9:
            return self._reject(order, dm, decision_price, decision_book,
                                f"limit price {limit} violates Rule 612 minimum increment")
        book, mid = self.quote_at(dm, start_interval)
        # Marketable limit orders take liquidity immediately at the touch.
        if (order.side == BUY and limit >= book.best_ask) or \
           (order.side == SELL and limit <= book.best_bid and book.best_bid > 0):
            return self._execute_market(dm, order, qty, decision_price,
                                        decision_book, rng,
                                        start_interval=start_interval)
        crossed, k_cross = dm.path.crossed(limit, order.side,
                                           from_interval=start_interval)
        if not crossed:
            return self._reject(order, dm, decision_price, decision_book,
                                "limit never traded through (expired)", status="expired")
        # Queue position: a limit order resting at the touch only fills if
        # enough volume trades at or through its price.  Model the fill ratio
        # from how far the path travelled beyond the limit.
        beyond = 0.0
        for k in range(k_cross, dm.path.K + 1):
            p = dm.path.price(k)
            if order.side == BUY:
                beyond = max(beyond, (limit - p) / max(limit, 1e-9))
            else:
                beyond = max(beyond, (p - limit) / max(limit, 1e-9))
        fill_ratio = min(1.0, 0.35 + beyond * 400.0)
        take = int(qty * fill_ratio)
        take = int(max(take, 0))
        if take <= 0:
            return self._reject(order, dm, decision_price, decision_book,
                                "queue position not reached (expired)", status="expired")
        book_k, mid_k = self.quote_at(dm, k_cross)
        price = limit
        # A resting order earns the maker rebate instead of paying the taker fee.
        filled = take
        notional = filled * price
        rebate = self.costs.maker_rebate(filled)
        commission = self.costs.commission(filled, price)
        reg = self.costs.regulatory(order.side, filled, price, dm.date)
        dm.volume_taken += filled
        dm.makers.take_inventory(order.side, filled)
        avg = notional / filled if filled else price
        slippage = 0.0 if decision_price == 0 else \
            10_000.0 * (avg - decision_price) / decision_price * (1 if order.side == BUY else -1)
        return Fill(order=order, date=dm.date, filled_qty=filled, requested_qty=order.quantity,
                    avg_price=avg, decision_price=decision_price, spread_cost=0.0,
                    depth_cost=0.0, impact_cost=0.0, commission=commission,
                    exchange_fee=0.0, regulatory_fee=reg, rebate=rebate,
                    quote_at_fill=book_k.snapshot(),
                    status="filled" if filled >= order.quantity else "partial",
                    interval=k_cross, slippage_bps=slippage)

    def _execute_stop(self, dm: ParticipantVenue, order: Order, qty: int,
                      decision_price: float, decision_book: OrderBook,
                      rng: random.Random, start_interval: int = 0) -> Fill:
        stop = float(order.stop_price or 0.0)
        # A sell-stop triggers when the path trades at/below the stop; a
        # buy-stop triggers at/above it.
        triggered, k = False, dm.path.K
        for j in range(max(1, start_interval), dm.path.K + 1):
            p = dm.path.price(j)
            if (order.side == BUY and p >= stop) or (order.side == SELL and p <= stop):
                triggered, k = True, j
                break
        if not triggered:
            return self._reject(order, dm, decision_price, decision_book,
                                "stop never triggered (expired)", status="expired")
        fill = self._execute_market(dm, order, qty, decision_price, decision_book,
                                    rng, start_interval=k)
        return fill

    # -- helpers ----------------------------------------------------------
    def _slice(self, qty: int, dm: ParticipantVenue, start_interval: int) -> List[Tuple[int, int]]:
        """Capacity-driven slicing: execute immediately, spread only if size demands.

        A market order submitted at the open is not a VWAP algorithm.  It trades
        at the touch and keeps going only for as long as each interval's
        liquidity can absorb it inside the participation limit.  Small orders
        therefore complete in the first interval; only an order large relative
        to that session's volume is forced to span the day - which is what
        actually happens in a real market.

        The previous behaviour spread *every* market order across the whole
        session by volume weight.  That handed each participant a free intraday
        average price which systematically rewarded orders placed on days the
        name trended, and showed up as a large negative "intraday drift" term
        in the implementation-shortfall decomposition (measured at -$1,857 on
        $614k of paper notional for one participant before this change).
        """
        K = dm.path.K
        cap_frac = self.cfg.liquidity.max_participation
        slices: List[Tuple[int, int]] = []
        remaining = int(qty)
        k = max(0, min(start_interval, K))
        while remaining > 0 and k <= K:
            cap = int(dm.path.interval_volume(min(k, K - 1)) * cap_frac)
            take = min(remaining, max(cap, 1))
            slices.append((min(k, K), take))
            remaining -= take
            k += 1
        if remaining > 0 and slices:
            # Residual beyond the interval caps is absorbed as non-displayed
            # liquidity at the last interval reached (priced by the impact
            # model, not by walking a deeper ladder).
            kk, qq = slices[-1]
            slices[-1] = (kk, qq + remaining)
        elif remaining > 0:
            slices.append((min(start_interval, K), remaining))
        return slices

    def _settle(self, order: Order, dm: ParticipantVenue, filled: int, requested: int,
                notional: float, decision_price: float, spread_cost: float,
                depth_cost: float, impact_cost: float, quote: Dict[str, float],
                interval: int) -> Fill:
        if filled <= 0:
            return self._reject(order, dm, decision_price, None,
                                "no executable liquidity inside the participation limit")
        avg = notional / filled
        commission = self.costs.commission(filled, avg)
        exch = self.costs.taker_fee(filled)
        reg = self.costs.regulatory(order.side, filled, avg, dm.date)
        status = "filled" if filled >= requested else "partial"
        slippage = 0.0 if decision_price == 0 else \
            10_000.0 * (avg - decision_price) / decision_price * (1 if order.side == BUY else -1)
        return Fill(order=order, date=dm.date, filled_qty=filled, requested_qty=order.quantity,
                    avg_price=avg, decision_price=decision_price, spread_cost=spread_cost,
                    depth_cost=depth_cost, impact_cost=impact_cost, commission=commission,
                    exchange_fee=exch, regulatory_fee=reg, rebate=0.0,
                    quote_at_fill=quote or {}, status=status, interval=interval,
                    slippage_bps=slippage)

    def _reject(self, order: Order, dm: ParticipantVenue, decision_price: float,
                book: Optional[OrderBook], reason: str, status: str = "rejected") -> Fill:
        return Fill(order=order, date=dm.date, filled_qty=0,
                    requested_qty=order.quantity, avg_price=0.0,
                    decision_price=decision_price, spread_cost=0.0, depth_cost=0.0,
                    impact_cost=0.0, commission=0.0, exchange_fee=0.0,
                    regulatory_fee=0.0, rebate=0.0,
                    quote_at_fill=book.snapshot() if book is not None else {},
                    status=status, reject_reason=reason)
