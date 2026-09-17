"""The roster: twenty distinct, return-seeking strategies, honestly declared.

Two project requirements are tested directly here:
  * every participant has a unique username and a written strategy;
  * none of them is built around risk management - they are built to maximise
    return, and the ones that are structurally mis-measured by a daily-bar
    engine say so in their own spec.
Plus the property that makes the whole competition meaningful: no strategy may
look at a bar it has not lived through yet.
"""

from __future__ import annotations

import math
import unittest
from dataclasses import asdict

from fixtures import cfg, replay

from sim import config, strategies
from sim.microstructure import BUY, SELL
from sim.portfolio import Account
from sim.strategies import (ARCHETYPE_FACTORS, Context, Strategy, StrategySpec,
                            build_roster, correlation, donchian, ema, roster_specs,
                            zscore)

# Phrases that would mean a strategy was built to manage risk rather than to
# maximise return.  The project brief is explicit: highest returns only.
FORBIDDEN_OBJECTIVES = [
    "capital preservation", "preserve capital", "risk-adjusted return",
    "maximise the sharpe", "maximize the sharpe", "target volatility",
    "volatility targeting", "risk parity", "value at risk limit",
    "maximum drawdown limit", "drawdown limit", "capital protection",
    "protect the downside", "hedge the portfolio", "risk minimisation",
    "risk minimization", "defensive mandate",
]


class _SpyMarketData:
    """Delegates to MarketData and records the highest session index asked for."""

    def __init__(self, md):
        self._md = md
        self.max_t = -1
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._md, name)

    def _note(self, t):
        self.calls += 1
        if isinstance(t, int):
            self.max_t = max(self.max_t, t)

    def bar(self, symbol, t):
        self._note(t)
        return self._md.bar(symbol, t)

    def history_closes(self, symbol, t, n=None):
        self._note(t)
        return self._md.history_closes(symbol, t, n)

    def history_returns(self, symbol, t, n=None):
        self._note(t)
        return self._md.history_returns(symbol, t, n)

    def history_volume(self, symbol, t, n=None):
        self._note(t)
        return self._md.history_volume(symbol, t, n)

    def adv(self, symbol, t, window=63):
        self._note(t)
        return self._md.adv(symbol, t, window)

    def realised_sigma_daily(self, symbol, t, window=21):
        self._note(t)
        return self._md.realised_sigma_daily(symbol, t, window)

    def market_sigma_daily(self, t, window=21):
        self._note(t)
        return self._md.market_sigma_daily(t, window)

    def vix_regime_factor(self, t):
        self._note(t)
        return self._md.vix_regime_factor(t)


def _context(md, t, strategy, seed=1):
    account = Account(strategy.username, config.STARTING_CASH, cfg().margin)
    account.start_day(md.dates[t])
    return Context(md, t, account, cfg(), {}, seed), account


class TestRosterShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = build_roster()

    def test_twenty_distinct_participants(self):
        self.assertEqual(len(self.roster), 20)
        handles = [s.username for s in self.roster]
        self.assertEqual(len(set(handles)), 20)
        names = [s.spec.display_name for s in self.roster]
        self.assertEqual(len(set(names)), 20, "display names must be unique too")

    def test_every_username_is_a_handle(self):
        for s in self.roster:
            self.assertTrue(s.username.startswith("@"), s.username)
            self.assertGreater(len(s.username), 4)
            self.assertNotIn(" ", s.username)
            self.assertTrue(all(ch.isalnum() or ch in "@_" for ch in s.username),
                            f"{s.username} has characters a real handle would not allow")

    def test_every_archetype_has_a_declared_factor_exposure(self):
        for s in self.roster:
            self.assertIn(s.spec.archetype, ARCHETYPE_FACTORS,
                          f"{s.username}: archetype {s.spec.archetype!r} is not mapped")
            exposure = s.spec.factor_exposure
            self.assertTrue(exposure, f"{s.username}: no declared factor exposure")
            for factor, loading in exposure.items():
                self.assertIn(factor, ("market", "momentum", "reversal", "beta",
                                       "low_vol", "liquidity"))
                self.assertLessEqual(abs(loading), 1.0)

    def test_specs_are_complete_written_strategies(self):
        for s in self.roster:
            spec = s.spec
            self.assertGreater(len(spec.thesis), 60, spec.username)
            self.assertGreaterEqual(len(spec.entry_rules), 1, spec.username)
            self.assertGreaterEqual(len(spec.exit_rules), 1, spec.username)
            for rule in spec.entry_rules + spec.exit_rules:
                self.assertGreater(len(rule), 8, spec.username)
            for field in ("sizing", "leverage", "cadence", "horizon"):
                self.assertTrue(getattr(spec, field).strip(),
                                f"{spec.username}: empty {field}")
            self.assertTrue(spec.why_return_seeking.strip(),
                            f"{spec.username}: must say why it is return-seeking")
            self.assertGreaterEqual(len(spec.known_failure_modes), 1,
                                    f"{spec.username}: declares no failure modes")
            self.assertIn(spec.aggression, (1, 2, 3, 4, 5))

    def test_no_strategy_is_built_around_risk_management(self):
        """The brief: strategies must chase return, not manage risk."""
        for s in self.roster:
            spec = s.spec
            blob = " ".join([spec.thesis, spec.sizing, spec.leverage,
                             spec.why_return_seeking, spec.horizon] +
                            spec.exit_rules).lower()
            for phrase in FORBIDDEN_OBJECTIVES:
                self.assertNotIn(phrase, blob,
                                 f"{spec.username}: objective drifts into risk "
                                 f"management ({phrase!r})")
            self.assertGreater(len(spec.why_return_seeking), 40,
                               f"{spec.username}: why_return_seeking is too thin")
            why = spec.why_return_seeking.lower()
            # A keyword list would be brittle; what the brief actually requires is
            # that the rationale is a substantive statement of how the strategy
            # tries to make money and that it is NOT framed as loss limitation.
            self.assertGreater(len(why), 40,
                               f"{spec.username}: why_return_seeking is too thin")
            self.assertTrue(why.endswith((".", "year.", "one.")) or len(why) > 60,
                            f"{spec.username}: why_return_seeking is a fragment")
            # The rationale must be about making money, not about limiting loss.
            for phrase in ("limit losses", "cut drawdown", "reduce risk",
                           "protect capital", "minimise loss", "minimize loss",
                           "cap the downside", "control risk", "risk budget",
                           "stop out to protect", "hedge against loss"):
                self.assertNotIn(phrase, why,
                                 f"{spec.username}: rationale is about risk control")

    def test_aggression_is_high_across_the_roster(self):
        mean_aggression = sum(s.spec.aggression for s in self.roster) / len(self.roster)
        self.assertGreaterEqual(mean_aggression, 3.5)
        self.assertGreaterEqual(max(s.spec.aggression for s in self.roster), 5)

    def test_academic_citations_are_complete_and_labelled(self):
        for s in self.roster:
            for ref in s.spec.academic_basis:
                for key in ("claim", "url", "ref", "status"):
                    self.assertIn(key, ref, f"{s.username}: citation missing {key}")
                self.assertTrue(ref["url"].startswith("https://"), ref["url"])
                # Statuses are words from the register legend, optionally with
                # the date they were fetched: FETCHED-2026-09-17.
                self.assertRegex(ref["status"],
                                 r"^[A-Z][A-Z -]*(-\d{4}-\d{2}-\d{2})?$")
                self.assertGreater(len(ref["claim"]), 20)

    def test_structurally_mismeasured_strategies_say_so(self):
        """IR-07 / IR-08: the daily-bar engine cannot measure these three."""
        by = {s.username: s.spec for s in self.roster}
        for user in ("@GapAndGo_YOLO", "@OvernightCarry_NO", "@SpreadHarvester_MM",
                     "@DriftRider_PEAD", "@SqueezeHunter_TF"):
            self.assertIn(user, by)
            blob = " ".join(by[user].known_failure_modes).upper()
            self.assertTrue(any(tag in blob for tag in
                                ("GRANULARITY", "PROXY DATA", "TIMING LIMIT",
                                 "DAILY DECISION", "DAILY-BAR", "IR-07", "IR-08",
                                 "NOT AVAILABLE OFFLINE")),
                            f"{user} is structurally mis-measured and must declare it")

    def test_roster_specs_serialise_with_the_factor_exposure_property(self):
        """asdict() drops properties; the serialiser must put it back."""
        rows = roster_specs()
        self.assertEqual(len(rows), 20)
        for row in rows:
            self.assertIn("factor_exposure", row)
            self.assertTrue(row["factor_exposure"])
        import json
        json.dumps(rows)


