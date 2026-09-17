"""End-to-end smoke test of the competition engine.

Runs a short season (~3 months) over two market-data realisations into a
throwaway memory root and checks the things the whole project depends on:
the run completes, every participant gets a report with a written verdict, the
books are flat at the end, the ledger closes, the memory verifies, and the same
seed reproduces the same result bit for bit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from fixtures import REPO_ROOT, cfg, short_replay

from sim import config, engine, memory, microstructure, strategies

SEEDS = (7, 8)
# The four-value verdict vocabulary (analytics.build_narrative).
VERDICTS = ("beat the market", "roughly matched the market",
            "made money but lagged the index", "lost money")
RUN_IDS = [f"smoke-seed{s}" for s in SEEDS]


class TestEngineSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sps-engine-")
        cls.c = cfg(end="2025-12-31", seed=SEEDS[0])
        cls.md = {s: short_replay(s) for s in SEEDS}
        cls.t_start = time.time()
        cls.records = engine.run_scenarios(cls.c, SEEDS, cls.md, root=cls.tmp,
                                           run_ids=RUN_IDS)
        cls.elapsed = time.time() - cls.t_start
        cls.rec = cls.records[0]
        cls.store = memory.MemoryStore(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------- helpers

    def reports(self, rec=None):
        """``record['reports']`` is a list; analysts want it keyed by username."""
        rec = rec or self.rec
        return {r["username"]: r for r in rec["reports"]}

    # -------------------------------------------------------------- record

    def test_the_run_completes_quickly(self):
        self.assertLess(self.elapsed, 120.0,
                        f"two short scenarios took {self.elapsed:.1f}s")
        self.assertEqual(len(self.records), 2)

    def test_record_carries_every_block(self):
        for key in ("seed", "config", "config_fingerprint", "market", "factors",
                    "reports", "leaderboard", "irregularities", "provenance",
                    "timing", "winner", "run_id", "scenario_index",
                    "participant_count", "session_count"):
            self.assertIn(key, self.rec, f"record is missing {key}")
        self.assertEqual(self.rec["participant_count"], 20)
        self.assertEqual(self.rec["session_count"],
                         self.rec["market"]["window"]["sessions"])
        self.assertEqual(self.rec["seed"], SEEDS[0])
        self.assertEqual(self.rec["scenario_index"], 0)
        self.assertEqual(self.rec["config_fingerprint"], self.c.fingerprint())

    def test_leaderboard_is_ordered_by_total_return(self):
        board = self.rec["leaderboard"]
        self.assertEqual(len(board), 20)
        returns = [row["total_return_pct"] for row in board]
        self.assertEqual(returns, sorted(returns, reverse=True))
        self.assertEqual([row["rank"] for row in board], list(range(1, 21)))
        self.assertEqual(board[0]["username"], self.rec["winner"])
        expected = {"username", "strategy_id", "archetype", "rank",
                    "total_return_pct", "net_pnl_usd", "final_equity",
                    "max_drawdown_pct", "sharpe", "sortino", "win_rate_pct",
                    "profit_factor", "closed_trades", "verdict", "beta",
                    "alpha_annual_pct", "execution_cost_pct", "turnover_x",
                    "margin_calls"}
        for row in board:
            self.assertTrue(expected <= set(row),
                            f"leaderboard row is thin: {sorted(expected - set(row))}")
            self.assertIn(row["verdict"], VERDICTS)
            self.assertAlmostEqual(row["final_equity"],
                                   config.CompetitionConfig().starting_cash
                                   * (1 + row["total_return_pct"] / 100.0),
                                   delta=1.0)

    def test_every_participant_has_a_full_report(self):
        reports = self.reports()
        self.assertEqual(len(reports), 20)
        self.assertEqual(set(reports), {p.spec.username
                                        for p in strategies.build_roster()})
        for user, rep in reports.items():
            self.assertEqual(rep["username"], user)
            narr = rep["narrative"]
            self.assertEqual(set(narr), {"headline", "prose", "verdict", "clauses",
                                         "factors_considered",
                                         "failure_modes_fired",
                                         "failure_modes_untested",
                                         "structural_caveats"}, user)
            self.assertIn(narr["verdict"], VERDICTS, user)
            self.assertIsInstance(narr["prose"], list)
            self.assertGreaterEqual(len(narr["prose"]), 3, user)
            for para in narr["prose"]:
                self.assertEqual(set(para), {"title", "text"}, user)
                self.assertGreater(len(para["title"]), 5, user)
                self.assertGreater(len(para["text"]), 40, user)
            titles = [para["title"] for para in narr["prose"]]
            self.assertTrue(any(t.startswith("Answer") for t in titles),
                            f"{user}: no 'Answer:' section in {titles}")
            self.assertTrue(any("Why" in t for t in titles),
                            f"{user}: no 'Why' section in {titles}")
            self.assertGreater(sum(len(p_["text"]) for p_ in narr["prose"]), 800, user)
            self.assertGreater(len(narr["headline"]), 20, user)
            self.assertGreater(len(narr["clauses"]), 3, user)
            for cl in narr["clauses"]:
                self.assertEqual(set(cl), {"kind", "text"}, user)
            self.assertEqual(rep["errors"], [], f"{user} raised errors")
            self.assertGreater(len(rep["contribution_by_symbol"]), 0, user)
            self.assertGreater(len(rep["monthly_returns"]), 0, user)
            for block in ("pnl_decomposition", "trades", "risk", "exposure",
                          "costs", "carry", "market_relation",
                          "implementation_shortfall", "turnover"):
                self.assertIn(block, rep, f"{user} is missing {block}")
            self.assertAlmostEqual(rep["starting_cash"],
                                   config.CompetitionConfig().starting_cash,
                                   delta=1e-6)
            self.assertEqual(rep["sessions"], self.rec["session_count"])

    def test_all_books_are_liquidated_at_the_season_end(self):
        for user, rep in self.reports().items():
            self.assertEqual(rep["trades"]["open_trades"], 0,
                             f"{user} still holds positions at the end")
            self.assertAlmostEqual(rep["pnl_decomposition"]["open_position_pnl_usd"],
                                   0.0, delta=0.01, msg=user)
            self.assertAlmostEqual(rep["final_equity"],
                                   rep["starting_cash"] + rep["net_pnl_usd"],
                                   delta=0.01, msg=user)

    def test_the_ledger_closes_for_every_participant(self):
        worst_user, worst = "", 0.0
        for user, rep in self.reports().items():
            d = rep["pnl_decomposition"]
            residual = d["unexplained_residual_usd"]
            if abs(residual) > abs(worst):
                worst_user, worst = user, residual
            self.assertAlmostEqual(
                d["total_net_pnl_usd"],
                d["realized_trading_pnl_usd"] + d["open_position_pnl_usd"]
                + d["dividends_usd"] + d["borrow_fees_usd"] + residual,
                delta=0.02, msg=f"{user} decomposition does not add up")
            self.assertAlmostEqual(d["total_net_pnl_usd"], rep["net_pnl_usd"],
                                   delta=0.02, msg=user)
            # Execution costs are already inside the fill prices.
            self.assertAlmostEqual(d["execution_costs_already_netted_usd"],
                                   -rep["costs"]["total_cost_usd"], delta=0.02,
                                   msg=user)
        self.assertLess(abs(worst), 1.0,
                        f"{worst_user} ledger residual is ${worst:,.2f}")

    def test_market_block_describes_the_window(self):
        m = self.rec["market"]
        self.assertEqual(m["window"]["sessions"], self.rec["session_count"])
        self.assertEqual(m["window"]["start"], self.c.start)
        self.assertEqual(m["window"]["end"], self.c.end)
        for key in ("spx_return_pct", "spx_annualised_vol_pct",
                    "spy_price_return_pct", "vix", "max_drawdown",
                    "drawdown_episodes", "worst_sessions", "best_sessions",
                    "up_sessions", "down_sessions", "cross_section"):
            self.assertIn(key, m)
        self.assertIn("max_date", m["vix"])
        self.assertEqual(m["up_sessions"] + m["down_sessions"],
                         m["window"]["sessions"] - 1)
        cs = m["cross_section"]
        self.assertEqual(len(cs["per_symbol_pct"]), 17)
        self.assertGreater(cs["dispersion_stdev_pp"], 0.0)
        # Both lists are in DESCENDING return order, so the worst performer is
        # the LAST entry of `worst`, not the first.
        self.assertAlmostEqual(cs["spread_best_minus_worst_pp"],
                               cs["per_symbol_pct"][cs["best"][0]["symbol"]]
                               - cs["per_symbol_pct"][cs["worst"][-1]["symbol"]],
                               places=2)
        self.assertEqual([r["return_pct"] for r in cs["best"]],
                         sorted((r["return_pct"] for r in cs["best"]), reverse=True))
        self.assertEqual([r["return_pct"] for r in cs["worst"]],
                         sorted((r["return_pct"] for r in cs["worst"]), reverse=True))
        self.assertGreater(cs["best"][-1]["return_pct"], cs["worst"][0]["return_pct"])
        for row in cs["best"] + cs["worst"]:
            self.assertEqual(set(row), {"symbol", "return_pct"})
        for row in m["worst_sessions"] + m["best_sessions"]:
            self.assertEqual(set(row), {"date", "spx_return_pct"})
        self.assertLessEqual(m["max_drawdown"]["max_drawdown_pct"], 0.0)

    def test_factor_block_has_the_six_declared_priors(self):
        factors = self.rec["factors"]
        self.assertEqual(set(factors), {"market", "momentum", "reversal", "beta",
                                        "low_vol", "liquidity"})
        for name, row in factors.items():
            self.assertEqual(set(row), {"factor", "description",
                                        "cumulative_return_pct", "mean_daily_bps",
                                        "annualised_vol_pct", "sharpe"})
            self.assertGreater(len(row["description"]), 20, name)
            self.assertGreater(row["annualised_vol_pct"], 0.0, name)

    def test_provenance_and_irregularities_are_attached(self):
        prov = self.rec["provenance"]
        self.assertEqual(prov["mode"], "real-anchored-replay")
        self.assertTrue(prov["spx_file"].endswith(".csv"))
        self.assertTrue(prov["vix_file"].endswith(".csv"))
        self.assertTrue(prov["spx_url"].startswith("https://fred.stlouisfed.org/"))
        self.assertTrue(prov["vix_url"].startswith("https://fred.stlouisfed.org/"))
        self.assertTrue(prov["holiday_cross_check_url"].startswith("https://"))
        self.assertEqual(prov["sessions"], self.rec["session_count"])
        self.assertEqual(prov["first_session"], self.c.start)
        self.assertEqual(prov["last_session"], self.c.end)
        self.assertIn("SIMULATED", prov["single_name_daily_ohlcv"])
        self.assertGreater(prov["warmup_sessions_simulated"], 0)
        self.assertEqual(len(prov["warmup_dates"]), 2)      # first and last
        self.assertLess(prov["warmup_dates"][0], self.c.start)
        self.assertLess(prov["warmup_dates"][1], self.c.start)
        self.assertIn("monthly_range_audit_spy", prov)
        self.assertIn("real_anchors", prov)
        self.assertLess(prov["spy_ratio_max_abs_pct_error_vs_real_monthly_closes"], 1.0)

        rows = self.rec["irregularities"]
        # These are the RUNTIME flags (pre-trade rejections/clips and anything
        # the simulation itself detects).  The 24-item standing register in
        # research/IRREGULARITIES.json is merged in at publish time, so every
        # runtime id must be a real register id - no invented codes.
        self.assertGreaterEqual(len(rows), 1)
        import json
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "research", "IRREGULARITIES.json"),
                encoding="utf-8") as fh:
            payload = json.load(fh)
            rows_reg = payload["irregularities"] if isinstance(payload, dict) else payload
            register = {r["id"] for r in rows_reg}
        for row in rows:
            self.assertIn(row["id"], register,
                          f"runtime flag {row['id']} is not in the register")
        for row in rows:
            self.assertRegex(row["id"], r"^IR-\d\d$")
            self.assertIn(row["severity"], ("low", "medium", "high"))
            self.assertTrue(row["message"].strip())

    def test_timing_is_recorded(self):
        t = self.rec["timing"]
        self.assertEqual(set(t), {"started_utc", "finished_utc", "elapsed_seconds"})
        self.assertRegex(t["started_utc"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertGreater(t["elapsed_seconds"], 0.0)
        self.assertLess(t["elapsed_seconds"], 600.0)

    # -------------------------------------------------------------- memory

    def test_memory_verifies_for_both_scenarios(self):
        for i, run_id in enumerate(RUN_IDS):
            v = self.store.verify(run_id)
            self.assertTrue(v["intact"], f"{run_id}: {v}")
            man = self.store.load(run_id, "manifest.json")
            self.assertEqual(man["full_event_memory"], i == 0)
            self.assertEqual(man["seed"], SEEDS[i])
            self.assertEqual(man["participant_count"], 20)
            self.assertEqual(man["session_count"], self.rec["session_count"])
            if i == 0:
                self.assertGreater(self.store.stream(run_id, "fills").count(), 100)
                self.assertEqual(self.store.stream(run_id, "equity").count(),
                                 20 * self.rec["session_count"])
                self.assertEqual(self.store.stream(run_id, "quotes").count(),
                                 17 * self.rec["session_count"])
            else:
                # Summary memory only: the leaderboard, not the tape.
                self.assertEqual(self.store.stream(run_id, "fills").count(), 0)
                self.assertEqual(self.store.stream(run_id, "quotes").count(), 0)
            self.assertEqual(len(self.store.load(run_id, "leaderboard.json")
                                 ["leaderboard"]), 20)
            self.assertEqual(len(self.store.reports(run_id)), 20)

    def test_the_two_scenarios_share_the_market_path_but_not_the_outcomes(self):
        a, b = self.records
        self.assertEqual(a["market"]["window"], b["market"]["window"])
        self.assertEqual(a["market"]["spx_return_pct"], b["market"]["spx_return_pct"])
        self.assertEqual(a["market"]["vix"], b["market"]["vix"])
        ra = {r["username"]: r["rank"] for r in a["leaderboard"]}
        rb = {r["username"]: r["rank"] for r in b["leaderboard"]}
        self.assertGreater(sum(1 for u in ra if ra[u] != rb[u]), 0,
                           "two different seeds produced identical ranks")
        self.assertNotEqual([r["total_return_pct"] for r in a["leaderboard"]],
                            [r["total_return_pct"] for r in b["leaderboard"]])

    # -------------------------------------------------------- determinism

    def test_the_same_seed_reproduces_the_same_season(self):
        md = short_replay(SEEDS[0])
        rec_a = engine.CompetitionEngine(self.c, md, seed=SEEDS[0]).run()
        rec_b = engine.CompetitionEngine(self.c, md, seed=SEEDS[0]).run()
        self.assertEqual([r["total_return_pct"] for r in rec_a["leaderboard"]],
                         [r["total_return_pct"] for r in rec_b["leaderboard"]])
        self.assertEqual(rec_a["winner"], rec_b["winner"])
        self.assertEqual(rec_a["irregularities"], rec_b["irregularities"])

    def test_the_season_is_reproducible_across_processes(self):
        """IR-30: two processes, two PYTHONHASHSEEDs, one answer.

        The in-process test above cannot catch this class of bug, because every
        object in one interpreter shares one hash seed.  Three strategies used
        to iterate a *set* of held symbols when building their exit list; set
        iteration order is salted per process, the exit order changes what cash
        and margin the following entries see, and the same seed produced a
        different season in every process - one participant swung from +39.9% to
        +73.6% on a half-tick of nothing.  This test runs the short season in
        two subprocesses with different hash seeds and requires identical
        leaderboards.
        """
        runner = (
            "import hashlib, json, sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "import fixtures\n"
            "from sim import engine\n"
            "c = fixtures.cfg(end='2025-12-31', seed=7)\n"
            "md = fixtures.short_replay(7)\n"
            "rec = engine.CompetitionEngine(c, md, seed=7).run()\n"
            "blob = json.dumps(rec['leaderboard'], sort_keys=True)\n"
            "print(hashlib.sha256(blob.encode()).hexdigest())\n"
            "print(json.dumps([r['total_return_pct'] for r in rec['leaderboard']]))\n"
        ) % (os.path.join(REPO_ROOT, "tests"), REPO_ROOT)
        digests = []
        for hash_seed in ("0", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            out = subprocess.run([sys.executable, "-c", runner], cwd=REPO_ROOT,
                                 env=env, capture_output=True, text=True,
                                 timeout=600)
            self.assertEqual(out.returncode, 0,
                             f"runner failed under PYTHONHASHSEED={hash_seed}: "
                             f"{out.stderr[-800:]}")
            digest, returns = out.stdout.strip().split("\n")
            digests.append((hash_seed, digest, returns))
        self.assertEqual(digests[0][1], digests[1][1],
                         "the leaderboard depends on PYTHONHASHSEED - something "
                         "in the engine iterates a set: "
                         f"{digests[0][2]} vs {digests[1][2]}")

    def test_no_strategy_iterates_a_set(self):
        """Static guard for the same bug: a `for` loop over a set comprehension.

        Dicts are insertion-ordered and safe to iterate; sets are salted per
        process.  Any strategy that loops over a set is one PYTHONHASHSEED away
        from publishing a season nobody can reproduce.
        """
        import ast
        path = os.path.join(REPO_ROOT, "sim", "strategies.py")
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        offenders = []
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            set_names = set()
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and isinstance(
                        node.value, (ast.SetComp, ast.Set)):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            set_names.add(target.id)
            for node in ast.walk(scope):
                if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) \
                        and node.iter.id in set_names:
                    offenders.append(f"{scope.name}:{node.lineno} iterates "
                                     f"'{node.iter.id}'")
        self.assertEqual(offenders, [],
                         "set iteration is not reproducible across processes: "
                         + "; ".join(offenders))

    def test_run_scenarios_defaults_are_sane(self):
        self.assertTrue(memory.DEFAULT_ROOT.endswith("memory"))
        self.assertTrue(os.path.isabs(memory.DEFAULT_ROOT))


class TestEngineControls(unittest.TestCase):
    """Pre-trade controls, quoting, and roster restriction."""

    def setUp(self):
        # A fresh engine per test: _setup() mutates participant accounts, so a
        # shared one would make these tests order-dependent.
        self.c = cfg(end="2025-11-30", seed=11)
        self.md = short_replay(11)
        self.eng = engine.CompetitionEngine(self.c, self.md, seed=11)
        self.eng._setup()
        self.p = self.eng.participants[0]
        self.marks = self.eng.closes_by_date[self.eng.md.dates[self.eng.t0]]

    def order(self, symbol="SPY", side=microstructure.BUY, quantity=10,
              order_type=microstructure.MARKET, **kw):
        return microstructure.Order(symbol=symbol, side=side, quantity=quantity,
                                    order_type=order_type,
                                    participant=self.p.spec.username,
                                    reason="unit test", **kw)

    def test_setup_creates_one_account_per_strategy(self):
        self.assertEqual(len(self.eng.participants), 20)
        self.assertEqual(len({p.spec.username for p in self.eng.participants}), 20)
        for p in self.eng.participants:
            self.assertAlmostEqual(p.account.cash,
                                   config.CompetitionConfig().starting_cash,
                                   delta=1e-6)
            self.assertEqual(p.orders, [])
            self.assertEqual(p.fills, [])
        self.assertEqual(len(self.eng.closes_by_date),
                         self.eng.t1 - self.eng.t0)
        self.assertEqual(len(self.eng.closes_by_date[self.eng.md.dates[self.eng.t0]]),
                         17)

    def test_a_sane_order_passes_the_pre_trade_check(self):
        out = self.eng._risk_check(self.p, self.order(quantity=10), self.marks)
        self.assertIsNotNone(out)
        self.assertEqual(out.quantity, 10)

    def test_an_order_too_large_for_the_account_is_clipped_or_rejected(self):
        out = self.eng._risk_check(self.p, self.order(quantity=1_000_000),
                                   self.marks)
        if out is not None:
            self.assertLess(out.quantity, 1_000_000)
            self.assertIn("clip", out.reason.lower())
        self.assertTrue(out is None or out.quantity < 1_000_000)

    def test_orders_with_no_reference_price_are_rejected(self):
        out = self.eng._risk_check(self.p, self.order(symbol="ZZZZ"), {})
        self.assertIsNone(out)

    def test_a_risk_reducing_order_always_passes(self):
        """Selling a position you already hold must never be blocked."""
        acct = self.p.account
        acct.apply_fill(_fill(self.p.spec.username, "SPY", "buy", 500,
                              self.marks["SPY"]), self.marks)
        self.assertEqual(acct.position("SPY").quantity, 500)
        out = self.eng._risk_check(self.p,
                                   self.order(side=microstructure.SELL,
                                              quantity=500), self.marks)
        self.assertIsNotNone(out, "risk-reducing order was blocked")
        self.assertEqual(out.quantity, 500)

    def test_malformed_orders_are_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            self.order(quantity=0)
        with self.assertRaises(ValueError):
            self.order(quantity=-5)
        with self.assertRaises(ValueError):
            self.order(side="hold")
        with self.assertRaises(ValueError):
            self.order(order_type=microstructure.LIMIT)      # no limit price
        with self.assertRaises(ValueError):
            self.order(order_type=microstructure.STOP)       # no stop price

    def test_opening_quotes_cover_the_universe_with_two_sided_prices(self):
        quotes = self.eng._opening_quotes(self.eng.t0)
        self.assertEqual(len(quotes), 17)
        for sym, q in quotes.items():
            self.assertGreater(q["bid"], 0.0, sym)
            self.assertGreater(q["ask"], q["bid"], sym)
            self.assertGreaterEqual(q["bid_size"], 0, sym)
            self.assertGreaterEqual(q["ask_size"], 0, sym)
            self.assertAlmostEqual(q["mid"], (q["bid"] + q["ask"]) / 2.0,
                                   places=6, msg=sym)
            self.assertAlmostEqual(q["spread"], q["ask"] - q["bid"],
                                   places=6, msg=sym)
            self.assertGreater(q["levels"], 0, sym)
            self.assertGreater(q["sigma_daily"], 0.0, sym)
            self.assertGreater(q["vix_factor"], 0.0, sym)
            self.assertGreaterEqual(q["spread_ticks"], 1, sym)

    def test_quoted_spreads_match_the_rule_612_tick_grid(self):
        """`_opening_quotes` returns reference marks (mid +/- half spread); the
        book itself is what has to land on the Rule 612 grid, and the recorded
        fills prove that it does - see test_memory for the fill-grid check."""
        quotes = self.eng._opening_quotes(self.eng.t0)
        for sym, q in quotes.items():
            tick = config.minimum_tick(q["mid"])
            self.assertIn(tick, (0.01, 0.0001), sym)
            self.assertAlmostEqual(q["spread"], q["spread_ticks"] * tick,
                                   delta=tick, msg=sym)
            self.assertGreaterEqual(q["spread"], tick, msg=sym)

    def test_the_roster_can_be_restricted(self):
        """A subset roster must work (used by the CLI and by future research)."""
        subset = [p for p in strategies.build_roster()
                  if p.spec.username in ("@BuyHold_MaxBeta", "@MomentumMax_12x1")]
        eng = engine.CompetitionEngine(self.c, self.md, seed=11, roster=subset)
        rec = eng.run()
        self.assertEqual(len(rec["leaderboard"]), 2)
        self.assertEqual(len(rec["reports"]), 2)
        self.assertIn(rec["winner"], ("@BuyHold_MaxBeta", "@MomentumMax_12x1"))

    def test_the_engine_flags_a_strategy_that_raises(self):
        """A crashing strategy must be recorded, not silently dropped."""

        spec = strategies.StrategySpec(
            username="@Broken_Test", display_name="Broken Test",
            archetype="momentum", thesis="A test double that always raises.",
            entry_rules=["raise RuntimeError every session"],
            exit_rules=["never"], sizing="n/a", leverage="n/a",
            cadence="daily", horizon="n/a", aggression=3,
            why_return_seeking="It is a test double: it exists to prove that a "
                               "crashing strategy is flagged in memory rather "
                               "than silently dropped from the leaderboard.")

        class Broken(strategies.Strategy):
            def on_day(self, ctx):
                raise RuntimeError("deliberate failure for the test suite")

        Broken.spec = spec
        roster = [Broken()] + [p for p in strategies.build_roster()][:2]
        eng = engine.CompetitionEngine(self.c, self.md, seed=11, roster=roster)
        rec = eng.run()
        broken = {r["username"]: r for r in rec["reports"]}["@Broken_Test"]
        self.assertGreater(len(broken["errors"]), 0)
        self.assertTrue(any("IR-11" == row["id"] or "deliberate failure"
                            in row["message"]
                            for row in rec["irregularities"]),
                        "a strategy crash was not flagged as an irregularity")


def _fill(participant, symbol, side, qty, price):
    """A minimal already-executed fill, for account-state setup only."""
    order = microstructure.Order(symbol=symbol, side=side, quantity=qty,
                                 participant=participant, reason="test setup")
    return microstructure.Fill(
        order=order, date="2025-09-17", filled_qty=qty, requested_qty=qty,
        avg_price=price, decision_price=price, spread_cost=0.0, depth_cost=0.0,
        impact_cost=0.0, commission=0.0, exchange_fee=0.0, regulatory_fee=0.0,
        rebate=0.0, interval=0, status="filled")


if __name__ == "__main__":
    unittest.main()
