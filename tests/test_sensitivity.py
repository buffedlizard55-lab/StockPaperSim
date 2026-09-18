"""The venue-parameter sensitivity grid (the answer to IR-29).

Two things are worth testing here and they are different: that the *grid* is
honest - every row names a real parameter, records its shipped value, and moves
that and nothing else - and that the *arithmetic* the report publishes (rank
moves, sign flips, rank correlation) follows from the runs rather than from a
hand-written summary.  A third test checks the published artefact against the
shipped configuration, so a change to a default cannot leave the site describing
a parameter value the model no longer uses.
"""

from __future__ import annotations

import json
import os
import re
import unittest

from fixtures import REPO_ROOT

from sim import config, sensitivity
from sim.microstructure import ExecutionEngine

SENSITIVITY_JSON = os.path.join(REPO_ROOT, "memory", "sensitivity.json")


def _record(pairs):
    """A minimal engine record: rank is order of return, as the engine sets it."""
    board = [{"rank": i + 1, "username": u, "total_return_pct": v}
             for i, (u, v) in enumerate(sorted(pairs, key=lambda p: -p[1]))]
    return {"leaderboard": board, "seed": 20260917,
            "config_fingerprint": config.DEFAULT_COMPETITION.fingerprint()}


class GridTest(unittest.TestCase):
    def test_every_config_row_names_a_real_parameter_at_its_shipped_value(self):
        base = config.DEFAULT_COMPETITION
        for p in sensitivity.PERTURBATIONS:
            if p.kind != "config" or p.mutate is not None:
                continue
            # Names a real field, and the recorded baseline is what the shipped
            # config actually holds: a drifted default would make the "moved
            # from X to Y" column a lie.
            shipped = sensitivity._dotted(base, p.parameter)
            self.assertEqual(shipped, p.baseline, p.name)
            probe = sensitivity.perturbed_config(base, p)
            self.assertEqual(sensitivity._dotted(probe, p.parameter), p.value, p.name)
            self.assertIsNot(probe, base, p.name)

    def test_every_venue_row_names_an_execution_engine_switch(self):
        import inspect
        params = inspect.signature(ExecutionEngine.__init__).parameters
        for p in sensitivity.PERTURBATIONS:
            if p.kind != "venue":
                continue
            self.assertIn(p.parameter, params, p.name)
            # The whole point: a model-form switch must not be a config field,
            # or moving it would change the fingerprint and invalidate the
            # published season's replay.
            self.assertFalse(hasattr(config.DEFAULT_COMPETITION, p.parameter), p.name)

    def test_a_config_row_moves_exactly_one_value(self):
        base = config.DEFAULT_COMPETITION
        before = base.as_dict()
        probe = sensitivity.perturbed_config(
            base, next(p for p in sensitivity.PERTURBATIONS
                       if p.name == "spread_k_x0.8"))
        after = probe.as_dict()
        changed = [k for k in before if before[k] != after[k]]
        self.assertEqual(changed, ["liquidity"])
        self.assertAlmostEqual(before["liquidity"]["spread_k_ticks"],
                               probe.liquidity.spread_k_ticks / 0.8, places=9)
        # And the shipped config object itself must not have moved.
        self.assertEqual(base.as_dict(), before)

    def test_the_tick_rows_move_every_tier_and_only_the_tiers(self):
        base = config.DEFAULT_COMPETITION
        before = base.as_dict()
        for name, delta in (("spread_ticks_plus1", 1), ("spread_ticks_minus1", -1)):
            p = next(q for q in sensitivity.PERTURBATIONS if q.name == name)
            probe = sensitivity.perturbed_config(base, p)
            self.assertEqual(sorted(probe.liquidity.min_spread_ticks),
                             sorted(base.liquidity.min_spread_ticks))
            for tier, ticks in base.liquidity.min_spread_ticks.items():
                self.assertEqual(probe.liquidity.min_spread_ticks[tier],
                                 max(1, ticks + delta), name)
            for tier, ticks in base.liquidity.max_spread_ticks.items():
                self.assertGreaterEqual(probe.liquidity.max_spread_ticks[tier],
                                        probe.liquidity.min_spread_ticks[tier], name)
                self.assertEqual(probe.liquidity.max_spread_ticks[tier],
                                 max(1, ticks + delta), name)
            self.assertEqual(probe.liquidity.spread_k_ticks,
                             base.liquidity.spread_k_ticks, name)
            self.assertEqual(probe.liquidity.spread_cap_bps,
                             base.liquidity.spread_cap_bps, name)
        self.assertEqual(base.as_dict(), before)

    def test_perturbing_a_copy_does_not_move_the_published_fingerprint(self):
        fingerprint = config.DEFAULT_COMPETITION.fingerprint()
        for p in sensitivity.PERTURBATIONS:
            if p.kind == "config":
                sensitivity.perturbed_config(config.DEFAULT_COMPETITION, p)
        self.assertEqual(fingerprint, config.DEFAULT_COMPETITION.fingerprint())

    def test_venue_overrides_are_keyword_arguments_only(self):
        for p in sensitivity.PERTURBATIONS:
            if p.kind == "venue":
                self.assertEqual(sensitivity.venue_overrides(p), {p.parameter: p.value})
                with self.assertRaises(ValueError):
                    sensitivity.venue_overrides(
                        next(q for q in sensitivity.PERTURBATIONS
                             if q.kind == "config"))