class TestNoLookAhead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.roster = build_roster()

    def test_no_strategy_reads_a_future_bar(self):
        for strategy in self.roster:
            for t in (self.md.first_competition_index + 5,
                      self.md.first_competition_index + 60,
                      len(self.md.dates) - 2):
                spy = _SpyMarketData(self.md)
                ctx, _ = _context(spy, t, strategy)
                try:
                    strategy.on_day(ctx)
                except Exception as exc:                    # pragma: no cover
                    self.fail(f"{strategy.username} raised at t={t}: {exc!r}")
                self.assertGreater(spy.calls, 0,
                                   f"{strategy.username} never touched market data")
                self.assertLessEqual(spy.max_t, t,
                                     f"{strategy.username} looked ahead to session "
                                     f"{spy.max_t} while deciding session {t}")

    def test_context_vix_and_prior_close_use_the_previous_session(self):
        md = self.md
        strategy = self.roster[0]
        t = md.first_competition_index + 10
        ctx, _ = _context(md, t, strategy)
        self.assertAlmostEqual(ctx.vix(), md.vix[t - 1], places=12)
        self.assertTrue(math.isfinite(ctx.vix_ma(21)))
        self.assertAlmostEqual(ctx.prior_close("SPY"), md.bar("SPY", t - 1).close,
                               places=9)
        self.assertAlmostEqual(ctx.open_price("SPY"), md.bar("SPY", t).open, places=9)
        self.assertEqual(ctx.date, md.dates[t])

    def test_context_marks_are_opening_prices_not_closes(self):
        md = self.md
        t = md.first_competition_index + 10
        ctx, _ = _context(md, t, self.roster[0])
        marks = ctx.marks()
        for sym in md.symbols:
            self.assertAlmostEqual(marks[sym], md.bar(sym, t).open, places=9)


class TestOrderGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.roster = build_roster()

    def _orders(self, strategy, t, seed=1):
        ctx, account = _context(self.md, t, strategy, seed)
        orders = strategy.on_day(ctx) or []
        return ctx, account, orders

    def test_every_strategy_produces_valid_orders(self):
        t = self.md.first_competition_index + 30
        for strategy in self.roster:
            _, _, orders = self._orders(strategy, t)
            for o in orders:
                self.assertIn(o.side, (BUY, SELL))
                self.assertGreater(o.quantity, 0)
                self.assertIn(o.symbol, self.md.symbols)
                self.assertEqual(o.participant, strategy.username,
                                 "orders must be attributed to their participant")
                self.assertTrue(o.reason.strip(),
                                f"{strategy.username}: order carries no rationale")
                if o.order_type == "limit":
                    self.assertIsNotNone(o.limit_price)
                if o.order_type == "stop":
                    self.assertIsNotNone(o.stop_price)

    def test_orders_from_the_helper_never_exceed_the_leverage_cap(self):
        """A strategy that uses Context.orders_to_targets cannot breach margin."""
        checked = 0
        for t in (self.md.first_competition_index + 20,
                  self.md.first_competition_index + 40,
                  self.md.first_competition_index + 90):
          for strategy in self.roster:
            ctx, account, orders = self._orders(strategy, t)
            for o in orders:
                price = ctx.open_price(o.symbol)
                ok, why = account.can_increase(o.symbol, o.side, o.quantity,
                                               price, ctx.marks())
                reducing = abs(account.quantity(o.symbol) +
                               (o.quantity if o.side == BUY else -o.quantity)) < \
                    abs(account.quantity(o.symbol))
                self.assertTrue(ok or reducing,
                                f"{strategy.username} {o.symbol} {o.side} "
                                f"{o.quantity} at t={t}: {why}")
                checked += 1
        self.assertGreater(checked, 100, "too few orders generated to be a real test")

    def test_orders_to_targets_respects_a_full_book(self):
        strategy = self.roster[0]
        t = self.md.first_competition_index + 20
        ctx, account = _context(self.md, t, strategy)
        targets = {s: 3.0 for s in ctx.symbols}        # absurd: 300% per name
        orders = ctx.orders_to_targets(targets, reason="deliberate over-reach")
        total = sum(o.quantity * ctx.open_price(o.symbol) for o in orders)
        self.assertLessEqual(total,
                             ctx.equity * cfg().margin.max_gross_leverage + 1.0)

    def test_flatten_produces_closing_orders_only(self):
        strategy = self.roster[0]
        t = self.md.first_competition_index + 20
        ctx, account = _context(self.md, t, strategy)
        account.apply_fill(_fill("SPY", BUY, 100, ctx.open_price("SPY"),
                                 ctx.date, strategy.username), ctx.marks())
        orders = ctx.flatten(reason="test flatten", symbols=["SPY"])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, SELL)
        self.assertEqual(orders[0].quantity, 100)

    def test_below_the_minimum_notional_no_order_is_generated(self):
        strategy = self.roster[0]
        t = self.md.first_competition_index + 20
        ctx, _ = _context(self.md, t, strategy)
        tiny = {ctx.symbols[0]: 1e-9}
        self.assertEqual(ctx.orders_to_targets(tiny, reason="dust"), [])


