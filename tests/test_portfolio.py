"""Portfolio and margin accounting.

The accounting identities here are the ones the whole competition rests on:
cash plus marked positions equals equity; realised P&L plus unrealised P&L plus
dividends minus costs equals the change in equity; and the broker-side risk
check must clip rather than trap.
"""

from __future__ import annotations

import unittest

from fixtures import cfg

from sim import config
from sim.microstructure import BUY, SELL, Fill, Order
from sim.portfolio import Account, Position


def mk_fill(symbol: str, side: str, qty: int, price: float, date: str = "2026-01-05",
            commission: float = 0.0, exchange_fee: float = 0.0,
            regulatory_fee: float = 0.0, rebate: float = 0.0,
            spread_cost: float = 0.0, depth_cost: float = 0.0,
            impact_cost: float = 0.0, participant: str = "@tester") -> Fill:
    order = Order(symbol=symbol, side=side, quantity=qty, participant=participant,
                  reason="test")
    return Fill(order=order, date=date, filled_qty=qty, requested_qty=qty,
                avg_price=price, decision_price=price, spread_cost=spread_cost,
                depth_cost=depth_cost, impact_cost=impact_cost,
                commission=commission, exchange_fee=exchange_fee,
                regulatory_fee=regulatory_fee, rebate=rebate)


def acct(cash: float = 100_000.0, **margin_overrides) -> Account:
    margin = config.MarginConfig(**margin_overrides) if margin_overrides else cfg().margin
    return Account("@tester", cash, margin)


