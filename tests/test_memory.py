"""Run memory: the audit trail every future test and analysis reads from.

The requirement is that ALL competition data is stored as memory for future
testing, analysis and evaluation - so these tests check that what is written
can be read back exactly, that it is checksummed, that tampering is detected,
and that the query API answers the questions an analyst would ask.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
import unittest

from fixtures import (PUBLISHED_SESSIONS, PUBLISHED_TOP_RETURN_PCT,
                       PUBLISHED_TOP_USERNAME, REPO_ROOT)

from sim import config, memory

PUBLISHED_RUN = "season1-primary-seed20260917"
MEMORY_ROOT = os.path.join(REPO_ROOT, "memory")


class TestPublishedMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = memory.MemoryStore(MEMORY_ROOT)
        if not os.path.isdir(cls.store.run_dir(PUBLISHED_RUN)):
            raise unittest.SkipTest("run memory not built; run `python3 -m sim.cli run`")

    def test_the_store_indexes_all_six_scenarios(self):
        runs = self.store.runs()
        ids = {r["run_id"] for r in runs}
        self.assertIn(PUBLISHED_RUN, ids)
        self.assertEqual(len([i for i in ids if i.startswith("season1")]), 6)
        for r in runs:
            self.assertIn("seed", r)
            self.assertIn("winner", r)
            self.assertIn("sessions", r)
            self.assertIn("participants", r)

    def test_latest_run_id_points_at_a_real_run(self):
        latest = self.store.latest_run_id()
        self.assertIn(latest, {r["run_id"] for r in self.store.runs()})

    def test_every_file_in_the_manifest_exists_and_hashes(self):
        v = self.store.verify(PUBLISHED_RUN)
        self.assertTrue(v["intact"], f"memory failed its own checksums: {v}")
        self.assertEqual(v["mismatched"], [])
        self.assertEqual(v["missing"], [])
        self.assertGreater(v["files_checked"], 10)

    def test_tampering_with_a_stream_is_detected(self):
        """Copy the run to a temp dir, alter one byte, and expect a failure."""
        tmp = tempfile.mkdtemp(prefix="sps-tamper-")
        try:
            shutil.copytree(self.store.run_dir(PUBLISHED_RUN),
                            os.path.join(tmp, "runs", PUBLISHED_RUN))
            shutil.copy(os.path.join(MEMORY_ROOT, "manifest.json"),
                        os.path.join(tmp, "manifest.json"))
            store = memory.MemoryStore(tmp)
            self.assertTrue(store.verify(PUBLISHED_RUN)["intact"])
            target = None
            events = os.path.join(tmp, "runs", PUBLISHED_RUN, "events")
            for name in sorted(os.listdir(events)):
                if name.startswith("fills"):
                    target = os.path.join(events, name)
                    break
            self.assertIsNotNone(target, "no fills stream to tamper with")
            with gzip.open(target, "rt", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            rows[0]["avg_price"] = rows[0]["avg_price"] * 1.5
            with gzip.open(target, "wt", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            v = store.verify(PUBLISHED_RUN)
            self.assertFalse(v["intact"])
            self.assertEqual(len(v["mismatched"]), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_manifest_records_the_code_that_produced_the_run(self):
        man = self.store.load(PUBLISHED_RUN, "manifest.json")
        self.assertEqual(man["schema_version"], memory.SCHEMA_VERSION)
        self.assertEqual(man["run_id"], PUBLISHED_RUN)
        self.assertIn("git", man["code"])
        self.assertIn("python_module_hashes", man["code"])
        self.assertGreater(len(man["code"]["python_module_hashes"]), 5)
        self.assertEqual(man["seed"], 20260917)
        self.assertEqual(man["participant_count"], 20)
        self.assertEqual(man["session_count"], 251)
        self.assertTrue(man["full_event_memory"])
        self.assertEqual(man["winner"], "@BetaChaser_3xProxy")
        self.assertEqual(len(man["top3"]), 3)
        self.assertEqual(man["competition"]["start"], "2025-09-17")
        self.assertEqual(man["competition"]["end"], "2026-09-16")
        self.assertIn("config_fingerprint", man)

    def test_event_streams_are_present_and_counted(self):
        counts = {name: self.store.stream(PUBLISHED_RUN, name).count()
                  for name in ("orders", "fills", "equity", "positions", "quotes")}
        self.assertEqual(counts["equity"], 20 * 251)
        self.assertGreater(counts["orders"], 2_000)
        self.assertGreater(counts["fills"], 2_000)
        self.assertLessEqual(counts["fills"], counts["orders"])
        self.assertGreater(counts["positions"], 10_000)
        self.assertEqual(counts["quotes"], 17 * 251)

    def test_fill_rows_carry_the_full_cost_decomposition(self):
        required = {"participant", "date", "symbol", "side", "order_type",
                    "requested_qty", "filled_qty", "avg_price", "decision_price",
                    "notional", "spread_cost", "depth_cost", "impact_cost",
                    "commission", "exchange_fee", "regulatory_fee", "rebate",
                    "total_cost", "price_difference", "drift_cost",
                    "implementation_shortfall", "execution_cost_bps", "status",
                    "reason", "interval"}
        rows = self.store.stream(PUBLISHED_RUN, "fills").list()
        missing = required - set(rows[0])
        self.assertFalse(missing, f"fill row is missing {sorted(missing)}")
        order = self.store.stream(PUBLISHED_RUN, "orders").list()[0]
        for field in ("date", "participant", "symbol", "side", "order_type",
                      "quantity", "tif", "reason"):
            self.assertIn(field, order)

    def test_every_recorded_price_is_a_legal_rule_612_increment(self):
        """The audit trail is the deliverable, so the grid is checked on what was
        actually WRITTEN, not only on what the venue code does in a unit test.

        ``tests/test_engine_smoke.py`` points here for the fill-grid check; this
        is that check.  It covers both streams a reader would quote prices from:
        every executed fill price, and every displayed bid/ask the book
        published (IR-28 - displayed levels were being re-centred off-grid).
        """
        fills = self.store.stream(PUBLISHED_RUN, "fills").list()
        quotes = self.store.stream(PUBLISHED_RUN, "quotes").list()
        self.assertGreater(len(fills), 1000)
        self.assertGreater(len(quotes), 1000)
        off_grid = []
        for row in fills:
            if row.get("filled_qty", 0) <= 0:
                continue
            tick = config.minimum_tick(row["avg_price"])
            decimals = 2 if tick >= 0.01 else 4
            if abs(row["avg_price"] - round(row["avg_price"], decimals)) > 1e-9:
                off_grid.append(("fill", row["symbol"], row["date"],
                                 row["avg_price"], tick))
        for row in quotes:
            for side in ("bid", "ask"):
                price = row.get(side)
                if price is None:
                    continue
                tick = config.minimum_tick(price)
                decimals = 2 if tick >= 0.01 else 4
                if abs(price - round(price, decimals)) > 1e-9:
                    off_grid.append((side, row["symbol"], row["date"], price, tick))
        self.assertEqual(off_grid, [],
                         f"{len(off_grid)} recorded prices are off the Rule 612 "
                         f"grid; first offenders: {off_grid[:5]}")

    def test_the_decision_price_is_deliberately_off_grid(self):
        """The counterpart of the test above: the price a strategy DECIDED at is
        the unrounded intraday mark, and the gap between it and the executed
        price is the tick cost.  If both were on-grid, the venue would be
        handing participants a free half-tick."""
        fills = [r for r in self.store.stream(PUBLISHED_RUN, "fills").list()
                 if r.get("filled_qty", 0) > 0]
        unrounded = sum(1 for r in fills
                        if abs(r["decision_price"] - round(r["decision_price"], 2)) > 1e-9)
        self.assertGreater(unrounded, len(fills) * 0.9,
                           "decision prices look pre-rounded; the tick cost is "
                           "being hidden")
        for row in fills[:200]:
            self.assertIn("price_difference", row)

    def test_fill_stats_match_a_manual_scan(self):
        stats = self.store.fill_stats(PUBLISHED_RUN)
        rows = self.store.stream(PUBLISHED_RUN, "fills").list()
        self.assertEqual(stats["fills"], len(rows))
        self.assertAlmostEqual(stats["total_execution_cost_usd"],
                               round(sum(r.get("total_cost", 0.0) for r in rows), 2),
                               places=2)
        self.assertEqual(sum(stats["by_status"].values()), len(rows))
        self.assertAlmostEqual(sum(stats["notional_by_symbol_usd"].values()),
                               round(sum(r.get("notional", 0.0) for r in rows), 2),
                               delta=1.0)

    def test_query_filters(self):
        all_fills = self.store.query_fills(PUBLISHED_RUN).count()
        one = self.store.query_fills(PUBLISHED_RUN, username="@BetaChaser_3xProxy").count()
        self.assertGreater(one, 0)
        self.assertLess(one, all_fills)
        spy = self.store.query_fills(PUBLISHED_RUN, symbol="SPY").count()
        self.assertGreater(spy, 0)
        windowed = self.store.query_fills(PUBLISHED_RUN, start="2026-01-01",
                                          end="2026-03-31").count()
        self.assertGreater(windowed, 0)
        self.assertLess(windowed, all_fills)
        buys = self.store.query_fills(PUBLISHED_RUN, side="buy").count()
        sells = self.store.query_fills(PUBLISHED_RUN, side="sell").count()
        self.assertEqual(buys + sells, all_fills)

    def test_query_select_sorted_and_sum(self):
        q = self.store.query_fills(PUBLISHED_RUN, username="@MomentumMax_12x1")
        rows = q.sorted_by("date")
        self.assertEqual([r["date"] for r in rows], sorted(r["date"] for r in rows))
        # Query is single-pass (rows is an iterator), so each pass needs a fresh
        # query - that is deliberate: a full season of fills must not be held in
        # RAM just to be filtered twice.
        cols = list(self.store.query_fills(PUBLISHED_RUN,
                                           username="@MomentumMax_12x1")
                    .select("date", "symbol", "avg_price"))
        self.assertEqual(set(cols[0]), {"date", "symbol", "avg_price"})
        self.assertAlmostEqual(self.store.query_fills(PUBLISHED_RUN,
                                                      username="@MomentumMax_12x1")
                               .sum("total_cost"),
                               sum(r["total_cost"] for r in rows), places=2)

    def test_equity_panel_is_rectangular(self):
        panel = self.store.equity_panel(PUBLISHED_RUN)
        self.assertEqual(len(panel), 251)
        for date, row in panel.items():
            self.assertEqual(len(row), 20, f"{date} is missing participants")
        # Day one is marked at the close AFTER the opening fills, so it is not
        # exactly the preset capital - but it must be close to it.
        first = panel[min(panel)]
        for user, equity in first.items():
            self.assertAlmostEqual(equity, 100_000.0, delta=10_000.0,
                                   msg=f"{user} day-one equity is not near the preset")
        for user, rep in {r["username"]: r
                          for r in self.store.reports(PUBLISHED_RUN)}.items():
            self.assertAlmostEqual(rep["starting_cash"], 100_000.0, delta=1e-6,
                                   msg=f"{user} starting capital is not the preset")

    def test_returns_panel_and_correlations(self):
        dates, users, series = self.store.returns_panel(PUBLISHED_RUN)
        self.assertEqual(len(dates), 250)
        self.assertEqual(len(users), 20)
        for u in users:
            self.assertEqual(len(series[u]), 250)
        corr = self.store.correlation_matrix(PUBLISHED_RUN)
        self.assertEqual(len(corr), 20)
        for a, row in corr.items():
            self.assertAlmostEqual(row[a], 1.0, places=3)
            for b, value in row.items():
                self.assertGreaterEqual(value, -1.0001)
                self.assertLessEqual(value, 1.0001)
                self.assertAlmostEqual(value, row[a] if a == b else corr[b][a],
                                       places=4)

    def test_reports_round_trip(self):
        reports = self.store.reports(PUBLISHED_RUN)
        self.assertIsInstance(reports, list)
        self.assertEqual(len(reports), 20)
        one = self.store.report(PUBLISHED_RUN, "BetaChaser_3xProxy")
        self.assertEqual(one["username"], PUBLISHED_TOP_USERNAME)
        self.assertAlmostEqual(one["total_return_pct"],
                               PUBLISHED_TOP_RETURN_PCT, delta=0.05)
        self.assertEqual(one["sessions"], PUBLISHED_SESSIONS)

    def test_market_data_dump_labels_simulation_as_simulation(self):
        md = self.store.load(PUBLISHED_RUN, "market_data.json")
        self.assertEqual(md["source"], "replay")
        self.assertEqual(md["provenance"]["mode"], "real-anchored-replay")
        self.assertIn("SIMULATED", md["provenance"]["single_name_daily_ohlcv"])
        self.assertEqual(len(md["dates"]), 377)
        self.assertEqual(md["first_competition_index"], 126)
        self.assertEqual(len(md["spx"]), 377)
        self.assertEqual(len(md["vix"]), 377)
        for sym, bars in md["bars"].items():
            self.assertEqual(len(bars), 377, sym)
        for sym, inst in md["instruments"].items():
            self.assertIn("provenance", inst)
            self.assertIn("liquidity_tier", inst)

    def test_irregularity_records_are_complete(self):
        rows = self.store.load(PUBLISHED_RUN, "irregularities.json")
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertRegex(row["id"], r"^IR-\d\d$")
            self.assertIn(row["severity"], ("low", "medium", "high"))
            self.assertTrue(row["message"].strip())
            self.assertIn("symbol", row)

    def test_csv_export_is_parseable_and_complete(self):
        tmp = tempfile.mkdtemp(prefix="sps-csv-")
        try:
            dest = os.path.join(tmp, "fills.csv")
            n = self.store.export_csv(PUBLISHED_RUN, "fills", dest)
            self.assertGreater(n, 2_000)
            import csv
            with open(dest, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                rows = list(reader)
            self.assertEqual(len(rows), n)
            self.assertIn("avg_price", header)
            self.assertIn("participant", header)
            for row in rows[:50]:
                self.assertEqual(len(row), len(header))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_robust_scenarios_store_summary_memory_only(self):
        """The five robustness runs keep the leaderboard, not the full tape."""
        for run in self.store.runs():
            rid = run["run_id"]
            if not rid.startswith("season1-robust"):
                continue
            man = self.store.load(rid, "manifest.json")
            self.assertFalse(man["full_event_memory"])
            self.assertGreater(self.store.load(rid, "leaderboard.json")
                               ["leaderboard"], [])
            self.assertEqual(self.store.stream(rid, "fills").count(), 0)


class TestWriterRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sps-mem-")
        self.run_id = "unit-test-run"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_write_json_report_and_finalise(self):
        writer = memory.RunWriter(self.run_id, root=self.tmp)
        rows = [{"date": f"2026-01-{i:02d}", "participant": "@a", "equity": 100.0 + i}
                for i in range(1, 6)]
        writer.extend("equity", rows)
        writer.append("fills", {"date": "2026-01-01", "participant": "@a",
                                "symbol": "SPY", "avg_price": 700.0,
                                "total_cost": 1.25, "status": "filled"})
        writer.write_json("market_report.json", {"spx_return_pct": 1.0})
        writer.write_report("@a", {"username": "@a", "total_return_pct": 1.0})
        manifest = writer.finalise({"seed": 1, "participant_count": 1,
                                    "session_count": 5, "winner": "@a",
                                    "competition": {"start": "2026-01-01",
                                                    "end": "2026-01-05"}})
        self.assertEqual(manifest["run_id"], self.run_id)

        store = memory.MemoryStore(self.tmp)
        self.assertEqual(store.latest_run_id(), self.run_id)
        self.assertEqual(store.stream(self.run_id, "equity").count(), 5)
        self.assertEqual(store.stream(self.run_id, "fills").count(), 1)
        self.assertEqual(store.load(self.run_id, "market_report.json")["spx_return_pct"],
                         1.0)
        self.assertEqual(store.report(self.run_id, "@a")["username"], "@a")
        self.assertTrue(store.verify(self.run_id)["intact"])
        self.assertEqual(store.equity_panel(self.run_id)["2026-01-03"]["@a"], 103.0)

    def test_streams_are_gzipped_jsonl(self):
        writer = memory.RunWriter(self.run_id, root=self.tmp)
        writer.append("orders", {"a": 1})
        writer.finalise({"seed": 2})
        path = os.path.join(self.tmp, "runs", self.run_id, "events", "orders.jsonl.gz")
        self.assertTrue(os.path.exists(path))
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline()), {"a": 1})

    def test_non_serialisable_values_are_coerced_not_crashed(self):
        from decimal import Decimal
        writer = memory.RunWriter(self.run_id, root=self.tmp)
        writer.append("orders", {"qty": Decimal("100"), "when": None,
                                 "nested": {"x": Decimal("1.5")}})
        writer.finalise({"seed": 3})
        store = memory.MemoryStore(self.tmp)
        row = store.stream(self.run_id, "orders").list()[0]
        self.assertEqual(row["qty"], 100.0)
        self.assertEqual(row["nested"]["x"], 1.5)

    def test_uncompressed_streams_are_readable_too(self):
        writer = memory.RunWriter(self.run_id, root=self.tmp, compress=False)
        writer.append("equity", {"date": "2026-01-01", "participant": "@a",
                                 "equity": 100.0})
        writer.finalise({"seed": 4})
        path = os.path.join(self.tmp, "runs", self.run_id, "events", "equity.jsonl")
        self.assertTrue(os.path.exists(path))
        store = memory.MemoryStore(self.tmp)
        self.assertEqual(store.stream(self.run_id, "equity").count(), 1)

    def test_missing_streams_read_as_empty(self):
        writer = memory.RunWriter(self.run_id, root=self.tmp)
        writer.finalise({"seed": 5})
        store = memory.MemoryStore(self.tmp)
        self.assertEqual(store.stream(self.run_id, "nonexistent").count(), 0)
        self.assertEqual(store.stream(self.run_id, "nonexistent").list(), [])
        self.assertIsNone(store.load(self.run_id, "nope.json"))


if __name__ == "__main__":
    unittest.main()
