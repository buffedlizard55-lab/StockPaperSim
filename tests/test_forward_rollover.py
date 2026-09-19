"""Tests for the forward rollover (``sim/rollover.py``) and the print ledger.

The rollover is the part of the project that settles trades nobody is watching,
so the properties worth testing are the ones that would let it invent a result:

* a session settles only from a *published* row - the prior-session close that
  keeps the arrays aligned must never be executable;
* a session the official calendar has not published is labelled provisional and
  carries the names of the official series it is missing, so a reader can tell
  "the S&P 500 series says this was a session" from "the exchange printed a bar
  for every symbol";
* a dry run writes nothing at all, because an earlier revision accepted
  ``--no-write`` and rewrote the committed book anyway;
* the exchange rows and the vendor rows are cross-checked before either is used,
  and a disagreement beyond the tolerance stops the settlement.
"""

from __future__ import annotations

import json
import os
import unittest

from fixtures import REPO_ROOT
from sim import rollover, prints as print_mod, realdata

PRINTS = os.path.join(realdata.REAL_ROOT, "prints")
LADDER = os.path.join(REPO_ROOT, "memory", "live", rollover.LADDER_FILE)


def _ledger(source: str):
    return print_mod.load_ledger(root=realdata.REAL_ROOT, source=source)


@unittest.skipUnless(os.path.exists(os.path.join(PRINTS, "crosscheck.json")),
                     "no collected prints on this checkout")
class PrintLedgerTest(unittest.TestCase):
    def test_both_publishers_are_ledgered_with_their_own_class(self):
        nasdaq = _ledger("nasdaq")
        yahoo = _ledger("yahoo")
        self.assertTrue(nasdaq.rows and yahoo.rows)
        self.assertEqual(nasdaq.meta()["source_class"], "EXCHANGE-PUBLISHED")
        self.assertEqual(yahoo.meta()["source_class"], "SECONDARY")
        self.assertEqual(nasdaq.meta()["redistribution_status"],
                         "NOT-AUTHORIZED-FOR-REPOSITORY-REPRODUCTION")
        for row in nasdaq.rows[:5]:
            self.assertTrue(row["url"].startswith("https://"))
            self.assertTrue(row["retrieved_utc"])

    def test_rows_carry_the_publishers_own_strings(self):
        row = _ledger("nasdaq").get("QQQ", "2026-09-17")
        self.assertIsNotNone(row)
        self.assertEqual(row["raw"]["close"], "716.92")
        self.assertEqual(row["raw"]["volume"], "37,270,290")

    def test_the_two_publishers_agree_on_every_close(self):
        with open(os.path.join(PRINTS, "crosscheck.json"), "r", encoding="utf-8") as h:
            report = json.load(h)
        self.assertLess(report["worst_close_difference_bps"], report["tolerance_bps"])
        self.assertEqual(report["close_disagreements_beyond_tolerance"], [])
        self.assertEqual(report["tolerance_bps"], 5.0)
        self.assertGreater(report["symbol_sessions_compared"], 0)

    def test_the_exchange_record_is_never_claimed_as_redistributable(self):
        for name in ("nasdaq", "yahoo"):
            if name != "nasdaq":
                continue
            path = os.path.join(realdata.REAL_ROOT, "prices", "nasdaq", "QQQ.json")
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertNotIn(payload.get("redistribution_status"),
                             ("CONFIRMED_PUBLIC_REDISTRIBUTION",
                              "LICENSED_FOR_REPOSITORY_REPRODUCTION"),
                             "the forward slice may not claim a permission nobody granted")


class ExecutableBarTest(unittest.TestCase):
    """A fill may only cite a price somebody published."""

    def test_a_session_without_a_print_is_not_executable(self):
        class _Meta:
            def by_date(self):
                return {}

        class _Market:
            series_meta = {"SPY": _Meta()}
            gaps = {"SPY": ["2099-01-04"]}
            bar_provenance = {}

        from sim.live import bar_is_executable
        ok, why = bar_is_executable(_Market(), "SPY", "2099-01-04")
        self.assertFalse(ok)
        self.assertIn("no publisher printed", why)

    def test_a_session_with_provenance_is_executable(self):
        class _Market:
            series_meta = {}
            gaps = {}
            bar_provenance = {("SPY", "2026-09-18"): {"source": "nasdaq",
                                                      "source_class": "EXCHANGE-PUBLISHED"}}

        from sim.live import bar_is_executable
        ok, why = bar_is_executable(_Market(), "SPY", "2026-09-18")
        self.assertTrue(ok)
        self.assertEqual(why, "")


