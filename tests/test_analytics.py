"""Analytics: the numbers the leaderboard and every post-mortem are built from.

Includes direct regression tests for the three measurement bugs found during
the build (IR-21 phantom drift, IR-22 dropped flip residual, IR-23 beta
misalignment) and an end-to-end consistency pass over the real run memory.
"""

from __future__ import annotations

import json
import math
import os
import unittest

from fixtures import (PUBLISHED_TOP_RETURN_PCT, PUBLISHED_TOP_USERNAME,
                       REPO_ROOT, cfg, replay)

from sim import analytics, config, memory, strategies
from sim.analytics import DECOMPOSITION_BUCKETS

TRADING_DAYS = config.TRADING_DAYS_PER_YEAR


class TestReturnSeries(unittest.TestCase):
    def test_daily_returns_are_simple_returns_off_the_curve(self):
        curve = [("d0", 100.0), ("d1", 110.0), ("d2", 99.0)]
        rets = analytics.daily_returns(curve)
        self.assertEqual(len(rets), 2)
        self.assertAlmostEqual(rets[0], 0.10, places=12)
        self.assertAlmostEqual(rets[1], -0.10, places=12)

    def test_flat_curve_has_no_returns(self):
        self.assertEqual(analytics.daily_returns([("d0", 100.0)]), [])
        self.assertEqual(analytics.daily_returns([]), [])

    def test_drawdown_series_is_non_positive_and_recovers(self):
        curve = [("d0", 100.0), ("d1", 120.0), ("d2", 90.0), ("d3", 130.0)]
        dd = analytics.drawdown_series(curve)
        self.assertEqual([d for _, d in dd][0], 0.0)
        self.assertAlmostEqual(dd[2][1], 90.0 / 120.0 - 1.0, places=12)
        self.assertAlmostEqual(dd[3][1], 0.0, places=12)

    def test_max_drawdown_value_dates_and_recovery(self):
        curve = [("2026-01-01", 100.0), ("2026-01-02", 120.0),
                 ("2026-01-03", 90.0), ("2026-01-04", 95.0),
                 ("2026-01-05", 125.0)]
        dd = analytics.max_drawdown(curve)
        self.assertAlmostEqual(dd["max_drawdown_pct"], -25.0, places=4)
        self.assertAlmostEqual(dd["max_drawdown_usd"], -30.0, places=4)
        self.assertEqual(dd["peak_date"], "2026-01-02")
        self.assertEqual(dd["trough_date"], "2026-01-03")
        self.assertEqual(dd["recovery_date"], "2026-01-05")
        self.assertFalse(dd["never_recovered"])
        self.assertEqual(dd["sessions_underwater"], 2)

    def test_never_recovered_drawdown(self):
        curve = [("d0", 100.0), ("d1", 50.0), ("d2", 60.0)]
        dd = analytics.max_drawdown(curve)
        self.assertTrue(dd["never_recovered"])
        self.assertIsNone(dd["recovery_date"])
        self.assertAlmostEqual(dd["max_drawdown_pct"], -50.0, places=4)

    def test_monotone_curve_has_zero_drawdown(self):
        curve = [(f"d{i}", 100.0 + i) for i in range(10)]
        self.assertAlmostEqual(analytics.max_drawdown(curve)["max_drawdown_pct"],
                               0.0, places=9)

    def test_empty_curve_is_handled(self):
        self.assertEqual(analytics.max_drawdown([])["max_drawdown_pct"], 0.0)


class TestTailRisk(unittest.TestCase):
    def test_insufficient_history_returns_zeros_not_nonsense(self):
        out = analytics.var_cvar([0.01] * 10)
        self.assertEqual(out["var_95_pct"], 0.0)
        self.assertEqual(out["cvar_99_pct"], 0.0)

    def test_var_and_cvar_are_ordered_and_negative_in_the_tail(self):
        rets = [0.01] * 90 + [-0.05, -0.08, -0.12, -0.20, -0.30,
                              -0.02, -0.03, -0.04, -0.06, -0.09]
        out = analytics.var_cvar(rets)
        self.assertLess(out["var_95_pct"], 0.0)
        self.assertLess(out["var_99_pct"], out["var_95_pct"])
        self.assertLessEqual(out["cvar_95_pct"], out["var_95_pct"])
        self.assertLessEqual(out["cvar_99_pct"], out["var_99_pct"])

    def test_known_quantiles(self):
        rets = [i / 100.0 for i in range(-50, 50)]     # -0.50 .. +0.49
        out = analytics.var_cvar(rets)
        self.assertAlmostEqual(out["var_95_pct"], -45.0, delta=2.0)
        self.assertLess(out["cvar_95_pct"], -45.0)

    def test_skew_and_kurtosis_signs(self):
        symmetric = [0.01, -0.01] * 50
        self.assertAlmostEqual(analytics._skew(symmetric), 0.0, places=9)
        self.assertAlmostEqual(analytics._kurtosis(symmetric), -2.0, delta=0.5)
        crashed = [0.005] * 99 + [-0.50]
        self.assertLess(analytics._skew(crashed), 0.0)
        self.assertGreater(analytics._kurtosis(crashed), 0.0)


