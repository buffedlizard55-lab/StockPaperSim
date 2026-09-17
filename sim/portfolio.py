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
        self.day_trades: List[str] = []  # dates with a round-trip (PDT tracking)
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
        self._current_date = date

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

        # Pattern-day-trader style bookkeeping (informational).
        if self._opened_today.get(fill.order.symbol, 0) and \
                self._day_trade_closes(pos, side):
            if self._current_date not in self.day_trades:
                self.day_trades.append(self._current_date)
        self._opened_today[fill.order.symbol] = self._opened_today.get(fill.order.symbol, 0) + qty

    @staticmethod
    def _day_trade_closes(pos: Position, side: str) -> bool:
        return True

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

    def pay_dividend(self, symbol: str, per_share: float) -> float:
        pos = self.position(symbol)
        if pos.quantity <= 0 or per_share <= 0:
            return 0.0
        amount = pos.quantity * per_share
        self.cash += amount
        pos.dividends += amount
        self.dividends_received += amount
        return amount

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
            "open_positions": {s: p.to_row(marks.get(s, p.avg_cost))
                               for s, p in self.positions.items() if p.quantity},
        }