class TestAccountingIdentities(unittest.TestCase):
    def test_equity_is_cash_plus_marked_positions(self):
        a = acct()
        marks = {"SPY": 700.0, "NVDA": 185.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), marks)
        a.apply_fill(mk_fill("NVDA", BUY, 200, 185.0), marks)
        expected = a.cash + 100 * 700.0 + 200 * 185.0
        self.assertAlmostEqual(a.equity(marks), expected, places=6)
        self.assertAlmostEqual(a.equity(marks), 100_000.0, places=6,
                               msg="buying at the mark must not change equity")

    def test_equity_moves_with_the_mark_not_with_the_trade(self):
        a = acct()
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), {"SPY": 700.0})
        self.assertAlmostEqual(a.equity({"SPY": 710.0}), 101_000.0, places=6)
        self.assertAlmostEqual(a.equity({"SPY": 690.0}), 99_000.0, places=6)

    def test_costs_reduce_equity_and_are_ledgered(self):
        a = acct()
        marks = {"SPY": 700.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0, commission=1.0,
                             exchange_fee=0.30, spread_cost=5.0, impact_cost=2.0),
                     marks)
        self.assertAlmostEqual(a.cash, 100_000.0 - 70_000.0 - 1.30, places=6)
        self.assertAlmostEqual(a.equity(marks), 100_000.0 - 1.30, places=6)
        self.assertAlmostEqual(a.costs.commission, 1.0, places=9)
        self.assertAlmostEqual(a.costs.exchange_fee, 0.30, places=9)
        self.assertAlmostEqual(a.costs.spread_cost, 5.0, places=9)
        self.assertAlmostEqual(a.costs.impact_cost, 2.0, places=9)
        self.assertAlmostEqual(a.costs.total, 1.30 + 5.0 + 2.0, places=6)

    def test_rebate_is_credited_not_charged(self):
        a = acct()
        marks = {"SPY": 700.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0, rebate=2.0), marks)
        self.assertAlmostEqual(a.equity(marks), 100_002.0, places=6)
        self.assertAlmostEqual(a.costs.rebate, 2.0, places=9)

    def test_average_cost_basis_on_adds_and_realised_pnl_on_exits(self):
        a = acct()
        marks = {"SPY": 100.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0), marks)
        a.apply_fill(mk_fill("SPY", BUY, 100, 110.0), marks)
        self.assertEqual(a.quantity("SPY"), 200)
        self.assertAlmostEqual(a.position("SPY").avg_cost, 105.0, places=9)
        a.apply_fill(mk_fill("SPY", SELL, 100, 120.0), marks)
        self.assertEqual(a.quantity("SPY"), 100)
        self.assertAlmostEqual(a.realized_gross, (120.0 - 105.0) * 100, places=6)
        self.assertAlmostEqual(a.position("SPY").realized_gross, 1_500.0, places=6)

    def test_flipping_through_zero_keeps_the_residual_short_at_the_fill_price(self):
        """Regression: the residual leg used to vanish from the attribution."""
        a = acct()
        marks = {"NVDA": 185.0}
        a.apply_fill(mk_fill("NVDA", BUY, 100, 180.0), marks)
        a.apply_fill(mk_fill("NVDA", SELL, 300, 190.0), marks)
        self.assertEqual(a.quantity("NVDA"), -200)
        self.assertAlmostEqual(a.position("NVDA").avg_cost, 190.0, places=9)
        self.assertAlmostEqual(a.realized_gross, (190.0 - 180.0) * 100, places=6)

    def test_short_sale_credits_proceeds_and_flattens_on_cover(self):
        a = acct()
        marks = {"RIVN": 12.0}
        a.apply_fill(mk_fill("RIVN", SELL, 1_000, 12.0), marks)
        self.assertAlmostEqual(a.cash, 112_000.0, places=6)
        self.assertAlmostEqual(a.equity(marks), 100_000.0, places=6)
        a.apply_fill(mk_fill("RIVN", BUY, 1_000, 10.0), marks)
        self.assertAlmostEqual(a.realized_gross, 2_000.0, places=6)
        self.assertAlmostEqual(a.equity(marks), 102_000.0, places=6)

    def test_exposure_leverage_and_netting(self):
        a = acct()
        marks = {"SPY": 700.0, "RIVN": 12.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), marks)
        a.apply_fill(mk_fill("RIVN", SELL, 1_000, 12.0), marks)
        self.assertAlmostEqual(a.gross_exposure(marks), 70_000.0 + 12_000.0, places=6)
        self.assertAlmostEqual(a.net_exposure(marks), 70_000.0 - 12_000.0, places=6)
        self.assertAlmostEqual(a.leverage(marks),
                               a.gross_exposure(marks) / a.equity(marks), places=9)

    def test_zero_and_rejected_fills_do_not_touch_the_book(self):
        a = acct()
        # A rejected fill keeps a valid order (quantity >= 1) but fills nothing,
        # which is how the execution engine represents a rejection.
        rejected = mk_fill("SPY", BUY, 1, 700.0)
        rejected.filled_qty = 0
        rejected.status = "rejected"
        rejected.reject_reason = "gross leverage cap would be breached"
        a.apply_fill(rejected, {"SPY": 700.0})
        self.assertEqual(a.quantity("SPY"), 0)
        self.assertAlmostEqual(a.cash, 100_000.0, places=6)
        self.assertEqual(len(a.fills), 0)
        self.assertEqual(len(a.rejected), 1)

    def test_position_row_is_serialisable_and_consistent(self):
        a = acct()
        marks = {"SPY": 705.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), marks)
        pos = a.position("SPY")
        row = pos.to_row(705.0)
        self.assertAlmostEqual(row["market_value"], 70_500.0, places=6)
        self.assertAlmostEqual(row["unrealized_pnl"], 500.0, places=6)
        self.assertAlmostEqual(pos.market_value(705.0), 70_500.0, places=6)
        self.assertAlmostEqual(pos.unrealized(705.0), 500.0, places=6)
        import json
        json.dumps(row)


