"""The competition engine: one season, N participants, one shared market path.

Session lifecycle (all participants see the same information set):

1. Build the shared venue state for every tradable symbol (intraday path,
   realised volatility, ADV, VIX regime factor).  This is participant
   independent, so it is built once per symbol-session and cached.
2. Snapshot the opening touch quote for every symbol.  Every participant is
   handed the *same* opening quote, so the leaderboard cannot depend on the
   order in which participants are processed.
3. For each participant: decide at the open (strategies may only read
   prior-session history plus today's opening quote - enforced by
   :class:`sim.strategies.Context`), then execute against that participant's
   own replica of the venue.
4. Accrue carry: borrow fees on shorts, cash dividends on ex-dates.
5. Mark every account to the session close, append the equity row and the
   position snapshot to memory, and test the maintenance-margin rule.  A
   breach is not liquidated intraday (this is a daily-bar engine); it is
   liquidated at the *next* open, which is recorded as a margin event.
6. On the final session, force-liquidate every open position at the last
   intraday interval before the final mark - the rule copied from The Leap
   ("all open positions are automatically closed at the end of the
   competition"), but costed through the venue instead of waved through at the
   close print.

Determinism: every random draw in the system is keyed by an explicit string
(seed, participant, symbol, date, order attributes), never by wall-clock or
hash ordering.  Two runs with the same seed produce byte-identical memory.
"""

from __future__ import annotations

import re
import time
import traceback
from dataclasses import asdict
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import analytics, config, memory
from .microstructure import (BUY, SELL, LIMIT, LOC, MARKET, ExecutionEngine,
                             Fill, Order, ParticipantVenue, VenueDay)
from .portfolio import Account
from .strategies import Context, Strategy, StrategySpec, build_roster


class ParticipantRuntime:
    """One competitor's live state during the run."""

    def __init__(self, spec: StrategySpec, strategy, account: Account) -> None:
        self.spec = spec
        self.strategy = strategy
        self.account = account
        self.orders: List[Order] = []
        self.fills: List[Fill] = []
        self.rejected: List[Fill] = []
        self.marks: List[dict] = []
        self.forced_liquidation: Optional[str] = None
        self.errors: List[dict] = []
        self.closed = False
        self.sessions_traded = 0
        self.open_orders: List[Order] = []   # resting DAY book (minute-bar lane)
        self.final_marks: Dict[str, float] = {}
        self.final_positions: Dict[str, int] = {}


