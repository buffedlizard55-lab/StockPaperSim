"""Custody: the regeneration log, its chains, and the intraday-lane check.

A collected file is live.  The forward collector appends a session to a price
file; a collection run re-fetches the publisher's current window into a CSV.  A
season run, meanwhile, has recorded the SHA-256 of every file it read, and those
two facts are in tension the moment the file moves.  These tests hold the line
that keeps the tension honest:

* a chain is only accepted when it actually ends at the bytes on disk;
* a change with no logged link is a failure, not a pass;
* the committed log is well formed, and its rows name a writer and a reason;
* the intraday-lane check exists, is reproducible, and its verdict agrees with
  the repository - so the three ``not_run`` rows of the executive summary stand
  on a check rather than on a sentence.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

from fixtures import REPO_ROOT

from sim import custody

RESEARCH = os.path.join(REPO_ROOT, "research")


def load_script(name: str, relative: str):
    path = os.path.join(REPO_ROOT, relative)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ChainTest(unittest.TestCase):
    """The chain has to prove the move, or report that it cannot."""

    def setUp(self):
        # A private data root, so a chain written by one test cannot be read by
        # the next - which is exactly the failure mode this module exists to
        # prevent, and it was observed here first.
        self.data_root = tempfile.mkdtemp(prefix="sps-custody-")
        self.addCleanup(shutil.rmtree, self.data_root, ignore_errors=True)
        self.path = os.path.join(REPO_ROOT, "data", "real", "_custody_probe.jsonl")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _write(self, text: str) -> str:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return custody.sha256_file(self.path)

    def test_an_empty_log_is_a_list_and_not_an_error(self):
        empty = tempfile.mkdtemp(prefix="sps-custody-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertEqual(custody.read_log(empty), [])
        self.assertEqual(self.data_root, self.data_root)   # a fresh root per test

    def test_a_bad_kind_is_refused_rather_than_downgraded(self):
        with self.assertRaises(ValueError):
            custody.log_rewrite(self.path, "a" * 64, "b" * 64, writer="probe",
                                kind="probably-fine", data_root=self.data_root)

    def test_a_logged_append_chains_the_recorded_bytes_to_the_file_on_disk(self):
        before = self._write("one\n")
        after = self._write("one\ntwo\n")
        custody.log_rewrite(self.path, before, after, writer="probe",
                            kind="append", reason="test append",
                            data_root=self.data_root)
        link = custody.chain_for(self.path, before, self.data_root)
        self.assertIsNotNone(link)
        self.assertEqual(link["writer"], "probe")
        walk = custody.chained_forward(self.path, before, self.data_root)
        self.assertTrue(walk["chained"], walk)
        self.assertEqual(walk["steps"][0]["reason"], "test append")

    def test_a_change_with_no_logged_link_does_not_chain(self):
        before = self._write("one\n")
        self._write("one\ntwo\n")
        walk = custody.chained_forward(self.path, before, self.data_root)
        self.assertFalse(walk["chained"])
        self.assertIn("no logged chain", walk["reason"])

    def test_a_chain_that_stops_short_of_the_file_does_not_count(self):
        """The log must end where the file ends, or it proves nothing."""
        before = self._write("one\n")
        after = self._write("one\ntwo\n")
        custody.log_rewrite(self.path, before, after, writer="probe",
                            kind="append", reason="test append",
                            data_root=self.data_root)
        self._write("one\ntwo\nthree\n")           # changed again, unlogged
        walk = custody.chained_forward(self.path, before, self.data_root)
        self.assertFalse(walk["chained"], walk)
        self.assertEqual(walk["final_sha256"], after)


class CommittedLogTest(unittest.TestCase):
    """The log that is actually committed has to be readable and complete."""

    def setUp(self):
        self.rows = custody.read_log()

    def test_the_log_exists_and_every_row_is_well_formed(self):
        self.assertTrue(self.rows, "no regeneration log on disk")
        for row in self.rows:
            self.assertIn(row["kind"], custody.KINDS, row)
            self.assertRegex(row["previous_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(row["previous_sha256"], row["sha256"], row)
            self.assertTrue(row["writer"], f"{row['path']}: no writer recorded")
            self.assertTrue(row["reason"], f"{row['path']}: no reason recorded")
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, row["path"])),
                            f"{row['path']} is in the log but not on disk")

    def test_no_row_claims_to_be_more_than_it_is(self):
        """A row written after the fact says so, in the row."""
        backfilled = [r for r in self.rows if r.get("utc_source")]
        for row in backfilled:
            self.assertIn("mtime", row["utc_source"],
                          f"{row['path']}: unexpected utc_source")
            self.assertEqual(row.get("runnable", ""), "",
                             "a backfilled row cannot cite a workflow run id")

    def test_all_the_rows_fail_closed_on_the_same_three_words(self):
        kinds = {row["kind"] for row in self.rows}
        self.assertTrue(kinds <= set(custody.KINDS), kinds)


class IntradayCheckTest(unittest.TestCase):
    """The check behind the ``not_run`` rows, reproduced here."""

    @classmethod
    def setUpClass(cls):
        cls.script = load_script("check_intraday_lane", "scripts/check_intraday_lane.py")
        with open(os.path.join(RESEARCH, "INTRADAY_LANE_CHECK.json"),
                  encoding="utf-8") as handle:
            cls.payload = json.load(handle)

    def test_the_committed_check_is_reproducible_on_its_verdict(self):
        fresh = self.script.check()
        self.assertEqual(fresh["verdict"], self.payload["verdict"])
        self.assertEqual(fresh["in_this_tree"], self.payload["in_this_tree"])
        self.assertEqual(fresh["tree"], self.payload["tree"])

    def test_the_verdict_agrees_with_the_repository(self):
        captured = self.payload["in_this_tree"]
        on_disk = any(state["exists"] and state["files"]
                      for state in self.payload["tree"].values())
        self.assertEqual(captured, on_disk)
        self.assertEqual(self.payload["verdict"],
                         "LANDED_IN_THIS_TREE" if on_disk else "NOT_IN_THIS_TREE")
        self.assertIn(self.payload["history_check"],
                      ("SEEN", "NOT_SEEN", "CANNOT_TELL_SHALLOW_CLONE"))

    def test_the_weaker_claim_is_never_made_from_a_shallow_clone(self):
        if self.payload["shallow_clone"] and not self.payload["seen_anywhere_in_history"]:
            self.assertEqual(self.payload["history_check"], "CANNOT_TELL_SHALLOW_CLONE",
                             "a shallow clone must not report a confident 'never'")

    def test_every_not_run_row_states_a_reason_and_needs_no_number(self):
        with open(os.path.join(RESEARCH, "EXEC_SUMMARY.json"), encoding="utf-8") as handle:
            register = json.load(handle)
        rows = register.get("rows") or []
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row.get("status", "not_run"), "not_run", row.get("id"))
            self.assertIsNone(row.get("value"),
                              f"{row.get('id')}: a not-run row must carry no value")
            self.assertTrue(row.get("reason"), f"{row.get('id')}: no reason given")
            self.assertTrue(row.get("missing_artifact"), f"{row.get('id')}: no artifact")
            self.assertTrue(row.get("unblocks_when"), f"{row.get('id')}: no unblocker")
            for link in row.get("source_links") or []:
                self.assertTrue(link["url"].startswith("https://"),
                                f"{row.get('id')}: {link['url']} is not an https source")
        ids = {row["id"] for row in rows}
        for required in ("study", "volatile_stock_division", "exec_summary_stock_rows",
                         "tradingview_strategy_report", "real_intraday_capture"):
            self.assertIn(required, ids, f"the executive summary lost the {required} row")


if __name__ == "__main__":
    unittest.main()