class TestMarginScreen(unittest.TestCase):
    def test_gross_cap_blocks_an_increasing_order(self):
        a = acct()
        marks = {"SPY": 100.0}
        ok, why = a.can_increase("SPY", BUY, 3_000, 100.0, marks)   # 3.0x gross
        self.assertFalse(ok)
        self.assertIn("gross leverage cap", why)
        self.assertIn("3.00x", why)

    def test_orders_inside_the_cap_pass(self):
        a = acct()
        marks = {"SPY": 100.0}
        ok, why = a.can_increase("SPY", BUY, 2_000, 100.0, marks)   # exactly 2.0x
        self.assertTrue(ok, why)
        self.assertEqual(why, "")

    def test_risk_reducing_orders_are_never_blocked(self):
        """Regression: a book that drifts above the cap through price movement
        used to be untradeable, which locked in 906 rejections."""
        a = acct()
        a.apply_fill(mk_fill("SPY", BUY, 2_000, 100.0), {"SPY": 100.0})
        self.assertAlmostEqual(a.leverage({"SPY": 100.0}), 2.0, places=6)
        # The mark collapses, so gross is now 6x equity: the cap is breached by
        # the market, not by anything the participant just did.
        marks = {"SPY": 60.0}
        self.assertGreater(a.leverage(marks), a.margin.max_gross_leverage)
        self.assertFalse(a.can_increase("SPY", BUY, 1, 60.0, marks)[0])
        ok, why = a.can_increase("SPY", SELL, 1_000, 60.0, marks)
        self.assertTrue(ok, f"a risk-reducing order must always pass: {why}")

    def test_max_permissible_quantity_is_exact_to_one_share(self):
        a = acct()
        marks = {"SPY": 100.0}
        qty = a.max_permissible_quantity("SPY", BUY, 100.0, marks)
        self.assertGreater(qty, 0)
        self.assertTrue(a.can_increase("SPY", BUY, qty, 100.0, marks)[0])
        self.assertFalse(a.can_increase("SPY", BUY, qty + 1, 100.0, marks)[0])
        self.assertLessEqual(qty * 100.0,
                             a.equity(marks) * a.margin.max_gross_leverage + 1e-6)

    def test_max_permissible_is_zero_when_not_even_one_share_fits(self):
        a = acct(cash=100.0)
        marks = {"SPY": 100.0}
        a.apply_fill(mk_fill("SPY", BUY, 2, 100.0), marks)   # already at the cap
        self.assertEqual(a.max_permissible_quantity("SPY", BUY, 100.0, marks), 0)
        self.assertGreater(a.max_permissible_quantity("SPY", SELL, 100.0, marks), 0)

    def test_exhausted_equity_refuses_everything(self):
        a = acct(cash=1_000.0)
        ok, why = a.can_increase("SPY", BUY, 100, 100.0, {"SPY": 100.0})
        self.assertFalse(ok)
        a.cash = -5_000.0
        ok, why = a.can_increase("SPY", BUY, 1, 100.0, {"SPY": 100.0})
        self.assertFalse(ok)
        self.assertIn("equity exhausted", why)

    def test_shorting_can_be_disabled_by_rule_and_by_locate(self):
        a = acct(shorting_allowed=False)
        ok, why = a.can_increase("SPY", SELL, 100, 100.0, {"SPY": 100.0})
        self.assertFalse(ok)
        self.assertIn("shorting disabled", why)

        from sim import microstructure
        original = microstructure.BORROW_TERMS.get("SPY")
        microstructure.BORROW_TERMS["SPY"] = {"shortable": False, "fee_annual": 0.0}
        try:
            b = acct()
            ok, why = b.can_increase("SPY", SELL, 100, 100.0, {"SPY": 100.0})
            self.assertFalse(ok)
            self.assertIn("locate", why)
        finally:
            if original is None:
                microstructure.BORROW_TERMS.pop("SPY", None)
            else:
                microstructure.BORROW_TERMS["SPY"] = original

    def test_buying_power_reflects_the_leverage_allowance(self):
        a = acct()
        marks = {"SPY": 100.0}
        bp = a.buying_power(marks)
        self.assertGreater(bp, a.cash - 1e-9)
        self.assertLessEqual(bp, a.cash * a.margin.max_gross_leverage + 1e-6)