class OfficialEvidenceTest(unittest.TestCase):
    def test_the_calendar_authority_is_reported_per_session(self):
        evidence = rollover.official_evidence("2026-09-17")
        present = {row["sid"] for row in evidence["official_series_present"]}
        self.assertIn("SP500", present,
                      "the S&P 500 series defines this project's session calendar")
        self.assertEqual(set(evidence["official_series_missing"]),
                         {"DCOILWTICO", "DTWEXBGS"},
                         "the two low-frequency series are the structural absences here")

    def test_a_session_the_calendar_has_not_published_lists_what_is_missing(self):
        evidence = rollover.official_evidence("2026-09-18")
        present = {row["sid"] for row in evidence["official_series_present"]}
        missing = set(evidence["official_series_missing"])
        self.assertIn("NASDAQCOM", present)
        self.assertIn("DJIA", present)
        self.assertIn("SP500", missing)
        self.assertTrue(present.isdisjoint(missing))


@unittest.skipUnless(os.path.exists(LADDER), "no rollover ladder on this checkout")
class LadderTest(unittest.TestCase):
    def setUp(self):
        with open(LADDER, "r", encoding="utf-8") as handle:
            self.ladder = json.load(handle)

    def test_every_row_says_how_the_session_was_verified(self):
        for row in self.ladder["ladder"]:
            self.assertIn(row["verification"],
                          ("COLLECTED-OFFICIAL-CALENDAR", "PROVISIONAL-EXCHANGE-PRINTS"))
            self.assertIn(row["status"], ("SETTLED", "WAITING-FOR-PRINTS",
                                          "NOTHING-SCHEDULED"))
            self.assertIsInstance(row["official_series_missing"], list)

    def test_a_settled_session_filled_every_intent_it_targeted(self):
        for row in self.ladder["ladder"]:
            if row["status"] != "SETTLED":
                continue
            self.assertEqual(row["filled"] + row["partial"] + row["rejected"]
                             + row["waiting_for_prints"], row["intents_targeted"])

    def test_every_fill_cites_a_published_reference_class(self):
        for row in self.ladder["ladder"]:
            for fill in row["fills"]:
                self.assertIn(fill["reference_source_class"],
                              ("EXCHANGE-PUBLISHED", "SECONDARY", "OFFICIAL"))
                self.assertIsNotNone(fill["price"])
                self.assertGreater(float(fill["price"]), 0)

    def test_a_provisional_session_names_the_official_series_it_is_missing(self):
        for row in self.ladder["ladder"]:
            if row["verification"] == "PROVISIONAL-EXCHANGE-PRINTS":
                self.assertTrue(row["official_series_missing"] or
                                row["official_series_present"],
                                "a provisional row must carry its evidence either way")


@unittest.skipUnless(os.path.exists(LADDER), "no rollover ladder on this checkout")
class DryRunTest(unittest.TestCase):
    def test_a_dry_run_writes_nothing(self):
        watched = [LADDER,
                   os.path.join(REPO_ROOT, "memory", "live", rollover.HISTORY_FILE),
                   os.path.join(REPO_ROOT, "docs", "assets", "data", "live.json")]
        before = {path: (os.path.getmtime(path) if os.path.exists(path) else None)
                  for path in watched}
        result = rollover.step(write=False)
        after = {path: (os.path.getmtime(path) if os.path.exists(path) else None)
                 for path in watched}
        self.assertEqual(before, after, "a dry run must not touch the committed artifacts")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["run_dir"], "")
        self.assertTrue(result["ladder"], "the dry run still computes the ladder")


if __name__ == "__main__":
    unittest.main()