def _fill(symbol, side, qty, price, date, participant):
    from test_portfolio import mk_fill
    return mk_fill(symbol, side, qty, price, date=date, participant=participant)


class TestEveryStrategyActuallyTrades(unittest.TestCase):
    """Regression: a guard that could never be satisfied silently disabled
    @DriftRider_PEAD for a whole season (it produced zero orders)."""

    def test_every_participant_traded_in_the_published_run(self):
        import os
        from sim import memory
        from fixtures import REPO_ROOT
        store = memory.MemoryStore(os.path.join(REPO_ROOT, "memory"))
        run = "season1-primary-seed20260917"
        if not os.path.isdir(store.run_dir(run)):
            raise unittest.SkipTest("run memory not built")
        for report in store.reports(run):
            self.assertGreater(report["sessions_with_orders"], 0,
                               f"{report['username']} never traded")
            self.assertGreater(report["costs"]["fills"], 0,
                               f"{report['username']} never got a fill")
            self.assertGreater(report["turnover"]["traded_notional_usd"], 0.0)
            self.assertEqual(report["errors"], [],
                             f"{report['username']} raised inside on_day")
            self.assertFalse(report["account_closed_early"],
                             f"{report['username']} was closed out early")


class TestIndicatorHelpers(unittest.TestCase):
    def test_ema_is_seeded_with_the_sma_then_recurses(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        span = 3
        k = 2.0 / (span + 1)
        expected = sum(values[:span]) / span
        for v in values[span:]:
            expected = v * k + expected * (1 - k)
        self.assertAlmostEqual(ema(values, span), expected, places=12)
        self.assertAlmostEqual(ema(values[:span], span), 2.0, places=12)
        self.assertIsNone(ema([1.0, 2.0], 3), "not enough history for the span")
        self.assertIsNone(ema([], 3))

    def test_zscore_of_the_last_observation(self):
        values = [1.0, 1.0, 1.0, 1.0, 1.0, 5.0]
        z = zscore(values, 5)
        self.assertGreater(z, 1.0)
        # zscore needs n + 1 observations: the window is the last n and the
        # value being scored is the most recent one.
        self.assertIsNone(zscore([1.0, 1.0, 1.0, 1.0, 5.0], 5),
                          "n + 1 observations are required")
        self.assertIsNone(zscore([1.0, 1.0], 5), "not enough history")
        self.assertIsNone(zscore([2.0] * 6, 5),
                          "zero standard deviation must not divide by zero")

    def test_donchian_returns_the_channel_extremes(self):
        hi, lo = donchian([1.0, 5.0, 3.0, 2.0, 4.0], 5)
        self.assertEqual((hi, lo), (5.0, 1.0))
        self.assertIsNone(donchian([1.0], 5))

    def test_correlation_bounds_and_sign(self):
        a = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01,
             0.00, 0.015, -0.025]
        self.assertAlmostEqual(correlation(a, a), 1.0, places=9)
        self.assertAlmostEqual(correlation(a, [-x for x in a]), -1.0, places=9)
        self.assertEqual(correlation(a[:5], a[:5]), 0.0,
                         "fewer than ten points cannot estimate a correlation")
        b = [x * 2.0 + 0.001 for x in a]
        self.assertAlmostEqual(correlation(a, b), 1.0, places=9)

    def test_strategy_base_class_is_abstract(self):
        class Bare(Strategy):
            spec = StrategySpec(username="@Bare", display_name="Bare",
                                archetype="concentration", thesis="t",
                                entry_rules=["e"], exit_rules=["x"], sizing="s",
                                leverage="l", cadence="c", horizon="h")

        with self.assertRaises(NotImplementedError):
            Bare().on_day(None)
        self.assertEqual(Bare().username, "@Bare")
        self.assertEqual(Bare().spec.factor_exposure,
                         ARCHETYPE_FACTORS["concentration"])

    def test_duplicate_usernames_are_refused_at_construction(self):
        original = list(strategies.STRATEGY_CLASSES)
        try:
            strategies.STRATEGY_CLASSES = original + [original[0]]
            with self.assertRaises(ValueError):
                build_roster()
        finally:
            strategies.STRATEGY_CLASSES = original


if __name__ == "__main__":
    unittest.main()