class TestMaintenanceAndCarry(unittest.TestCase):
    def test_no_breach_on_a_fresh_account(self):
        a = acct()
        self.assertIsNone(a.maintenance_breach({"SPY": 100.0}))

    def test_breach_is_reported_with_the_worst_symbol(self):
        a = acct()
        a.apply_fill(mk_fill("SPY", BUY, 1_000, 100.0), {"SPY": 100.0})
        a.apply_fill(mk_fill("NVDA", BUY, 200, 100.0), {"NVDA": 100.0})
        crashed = {"SPY": 20.0, "NVDA": 20.0}
        worst = a.maintenance_breach(crashed)
        self.assertEqual(worst, "SPY")
        self.assertTrue(a.margin_calls)
        self.assertIn("maintenance requirement", a.margin_calls[-1])

    def test_breach_on_zero_equity_returns_the_largest_position(self):
        a = acct(cash=10_000.0)
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0), {"SPY": 100.0})
        a.cash = 0.0
        worst = a.maintenance_breach({"SPY": 0.0})
        self.assertIn(worst, (None, "SPY"))

    def test_borrow_fee_accrues_on_shorts_at_the_declared_annual_rate(self):
        from sim.microstructure import borrow_terms
        a = acct()
        marks = {"CVNA": 300.0}
        a.apply_fill(mk_fill("CVNA", SELL, 100, 300.0), marks)
        rate = float(borrow_terms("CVNA")["fee_annual"])
        out = a.accrue_carry("2026-01-05", marks, 1.0 / 252.0)
        expected = 100 * 300.0 * rate / 252.0
        self.assertAlmostEqual(out["borrow_fee"], expected, places=6)
        self.assertAlmostEqual(a.borrow_paid, expected, places=6)
        self.assertAlmostEqual(a.costs.borrow_fee, expected, places=6)
        self.assertAlmostEqual(a.cash, 130_000.0 - expected, places=6)

    def test_no_borrow_fee_on_longs_and_none_on_flat_books(self):
        a = acct()
        self.assertEqual(a.accrue_carry("2026-01-05", {"SPY": 100.0})["borrow_fee"], 0.0)
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0), {"SPY": 100.0})
        self.assertEqual(a.accrue_carry("2026-01-05", {"SPY": 100.0})["borrow_fee"], 0.0)

    def test_borrow_fee_scales_with_the_declared_scarcity(self):
        cheap, dear = acct(), acct()
        cheap.apply_fill(mk_fill("NVDA", SELL, 100, 185.0), {"NVDA": 185.0})
        dear.apply_fill(mk_fill("CVNA", SELL, 100, 185.0), {"CVNA": 185.0})
        f_cheap = cheap.accrue_carry("2026-01-05", {"NVDA": 185.0})["borrow_fee"]
        f_dear = dear.accrue_carry("2026-01-05", {"CVNA": 185.0})["borrow_fee"]
        self.assertGreater(f_dear, f_cheap)

    def test_dividends_are_paid_on_longs_and_owed_by_shorts(self):
        a = acct()
        marks = {"SPY": 700.0}
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), marks)
        out = a.settle_dividend("SPY", 1.90)
        self.assertAlmostEqual(out["received"], 190.0, places=6)
        self.assertEqual(out["in_lieu"], 0.0)
        self.assertAlmostEqual(a.dividends_received, 190.0, places=6)
        self.assertAlmostEqual(a.cash, 100_000.0 - 70_000.0 + 190.0, places=6)
        self.assertEqual(a.settle_dividend("NVDA", 1.00)["received"], 0.0)
        self.assertEqual(a.settle_dividend("SPY", 0.0)["received"], 0.0)

    def test_short_position_owes_a_manufactured_dividend(self):
        """A borrower must pay the lender the dividend that goes ex."""
        b = acct()
        b.apply_fill(mk_fill("RIVN", SELL, 100, 12.0), {"RIVN": 12.0})
        out = b.settle_dividend("RIVN", 1.00)
        self.assertEqual(out["received"], 0.0,
                         "a short position does not receive the dividend")
        self.assertAlmostEqual(out["in_lieu"], 100.0, places=6)
        self.assertAlmostEqual(b.dividends_in_lieu_paid, 100.0, places=6)
        self.assertAlmostEqual(b.cash, 100_000.0 + 1_200.0 - 100.0, places=6,
                               msg="short proceeds credited, manufactured "
                                   "dividend debited")
        # The per-symbol row shows the net carry on the position.
        self.assertAlmostEqual(b.position("RIVN").dividends, -100.0, places=6)

    def test_dividend_entitlement_is_the_position_carried_into_the_ex_date(self):
        """Buying ON the ex-date earns nothing; selling ON it is still paid."""
        buyer = acct()
        buyer.apply_fill(mk_fill("SPY", BUY, 100, 700.0), {"SPY": 700.0})
        # They held nothing before today, so entitled_qty=0 decides it.
        self.assertEqual(buyer.settle_dividend("SPY", 1.90, entitled_qty=0)["received"],
                         0.0, "a position opened on the ex-date is not entitled")

        seller = acct()
        seller.apply_fill(mk_fill("SPY", BUY, 100, 700.0), {"SPY": 700.0})
        seller.start_day("2026-01-06")
        seller.apply_fill(mk_fill("SPY", SELL, 100, 700.0, date="2026-01-06"),
                          {"SPY": 700.0})
        out = seller.settle_dividend("SPY", 1.90, entitled_qty=100)
        self.assertAlmostEqual(out["received"], 190.0, places=6,
                               msg="the holder of record before the ex-date is "
                                   "paid even if they sold during the day")
        self.assertEqual(seller.quantity("SPY"), 0)

    def test_quantities_at_open_snapshots_before_trading(self):
        a = acct()
        a.apply_fill(mk_fill("SPY", BUY, 100, 700.0), {"SPY": 700.0})
        a.start_day("2026-01-06")
        self.assertEqual(a.quantities_at_open(), {"SPY": 100},
                         "the snapshot must be taken before today's fills")
        a.apply_fill(mk_fill("SPY", SELL, 100, 701.0, date="2026-01-06"),
                     {"SPY": 701.0})
        self.assertEqual(a.quantities_at_open(), {},
                         "flat book snapshots to nothing, not to yesterday")

    def test_day_trades_are_counted_once_per_session(self):
        a = acct()
        marks = {"SPY": 100.0}
        a.start_day("2026-01-05")
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0, date="2026-01-05"), marks)
        a.apply_fill(mk_fill("SPY", SELL, 100, 101.0, date="2026-01-05"), marks)
        self.assertEqual(a.day_trades, ["2026-01-05"])
        self.assertEqual(a.day_trade_count, 1)
        a.apply_fill(mk_fill("SPY", BUY, 100, 101.0, date="2026-01-05"), marks)
        a.apply_fill(mk_fill("SPY", SELL, 100, 102.0, date="2026-01-05"), marks)
        self.assertEqual(a.day_trades, ["2026-01-05"], "one entry per session")
        self.assertEqual(a.day_trade_count, 2, "but each round trip is counted")
        a.start_day("2026-01-06")
        a.apply_fill(mk_fill("SPY", BUY, 100, 102.0, date="2026-01-06"), marks)
        a.apply_fill(mk_fill("SPY", SELL, 100, 103.0, date="2026-01-06"), marks)
        self.assertEqual(a.day_trades, ["2026-01-05", "2026-01-06"])
        self.assertEqual(a.day_trade_count, 3)

    def test_overnight_round_trip_is_not_a_day_trade(self):
        a = acct()
        marks = {"SPY": 100.0}
        a.start_day("2026-01-05")
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0, date="2026-01-05"), marks)
        a.start_day("2026-01-06")
        a.apply_fill(mk_fill("SPY", SELL, 100, 101.0, date="2026-01-06"), marks)
        self.assertEqual(a.day_trades, [])
        self.assertEqual(a.day_trade_count, 0,
                         "the intraday counter must reset with the session")

    def test_adding_to_a_position_intraday_is_not_a_day_trade(self):
        """FINRA counts a day trade as opening AND closing, not two buys.

        The pre-fix detector returned True for any second fill of a session,
        which turned scaling into day trading and, at season scale, flagged
        235 of 251 sessions for an overnight strategy that never round trips
        intraday at all.
        """
        a = acct()
        marks = {"SPY": 100.0}
        a.start_day("2026-01-05")
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0, date="2026-01-05"), marks)
        a.apply_fill(mk_fill("SPY", BUY, 100, 100.0, date="2026-01-05"), marks)
        a.apply_fill(mk_fill("SPY", BUY, 50, 100.0, date="2026-01-05"), marks)
        self.assertEqual(a.day_trades, [], "three buys are not a round trip")
        self.assertEqual(a.day_trade_count, 0)
        # A partial close of the intraday opening IS one day trade.
        a.apply_fill(mk_fill("SPY", SELL, 50, 101.0, date="2026-01-05"), marks)
        self.assertEqual(a.day_trades, ["2026-01-05"])
        self.assertEqual(a.day_trade_count, 1)
        # Short-side mirror: selling short then covering is a day trade too.
        b = acct()
        b.start_day("2026-01-05")
        b.apply_fill(mk_fill("AAPL", SELL, 100, 200.0, date="2026-01-05"),
                     {"AAPL": 200.0})
        b.apply_fill(mk_fill("AAPL", SELL, 100, 200.0, date="2026-01-05"),
                     {"AAPL": 200.0})
        self.assertEqual(b.day_trades, [])
        b.apply_fill(mk_fill("AAPL", BUY, 150, 199.0, date="2026-01-05"),
                     {"AAPL": 199.0})
        self.assertEqual(b.day_trades, ["2026-01-05"])
        self.assertEqual(b.day_trade_count, 1)