class ReportArithmeticTest(unittest.TestCase):
    def test_rank_moves_and_sign_flips_follow_from_the_runs(self):
        base = _record([("@a", 10.0), ("@b", -5.0), ("@c", 3.0), ("@d", 20.0)])
        moved = _record([("@a", -12.0), ("@b", 8.0), ("@c", 3.0), ("@d", 21.0)])
        perturbation = sensitivity.Perturbation(
            name="probe", parameter="probe", value=1, baseline=0, why="test")
        report = sensitivity.sensitivity_report(base, [(perturbation, moved)],
                                                grid=(perturbation,))
        rows = {r["username"]: r for r in report["by_participant"]}
        self.assertEqual(rows["@a"]["base_rank"], 2)
        self.assertEqual(rows["@a"]["perturbed_rank"]["probe"], 4)
        self.assertEqual(rows["@a"]["max_abs_rank_change"], 2)
        self.assertEqual(rows["@a"]["sign_flips"], 1)   # +10 -> -12
        self.assertEqual(rows["@b"]["sign_flips"], 1)   # -5 -> +8
        self.assertEqual(rows["@c"]["sign_flips"], 0)
        summary = report["summary"]
        self.assertEqual(summary["n_perturbations"], 1)
        self.assertEqual(summary["n_participants"], 4)
        # @d stays first and @c stays third; only @a and @b swap.
        self.assertEqual(summary["participants_with_rank_change"], 2)
        self.assertEqual(summary["sign_flips"], 2)
        # @d is 20 -> 21 in a run where everything else moved; the largest
        # absolute swing across the grid is @a's 22pp.
        self.assertAlmostEqual(summary["max_abs_return_swing_pp"], 22.0, places=3)
        self.assertIn("@c", [r["username"] for r in report["by_participant"]])

    def test_spearman_is_one_for_the_same_order_and_minus_one_reversed(self):
        base = _record([("@a", 10.0), ("@b", 5.0), ("@c", 1.0)])
        same = _record([("@a", 11.0), ("@b", 4.0), ("@c", 2.0)])
        flipped = _record([("@a", 1.0), ("@b", 5.0), ("@c", 9.0)])
        p = sensitivity.Perturbation(name="p", parameter="p", value=1, baseline=0,
                                     why="test")
        rep = sensitivity.sensitivity_report(base, [(p, same)], grid=(p,))
        self.assertAlmostEqual(rep["summary"]["mean_spearman"], 1.0, places=6)
        rep = sensitivity.sensitivity_report(base, [(p, flipped)], grid=(p,))
        self.assertAlmostEqual(rep["summary"]["mean_spearman"], -1.0, places=6)

    def test_a_tie_draws_the_same_rank_not_a_fabricated_order(self):
        ranks = sensitivity._rank_of({"?a": 5.0, "?b": 5.0, "?c": 1.0})
        self.assertEqual(ranks["?a"], ranks["?b"])
        self.assertAlmostEqual(ranks["?c"], 3.0)

    def test_a_participant_pushed_from_profit_to_loss_is_named(self):
        base = _record([("@a", 4.0), ("@b", -2.0)])
        run = _record([("@a", -1.0), ("@b", 3.0)])
        p = sensitivity.Perturbation(name="p", parameter="p", value=1, baseline=0,
                                     why="test")
        rep = sensitivity.sensitivity_report(base, [(p, run)], grid=(p,))
        self.assertEqual(rep["summary"]["participants_pushed_below_zero"], ["@a"])