class TestMonthlyReturns(unittest.TestCase):
    def test_months_compound_from_the_previous_month_end(self):
        curve = [("2026-01-05", 100.0), ("2026-01-20", 110.0),
                 ("2026-02-10", 121.0), ("2026-02-25", 108.9)]
        rows = analytics.monthly_returns(curve)
        self.assertEqual([r["month"] for r in rows], ["2026-01", "2026-02"])
        self.assertAlmostEqual(rows[0]["return_pct"], 10.0, places=4)
        self.assertAlmostEqual(rows[1]["start_equity"], 110.0, places=2)
        self.assertAlmostEqual(rows[1]["return_pct"], -1.0, places=4)
        self.assertEqual(rows[0]["sessions"], 2)
        # Compounding the monthly returns reproduces the total return.
        compounded = 1.0
        for r in rows:
            compounded *= 1.0 + r["return_pct"] / 100.0
        self.assertAlmostEqual(compounded, 108.9 / 100.0, places=6)


class TestRoundTrips(unittest.TestCase):
    """The trade ledger must never drop a fill or invent P&L."""

    def _fill(self, symbol, side, qty, price, date, fees=0.0):
        from test_portfolio import mk_fill
        f = mk_fill(symbol, side, qty, price, date=date)
        f.commission = fees
        return f

    def test_single_round_trip(self):
        fills = [self._fill("SPY", "buy", 100, 100.0, "2026-01-05"),
                 self._fill("SPY", "sell", 100, 110.0, "2026-02-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual(len(trips), 1)
        t = trips[0]
        self.assertEqual(t["status"], "closed")
        self.assertAlmostEqual(t["gross_pnl"], 1_000.0, places=6)
        self.assertAlmostEqual(t["return_on_risk_pct"], 10.0, places=4)
        self.assertEqual(t["direction"], "long")

    def test_open_position_is_emitted_not_dropped(self):
        fills = [self._fill("SPY", "buy", 100, 100.0, "2026-01-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["status"], "open")
        self.assertEqual(trips[0]["exit_date"], "OPEN")
        self.assertEqual(trips[0]["quantity"], 100)

    def test_flip_through_zero_keeps_the_residual_leg(self):
        """IR-22 regression: long 100 then sell 300 must leave a short 200."""
        fills = [self._fill("NVDA", "buy", 100, 180.0, "2026-01-05"),
                 self._fill("NVDA", "sell", 300, 190.0, "2026-03-05")]
        trips = analytics.round_trips(fills)
        closed = [t for t in trips if t["status"] == "closed"]
        opened = [t for t in trips if t["status"] == "open"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["direction"], "long")
        self.assertEqual(closed[0]["quantity"], 100)
        self.assertAlmostEqual(closed[0]["gross_pnl"], 1_000.0, places=6)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["direction"], "short")
        self.assertEqual(opened[0]["quantity"], 200)
        self.assertAlmostEqual(opened[0]["entry_price"], 190.0, places=6)
        # Every share that was filled appears in exactly one trip.
        bought = sum(t["quantity"] for t in trips if t["direction"] == "long")
        sold = sum(t["quantity"] for t in trips if t["direction"] == "short")
        self.assertEqual(bought, 100)
        self.assertEqual(sold, 200)
        self.assertEqual(bought - sold, -100, "net position must be short 100")

    def test_average_cost_blending(self):
        fills = [self._fill("SPY", "buy", 100, 100.0, "2026-01-05"),
                 self._fill("SPY", "buy", 100, 110.0, "2026-01-06"),
                 self._fill("SPY", "sell", 200, 120.0, "2026-02-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual(len(trips), 1)
        self.assertAlmostEqual(trips[0]["entry_price"], 105.0, places=6)
        self.assertAlmostEqual(trips[0]["gross_pnl"], 3_000.0, places=6)

    def test_partial_exits_emit_one_trip_each_and_conserve_pnl(self):
        fills = [self._fill("SPY", "buy", 300, 100.0, "2026-01-05"),
                 self._fill("SPY", "sell", 100, 110.0, "2026-02-05"),
                 self._fill("SPY", "sell", 100, 90.0, "2026-03-05"),
                 self._fill("SPY", "sell", 100, 120.0, "2026-04-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual(len([t for t in trips if t["status"] == "closed"]), 3)
        total = sum(t["gross_pnl"] for t in trips)
        self.assertAlmostEqual(total, 100 * (110 - 100) + 100 * (90 - 100) +
                               100 * (120 - 100), places=6)

    def test_fees_are_netted_into_net_pnl(self):
        fills = [self._fill("SPY", "buy", 100, 100.0, "2026-01-05", fees=5.0),
                 self._fill("SPY", "sell", 100, 110.0, "2026-02-05", fees=7.0)]
        trips = analytics.round_trips(fills)
        self.assertAlmostEqual(trips[0]["fees"], 12.0, places=6)
        self.assertAlmostEqual(trips[0]["net_pnl"], 988.0, places=6)

    def test_rejected_and_expired_rows_are_ignored(self):
        good = self._fill("SPY", "buy", 100, 100.0, "2026-01-05")
        bad = self._fill("SPY", "buy", 100, 100.0, "2026-01-06")
        bad.filled_qty = 0
        bad.status = "rejected"
        trips = analytics.round_trips([good, bad])
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["quantity"], 100)

    def test_short_round_trip_pnl_sign(self):
        fills = [self._fill("RIVN", "sell", 100, 12.0, "2026-01-05"),
                 self._fill("RIVN", "buy", 100, 10.0, "2026-02-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual(trips[0]["direction"], "short")
        self.assertAlmostEqual(trips[0]["gross_pnl"], 200.0, places=6)

    def test_symbols_are_tracked_independently(self):
        fills = [self._fill("SPY", "buy", 100, 100.0, "2026-01-05"),
                 self._fill("NVDA", "buy", 10, 180.0, "2026-01-05"),
                 self._fill("SPY", "sell", 100, 105.0, "2026-02-05")]
        trips = analytics.round_trips(fills)
        self.assertEqual({t["symbol"] for t in trips}, {"SPY", "NVDA"})
        self.assertEqual(len([t for t in trips if t["status"] == "closed"]), 1)
        self.assertEqual(len([t for t in trips if t["status"] == "open"]), 1)


class TestHoldingPeriods(unittest.TestCase):
    def _trips(self):
        return [
            {"symbol": "A", "entry_date": "2026-01-05", "exit_date": "2026-01-09",
             "status": "closed", "sessions_held": None},
            {"symbol": "A", "entry_date": "2026-01-05", "exit_date": "2026-01-16",
             "status": "closed", "sessions_held": None},
            {"symbol": "B", "entry_date": "2026-01-12", "exit_date": "OPEN",
             "status": "open", "sessions_held": None},
        ]

    def test_holding_days_uses_the_session_index_not_calendar_days(self):
        di = {"2026-01-05": 0, "2026-01-09": 4, "2026-01-12": 5, "2026-01-16": 8}
        trips = analytics.holding_days(self._trips(), di)
        self.assertEqual(trips[0]["sessions_held"], 4)
        self.assertEqual(trips[1]["sessions_held"], 8)
        self.assertIsNone(trips[2]["sessions_held"], "open trips have no holding period")

    def test_exposure_union_does_not_double_count_overlapping_tranches(self):
        di = {"2026-01-05": 0, "2026-01-09": 4, "2026-01-12": 5, "2026-01-16": 8}
        trips = analytics.holding_days(self._trips(), di)
        exp = analytics.market_exposure_sessions(trips, di)
        self.assertEqual(exp["sum_of_tranche_sessions"], 12)
        self.assertEqual(exp["sessions_in_market"], 9)   # A: 0..8, B: open 5..8
        self.assertTrue(exp["tranche_intervals_overlap"])
        self.assertEqual(exp["per_symbol_sessions_in_market"]["A"], 9)

    def test_disjoint_trades_do_not_overlap(self):
        di = {"2026-01-05": 0, "2026-01-06": 1, "2026-01-08": 3, "2026-01-09": 4}
        trips = [{"symbol": "A", "entry_date": "2026-01-05", "exit_date": "2026-01-06",
                  "status": "closed", "sessions_held": None},
                 {"symbol": "A", "entry_date": "2026-01-08", "exit_date": "2026-01-09",
                  "status": "closed", "sessions_held": None}]
        trips = analytics.holding_days(trips, di)
        exp = analytics.market_exposure_sessions(trips, di)
        self.assertEqual(exp["sessions_in_market"], exp["sum_of_tranche_sessions"] + 2)
        self.assertFalse(exp["tranche_intervals_overlap"])


class TestTradeStatistics(unittest.TestCase):
    def _stats(self, pnls):
        trips = [{"symbol": "A", "status": "closed", "net_pnl": p,
                  "sessions_held": 5, "entry_date": "2026-01-05",
                  "exit_date": "2026-01-12", "direction": "long"} for p in pnls]
        return analytics.trade_statistics(trips)

    def test_win_rate_profit_factor_and_expectancy(self):
        s = self._stats([100.0, -50.0, 200.0, -25.0])
        self.assertEqual(s["closed_trades"], 4)
        self.assertAlmostEqual(s["win_rate_pct"], 50.0, places=3)
        self.assertAlmostEqual(s["profit_factor"], 300.0 / 75.0, places=4)
        self.assertAlmostEqual(s["expectancy_usd_per_trade"], 56.25, places=4)
        self.assertAlmostEqual(s["avg_win_usd"], 150.0, places=4)
        self.assertAlmostEqual(s["avg_loss_usd"], -37.5, places=4)

    def test_no_losing_trades_gives_a_none_profit_factor_not_infinity(self):
        """JSON cannot carry infinity, so the sentinel is None and the site
        renders it as an infinity sign with an explanation."""
        s = self._stats([100.0, 50.0])
        self.assertIsNone(s["profit_factor"])
        self.assertAlmostEqual(s["win_rate_pct"], 100.0, places=3)
        self.assertEqual(s["avg_loss_usd"], 0.0)

    def test_streaks(self):
        s = self._stats([1.0, 2.0, 3.0, -1.0, 4.0, -2.0, -3.0])
        self.assertEqual(s["longest_win_streak"], 3)
        self.assertEqual(s["longest_loss_streak"], 2)

    def test_empty_tape(self):
        s = analytics.trade_statistics([])
        self.assertEqual(s["closed_trades"], 0)
        self.assertEqual(s["win_rate_pct"], 0.0)
        self.assertIsNone(s["profit_factor"])
        self.assertIsNone(s["best_trade"])


class TestOlsAlignment(unittest.TestCase):
    """IR-23 regression: the market series must start at t0+1, like the curve."""

    def test_beta_of_a_synthetic_2x_book_is_two(self):
        md = replay()
        t0, t1 = md.first_competition_index, len(md.dates)
        market = analytics.market_report(md, t0, t1)
        factors = analytics.factor_report(md, t0, t1)
        participant = _synthetic_participant(md, t0, t1, leverage=2.0)
        report = analytics.performance_report(participant, md, t0, t1,
                                              config.STARTING_CASH, market, factors)
        rel = report["market_relation"]
        self.assertAlmostEqual(rel["beta"], 2.0, delta=0.02)
        self.assertAlmostEqual(rel["r_squared"], 1.0, delta=0.005)
        self.assertLess(abs(rel["alpha_annual_pct"]), 1.0)
        self.assertAlmostEqual(rel["beta_contribution_pct"],
                               2.0 * market["spx_return_pct"], delta=0.5)

    def test_a_market_neutral_book_measures_near_zero_beta(self):
        md = replay()
        t0, t1 = md.first_competition_index, len(md.dates)
        market = analytics.market_report(md, t0, t1)
        factors = analytics.factor_report(md, t0, t1)
        flat = _synthetic_participant(md, t0, t1, leverage=0.0)
        report = analytics.performance_report(flat, md, t0, t1,
                                              config.STARTING_CASH, market, factors)
        self.assertAlmostEqual(report["market_relation"]["beta"], 0.0, delta=0.02)
        self.assertAlmostEqual(report["total_return_pct"], 0.0, places=4)

    def test_a_one_session_shift_would_destroy_the_estimate(self):
        """The failure mode the alignment fix removed: pair each equity return
        with the PREVIOUS session's market return and beta collapses."""
        md = replay()
        t0, t1 = md.first_competition_index, len(md.dates)
        curve = _levered_curve(md, t0, t1, 2.0)
        rets = analytics.daily_returns(curve)
        aligned = [md.spx[t] / md.spx[t - 1] - 1.0 for t in range(t0 + 1, t1)]
        shifted = [md.spx[t] / md.spx[t - 1] - 1.0 for t in range(t0, t1 - 1)]
        self.assertEqual(len(rets), len(aligned))
        good = analytics._ols(rets, aligned)
        bad = analytics._ols(rets, shifted)
        self.assertAlmostEqual(good["beta"], 2.0, delta=0.02)
        self.assertLess(abs(bad["beta"]), 0.5,
                        "a one-session shift should visibly break the estimate")
        self.assertGreater(good["r_squared"], bad["r_squared"])


def _levered_curve(md, t0: int, t1: int, leverage: float):
    equity = config.STARTING_CASH
    curve = [(md.dates[t0], equity)]
    for t in range(t0 + 1, t1):
        equity *= 1.0 + leverage * (md.spx[t] / md.spx[t - 1] - 1.0)
        curve.append((md.dates[t], equity))
    return curve


def _synthetic_participant(md, t0: int, t1: int, leverage: float) -> dict:
    """A participant record shaped exactly like the engine's, with no trades."""
    curve = _levered_curve(md, t0, t1, leverage)
    spec = strategies.build_roster()[0].spec
    marks = []
    for date, equity in curve:
        gross = leverage * equity
        marks.append({"date": date, "equity": round(equity, 2), "cash": 0.0,
                      "gross_exposure": gross, "net_exposure": gross,
                      "leverage": leverage, "realized_gross": 0.0,
                      "unrealized": 0.0, "open_positions": 1 if leverage else 0,
                      "return_pct": 0.0})
    closes = {md.dates[t]: {s: md.bar(s, t).close for s in md.symbols}
              for t in range(t0, t1)}
    return {
        "username": spec.username,
        "strategy_id": spec.username,
        "archetype": spec.archetype,
        "spec": _spec_dict(spec),
        "account_summary": {"dividends_received": 0.0, "borrow_fees_paid": 0.0,
                            "margin_calls": [], "day_trades": 0},
        "equity_curve": curve,
        "fills": [],
        "orders": [],
        "marks": marks,
        "closes_by_date": closes,
        "final_marks": closes[md.dates[t1 - 1]],
        "final_positions": {},
    }


def _spec_dict(spec) -> dict:
    from dataclasses import asdict
    d = asdict(spec)
    d["factor_exposure"] = spec.factor_exposure   # asdict drops properties
    return d


class TestRealRunReports(unittest.TestCase):
    """Consistency checks over the run that is actually published."""

    @classmethod
    def setUpClass(cls):
        cls.store = memory.MemoryStore(os.path.join(REPO_ROOT, "memory"))
        cls.run_id = "season1-primary-seed20260917"
        if not os.path.isdir(cls.store.run_dir(cls.run_id)):
            raise unittest.SkipTest("run memory not built; run `python3 -m sim.cli run`")
        cls.reports = cls.store.reports(cls.run_id)
        cls.lb = cls.store.load(cls.run_id, "leaderboard.json")["leaderboard"]

    def test_every_participant_has_a_complete_report(self):
        self.assertEqual(len(self.reports), 20)
        required = ["username", "strategy_id", "archetype", "starting_cash",
                    "final_equity", "net_pnl_usd", "total_return_pct", "sessions",
                    "risk", "market_relation", "trades", "costs",
                    "implementation_shortfall", "carry", "exposure", "turnover",
                    "contribution_by_symbol", "monthly_returns",
                    "pnl_decomposition", "narrative"]
        for r in self.reports:
            for key in required:
                self.assertIn(key, r, f"{r['username']} is missing {key}")
            self.assertEqual(r["sessions"], 251)
            self.assertEqual(r["starting_cash"], config.STARTING_CASH)

    def test_return_and_pnl_are_mutually_consistent(self):
        for r in self.reports:
            self.assertAlmostEqual(r["net_pnl_usd"],
                                   r["final_equity"] - r["starting_cash"], places=2)
            self.assertAlmostEqual(r["total_return_pct"],
                                   100.0 * r["net_pnl_usd"] / r["starting_cash"],
                                   places=3)

    def test_pnl_decomposition_residual_is_small(self):
        """IR-22 regression: an unexplained residual used to be $20,169."""
        for r in self.reports:
            dec = r["pnl_decomposition"]
            residual = dec["unexplained_residual_usd"]
            # With explicit cash costs allocated to the round trips that earned
            # them, the decomposition closes to the cent: the residual is
            # storage rounding only.
            tol = max(1.0, 0.0001 * abs(r["net_pnl_usd"]))
            self.assertLessEqual(abs(residual), tol,
                                 f"{r['username']}: residual {residual:,.2f}")
            # The named buckets have to add up to the equity change, residual
            # included.  This iterates analytics.DECOMPOSITION_BUCKETS rather
            # than listing the keys, because a hardcoded list is how this test
            # managed to keep passing while silently ignoring the
            # dividends_in_lieu_usd bucket that the short-side manufactured
            # dividend added - it was summing four of five buckets and calling
            # the missing one "the residual" (see IR-31).  borrow_fees_usd and
            # the in-lieu bucket are stored as negative numbers.
            explained = sum(dec[b] for b in DECOMPOSITION_BUCKETS)
            self.assertAlmostEqual(r["starting_cash"] + explained + residual,
                                   r["final_equity"], places=2)
            self.assertIn("dividends_in_lieu_usd", DECOMPOSITION_BUCKETS,
                          "a published bucket must be inside the identity")
            # Reported as the negative of the all-in execution cost, so it is
            # positive for a participant that earned net rebates.
            self.assertAlmostEqual(dec["execution_costs_already_netted_usd"],
                                   -r["costs"]["total_cost_usd"], places=2)
            self.assertAlmostEqual(dec["total_net_pnl_usd"], r["net_pnl_usd"], places=2)

    def test_cost_report_adds_up_and_separates_drift(self):
        for r in self.reports:
            c = r["costs"]
            # Each component is rounded to cents for storage, so allow a cent
            # of slack on the total.
            # rebate_usd is already stored as a negative (a credit).
            self.assertAlmostEqual(
                c["total_cost_usd"],
                c["spread_cost_usd"] + c["depth_cost_usd"] + c["impact_cost_usd"] +
                c["commission_usd"] + c["exchange_fee_usd"] + c["regulatory_fee_usd"] +
                c["rebate_usd"], delta=0.02)
            self.assertAlmostEqual(c["total_cost_pct_of_starting_cash"],
                                   100.0 * c["total_cost_usd"] / config.STARTING_CASH,
                                   delta=1e-4)
            split = c["market_vs_liquidity_split"]
            if c["net_liquidity_provider"]:
                self.assertLessEqual(c["total_cost_usd"], 0.0)
                self.assertIsNone(split["spread_and_impact_pct"],
                                  "percentages of a negative total must not be reported")
                self.assertIsNone(split["explicit_fees_pct"])
            else:
                self.assertAlmostEqual(split["spread_and_impact_pct"] +
                                       split["explicit_fees_pct"], 100.0, delta=0.5)

    def test_implementation_shortfall_reconciles_with_the_fills(self):
        for r in self.reports:
            isf = r["implementation_shortfall"]
            if not isf.get("orders"):
                continue
            friction = (isf["spread_cost_usd"] + isf["depth_cost_usd"] +
                        isf["impact_cost_usd"] + isf["fees_usd"])
            self.assertAlmostEqual(
                isf["total_shortfall_usd"],
                friction + isf["intraday_drift_usd"] + isf["missed_trade_cost_usd"],
                delta=0.05)
            self.assertAlmostEqual(isf["friction_only_bps_of_paper"],
                                   10_000.0 * friction / isf["paper_notional_usd"],
                                   delta=0.02)
            self.assertGreaterEqual(isf["paper_notional_usd"], 0.0)
            # Drift is reported separately from friction, so it can be negative
            # (a favourable move) without making execution look free.
            self.assertIn("drift", isf["note"])

    def test_symbol_contribution_sums_to_the_pnl_it_claims_to_explain(self):
        for r in self.reports:
            rows = r["contribution_by_symbol"]
            self.assertTrue(rows)
            total = sum(x["total_pnl"] for x in rows)
            dec = r["pnl_decomposition"]
            self.assertAlmostEqual(total,
                                   dec["realized_trading_pnl_usd"] +
                                   dec["open_position_pnl_usd"],
                                   delta=max(250.0, 0.005 * abs(total) + 1.0),
                                   msg=r["username"])
            for x in rows:
                self.assertAlmostEqual(x["total_pnl"],
                                       x["realized_pnl"] + x["open_pnl"], places=2)
                self.assertIn("all_in_cost_usd", x)
                self.assertTrue(math.isfinite(x["fees"]))
                # Explicit cash fees can be negative for a net liquidity
                # provider (rebates exceed fees), but the all-in cost must then
                # still be reported.
                if x["fees"] < 0:
                    self.assertLess(x["all_in_cost_usd"], 0.0)
            self.assertEqual(rows, sorted(rows, key=lambda x: -x["total_pnl"]),
                               "contributions should be ordered best to worst")

    def test_beta_and_r_squared_are_plausible_for_the_declared_archetypes(self):
        by = {r["username"]: r for r in self.reports}
        levered = by["@BetaChaser_3xProxy"]["market_relation"]
        self.assertGreater(levered["beta"], 2.0)
        self.assertGreater(levered["r_squared"], 0.3)
        pairs = by["@PairsArb_ZScore2"]["market_relation"]
        self.assertLess(abs(pairs["beta"]), 0.75)
        hold = by["@BuyHold_MaxBeta"]["market_relation"]
        self.assertGreater(hold["beta"], 1.0)

    def test_leaderboard_is_sorted_and_numbered_from_the_reports(self):
        rebuilt = analytics.leaderboard(self.reports, "total_return_pct")
        self.assertEqual([r["username"] for r in rebuilt],
                         [r["username"] for r in self.lb])
        self.assertEqual([r["rank"] for r in rebuilt], list(range(1, 21)))
        returns = [r["total_return_pct"] for r in rebuilt]
        self.assertEqual(returns, sorted(returns, reverse=True))
        self.assertEqual(rebuilt[0]["username"], PUBLISHED_TOP_USERNAME)
        self.assertAlmostEqual(rebuilt[0]["total_return_pct"],
                               PUBLISHED_TOP_RETURN_PCT, delta=0.02)

    def test_market_report_matches_the_real_index(self):
        md = replay()
        t0, t1 = md.first_competition_index, len(md.dates)
        m = analytics.market_report(md, t0, t1)
        self.assertAlmostEqual(m["spx_return_pct"], 14.415, places=2)
        self.assertAlmostEqual(m["max_drawdown"]["max_drawdown_pct"], -9.10, delta=0.02)
        self.assertEqual(m["window"]["sessions"], 251)
        self.assertEqual(m["window"]["start"], "2025-09-17")
        self.assertEqual(m["window"]["end"], "2026-09-16")
        # Returns are measured between sessions, so 251 sessions give 250
        # classified moves; the first session has no prior close in the window.
        self.assertEqual(m["up_sessions"] + m["down_sessions"], 250)
        self.assertAlmostEqual(m["vix"]["max"], 31.05, places=2)
        self.assertEqual(m["vix"]["max_date"], "2026-03-27")

    def test_factor_report_has_all_six_factors_with_full_statistics(self):
        md = replay()
        t0, t1 = md.first_competition_index, len(md.dates)
        f = analytics.factor_report(md, t0, t1)
        for name in ("market", "momentum", "reversal", "beta", "low_vol", "liquidity"):
            self.assertIn(name, f)
            row = f[name]
            for key in ("factor", "description", "cumulative_return_pct",
                        "mean_daily_bps", "annualised_vol_pct", "sharpe"):
                self.assertIn(key, row, f"{name} is missing {key}")
            self.assertTrue(row["description"].strip())
            for value in row.values():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value), f"{name}: non-finite value")
        self.assertAlmostEqual(f["market"]["cumulative_return_pct"], 14.415, delta=0.05)

    def test_narratives_answer_why_and_carry_the_ir_16_caveat(self):
        for r in self.reports:
            n = r["narrative"]
            self.assertIn(n["verdict"], ("beat the market",
                                         "roughly matched the market",
                                         "made money but lagged the index",
                                         "lost money"))
            self.assertTrue(n["clauses"])
            kinds = {c["kind"] for c in n["clauses"]}
            self.assertIn("attribution", kinds, f"{r['username']}: no attribution clause")
            for clause in n["clauses"]:
                text = clause["text"]
                self.assertTrue(text.strip())
                self.assertNotRegex(text, r"(?i)\bnan\b|\binf\b|\bnull\b")
                # "None of the failure modes ..." is a legitimate sentence;
                # a bare None where a number belongs is not.
                self.assertNotRegex(text, r"(?<!\w)None(?!\s+of\b)")
                self.assertNotRegex(text, r"\{[a-z_]+\}")
            caveats = " ".join(c["text"] for c in n["clauses"]
                               if c["kind"] == "caveat")
            self.assertIn("IR-16", caveats,
                          f"{r['username']}: missing the declared-priors caveat")
            self.assertTrue(n["prose"])
            for para in n["prose"]:
                self.assertTrue(para["title"].strip())
                self.assertGreater(len(para["text"]), 40)
                self.assertNotIn("{", para["text"])
                self.assertNotRegex(para["text"], r"(?i)\bnan\b|\binf\b|\bnull\b")
            titles = [p_["title"] for p_ in n["prose"]]
            self.assertTrue(any(t.startswith("Answer:") for t in titles),
                            f"{r['username']}: the post-mortem must answer the question")
            self.assertTrue(any("Why" in t for t in titles),
                            f"{r['username']}: the post-mortem must explain why")

    def test_narrative_verdicts_are_consistent_with_the_numbers(self):
        """The verdict bands are the ones build_narrative declares."""
        mkt = 14.415
        for r in self.reports:
            verdict, ret = r["narrative"]["verdict"], r["total_return_pct"]
            if verdict == "beat the market":
                self.assertGreater(ret, mkt + 5.0)
            elif verdict == "roughly matched the market":
                self.assertGreater(ret, 0.0)
                self.assertGreaterEqual(ret, mkt - 5.0)
            elif verdict == "made money but lagged the index":
                self.assertGreater(ret, 0.0)
                self.assertLess(ret, mkt - 5.0)
            elif verdict == "lost money":
                self.assertLessEqual(ret, 0.0)
            else:
                self.fail(f"unknown verdict {verdict!r}")

    def test_failure_mode_detection_reports_what_fired_and_what_did_not(self):
        fired_any = False
        for r in self.reports:
            n = r["narrative"]
            self.assertIn("failure_modes_fired", n)
            self.assertIn("failure_modes_untested", n)
            self.assertIn("structural_caveats", n)
            fired_any = fired_any or bool(n["failure_modes_fired"])
            for text in n["failure_modes_fired"] + n["failure_modes_untested"]:
                self.assertTrue(text.strip())
                self.assertNotIn("UNTESTED", text,
                                 "untestable modes must not be listed as fired")
            kinds = {c["kind"] for c in n["clauses"]}
            self.assertIn("failure_mode", kinds)
            if n["failure_modes_untested"]:
                self.assertIn("failure_mode_untested", kinds)
        self.assertTrue(fired_any, "no declared failure mode fired anywhere: "
                                   "the detector is probably not wired up")


class TestRobustnessPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = memory.MemoryStore(os.path.join(REPO_ROOT, "memory"))
        cls.scenarios = [r for r in cls.store.runs() if r.get("run_id", "").startswith("season1")]

    def test_panel_covers_every_participant_and_every_scenario(self):
        per = self._scenarios()
        panel = analytics.robustness_panel(per)
        rows = panel["by_participant"]
        self.assertEqual(len(rows), 20)
        self.assertIn("note", panel)
        for row in rows:
            self.assertEqual(row["scenarios"], len(self.scenarios))
            self.assertLessEqual(row["worst_return_pct"], row["median_return_pct"])
            self.assertLessEqual(row["median_return_pct"], row["best_return_pct"])
            self.assertLessEqual(row["mean_return_pct"], row["best_return_pct"])
            self.assertGreaterEqual(row["mean_return_pct"], row["worst_return_pct"])
            self.assertGreaterEqual(row["stdev_pp"], 0.0)
            vals = list(row["returns_by_scenario_pct"].values())
            self.assertEqual(len(vals), len(self.scenarios))
            self.assertEqual(row["positive_scenarios"], len([v for v in vals if v > 0]))
            self.assertAlmostEqual(row["mean_return_pct"],
                                   sum(vals) / len(vals), places=2)
            self.assertAlmostEqual(row["best_return_pct"], max(vals), places=3)
            self.assertAlmostEqual(row["worst_return_pct"], min(vals), places=3)
            self.assertEqual(len(row["index_by_scenario_pct"]), len(vals))

    def _scenarios(self):
        """robustness_panel wants {seed, reports, market} per scenario."""
        out = []
        for run in self.scenarios:
            rid = run["run_id"]
            lb = self.store.load(rid, "leaderboard.json")["leaderboard"]
            market = self.store.load(rid, "market_report.json") or {}
            out.append({"run_id": rid, "seed": run.get("seed"),
                        "reports": lb, "market": market})
        return out

    def test_panel_ranks_and_flags_match_the_underlying_returns(self):
        panel = analytics.robustness_panel(self._scenarios())
        by = {r["username"]: r for r in panel["by_participant"]}
        gap = by["@GapAndGo_YOLO"]
        self.assertEqual(gap["positive_scenarios"], 0)
        self.assertLess(gap["mean_return_pct"], 0.0)
        timer = by["@VIXRegime_Timer"]
        self.assertEqual(timer["scenarios"], 6)
        self.assertLess(timer["stdev_pp"], 10.0)

    def test_panel_is_json_serialisable(self):
        json.dumps(analytics.robustness_panel(self._scenarios()))

    def test_beat_index_count_uses_each_scenario_own_index_return(self):
        per = self._scenarios()
        panel = analytics.robustness_panel(per)
        row = next(r for r in panel["by_participant"]
                   if r["username"] == "@BetaChaser_3xProxy")
        expected = sum(1 for seed, v in row["returns_by_scenario_pct"].items()
                       if v > row["index_by_scenario_pct"][seed])
        self.assertEqual(row["beat_index_scenarios"], expected)


if __name__ == "__main__":
    unittest.main()