class TestFinraDayTradeExamples(unittest.TestCase):
    """The six worked examples in FINRA Regulatory Notice 21-13, verbatim.

    https://www.finra.org/rules-guidance/notices/21-13 is the only place the
    arithmetic behind "four or more day trades in five business days" is
    written out, and it is unusually explicit: Interpretation /02 lets a firm
    count "the number of times during the day that the day trading customer
    changes its trading direction", and then works six sequences out by hand.

    Those six answers are the specification for Account._count_day_trade.  They
    rule out the two implementations a reasonable person writes first: counting
    closing transactions (which would call example C three day trades, not one)
    and counting any fill that reduces today's quantity (example D would be
    two).  Only a count of direction CHANGES, with consecutive same-direction
    fills merged, reproduces all six.  A test that merely asserted "a round trip
    counts once" would have passed against all three implementations and told us
    nothing; these examples are why the rule here is the rule FINRA publishes.
    """

    def run_sequence(self, *legs):
        """Apply (side, symbol, qty) legs in one session and return the count."""
        a = acct()
        a.start_day("2026-01-05")
        marks = {sym: 100.0 for _, sym, _ in legs}
        for side, sym, qty in legs:
            a.apply_fill(mk_fill(sym, side, qty, 100.0, date="2026-01-05"), marks)
        return a, a.day_trade_count

    def test_example_a_one_round_trip_is_one_trade(self):
        # 09:30 Buy 250 ABC; 09:31 Buy 250 ABC; 13:00 Sell 500 ABC.
        # Notice: "The customer has executed one day trade."
        _, n = self.run_sequence((BUY, "SPY", 250), (BUY, "SPY", 250),
                                 (SELL, "SPY", 500))
        self.assertEqual(n, 1)

    def test_example_b_two_round_trips_are_two(self):
        # 09:30 Buy 100; 09:31 Sell 100; 09:32 Buy 100; 13:00 Sell 100.
        _, n = self.run_sequence((BUY, "SPY", 100), (SELL, "SPY", 100),
                                 (BUY, "SPY", 100), (SELL, "SPY", 100))
        self.assertEqual(n, 2)

    def test_example_c_three_exits_of_one_entry_are_one_trade(self):
        # 09:30 Buy 500; 13:00 Sell 100; 13:01 Sell 100; 13:03 Sell 300.
        # Scaling OUT is not three day trades.  This is the case a naive
        # per-fill detector gets wrong.
        _, n = self.run_sequence((BUY, "SPY", 500), (SELL, "SPY", 100),
                                 (SELL, "SPY", 100), (SELL, "SPY", 300))
        self.assertEqual(n, 1)

    def test_example_d_partial_exit_of_a_scaled_position_is_one_trade(self):
        # 09:30 Buy 250; 09:31 Buy 300; 13:01 Buy 100; 13:02 Sell 150;
        # 13:03 Sell 175.  The position is still 325 shares long at the close
        # and the Notice still counts one trade.
        _, n = self.run_sequence((BUY, "SPY", 250), (BUY, "SPY", 300),
                                 (BUY, "SPY", 100), (SELL, "SPY", 150),
                                 (SELL, "SPY", 175))
        self.assertEqual(n, 1)

    def test_example_e_reversal_then_exit_is_two(self):
        # 09:30 Buy 199; 09:31 Buy 142; 13:00 Sell 1; 13:01 Buy 45;
        # 13:02 Sell 100; 13:03 Sell 200.  The one-share sale is a real
        # direction change, so it counts even though it barely touches the
        # position.
        _, n = self.run_sequence((BUY, "SPY", 199), (BUY, "SPY", 142),
                                 (SELL, "SPY", 1), (BUY, "SPY", 45),
                                 (SELL, "SPY", 100), (SELL, "SPY", 200))
        self.assertEqual(n, 2)

    def test_example_f_is_counted_per_security(self):
        # 09:30 Buy 200 ABC; 09:30 Buy 100 XYZ; 13:00 Sell 100 ABC;
        # 13:00 Sell 100 XYZ -> "two day trades", i.e. the rule is applied to
        # each security separately and never to the account's net flow.
        _, n = self.run_sequence((BUY, "SPY", 200), (BUY, "AAPL", 100),
                                 (SELL, "SPY", 100), (SELL, "AAPL", 100))
        self.assertEqual(n, 2)

    def test_one_laying_out_a_position_and_another_opening_it_is_not_a_trade(self):
        # Not from the Notice: this is the @OvernightCarry_NO shape.  Closing
        # yesterday's long and opening today's, in the same session, contains no
        # same-day round trip of the SAME shares, so the "except for positions
        # held overnight" carve-out applies.  The pre-fix stub called this 235
        # day trades in 251 sessions and made an overnight strategy look like a
        # pattern day trader.
        a = acct()
        marks = {"SPY": 100.0}
        a.start_day("2026-01-05")
        a.apply_fill(mk_fill("SPY", BUY, 1114, 100.0, date="2026-01-05"), marks)
        a.start_day("2026-01-06")
        a.apply_fill(mk_fill("SPY", SELL, 1114, 101.0, date="2026-01-06"), marks)
        a.apply_fill(mk_fill("SPY", BUY, 1108, 101.0, date="2026-01-06"), marks)
        a.start_day("2026-01-07")
        a.apply_fill(mk_fill("SPY", SELL, 1108, 102.0, date="2026-01-07"), marks)
        self.assertEqual(a.day_trade_count, 0)
        self.assertEqual(a.day_trades, [])