@unittest.skipUnless(os.path.exists(SENSITIVITY_JSON),
                     "sensitivity grid has not been run in this checkout")
class PublishedArtefactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SENSITIVITY_JSON, encoding="utf-8") as fh:
            cls.doc = json.load(fh)

    def test_the_summary_is_derivable_from_the_participant_rows(self):
        summary = self.doc["summary"]
        rows = self.doc["by_participant"]
        self.assertEqual(summary["n_participants"], len(rows))
        self.assertEqual(
            summary["participants_with_rank_change"],
            sum(1 for r in rows if r["max_abs_rank_change"] > 0))
        self.assertEqual(summary["max_abs_rank_change"],
                         max(r["max_abs_rank_change"] for r in rows))
        self.assertEqual(summary["sign_flips"],
                         sum(r["sign_flips"] for r in rows))
        self.assertEqual(
            summary["max_abs_return_swing_pp"],
            round(max(max(abs(r["max_return_pct"] - r["base_return_pct"]),
                         abs(r["min_return_pct"] - r["base_return_pct"]))
                     for r in rows), 3))
        self.assertEqual(summary["n_perturbations"], len(self.doc["perturbations"]))

    def test_the_base_run_is_the_published_season(self):
        base = self.doc["base"]
        self.assertEqual(base["config_fingerprint"],
                         config.DEFAULT_COMPETITION.fingerprint())
        self.assertEqual(base["seed"], config.DEFAULT_COMPETITION.seed)
        self.assertEqual(base["window"]["start"], config.DEFAULT_COMPETITION.start)
        self.assertEqual(base["window"]["end"], config.DEFAULT_COMPETITION.end)

    def test_every_participant_has_a_value_for_every_perturbation(self):
        names = {p["name"] for p in self.doc["perturbations"]}
        for row in self.doc["by_participant"]:
            self.assertEqual(set(row["perturbed_return_pct"]), names, row["username"])
            self.assertEqual(set(row["perturbed_rank"]), names, row["username"])

    def test_the_grid_documents_itself(self):
        by_name = {p["name"]: p for p in self.doc["perturbations"]}
        self.assertIn("impact_exponent_0.6", by_name)
        self.assertIn("tick_snap_off", by_name)
        for p in self.doc["perturbations"]:
            self.assertGreater(len(p["why"]), 40, p["name"])
            self.assertIn(p["kind"], ("config", "venue"), p["name"])
            self.assertNotEqual(p["baseline"], p["value"], p["name"])


class PageTest(unittest.TestCase):
    """The published page must exist in docs and carry the honesty note."""

    PATH = os.path.join(REPO_ROOT, "docs", "sensitivity.html")

    @unittest.skipUnless(os.path.exists(SENSITIVITY_JSON),
                         "sensitivity grid has not been run in this checkout")
    def test_published_page_states_what_the_band_is_not(self):
        self.assertTrue(os.path.exists(self.PATH), "docs/sensitivity.html missing")
        with open(self.PATH, encoding="utf-8") as fh:
            text = fh.read()
        flat = re.sub(r"\s+", " ", text)
        self.assertIn("sensitivity band over declared model choices", flat)
        self.assertIn("not a confidence interval", flat)
        self.assertIsNone(re.search(r"\bnan\b", text), "unrendered NaN on the page")
        self.assertIn("Nothing on this page is investment advice.", text)


if __name__ == "__main__":
    unittest.main()
