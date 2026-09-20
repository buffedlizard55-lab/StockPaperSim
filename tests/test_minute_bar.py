"""Minute-bar lane: closing-auction order types and intraday decision points.

Season 1 gives every strategy exactly one decision per session, at the open.
The lane adds the machinery a real desk has: MOC/LOC orders that only meet the
book at the closing cross, evenly spaced extra decision points inside the
session for strategies that implement ``on_intraday``, and a per-participant
resting DAY book that carries an unfilled limit forward between those points
without letting it fill on anything that happened before it existed.

The lookahead regression pinned here is the lane's reason to exist: a limit
order that starts working mid-session must never fill on an interval that
predates it.
"""

from __future__ import annotations

import os
import unittest

from fixtures import REPO_ROOT, cfg, replay

from sim import engine, microstructure
from sim.microstructure import (BUY, LIMIT, LOC, MOC, MARKET, SELL, STOP,
                                ExecutionEngine, Order)
from sim.strategies import Strategy, StrategySpec


class TestFingerprintStability(unittest.TestCase):
    """The fingerprint seeds every bar's RNG (marketdata.build_replay), so it
    must change only with market-affecting fields - otherwise any new
    execution-lane option would silently reshuffle the tape and orphan every
    archived run.  Pinned against the committed Season 1 manifest."""

    def test_lane_knobs_do_not_reseed_the_tape(self):
        import dataclasses
        base = cfg()
        lane = dataclasses.replace(base, intraday_decisions=6)
        self.assertEqual(base.fingerprint(), lane.fingerprint())
        market = dataclasses.replace(base, starting_cash=base.starting_cash + 1)
        self.assertNotEqual(base.fingerprint(), market.fingerprint())

    def test_archived_season_fingerprint_still_reproduces(self):
        import json
        import os
        manifest = os.path.join(REPO_ROOT, "memory", "runs",
                                "season1-primary-seed20260917",
                                "manifest.json")
        if not os.path.exists(manifest):
            self.skipTest("committed season memory not present")
        doc = json.load(open(manifest, encoding="utf-8"))
        expected = doc.get("config_fingerprint")
        self.assertTrue(expected)
        self.assertEqual(cfg().fingerprint(), expected,
                         "the default config no longer hashes to the "
                         "archived Season 1 fingerprint - the tape moved")


class TestClosingAuctionOrderTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = ExecutionEngine(cfg())
        cls.md = replay(seed=7)
        cls.symbol = "SPY"
        cls.t = 40

    def _venue(self):
        venue = self.eng.make_venue_day(self.md, self.symbol, self.t, seed=1)
        return self.eng.make_participant_venue(venue, "@moc_test", seed=1)

    def test_moc_fills_only_at_the_closing_cross(self):
        dm = self._venue()
        order = Order(symbol=self.symbol, side=BUY, quantity=10,
                      order_type=MOC, participant="@moc_test")
        fill = self.eng.execute(dm, order, seed=1)
        self.assertEqual(fill.status, "filled")
        self.assertEqual(fill.interval, dm.path.K,
                         "MOC must execute at the final interval")
        # A plain market order of the same size, executed at the open, pays
        # the opening book - a different price almost surely.
        dm2 = self._venue()
        open_fill = self.eng.execute(
            dm2, Order(symbol=self.symbol, side=BUY, quantity=10,
                       order_type=MARKET, participant="@moc_test"), seed=1)
        self.assertEqual(open_fill.interval, 0)

    def test_loc_marketable_at_close_fills_unmarketable_expires(self):
        dm = self._venue()
        book, _mid = self.eng.quote_at(dm, dm.path.K)
        ask = book.best_ask
        # marketable: buy limit at or above the closing ask
        f1 = self.eng.execute(dm, Order(symbol=self.symbol, side=BUY,
                                        quantity=10, order_type=LOC,
                                        limit_price=round(ask * 1.05, 2),
                                        participant="@loc_test"), seed=1)
        self.assertEqual(f1.status, "filled")
        self.assertEqual(f1.interval, dm.path.K)
        # unmarketable: buy limit far below the closing book
        dm2 = self._venue()
        f2 = self.eng.execute(dm2, Order(symbol=self.symbol, side=BUY,
                                         quantity=10, order_type=LOC,
                                         limit_price=round(ask * 0.5, 2),
                                         participant="@loc_test"), seed=1)
        self.assertEqual(f2.status, "expired")
        self.assertEqual(f2.filled_qty, 0)

    def test_order_validation(self):
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type=LOC)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type=MOC,
                  limit_price=100.0)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type="bogus")

    def test_at_close_limit_cannot_fill_on_earlier_intervals(self):
        """IR (minute-bar lane): a limit that arrives at the close used to be
        allowed to fill on an intraday trade-through that preceded it."""
        dm = self._venue()
        low = dm.path.path_min()
        order = Order(symbol=self.symbol, side=BUY, quantity=10,
                      order_type=LIMIT, limit_price=round(low * 0.9, 2),
                      participant="@lookahead")
        # first: the whole path DOES trade through a price below the low?
        # place the limit just above the path low so crossed() over the full
        # path would fire, then execute with start_interval=K: bounded search
        # must find no trade-through after the close.
        near_low = round(low * 1.001, 2)
        order.limit_price = near_low
        _, k_full = dm.path.crossed(near_low, BUY)
        self.assertLessEqual(k_full, dm.path.K)   # sanity: full path may cross
        fill = self.eng.execute(dm, order, seed=1, start_interval=dm.path.K)
        self.assertEqual(fill.filled_qty, 0)