class TestMarksAndSummary(unittest.TestCase):
    def test_mark_appends_to_the_equity_curve_and_reports_consistent_fields(self):
        a = acct()
        marks = {"SPY": 100.0}
        a.apply_fill(mk_fill("SPY", BUY, 500, 100.0), marks)
        row = a.mark("2026-01-05", marks)
        self.assertEqual(a.equity_curve, [("2026-01-05", 100_000.0)])
        self.assertAlmostEqual(row["equity"], 100_000.0, places=2)
        self.assertAlmostEqual(row["gross_exposure"], 50_000.0, places=2)
        self.assertAlmostEqual(row["leverage"], 0.5, places=4)
        self.assertEqual(row["open_positions"], 1)
        self.assertAlmostEqual(row["return_pct"], 0.0, places=4)
        self.assertAlmostEqual(row["unrealized"], 0.0, places=2)
        for key in ("date", "equity", "cash", "gross_exposure", "net_exposure",
                    "leverage", "realized_gross", "unrealized", "open_positions",
                    "return_pct"):
            self.assertIn(key, row)

    def test_summary_reconciles_to_the_equity_change(self):
        a = acct()
        marks = {"SPY": 110.0}
        a.apply_fill(mk_fill("SPY", BUY, 500, 100.0, commission=5.0), {"SPY": 100.0})
        a.apply_fill(mk_fill("SPY", SELL, 200, 110.0, regulatory_fee=0.43), marks)
        a.pay_dividend("SPY", 1.90)
        s = a.summary(marks)
        self.assertAlmostEqual(s["final_equity"], a.equity(marks), places=2)
        self.assertAlmostEqual(s["net_pnl"], s["final_equity"] - s["starting_cash"],
                               places=2)
        self.assertAlmostEqual(
            s["net_pnl"],
            s["realized_gross_pnl"] + s["unrealized_pnl"] + s["dividends_received"]
            - s["dividends_in_lieu_paid"]
            - s["borrow_fees_paid"] - s["incremental_cash_costs"],
            places=2,
            msg="P&L decomposition must close: realized + unrealized + dividends "
                "- manufactured dividends - borrow - cash costs = net P&L")
        self.assertAlmostEqual(s["total_return_pct"],
                               100.0 * s["net_pnl"] / s["starting_cash"], places=3)

    def test_starting_cash_and_participant_are_recorded(self):
        a = acct(cash=25_000.0)
        self.assertEqual(a.participant, "@tester")
        self.assertEqual(a.starting_cash, 25_000.0)
        self.assertEqual(a.summary({"SPY": 100.0})["starting_cash"], 25_000.0)


if __name__ == "__main__":
    unittest.main()
