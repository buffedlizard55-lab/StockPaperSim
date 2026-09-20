"""Tests for sim/significance.py and its site artifact.

The significance layer is an audit addition, so the tests pin three things:
the estimators behave correctly on constructed series where the right answer
is known; the page builder regenerates from committed memory deterministically;
and the published artifact stays in sync with the module (a deleted estimator
or a renamed JSON field fails here before it silently rots the page).
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import tempfile
import unittest

from fixtures import REPO_ROOT

from sim import significance as sig


def _seeded_returns(n: int, mean: float, sd: float, seed: int) -> list:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


class TestBootstrapSharpe(unittest.TestCase):
    def test_deterministic_for_fixed_seed(self):
        rets = _seeded_returns(250, 0.0008, 0.01, seed=7)
        a = sig.bootstrap_sharpe(rets, n_boot=200, seed=1)
        b = sig.bootstrap_sharpe(rets, n_boot=200, seed=1)
        self.assertEqual(a, b)

    def test_ci_brackets_point_and_flagship_series_reads_significant(self):
        rets = _seeded_returns(250, 0.002, 0.008, seed=11)
        out = sig.bootstrap_sharpe(rets, n_boot=300, seed=2)
        self.assertLessEqual(out["ci95_low"], out["sharpe_annualised"])
        self.assertLessEqual(out["sharpe_annualised"], out["ci95_high"])
        self.assertEqual(out["p_sharpe_le_zero"], 0.0)
        self.assertEqual(out["sessions"], 250)

    def test_zero_mean_series_has_high_p_of_being_zero(self):
        # perfectly alternating gains/losses: sample mean is exactly zero, so
        # roughly half the bootstrap replicates must read a non-positive Sharpe
        rets = [0.01, -0.01] * 125
        out = sig.bootstrap_sharpe(rets, n_boot=300, seed=3)
        self.assertGreater(out["p_sharpe_le_zero"], 0.2)
        self.assertAlmostEqual(out["sharpe_annualised"], 0.0, places=6)


class TestBootstrapAlpha(unittest.TestCase):
    def test_deterministic_relation_pins_alpha_and_beta(self):
        rng = random.Random(21)
        market = [rng.gauss(0.0004, 0.01) for _ in range(250)]
        y = [0.001 + 1.5 * m for m in market]
        out = sig.bootstrap_alpha(y, market, n_boot=200, seed=4)
        self.assertAlmostEqual(out["alpha_daily"], 0.001, places=6)
        self.assertAlmostEqual(out["beta"], 1.5, places=6)
        self.assertEqual(out["ci95_low_pct"], out["alpha_annual_pct"])
        self.assertEqual(out["ci95_high_pct"], out["alpha_annual_pct"])
        self.assertEqual(out["p_alpha_le_zero"], 0.0)

    def test_misaligned_lengths_are_truncated_not_shifted(self):
        market = _seeded_returns(300, 0.0004, 0.01, seed=31)
        y = [0.0005 + m for m in market[:250]]
        out = sig.bootstrap_alpha(y, market, n_boot=100, seed=5)
        self.assertEqual(out["sessions"], 250)


class TestBootstrapMaxDrawdown(unittest.TestCase):
    def test_realised_path_inside_interval_and_ordering_holds(self):
        up = _seeded_returns(250, 0.001, 0.012, seed=41)
        out = sig.bootstrap_max_drawdown(up, n_boot=300, seed=6)
        self.assertLessEqual(out["ci95_high_pct"], 0.0)
        self.assertLessEqual(out["ci95_low_pct"], out["max_drawdown_pct"])
        self.assertLessEqual(out["max_drawdown_pct"], 0.0)

    def test_pure_gain_series_has_zero_drawdown_everywhere(self):
        rets = [0.001] * 250
        out = sig.bootstrap_max_drawdown(rets, n_boot=50, seed=7)
        self.assertEqual(out["max_drawdown_pct"], 0.0)
        self.assertEqual(out["ci95_low_pct"], 0.0)


class TestDeflatedSharpe(unittest.TestCase):
    def test_strong_edge_survives_twenty_trials(self):
        rets = _seeded_returns(250, 0.004, 0.008, seed=51)
        out = sig.deflated_sharpe(rets, n_trials=20)
        self.assertIsNotNone(out["dsr"])
        self.assertGreater(out["dsr"], 0.95)
        self.assertEqual(out["n_trials"], 20)

    def test_zero_edge_fails_the_bar(self):
        rets = _seeded_returns(250, 0.0, 0.01, seed=53)
        out = sig.deflated_sharpe(rets, n_trials=20)
        self.assertIsNotNone(out["dsr"])
        self.assertLessEqual(out["dsr"], 0.6)

    def test_small_sample_returns_none_with_note(self):
        out = sig.deflated_sharpe([0.01] * 5, n_trials=20)
        self.assertIsNone(out["dsr"])
        self.assertIn("note", out)

    def test_explicit_trial_variance_is_used_and_labelled(self):
        rets = _seeded_returns(250, 0.002, 0.01, seed=57)
        a = sig.deflated_sharpe(rets, n_trials=20, trial_sr_variance=0.01)
        self.assertEqual(a["trial_sr_variance_source"], "robustness panel")
        b = sig.deflated_sharpe(rets, n_trials=20)
        self.assertEqual(b["trial_sr_variance_source"],
                         "asymptotic estimator variance")
        self.assertNotEqual(a["expected_max_sharpe_daily_under_null"],
                            b["expected_max_sharpe_daily_under_null"])


class TestRankTest(unittest.TestCase):
    def test_dominant_participant_is_distinguishable_from_chance(self):
        scenarios = {f"s{i}" for i in range(5)}
        board = {
            "A": {s: 10.0 for s in scenarios},
            "B": {s: 5.0 for s in scenarios},
            "C": {s: 1.0 for s in scenarios},
        }
        out = sig.rank_test(board)
        self.assertEqual(out["scenarios"], 5)
        row = out["rows"]["A"]
        self.assertEqual(row["scenario_wins"], 5)
        self.assertLess(row["p_value"], 0.05)

    def test_cycling_winners_are_not_distinguishable(self):
        # three participants each win in a strict rotation: 2 wins each in 6
        # scenarios against a 1/3 chance null must not read as significant
        scenarios = [f"s{i}" for i in range(6)]
        users = ["A", "B", "C"]
        board = {u: {} for u in users}
        for i, s in enumerate(scenarios):
            winner = users[i % 3]
            for u in users:
                board[u][s] = 10.0 if u == winner else 1.0
        out = sig.rank_test(board)
        for u in users:
            self.assertGreaterEqual(out["rows"][u]["p_value"], 0.05)


class TestSeasonBundle(unittest.TestCase):
    def test_bundle_carries_every_section(self):
        rng = random.Random(61)
        market = [rng.gauss(0.0004, 0.01) for _ in range(250)]
        curves = {}
        for i, name in enumerate(("@A", "@B", "@C")):
            level = 100000.0
            curve = []
            for j, m in enumerate(market):
                level *= 1 + m * (1.0 + i) + 0.0002 * i
                curve.append((f"2026-01-{(j % 28) + 1:02d}", level))
            curves[name] = curve
        market_curve = [(f"2026-01-{(j % 28) + 1:02d}", 5000.0 * (1 + j / 1000))
                        for j, _ in enumerate(market)]
        scenario = {"@A": {"1": 10.0, "2": 12.0}, "@B": {"1": 5.0, "2": 6.0},
                    "@C": {"1": 1.0, "2": 2.0}}
        bundle = sig.season_significance(curves, market_curve,
                                         scenario_returns_pct=scenario,
                                         n_boot=100, seed=8)
        self.assertEqual(set(bundle["participants"]), {"@A", "@B", "@C"})
        for row in bundle["participants"].values():
            for section in ("sharpe", "alpha", "max_drawdown",
                            "deflated_sharpe"):
                self.assertIn(section, row)
        self.assertIn("rank_test", bundle)
        self.assertEqual(bundle["rank_test"]["scenarios"], 2)


class TestPublishedArtifact(unittest.TestCase):
    """The committed page/JSON stay in sync with the module."""

    def _have_memory(self) -> bool:
        return os.path.isdir(os.path.join(REPO_ROOT, "memory", "runs"))

    def test_builder_regenerates_both_files_from_committed_memory(self):
        if not self._have_memory():
            self.skipTest("committed memory not present")
        spec = importlib.util.spec_from_file_location(
            "build_site_significance",
            os.path.join(REPO_ROOT, "scripts", "build_site_significance.py"))
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as out:
            written = mod.build(os.path.join(REPO_ROOT, "memory"), out)
            self.assertIn(os.path.join("assets", "data", "significance.json"),
                          written[0])
            bundle = json.load(open(os.path.join(
                out, "assets", "data", "significance.json"), encoding="utf-8"))
            self.assertIn("participants", bundle)
            self.assertIn("rank_test", bundle)
            page = open(os.path.join(out, "significance.html"),
                        encoding="utf-8").read()
            self.assertIn("Deflated Sharpe", page)
            self.assertIn(bundle["published_leader"], page)

    def test_committed_artifact_matches_the_module_schema(self):
        path = os.path.join(REPO_ROOT, "docs", "assets", "data",
                            "significance.json")
        if not os.path.exists(path):
            self.skipTest("significance artifact not built yet")
        bundle = json.load(open(path, encoding="utf-8"))
        for username, row in bundle["participants"].items():
            self.assertEqual(set(row),
                             {"sharpe", "alpha", "max_drawdown",
                              "deflated_sharpe"})
            sharpe = row["sharpe"]
            self.assertLessEqual(sharpe["ci95_low"],
                                 sharpe["ci95_high"])
            self.assertLessEqual(sharpe["p_sharpe_le_zero"], 1.0)


if __name__ == "__main__":
    unittest.main()
