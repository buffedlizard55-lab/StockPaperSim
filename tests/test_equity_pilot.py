"""Forward-pilot tests: timestamped journal, evidence-gate refusals, reconcile."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sim import equity_pilot
from sim.strict_equities import PaperLedger, EvidenceGate, SessionSchedule
from sim.venue_admin import session_record
from sim.equity_pilot import market_making_gate, load_registry


class PilotRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.memo_root = cls.temp.name
        cls.report = equity_pilot.run_pilot_day(
            dt.date(2026, 9, 21),
            dt.datetime(2026, 9, 21, 19, 50, tzinfo=dt.timezone.utc),
            memo_root=cls.memo_root,
            seeded_rehearsal=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_run_report_contract(self):
        r = self.report
        self.assertEqual(r["fills"], 0)
        self.assertEqual(r["settlements"], 0)
        self.assertEqual(r["feeds_approved"], 0)
        self.assertGreater(r["orders_submitted"], 0)
        self.assertTrue(r["seeded_rehearsal"])
        self.assertEqual(r["orders_submitted"], len(r["orders_rejected"]))
        self.assertEqual(len(r["journal_head_after"]), 64)
        self.assertNotEqual(r["journal_head_before"], r["journal_head_after"])

    def test_every_order_is_timestamped_and_labelled(self):
        orders = json.loads(Path(self.memo_root, "run-2026-09-21", "orders.json").read_text())
        self.assertEqual(len(orders), self.report["orders_submitted"])
        for order in orders:
            self.assertTrue(order["order_id"])
            self.assertEqual(order["input_class"], "SECONDARY")
            self.assertTrue(order["signal_source_url"].startswith("https://"))
            self.assertEqual(order["symbol"], "SPY")

    def test_journal_chain_verifies_and_refusals_are_recorded(self):
        journal = Path(self.memo_root, "journal.sqlite")
        self.assertTrue(journal.exists())
        registry = load_registry()
        record = session_record(dt.date(2026, 9, 21))
        gate = EvidenceGate(Path(equity_pilot.REPO_ROOT), registry.get("approved_feeds", {}))
        ledger = PaperLedger(journal, gate, SessionSchedule({record["date"]: record}), registry["competition"])
        self.assertEqual(len(ledger.verify()), 64)
        kinds = {}
        for e in ledger.events():
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        self.assertGreater(kinds.get("ORDER", 0), 0)
        self.assertEqual(kinds.get("BLOCKED", 0), kinds.get("ORDER", 0))
        self.assertEqual(kinds.get("FILL", 0), 0)
        ledger.close()

    def test_reconcile_pass_and_upcoming_rows(self):
        recon = json.loads(Path(self.memo_root, "reconcile.json").read_text())
        self.assertEqual(recon["verdict"], "PASS")
        upcoming = json.loads(Path(self.memo_root, "upcoming_orders.json").read_text())
        self.assertEqual(upcoming["generated_for_session"], "2026-09-22")
        self.assertEqual(len(upcoming["rows"]), 18)  # 18 implementable specs x 1 symbol
        self.assertTrue(all(r["state"] == "SCHEDULED_FOR_EVALUATION" for r in upcoming["rows"]))

    def test_after_close_submission_expires_next_session(self):
        # The real cron runs ~21:10 UTC, AFTER the 20:00Z close. Orders must
        # still validate (expire at the next session's close, not today).
        orders = json.loads(Path(self.memo_root, "run-2026-09-21", "orders.json").read_text())
        for order in orders:
            self.assertEqual(order["expires_at"], "2026-09-22T20:00:00Z")
        self.assertEqual(self.report["execution_session"], "2026-09-22")

    def test_corporate_action_standdown_blocks_orders(self):
        from unittest import mock
        from sim.venue_admin import CorporateAction, CorporateActionTable
        table = CorporateActionTable(
            [CorporateAction("SPY", "SPLIT", "2026-09-21", "test split",
                             "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
                             "2026-09-19")],
            coverage_note="test-only sourced row",
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            equity_pilot, "load_corporate_actions", return_value=table
        ):
            report = equity_pilot.run_pilot_day(
                dt.date(2026, 9, 21),
                dt.datetime(2026, 9, 21, 21, 10, tzinfo=dt.timezone.utc),
                memo_root=tmp,
                seeded_rehearsal=True,
            )
        self.assertEqual(report["orders_submitted"], 0)
        stands = [s for s in report["signals_skipped"] if s["code"] == "CORP_ACTION_STANDDOWN"]
        self.assertEqual(len(stands), 8)
        self.assertIn("SPLIT@2026-09-21", stands[0]["actions"])

    def test_closed_day_refused(self):
        with self.assertRaises(ValueError):
            equity_pilot.run_pilot_day(
                dt.date(2026, 11, 26),  # Thanksgiving
                dt.datetime(2026, 11, 26, 19, 50, tzinfo=dt.timezone.utc),
                memo_root=self.memo_root,
            )

    def test_rerun_is_idempotent_by_dated_folder(self):
        # A second run for the same day appends no duplicate orders (idempotent ids).
        first = self.report["orders_submitted"]
        again = equity_pilot.run_pilot_day(
            dt.date(2026, 9, 21),
            dt.datetime(2026, 9, 21, 19, 50, tzinfo=dt.timezone.utc),
            memo_root=self.memo_root,
            seeded_rehearsal=True,
        )
        self.assertEqual(again["orders_submitted"], first)


class GateTests(unittest.TestCase):
    def test_market_making_gate_is_closed(self):
        registry = load_registry()
        gate = market_making_gate(registry)
        self.assertFalse(gate["allowed"])
        self.assertIn("NO_APPROVED_FEEDS_AT_ALL", gate["reasons"])

    def test_gate_opens_only_with_full_evidence(self):
        fake = {
            "approved_feeds": {
                "reg-feed": {
                    "source_class": "SIP",
                    "redistribution": "APPROVED",
                    "quote_size_evidence": True,
                    "queue_priority_evidence": True,
                }
            }
        }
        self.assertTrue(market_making_gate(fake)["allowed"])
        half = {"approved_feeds": {"reg-feed": {"redistribution": "APPROVED", "quote_size_evidence": True}}}
        gate = market_making_gate(half)
        self.assertFalse(gate["allowed"])
        self.assertTrue(any("NO_QUEUE_PRIORITY" in r for r in gate["reasons"]))


if __name__ == "__main__":
    unittest.main()