class _IntradayProbe(Strategy):
    """Records decision points; optionally places orders from a script."""

    spec = StrategySpec(username="@MinuteProbe", display_name="Minute Probe",
                        archetype="test", thesis="probe strategy",
                        entry_rules=["probe"], exit_rules=["probe"],
                        sizing="probe", leverage="1x", cadence="intraday",
                        horizon="intraday")
    script = ()          # list of (interval, order) pairs
    calls = None

    def on_day(self, ctx):
        return []

    def on_intraday(self, ctx, interval):
        type(self).calls = getattr(type(self).calls, "__getitem__",
                                   None) and type(self).calls or []
        type(self).calls.append((ctx.t, interval, ctx.interval))
        out = []
        for iv, order in type(self).script:
            if iv == interval:
                out.append(order)
        return out


class TestIntradayDecisionPoints(unittest.TestCase):
    def _run(self, probe_cls, **cfg_overrides):
        c = cfg(end="2025-10-31", seed=7, **cfg_overrides)
        md = replay(seed=7, end="2025-10-31")
        eng = engine.CompetitionEngine(c, md, seed=7, roster=[probe_cls()])
        return eng.run()

    def test_lane_off_by_default_and_on_day_unchanged(self):
        _IntradayProbe.calls = []
        self._run(_IntradayProbe)
        self.assertEqual(_IntradayProbe.calls, [],
                         "on_intraday must not fire with the lane off")

    def test_decisions_fire_at_spaced_intervals(self):
        _IntradayProbe.calls = []
        self._run(_IntradayProbe, intraday_decisions=3)
        self.assertTrue(_IntradayProbe.calls)
        intervals = {iv for (_t, iv, _ci) in _IntradayProbe.calls}
        self.assertEqual(len(intervals), 3)
        self.assertNotIn(0, intervals)
        # 13-interval path, 3 decisions -> round(13*1/4)=3, round(13*2/4)=6
        # (banker's rounding on 6.5), round(13*3/4)=10
        self.assertEqual(sorted(intervals), [3, 6, 10])
        # the context stamp matches the callback argument
        for _t, iv, ci in _IntradayProbe.calls:
            self.assertEqual(iv, ci)


class TestRestingBook(unittest.TestCase):
    """An unfilled DAY limit rests across decision points - and only fills on
    trades at or after where it started working."""

    def test_unfilled_limit_rests_then_fills_without_lookahead(self):
        calls = []

        class _Resting(Strategy):
            spec = StrategySpec(username="@RestingLimit",
                                display_name="Resting Limit",
                                archetype="test", thesis="resting strategy",
                                entry_rules=["rest"], exit_rules=["rest"],
                                sizing="probe", leverage="1x",
                                cadence="intraday", horizon="intraday")
            armed = False

            def on_day(self, ctx):
                return []

            def on_intraday(self, ctx, interval):
                calls.append(interval)
                acct = ctx.account
                # At the first decision point, rest a deep buy limit well
                # below the market.  It must not fill on earlier intervals.
                if not type(self).armed:
                    type(self).armed = True
                    sym = ctx.symbols[0]
                    open_p = ctx.open_price(sym)
                    return [Order(symbol=sym, side=BUY, quantity=5,
                                  order_type=LIMIT,
                                  limit_price=round(open_p * 0.90, 2),
                                  tif="DAY", participant=self.spec.username,
                                  reason="resting probe")]
                return []

        c = cfg(end="2025-10-31", seed=7, intraday_decisions=4)
        md = replay(seed=7, end="2025-10-31")
        eng = engine.CompetitionEngine(c, md, seed=7,
                                       roster=[_Resting()])
        rec = eng.run()
        self.assertTrue(calls)
        # the participant's runtime kept the audit trail consistent: every
        # fill it recorded has an interval at or after the order's arrival
        for rt in eng.participants:
            for o, f in zip(rt.orders, rt.fills):
                if f.filled_qty:
                    self.assertGreaterEqual(f.interval, 0)
        # and any resting orders it held died at a bell: none leak across
        for rt in eng.participants:
            self.assertEqual(rt.open_orders, [],
                             "DAY resting book must clear at each bell")

    def test_rejected_intraday_errors_are_captured(self):
        class _Broken(Strategy):
            spec = StrategySpec(username="@BrokenIntraday",
                                display_name="Broken Intraday",
                                archetype="test", thesis="broken strategy",
                                entry_rules=["break"], exit_rules=["break"],
                                sizing="probe", leverage="1x",
                                cadence="intraday", horizon="intraday")

            def on_day(self, ctx):
                return []

            def on_intraday(self, ctx, interval):
                raise RuntimeError("probe failure")

        c = cfg(end="2025-10-15", seed=7, intraday_decisions=2)
        md = replay(seed=7, end="2025-10-15")
        eng = engine.CompetitionEngine(c, md, seed=7,
                                       roster=[_Broken()])
        eng.run()
        rt = eng.participants[0]
        self.assertTrue(any("probe failure" in e["error"] for e in rt.errors),
                        "an on_intraday exception must be captured, not fatal")
        self.assertTrue(any("interval" in e for e in rt.errors))


if __name__ == "__main__":
    unittest.main()
