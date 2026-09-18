"""Participant account: cash, positions, margin, P&L and cost attribution.

Accounting identity enforced by tests/test_accounting.py:

    equity == cash + sum(quantity * mark_price)

and every dollar that leaves or enters cash is attributable to exactly one of:
trade proceeds, incremental costs (commission / exchange fee / regulatory fee
net of rebate), borrow fees on short positions, or cash dividends.

Spread, depth and impact costs are *embedded in the fill price*, so they are
tracked separately as implementation shortfall against the decision mid rather
than being deducted from cash a second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config
from .microstructure import BUY, SELL, Fill, borrow_terms


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0
    realized_gross: float = 0.0
    shares_bought: int = 0
    shares_sold: int = 0
    borrow_fees: float = 0.0
    dividends: float = 0.0

    def market_value(self, mark: float) -> float:
        return self.quantity * mark

    def unrealized(self, mark: float) -> float:
        if self.quantity == 0:
            return 0.0
        return (mark - self.avg_cost) * self.quantity

    def to_row(self, mark: float) -> dict:
        return {"symbol": self.symbol, "quantity": self.quantity,
                "avg_cost": round(self.avg_cost, 6), "mark": round(mark, 6),
                "market_value": round(self.market_value(mark), 2),
                "unrealized_pnl": round(self.unrealized(mark), 2),
                "realized_gross": round(self.realized_gross, 2),
                "borrow_fees": round(self.borrow_fees, 4),
                "dividends": round(self.dividends, 4)}


@dataclass
class CostLedger:
    commission: float = 0.0
    exchange_fee: float = 0.0
    regulatory_fee: float = 0.0
    rebate: float = 0.0
    spread_cost: float = 0.0
    depth_cost: float = 0.0
    impact_cost: float = 0.0
    borrow_fee: float = 0.0

    @property
    def incremental_cash(self) -> float:
        """Cash actually paid on top of the fill price."""
        return self.commission + self.exchange_fee + self.regulatory_fee - self.rebate

    @property
    def slippage_vs_decision(self) -> float:
        """Embedded execution cost measured against the decision mid."""
        return self.spread_cost + self.depth_cost + self.impact_cost

    @property
    def total(self) -> float:
        return self.incremental_cash + self.slippage_vs_decision + self.borrow_fee

    def as_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}


class Account:
    """One competition participant's paper account."""

    def __init__(self, participant: str, starting_cash: float,
                 margin: config.MarginConfig) -> None:
        self.participant = participant
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.margin = margin
        self.positions: Dict[str, Position] = {}
        self.costs = CostLedger()
        self.realized_gross = 0.0
        self.dividends_received = 0.0
        self.borrow_paid = 0.0
        self.fills: List[Fill] = []
        self.rejected: List[Fill] = []
        self.equity_curve: List[Tuple[str, float]] = []
        self.margin_calls: List[str] = []
        self.day_trades: List[str] = []  # dates with a same-day round trip (PDT)
        self.day_trade_count = 0        # round trips closed intraday, all sessions
        # Dividends the account OWES because it was short across an ex-date.  A
        # stock loan requires the borrower to pay the lender a "manufactured"
        # dividend; the position does not receive the dividend, it pays one.
        self.dividends_in_lieu_paid = 0.0
        # Signed quantity built up per symbol during the current session, and
        # whether the current clip is accumulating or reducing it.  Used only
        # to detect genuine intraday round trips; see _count_day_trade.
        self._intraday_net: Dict[str, int] = {}
        self._intraday_clip: Dict[str, str] = {}
        # Quantity this account OPENED during the current session, per symbol:
        # the pool a closing clip has to draw on before it is a day trade.
        self._intraday_open: Dict[str, int] = {}
        # Session index at which each symbol's current position was opened.
        # Used by holding-period exit rules; part of participant state, so it
        # is persisted with the run and never shared between participants.
        self._entry_day: Dict[str, int] = {}
        self._opened_today: Dict[str, int] = {}
        self._current_date = ""

    # ------------------------------------------------------------------
    def start_day(self, date: str) -> None:
        if date != self._current_date:
            self._opened_today = {}
            self._intraday_net = {}
            self._intraday_clip = {}
            self._intraday_open = {}
        self._current_date = date

    def _count_day_trade(self, symbol: str, before_qty: int,
                         signed_qty: int) -> None:
        """Count intraday round trips the way the rule actually defines them.

        FINRA Rule 4210(f)(8)(B)(i) defines day trading as "the purchasing and
        selling or the selling and purchasing of the same security on the same
        day in a margin account except for positions held overnight", and
        Regulatory Notice 21-13 Interpretation /02 counts "the number of times
        during the day that the day trading customer changes its trading
        direction" (https://www.finra.org/rules-guidance/notices/21-13).  Both
        halves matter, and the Notice works six sequences out by hand.  Those
        six answers rule out the two implementations a reasonable person writes
        first: counting closing transactions calls buy 500 / sell 100 / sell 100
        / sell 300 three day trades instead of one, and counting any fill that
        reduces the day's quantity calls buy 250 / buy 300 / buy 100 / sell 150
        / sell 175 two instead of one.  A naive version also turns
        @OvernightCarry_NO's nightly lay-off-then-re-entry into a day trade on
        every single session, which is exactly what the "except for positions
        held overnight" carve-out forbids.

        So: consecutive same-direction fills are one clip, a clip is "opening"
        when it grows the ABSOLUTE position (not when it flips the day's net,
        which is what let the overnight case through), and one day trade is
        recorded for each switch from an opening clip to a closing clip that
        gives back shares this account opened today - `self._intraday_open`
        tracks that pool, so a lay-off of yesterday's position can never draw on
        it.  tests/test_portfolio.py::TestFinraDayTradeExamples pins all six
        examples plus the overnight case down; that test is the specification.
        """
        if not signed_qty:
            return
        growing = (before_qty == 0) or (before_qty > 0) == (signed_qty > 0)
        opened_today = self._intraday_open.get(symbol, 0)
        if growing:
            self._intraday_open[symbol] = opened_today + abs(signed_qty)
            self._intraday_clip[symbol] = "open"
            return
        # A closing clip.  It only makes a day trade if it hands back shares
        # opened in this session, and only the FIRST one of the clip counts:
        # scaling out of a position is one round trip, not one per tranche.
        self._intraday_open[symbol] = max(0, opened_today - abs(signed_qty))
        if self._intraday_clip.get(symbol) == "open" and opened_today > 0:
            self.day_trade_count += 1
            if self._current_date not in self.day_trades:
                self.day_trades.append(self._current_date)
        self._intraday_clip[symbol] = "close"

    def quantities_at_open(self) -> Dict[str, int]:
        """Positions as of the start of the current session.

        Dividend entitlement is decided by ownership at the close BEFORE the
        ex-date, not by what the participant happens to hold after today's
        trades, so the engine snapshots the book here and pays on that.
        """
        return {s: p.quantity for s, p in self.positions.items() if p.quantity}

    def position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def quantity(self, symbol: str) -> int:
        return self.positions.get(symbol, Position(symbol=symbol)).quantity

    # ------------------------------------------------------------------
    def gross_exposure(self, marks: Dict[str, float]) -> float:
        return sum(abs(p.quantity) * marks.get(s, p.avg_cost)
                   for s, p in self.positions.items() if p.quantity)

    def net_exposure(self, marks: Dict[str, float]) -> float:
        return sum(p.quantity * marks.get(s, p.avg_cost)
                   for s, p in self.positions.items() if p.quantity)

    def equity(self, marks: Dict[str, float]) -> float:
        return self.cash + sum(p.quantity * marks.get(s, p.avg_cost)
                               for s, p in self.positions.items())

    def leverage(self, marks: Dict[str, float]) -> float:
        eq = self.equity(marks)
        return self.gross_exposure(marks) / eq if eq > 0 else float("inf")

    def buying_power(self, marks: Dict[str, float]) -> float:
        eq = self.equity(marks)
        return max(0.0, eq * self.margin.max_gross_leverage - self.gross_exposure(marks))

    # ------------------------------------------------------------------
    def can_increase(self, symbol: str, side: str, qty: int, price: float,
                     marks: Dict[str, float]) -> Tuple[bool, str]:
        """Pre-trade margin / shortability screen."""
        pos = self.position(symbol)
        current = pos.quantity
        proposed = current + (qty if side == BUY else -qty)
        if side == SELL and current - qty < 0 and not self.margin.shorting_allowed:
            return False, "shorting disabled by competition rules"
        if side == SELL and current - qty < 0 and not borrow_terms(symbol).get("shortable", True):
            return False, "no locate available (Reg SHO)"
        eq = self.equity(marks)
        if eq <= 0:
            return False, "account equity exhausted"
        # A broker may refuse new risk, but it must never trap a participant in
        # an oversized position: any order that strictly REDUCES the absolute
        # size of the position is allowed through even when the account is
        # already above the gross cap (which happens when prices move, not when
        # the participant trades).  Without this, a book that drifts above 2.0x
        # gross can no longer be rebalanced at all and just bleeds.
        if abs(proposed) < abs(current):
            return True, ""
        # Gross leverage cap.
        new_gross = sum(abs(p.quantity) * marks.get(s, p.avg_cost)
                        for s, p in self.positions.items())
        new_gross -= abs(current) * marks.get(symbol, price)
        new_gross += abs(proposed) * price
        if new_gross > eq * self.margin.max_gross_leverage + 1e-6:
            return False, (f"gross leverage cap {self.margin.max_gross_leverage:.2f}x "
                           f"would be breached ({new_gross / eq:.2f}x)")
        # Cash availability for a plain unlevered buy.
        if side == BUY and proposed > 0 and price * qty > self.cash + eq * (
                self.margin.max_gross_leverage - 1.0) + 1e-6:
            return False, "insufficient buying power"
        return True, ""

    def max_permissible_quantity(self, symbol: str, side: str, price: float,
                                 marks: Dict[str, float]) -> int:
        """Largest size that still passes :meth:`can_increase`.

        The broker-side risk check.  Every order - including hand-built ones
        that did not come from :meth:`Context.orders_to_targets` - is clipped
        to this size before it reaches the venue, so no participant can exceed
        the competition's leverage limit no matter how its strategy is coded.
        Monotone in quantity, so a bisection is exact to one share.
        """
        ok, _ = self.can_increase(symbol, side, 1, price, marks)
        if not ok:
            return 0
        lo, hi = 1, 1
        while True:
            ok, _ = self.can_increase(symbol, side, hi, price, marks)
            if not ok or hi > 500_000_000:
                break
            lo, hi = hi, hi * 4
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            ok, _ = self.can_increase(symbol, side, mid, price, marks)
            if ok:
                lo = mid
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------------
    def apply_fill(self, fill: Fill, marks: Dict[str, float]) -> None:
        if fill.filled_qty <= 0:
            self.rejected.append(fill)
            return
        self.fills.append(fill)
        pos = self.position(fill.order.symbol)
        qty = fill.filled_qty
        price = fill.avg_price
        side = fill.order.side
        signed = qty if side == BUY else -qty
        before_qty = pos.quantity

        # Realised P&L on the reducing leg (average-cost basis).
        if pos.quantity != 0 and (pos.quantity > 0) != (signed > 0):
            closing = min(abs(pos.quantity), qty)
            if pos.quantity > 0:
                pos.realized_gross += (price - pos.avg_cost) * closing
            else:
                pos.realized_gross += (pos.avg_cost - price) * closing
            self.realized_gross += (price - pos.avg_cost) * closing if pos.quantity > 0 \
                else (pos.avg_cost - price) * closing
            remainder = qty - closing
            pos.quantity += -closing if pos.quantity > 0 else closing
            if remainder > 0:
                pos.avg_cost = price
                pos.quantity += remainder if side == BUY else -remainder
            if pos.quantity == 0:
                pos.avg_cost = 0.0
        else:
            new_qty = pos.quantity + signed
            if pos.quantity == 0:
                pos.avg_cost = price
            else:
                pos.avg_cost = (pos.avg_cost * abs(pos.quantity) + price * qty) / abs(new_qty)
            pos.quantity = new_qty

        if side == BUY:
            pos.shares_bought += qty
            self.cash -= qty * price
        else:
            pos.shares_sold += qty
            self.cash += qty * price

        # Incremental cash costs.
        self.cash -= fill.commission + fill.exchange_fee + fill.regulatory_fee
        self.cash += fill.rebate
        self.costs.commission += fill.commission
        self.costs.exchange_fee += fill.exchange_fee
        self.costs.regulatory_fee += fill.regulatory_fee
        self.costs.rebate += fill.rebate
        self.costs.spread_cost += fill.spread_cost
        self.costs.depth_cost += fill.depth_cost
        self.costs.impact_cost += fill.impact_cost

        # Pattern-day-trader bookkeeping; see _count_day_trade for the rule.
        # `before` is the position as it stood at the START of this fill, which
        # is what separates "unwinding yesterday" from "giving back today".
        self._count_day_trade(fill.order.symbol, before_qty, signed)
        self._intraday_net[fill.order.symbol] = \
            self._intraday_net.get(fill.order.symbol, 0) + signed
        self._opened_today[fill.order.symbol] = self._opened_today.get(fill.order.symbol, 0) + qty

    # ------------------------------------------------------------------
    def accrue_carry(self, date: str, marks: Dict[str, float],
                     calendar_day_fraction: float = 1.0 / 252.0) -> dict:
        """Daily borrow fee on shorts and cash dividends on longs."""
        out = {"borrow_fee": 0.0, "dividends": 0.0}
        for symbol, pos in self.positions.items():
            if pos.quantity == 0:
                continue
            if pos.quantity < 0:
                terms = borrow_terms(symbol)
                fee = abs(pos.quantity) * marks.get(symbol, pos.avg_cost) * \
                    float(terms["fee_annual"]) * calendar_day_fraction
                pos.borrow_fees += fee
                self.cash -= fee
                self.costs.borrow_fee += fee
                self.borrow_paid += fee
                out["borrow_fee"] += fee
        return out

    def settle_dividend(self, symbol: str, per_share: float,
                        entitled_qty: Optional[int] = None) -> dict:
        """Settle one ex-date for one symbol.

        ``entitled_qty`` is the quantity held on the close BEFORE the ex-date
        (the record-date position).  A long is paid the dividend; a short owes
        the lender a manufactured "payment in lieu", because the lender is
        entitled to the distribution even though the borrower holds the shares.
        Defaulting ``entitled_qty`` to None means "use the position as it stands
        after today's trades", which is what a naive simulator does and is wrong
        on both counts: a position opened on the ex-date itself is not entitled
        to anything, and a position closed on the ex-date is still owed (or
        still owes) it.
        """
        pos = self.position(symbol)
        qty = pos.quantity if entitled_qty is None else int(entitled_qty)
        per_share = float(per_share)
        received = max(0.0, qty) * per_share if per_share > 0 else 0.0
        in_lieu = max(0.0, -qty) * per_share if per_share > 0 else 0.0
        if received:
            self.cash += received
            pos.dividends += received
            self.dividends_received += received
        if in_lieu:
            self.cash -= in_lieu
            pos.dividends -= in_lieu
            self.dividends_in_lieu_paid += in_lieu
        return {"received": received, "in_lieu": in_lieu}

    def pay_dividend(self, symbol: str, per_share: float) -> float:
        """Back-compatible wrapper: dividends on the current long position."""
        return self.settle_dividend(symbol, per_share)["received"]

    # ------------------------------------------------------------------
    def mark(self, date: str, marks: Dict[str, float]) -> dict:
        equity = self.equity(marks)
        self.equity_curve.append((date, equity))
        return {
            "date": date,
            "equity": round(equity, 2),
            "cash": round(self.cash, 2),
            "gross_exposure": round(self.gross_exposure(marks), 2),
            "net_exposure": round(self.net_exposure(marks), 2),
            "leverage": round(self.leverage(marks), 4),
            "realized_gross": round(self.realized_gross, 2),
            "unrealized": round(sum(p.unrealized(marks.get(s, p.avg_cost))
                                    for s, p in self.positions.items()), 2),
            "open_positions": sum(1 for p in self.positions.values() if p.quantity),
            "return_pct": round(100.0 * (equity / self.starting_cash - 1.0), 4),
        }

    def maintenance_breach(self, marks: Dict[str, float]) -> Optional[str]:
        """Return the worst symbol if the house maintenance rule is breached."""
        eq = self.equity(marks)
        if eq <= 0:
            return max(self.positions, key=lambda s: abs(self.positions[s].quantity
                                                         * marks.get(s, 0.0)),
                       default=None)
        gross = self.gross_exposure(marks)
        # Maintenance: equity must stay above maintenance_margin * gross long MV.
        long_mv = sum(p.quantity * marks.get(s, p.avg_cost)
                      for s, p in self.positions.items() if p.quantity > 0)
        short_mv = sum(-p.quantity * marks.get(s, p.avg_cost)
                       for s, p in self.positions.items() if p.quantity < 0)
        required = self.margin.maintenance_margin * (long_mv + short_mv)
        if eq < required:
            self.margin_calls.append(f"{self._current_date}: equity {eq:,.2f} < "
                                     f"maintenance requirement {required:,.2f}")
            worst = max(self.positions.items(),
                        key=lambda kv: abs(kv[1].quantity) * marks.get(kv[0], 0.0),
                        default=(None,))[0]
            return worst
        return None

    # ------------------------------------------------------------------
    def summary(self, marks: Dict[str, float]) -> dict:
        equity = self.equity(marks)
        return {
            "participant": self.participant,
            "starting_cash": round(self.starting_cash, 2),
            "final_equity": round(equity, 2),
            "net_pnl": round(equity - self.starting_cash, 2),
            "total_return_pct": round(100.0 * (equity / self.starting_cash - 1.0), 4),
            "realized_gross_pnl": round(self.realized_gross, 2),
            "unrealized_pnl": round(sum(p.unrealized(marks.get(s, p.avg_cost))
                                        for s, p in self.positions.items()), 2),
            "dividends_received": round(self.dividends_received, 2),
            "dividends_in_lieu_paid": round(self.dividends_in_lieu_paid, 2),
            "borrow_fees_paid": round(self.borrow_paid, 2),
            "costs": self.costs.as_dict(),
            "incremental_cash_costs": round(self.costs.incremental_cash, 2),
            "slippage_vs_decision": round(self.costs.slippage_vs_decision, 2),
            "total_execution_cost": round(self.costs.total, 2),
            "fills": len(self.fills),
            "rejected_or_expired": len(self.rejected),
            "shares_bought": sum(p.shares_bought for p in self.positions.values()),
            "shares_sold": sum(p.shares_sold for p in self.positions.values()),
            "margin_calls": self.margin_calls,
            "day_trades": len(self.day_trades),
            "day_trade_count": self.day_trade_count,
            "open_positions": {s: p.to_row(marks.get(s, p.avg_cost))
                               for s, p in self.positions.items() if p.quantity},
        }