class CompetitionEngine:
    """Runs one competition season over one market-data replay."""

    def __init__(self, cfg: config.CompetitionConfig, md, seed: Optional[int] = None,
                 roster: Optional[Sequence[Strategy]] = None,
                 writer: Optional[memory.RunWriter] = None,
                 full_memory: bool = True, verbose: bool = False,
                 venue_overrides: Optional[Dict[str, object]] = None) -> None:
        self.cfg = cfg
        self.md = md
        self.seed = cfg.seed if seed is None else seed
        self.writer = writer
        self.full_memory = full_memory
        self.verbose = verbose
        #: Model-form switches for sim/sensitivity.py.  Deliberately not part of
        #: the configuration: a competition run must be described by exactly the
        #: config whose fingerprint the replay was generated from.
        self.venue_overrides: Dict[str, object] = dict(venue_overrides or {})
        self.exec_engine = ExecutionEngine(cfg, **self.venue_overrides)
        self.roster = list(roster) if roster is not None else build_roster()
        self.t0 = md.first_competition_index
        self.t1 = len(md.dates)
        self.participants: List[ParticipantRuntime] = []
        self.irregularities: List[dict] = []
        self.closes_by_date: Dict[str, Dict[str, float]] = {}
        self._venues: Dict[str, VenueDay] = {}
        self._pv: Dict[Tuple[str, str], ParticipantVenue] = {}
        self._canonical: Dict[str, ParticipantVenue] = {}
        self.started_utc = None
        self.finished_utc = None
        self.elapsed_seconds = 0.0
        self._rejection_count: Dict[Tuple[str, str], int] = defaultdict(int)
        self._clip_count: Dict[Tuple[str, str], int] = defaultdict(int)
        self._rejection_example: Dict[Tuple[str, str], str] = {}
        self._clip_example: Dict[Tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup(self) -> None:
        for strategy in self.roster:
            acct = Account(participant=strategy.username,
                           starting_cash=self.cfg.starting_cash,
                           margin=self.cfg.margin)
            self.participants.append(ParticipantRuntime(strategy.spec, strategy, acct))
        # Close matrix for missed-trade valuation in implementation shortfall.
        for t in range(self.t0, self.t1):
            d = self.md.dates[t]
            self.closes_by_date[d] = {s: self.md.bar(s, t).close for s in self.md.symbols}
        self._dividend_map = self._build_dividend_map()

    def _build_dividend_map(self) -> Dict[str, List[Tuple[str, float]]]:
        """ex-date -> [(symbol, amount)], snapped onto actual sessions.

        Declared ex-dates that fall on a market holiday are paid on the next
        session instead of being silently dropped.
        """
        sessions = set(self.md.dates)
        ordered = list(self.md.dates)
        out: Dict[str, List[Tuple[str, float]]] = {}
        for inst in self.md.instruments.values():
            for div in inst.dividends:
                ex = div["ex_date"]
                if not (self.md.dates[self.t0] <= ex <= self.md.dates[self.t1 - 1]):
                    continue
                if ex not in sessions:
                    ex = next((d for d in ordered if d >= div["ex_date"]), None)
                    if ex is None:
                        continue
                    self.irregularities.append({
                        "id": "IR-10", "severity": "low", "symbol": inst.symbol,
                        "message": f"declared ex-date {div['ex_date']} is not a trading "
                                   f"session; dividend paid on the next session {ex}",
                    })
                out.setdefault(ex, []).append((inst.symbol, float(div["amount"])))
        return out

    # ------------------------------------------------------------------
    # Venue access
    # ------------------------------------------------------------------
    def _venue(self, symbol: str, t: int) -> VenueDay:
        key = f"{symbol}@{self.md.dates[t]}"
        v = self._venues.get(key)
        if v is None:
            v = self.exec_engine.make_venue_day(self.md, symbol, t, self.seed)
            self._venues[key] = v
        return v

    def _participant_venue(self, username: str, symbol: str, t: int) -> ParticipantVenue:
        key = (username, symbol)
        pv = self._pv.get(key)
        if pv is None:
            pv = self.exec_engine.make_participant_venue(self._venue(symbol, t),
                                                         username, self.seed)
            self._pv[key] = pv
        return pv

    def _canonical_venue(self, symbol: str, t: int) -> ParticipantVenue:
        """Zero-inventory venue used only to snapshot the opening quote."""
        pv = self._canonical.get(symbol)
        if pv is None:
            pv = self.exec_engine.make_participant_venue(self._venue(symbol, t),
                                                         "__opening_snapshot__", self.seed)
            self._canonical[symbol] = pv
        return pv

    def _opening_quotes(self, t: int) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for s in self.md.symbols:
            book, mid = self.exec_engine.quote_at(self._canonical_venue(s, t), 0)
            snap = book.snapshot()
            snap["sigma_daily"] = round(self.md.realised_sigma_daily(s, t), 6)
            snap["vix_factor"] = round(self.md.vix_regime_factor(t), 4)
            snap["spread_ticks"] = round(snap["spread"] / config.minimum_tick(mid))
            out[s] = snap
        return out

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _submit(self, p: ParticipantRuntime, orders: Sequence[Order], t: int,
                marks: Dict[str, float], start_interval: int = 0) -> List[Fill]:
        """Pre-trade risk check, then execution against the venue replica.

        Orders flagged at_close are held back and worked from the session's
        final interval, so an opening-bell order and a market-on-close order can
        coexist in one decision.  The split happens here rather than inside the
        execution engine because the venue decides *when* an order meets the
        book, and that is a property of the session, not of the order ticket.
        """
        if not start_interval:
            late = [o for o in orders if getattr(o, "at_close", False)]
            early = [o for o in orders if not getattr(o, "at_close", False)]
            if late:
                fills = self._submit(p, early, t, marks, start_interval)
                K = self._venue(late[0].symbol, t).path.K
                if start_interval < K:
                    return fills + self._submit(p, late, t, marks,
                                                start_interval=K)
        fills: List[Fill] = []
        for order in orders:
            order = self._risk_check(p, order, marks)
            if order is None:
                continue
            pv = self._participant_venue(p.spec.username, order.symbol, t)
            try:
                fill = self.exec_engine.execute(pv, order, self.seed,
                                                start_interval=start_interval)
            except Exception as exc:                      # pragma: no cover
                self._flag("IR-11", "high", order.symbol,
                           f"execution error for {p.spec.username}: "
                           f"{type(exc).__name__}: {exc}")
                continue
            p.orders.append(order)
            p.fills.append(fill)
            if fill.filled_qty > 0:
                p.account.apply_fill(fill, marks)
            else:
                p.rejected.append(fill)
            fills.append(fill)
            if self.writer and self.full_memory:
                self.writer.append("orders", _order_row(order, self.md.dates[t]))
                self.writer.append(
                    "fills" if fill.filled_qty > 0 else "rejections", fill.to_row())
        return fills

    def _risk_check(self, p: ParticipantRuntime, order: Order,
                    marks: Dict[str, float]) -> Optional[Order]:
        """Broker-side pre-trade control.

        Mirrors what a real paper-trading platform does before an order reaches
        the market: reject or resize anything the account cannot support.  Two
        outcomes are possible:
          * the order is CLIPPED to the largest size that still satisfies the
            competition's leverage and buying-power limits (counted, and the
            clip is recorded on the order's reason so it is visible in memory);
          * the order is REJECTED outright if not even one share is permitted.
        This is defence in depth: :meth:`Context.orders_to_targets` already
        screens, but a strategy may construct an :class:`Order` directly.
        """
        acct = p.account
        price = marks.get(order.symbol, order.limit_price or order.stop_price or 0.0)
        if price <= 0:
            self._rejection_count[(p.spec.username, "no reference price")] += 1
            return None
        ok, why = acct.can_increase(order.symbol, order.side, order.quantity,
                                    price, marks)
        if ok:
            return order
        # Aggregate on a normalised reason: the raw text embeds the measured
        # leverage, which would otherwise make every event its own bucket.
        key = _normalise_reason(why)
        max_qty = acct.max_permissible_quantity(order.symbol, order.side, price, marks)
        if max_qty <= 0:
            self._rejection_count[(p.spec.username, key)] += 1
            self._rejection_example[(p.spec.username, key)] = why
            return None
        if max_qty >= order.quantity:
            self._rejection_count[(p.spec.username, key)] += 1
            self._rejection_example[(p.spec.username, key)] = why
            return None
        self._clip_count[(p.spec.username, key)] += 1
        self._clip_example[(p.spec.username, key)] = why
        return Order(symbol=order.symbol, side=order.side, quantity=max_qty,
                     order_type=order.order_type, limit_price=order.limit_price,
                     stop_price=order.stop_price, tif=order.tif,
                     participant=order.participant,
                     reason=(order.reason + f" [size clipped {order.quantity} -> "
                             f"{max_qty} by pre-trade margin: {why}]")[:400])

    def _flag(self, ir_id: str, severity: str, symbol: Optional[str], message: str) -> None:
        self.irregularities.append({"id": ir_id, "severity": severity,
                                    "symbol": symbol, "message": message})

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> dict:
        started = time.time()
        self.started_utc = memory._utcnow()
        self._setup()
        last = self.t1 - 1
        for t in range(self.t0, self.t1):
            date = self.md.dates[t]
            self._venues.clear()
            self._pv.clear()
            self._canonical.clear()
            quotes = self._opening_quotes(t)
            closes = self.closes_by_date[date]
            if self.writer and self.full_memory:
                for s, q in quotes.items():
                    self.writer.append("quotes", {"date": date, "symbol": s, **q})
            for p in self.participants:
                self._trade_session(p, t, date, quotes, closes, is_final=(t == last))
            for p in self.participants:
                self._close_session(p, t, date, closes)
            if self.verbose and ((t - self.t0) % 25 == 0 or t == last):
                lead = max(self.participants,
                           key=lambda p: p.account.equity(closes))
                print(f"  [{t - self.t0 + 1:3d}/{self.t1 - self.t0}] {date}  "
                      f"leader {lead.spec.username} "
                      f"{lead.account.equity(closes):,.0f}")
        self.elapsed_seconds = round(time.time() - started, 2)
        self.finished_utc = memory._utcnow()
        self._report_risk_events()
        return self.build_record()

    def _report_risk_events(self) -> None:
        """Aggregate pre-trade rejections/clips into irregularity records."""
        for (user, key), n in sorted(self._rejection_count.items(),
                                     key=lambda kv: -kv[1]):
            self._flag("IR-14", "medium" if n > 50 else "low", None,
                       f"{user}: {n} order(s) REJECTED by the broker-side pre-trade "
                       f"check [{key}] - e.g. "
                       f"{self._rejection_example.get((user, key), '')}")
        for (user, key), n in sorted(self._clip_count.items(), key=lambda kv: -kv[1]):
            self._flag("IR-15", "medium" if n > 50 else "low", None,
                       f"{user}: {n} order(s) SIZE-CLIPPED by the broker-side "
                       f"pre-trade check [{key}] - e.g. "
                       f"{self._clip_example.get((user, key), '')}")

    # -- one participant, one session ----------------------------------
    def _trade_session(self, p: ParticipantRuntime, t: int, date: str,
                       quotes: Dict[str, dict], closes: Dict[str, float],
                       is_final: bool) -> None:
        acct = p.account
        if p.closed:
            return
        acct.start_day(date)
        # Snapshot BEFORE any of today's fills: this is the book that carries
        # the record-date entitlement for a dividend going ex today.
        entitled = acct.quantities_at_open()
        marks = {s: quotes[s]["mid"] for s in quotes if s in closes}
        marks.update({s: closes[s] for s in closes if s not in marks})

        # A maintenance breach detected on the previous close is liquidated at
        # today's open, before the strategy is allowed to trade.
        if p.forced_liquidation:
            orders = [Order(symbol=s, side=SELL if acct.quantity(s) > 0 else BUY,
                            quantity=abs(acct.quantity(s)), order_type=MARKET,
                            participant=p.spec.username,
                            reason=f"forced liquidation: {p.forced_liquidation}")
                      for s in self.md.symbols if acct.quantity(s)]
            self._submit(p, orders, t, marks)
            if self.writer and self.full_memory:
                self.writer.append("margin", {
                    "date": date, "participant": p.spec.username,
                    "event": "forced_liquidation_at_open",
                    "trigger": p.forced_liquidation,
                    "orders": len(orders),
                    "equity_after": round(acct.equity(marks), 2),
                })
            p.forced_liquidation = None

        if not p.closed:
            ctx = Context(self.md, t, acct, self.cfg, quotes, self.seed)
            try:
                orders = p.strategy.on_day(ctx) or []
            except Exception as exc:
                err = {"date": date, "participant": p.spec.username,
                       "error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc(limit=3)}
                p.errors.append(err)
                self._flag("IR-12", "high", None,
                           f"{p.spec.username} raised {type(exc).__name__} on {date}: {exc}")
                orders = []
            marks = ctx.marks()
            self._submit(p, orders, t, marks)
            if orders:
                p.sessions_traded += 1

            # Minute-bar lane: extra decision points inside the session,
            # evenly spaced over the venue replica's intervals.  Each point
            # first works the resting DAY book carried in from earlier points
            # (an unfilled limit is re-priced against the book from the point
            # it now arrives at, never against intervals it has already lived
            # through), then asks the strategy again.  Whatever is still
            # unfilled after the last point is submitted once at the closing
            # cross so the audit trail carries its expiry; the book then
            # clears at the bell (DAY semantics).
            points = self._intraday_decision_points(t)
            if points and hasattr(p.strategy, "on_intraday"):
                for k in points:
                    if p.open_orders:
                        resting, p.open_orders = p.open_orders, []
                        fills = self._submit(p, resting, t, marks,
                                             start_interval=k)
                        for o, f in zip(resting, fills):
                            if (f.filled_qty == 0
                                    and o.order_type in (LIMIT, LOC)
                                    and o.tif == "DAY"):
                                p.open_orders.append(o)
                    ctx = Context(self.md, t, acct, self.cfg, quotes, self.seed)
                    ctx.interval = k
                    try:
                        iorders = p.strategy.on_intraday(ctx, k) or []
                    except Exception as exc:
                        p.errors.append({
                            "date": date, "participant": p.spec.username,
                            "interval": k,
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc(limit=3)})
                        self._flag("IR-12", "high", None,
                                   f"{p.spec.username} raised "
                                   f"{type(exc).__name__} intraday on "
                                   f"{date}@{k}: {exc}")
                        iorders = []
                    marks = ctx.marks()
                    ifills = self._submit(p, iorders, t, marks,
                                          start_interval=k)
                    for o, f in zip(iorders, ifills):
                        if (f.filled_qty == 0
                                and o.order_type in (LIMIT, LOC)
                                and o.tif == "DAY"):
                            p.open_orders.append(o)
                if p.open_orders:
                    resting, p.open_orders = p.open_orders, []
                    K = self._venue(resting[0].symbol, t).path.K
                    self._submit(p, resting, t, marks, start_interval=K)

        # End-of-competition liquidation at the last intraday interval.
        if is_final and self.cfg.force_liquidate_at_end:
            open_syms = [s for s in self.md.symbols if acct.quantity(s)]
            if open_syms:
                venue = self._venue(open_syms[0], t)
                orders = [Order(symbol=s, side=SELL if acct.quantity(s) > 0 else BUY,
                                quantity=abs(acct.quantity(s)), order_type=MARKET,
                                participant=p.spec.username,
                                reason="end-of-competition forced liquidation")
                          for s in open_syms]
                self._submit(p, orders, t, marks, start_interval=venue.path.K)
                if self.writer and self.full_memory:
                    self.writer.append("margin", {
                        "date": date, "participant": p.spec.username,
                        "event": "end_of_competition_liquidation",
                        "positions_closed": len(open_syms),
                        "equity_after": round(acct.equity(marks), 2)})

        # Carry: borrow fees on shorts, cash dividends on ex-dates.
        # Dividend entitlement is the position carried INTO the ex-date (the
        # record-date holding), not the position left after today's trades, so
        # the quantities snapshotted at the open decide who is paid.  A short
        # across the ex-date owes the lender a manufactured dividend.
        carry = acct.accrue_carry(date, closes)
        for symbol, amount in self._dividend_map.get(date, []):
            settle = acct.settle_dividend(symbol, amount,
                                          entitled_qty=entitled.get(symbol, 0))
            paid, in_lieu = settle["received"], settle["in_lieu"]
            if paid and self.writer and self.full_memory:
                self.writer.append("carry", {
                    "date": date, "participant": p.spec.username, "symbol": symbol,
                    "kind": "dividend", "amount": round(paid, 2),
                    "per_share": amount,
                    "entitled_qty": max(0, entitled.get(symbol, 0))})
            if in_lieu and self.writer and self.full_memory:
                self.writer.append("carry", {
                    "date": date, "participant": p.spec.username, "symbol": symbol,
                    "kind": "dividend_in_lieu", "amount": round(-in_lieu, 2),
                    "per_share": amount,
                    "entitled_qty": min(0, entitled.get(symbol, 0))})
        if carry["borrow_fee"] and self.writer and self.full_memory:
            self.writer.append("carry", {
                "date": date, "participant": p.spec.username, "symbol": None,
                "kind": "borrow_fee", "amount": round(-carry["borrow_fee"], 2)})

    # -- mark, snapshot, margin test -----------------------------------
    def _intraday_decision_points(self, t: int) -> List[int]:
        """Evenly spaced intraday intervals for the minute-bar lane.

        With ``cfg.intraday_decisions = D`` and a K-interval path the points
        are ``round(i*K/(D+1))`` for i = 1..D: never interval 0 (the on_day
        decision already owns the open) and never exactly K unless D forces
        it, so a mid-session decision cannot silently become an at-the-open
        or at-the-close one.  Returns [] when the lane is off.
        """
        d = max(0, int(getattr(self.cfg, "intraday_decisions", 0) or 0))
        if d <= 0:
            return []
        K = self._venue(self.md.symbols[0], t).path.K
        return [max(1, min(K, round(i * K / (d + 1)))) for i in range(1, d + 1)]

    def _close_session(self, p: ParticipantRuntime, t: int, date: str,
                       closes: Dict[str, float]) -> None:
        acct = p.account
        row = acct.mark(date, closes)
        p.marks.append(row)
        p.final_marks = dict(closes)
        p.final_positions = {s: acct.quantity(s) for s in self.md.symbols
                             if acct.quantity(s)}
        if self.writer and self.full_memory:
            self.writer.append("equity", {"date": date, "participant": p.spec.username,
                                          **row})
            for s, q in p.final_positions.items():
                pos = acct.positions[s]
                self.writer.append("positions", {
                    "date": date, "participant": p.spec.username, "symbol": s,
                    "quantity": q, "avg_cost": round(pos.avg_cost, 6),
                    "mark": closes[s],
                    "market_value": round(q * closes[s], 2),
                    "unrealized": round(pos.unrealized(closes[s]), 2),
                    "realized_gross": round(pos.realized_gross, 2),
                    "shares_bought": pos.shares_bought,
                    "shares_sold": pos.shares_sold,
                    "dividends": round(pos.dividends, 4),
                    "borrow_fees": round(pos.borrow_fees, 4),
                })
        breach = acct.maintenance_breach(closes)
        if breach:
            p.forced_liquidation = (
                f"maintenance margin breach on {date} "
                f"(equity {row['equity']:,.2f}, worst position {breach})")
            if self.writer and self.full_memory:
                self.writer.append("margin", {
                    "date": date, "participant": p.spec.username,
                    "event": "maintenance_breach", "worst_symbol": breach,
                    "equity": row["equity"], "leverage": row["leverage"]})
        if row["equity"] <= 0 and not p.closed:
            p.closed = True
            self._flag("IR-13", "high", None,
                       f"{p.spec.username} equity fell to {row['equity']:,.2f} on {date}; "
                       f"account closed (total loss)")
            if self.writer and self.full_memory:
                self.writer.append("margin", {
                    "date": date, "participant": p.spec.username,
                    "event": "account_wiped_out", "equity": row["equity"]})

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def build_record(self) -> dict:
        market = analytics.market_report(self.md, self.t0, self.t1)
        factors = analytics.factor_report(self.md, self.t0, self.t1)
        analytics.set_factor_cache(factors)
        reports: List[dict] = []
        for p in self.participants:
            summary = p.account.summary(p.final_marks)
            record = {
                "username": p.spec.username,
                "strategy_id": type(p.strategy).__name__,
                "archetype": p.spec.archetype,
                "spec": {**asdict(p.spec),
                         "factor_exposure": p.spec.factor_exposure,
                         "strategy_class": type(p.strategy).__name__},
                "account_summary": summary,
                "equity_curve": list(p.account.equity_curve),
                "fills": p.fills,
                "orders": p.orders,
                "marks": p.marks,
                "final_marks": p.final_marks,
                "final_positions": p.final_positions,
                "closes_by_date": self.closes_by_date,
                "errors": p.errors,
                "sessions_traded": p.sessions_traded,
            }
            rep = analytics.performance_report(record, self.md, self.t0, self.t1,
                                               self.cfg.starting_cash, market, factors)
            rep["errors"] = p.errors
            rep["account_closed_early"] = p.closed
            rep["sessions_with_orders"] = p.sessions_traded
            reports.append(rep)
            if self.writer:
                self.writer.write_report(p.spec.username, rep)
        board = analytics.leaderboard(reports, self.cfg.rank_metric)
        return {
            "seed": self.seed,
            "config": self.cfg.as_dict(),
            "config_fingerprint": self.cfg.fingerprint(),
            "market": market,
            "factors": factors,
            "reports": reports,
            "leaderboard": board,
            "irregularities": self.irregularities,
            "provenance": self.md.diagnostics,
            "timing": {"started_utc": self.started_utc,
                       "finished_utc": self.finished_utc,
                       "elapsed_seconds": self.elapsed_seconds},
            "winner": board[0]["username"] if board else None,
        }


_NUMERIC = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _normalise_reason(text: str) -> str:
    """Bucket key for a risk-check message: numbers replaced by '#'."""
    return _NUMERIC.sub("#", text)[:120]


def _order_row(order: Order, date: str) -> dict:
    return {
        "date": date,
        "participant": order.participant,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "tif": order.tif,
        "reason": order.reason,
    }


# ==========================================================================
# Multi-scenario driver
# ==========================================================================

def run_scenarios(cfg: config.CompetitionConfig, seeds: Sequence[int],
                  md_by_seed: Dict[int, object], root: str = memory.DEFAULT_ROOT,
                  run_ids: Optional[Sequence[str]] = None,
                  full_memory_for: Sequence[int] = (0,),
                  verbose: bool = False) -> List[dict]:
    """Run the same competition over several market-data realisations.

    Why: with a single 251-session path, one participant's rank is a single
    draw from the idiosyncratic distribution.  Several seeds on the *same* real
    market factor path separate a repeatable edge from a lucky idiosyncratic
    draw - see ``analytics.robustness_panel``.
    """
    out: List[dict] = []
    for i, seed in enumerate(seeds):
        md = md_by_seed[seed]
        run_id = run_ids[i] if run_ids else f"season1-seed{seed}"
        writer = memory.RunWriter(run_id, root=root)
        engine = CompetitionEngine(cfg, md, seed=seed, writer=writer,
                                   full_memory=(i in full_memory_for),
                                   verbose=verbose)
        record = engine.run()
        record["run_id"] = run_id
        record["full_memory"] = (i in full_memory_for)
        record["scenario_index"] = i
        record["participant_count"] = len(engine.participants)
        record["session_count"] = engine.t1 - engine.t0
        writer.write_json("market_report.json", record["market"])
        writer.write_json("factor_report.json", record["factors"])
        writer.write_json("leaderboard.json", {
            "leaderboard": record["leaderboard"], "rank_metric": cfg.rank_metric})
        writer.write_json("irregularities.json", record["irregularities"])
        # asdict() drops StrategySpec.factor_exposure because it is a property,
        # not a field - and the declared factor loading is an input the narrative
        # analysis is judged against, so memory must carry it.
        writer.write_json("participants.json",
                          [{**asdict(st.spec),
                            "factor_exposure": st.spec.factor_exposure}
                           for st in engine.roster])
        if i in full_memory_for:
            writer.write_json("market_data.json", _market_data_dump(md))
        manifest = writer.finalise({
            "run_id": run_id,
            "seed": seed,
            "scenario_index": i,
            "full_event_memory": bool(i in full_memory_for),
            "participant_count": record["participant_count"],
            "session_count": record["session_count"],
            "competition": {"name": cfg.name, "season": cfg.season,
                            "start": cfg.start, "end": cfg.end,
                            "starting_cash": cfg.starting_cash,
                            "rank_metric": cfg.rank_metric},
            "config_fingerprint": cfg.fingerprint(),
            "winner": record["winner"],
            "top3": [{"rank": r["rank"], "username": r["username"],
                      "total_return_pct": r["total_return_pct"]}
                     for r in record["leaderboard"][:3]],
            "market": record["market"],
            "factors": record["factors"],
            "timing": record["timing"],
            "irregularity_count": len(record["irregularities"]),
        })
        out.append(record)
    return out


def _market_data_dump(md) -> dict:
    """The exact series traded on, so any future analysis can replay it."""
    return {
        "source": md.source,
        "provenance": md.diagnostics,
        "dates": md.dates,
        "first_competition_index": md.first_competition_index,
        "warmup_dates": md.warmup_dates,
        "spx": [round(v, 4) for v in md.spx],
        "vix": [round(v, 4) for v in md.vix],
        "bars": {s: [[b.date, b.open, b.high, b.low, b.close, b.volume]
                     for b in md.bars[s]] for s in md.symbols},
        "instruments": {s: {"name": i.name, "sector": i.sector,
                            "asset_type": i.asset_type, "beta": i.beta,
                            "sigma_idio_annual": i.sigma_idio_annual,
                            "alpha_annual": i.alpha_annual,
                            "adv_shares": i.adv_shares,
                            "liquidity_tier": i.liquidity_tier,
                            "dividends": i.dividends,
                            "provenance": i.provenance}
                        for s, i in md.instruments.items()},
    }
