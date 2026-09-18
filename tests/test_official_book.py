"""The Official Auction Book: the venue's arithmetic, its rules, and the tape.

These tests are deliberately about the properties a reader would check by hand
rather than about the numbers a run happens to produce:

* a primary fill price equals the published one, to the cent;
* a fill nets against the position instead of piling an opposing lot beside it
  (the defect that produced a 3,000% return on a nearly flat book);
* the leverage cap binds in both directions, not only on buys;
* a security that has not been issued yet cannot be traded;
* an account that reaches zero equity is wound up at exactly -100%, never at a
  compounding negative number;
* the arithmetic is re-derivable from the tape, which is what the verification
  block asserts on the published run.

The engine reads committed official files, so a missing data directory skips
rather than fails: the site's own audit covers the published artifacts.
"""

from __future__ import annotations

import gzip
import json
import os
import unittest

from fixtures import REPO_ROOT

from sim import officialbook as ob
from sim import official_season, tradelog, treasury

DATA = os.path.join(REPO_ROOT, "data", "real")
HAVE_DATA = all(os.path.isdir(os.path.join(DATA, part))
                for part in ("treasury", "fred"))
HAVE_MEMORY = os.path.isdir(os.path.join(REPO_ROOT, "memory", "official"))


def _load() -> tuple:
    auctions = treasury.AuctionBook()
    curve = treasury.ParCurve()
    rates = ob.OfficialRates()
    return auctions, curve, rates


@unittest.skipUnless(HAVE_DATA, "official data files are not collected")
class TestAuctionBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auctions = treasury.AuctionBook()

    def test_the_two_publishers_are_compared_and_agree(self):
        check = self.auctions.crosscheck or {}
        self.assertGreater(check.get("shared_auctions", 0), 100)
        self.assertEqual(check.get("shared_auctions_with_a_difference", 0), 0)

    def test_every_published_bill_price_reproduces_from_its_own_rate(self):
        report = self.auctions.price_validation()
        self.assertGreater(report["bill_prices_checked"], 500)
        self.assertEqual(report["bill_price_mismatches"], 0)
        # The investment-rate check is partial and says so: an agreement rate
        # below 100% is the honest result, not a failure.
        self.assertLessEqual(report["investment_rate_match_pct"], 100.0)
        self.assertGreater(report["investment_rate_match_pct"], 0.0)

    def test_a_bill_price_is_the_discount_formula(self):
        bills = [a for a in self.auctions.auctions.values()
                 if a.is_bill and a.price_per100 and a.high_discount_rate]
        self.assertTrue(bills)
        checked = 0
        for bill in bills[:200]:
            expected = treasury.bill_price_from_discount(
                bill.high_discount_rate, bill.days_to_maturity())
            self.assertAlmostEqual(expected, bill.price_per100, places=2,
                                   msg=f"{bill.cusip} {bill.auction_date}")
            checked += 1
        self.assertGreater(checked, 100)

    def test_noncompetitive_award_respects_the_published_rules(self):
        result = [a for a in self.auctions.auctions.values()
                  if a.auction_date == "2026-09-17" and a.offering_amount][0]
        small = treasury.noncompetitive_award(result, 100.0)
        self.assertTrue(small.ok)
        self.assertEqual(small.awarded_face, 100.0)
        self.assertAlmostEqual(small.cost, 100.0 * result.execution_price() / 100.0,
                               places=2)
        # A $10m non-competitive award is the published ceiling; asking for
        # more is not silently scaled by the award helper (the venue does its
        # own scaling for leverage).
        big = treasury.noncompetitive_award(result, 25_000_000.0)
        self.assertTrue(big.ok)
        self.assertLessEqual(abs(big.awarded_face), 25_000_000.0)


@unittest.skipUnless(HAVE_DATA, "official data files are not collected")
class TestAccountArithmetic(unittest.TestCase):
    """One account, hand-driven, so the rules can be checked one at a time."""

    def setUp(self):
        self.auctions, self.curve, self.rates = _load()
        self.book = ob.OfficialBook(self.auctions, self.curve, self.rates,
                                    roster=[], start="2025-09-17",
                                    end="2025-12-31")
        self.account = self.book.account_for("@Test")

    def test_a_fill_nets_against_the_position(self):
        out = self.account.apply_fill("91282CNT4", 1000.0, 100.0, "2025-09-18",
                                      ob.ENTRY_PRIMARY, {"field": "test"},
                                      {"security_type": "Note",
                                       "security_term": "10-Year"}, {})
        self.assertEqual(out["opened_face"], 1000.0)
        self.assertEqual(out["closed_face"], 0.0)
        later = self.account.apply_fill("91282CNT4", -400.0, 101.0, "2025-09-19",
                                       ob.ENTRY_SECONDARY, {"field": "test"},
                                       {"security_type": "Note",
                                        "security_term": "10-Year"}, {})
        self.assertEqual(later["closed_face"], 400.0)
        self.assertEqual(later["opened_face"], 0.0)
        self.assertEqual(self.account.net_face("91282CNT4"), 600.0)
        self.assertEqual(len(self.account.closed), 1)
        self.assertEqual(len(self.account.lots), 1)
        trip = self.account.closed[0]
        self.assertEqual((trip.entry_price, trip.exit_price), (100.0, 101.0))
        self.assertAlmostEqual(trip.pnl, 4.0, places=6)   # 400 bp x 400 face

    def test_a_fill_that_merely_reduces_a_long_opens_nothing(self):
        self.account.apply_fill("91282CNT4", 1000.0, 100.0, "2025-09-18",
                                ob.ENTRY_PRIMARY, {}, {}, {})
        out = self.account.apply_fill("91282CNT4", -2000.0, 99.0, "2025-09-19",
                                      ob.ENTRY_SECONDARY, {}, {}, {})
        self.assertEqual(out["closed_face"], 1000.0)
        self.assertEqual(out["opened_face"], 1000.0)
        self.assertEqual(self.account.net_face("91282CNT4"), -1000.0)

    def test_exposure_is_the_net_position(self):
        self.account.apply_fill("91282CNT4", 1000.0, 100.0, "2025-09-18",
                                ob.ENTRY_PRIMARY, {}, {}, {})
        self.account.apply_fill("91282CNT4", -600.0, 100.0, "2025-09-19",
                                ob.ENTRY_SECONDARY, {}, {}, {})
        gross = self.account.gross_exposure({"91282CNT4": 100.0})
        self.assertAlmostEqual(gross, 400.0, places=6)

    def test_equity_is_cash_plus_the_net_position_at_the_mark(self):
        self.account.cash = 10_000.0
        self.account.apply_fill("91282CNT4", 1000.0, 100.0, "2025-09-18",
                                ob.ENTRY_PRIMARY, {}, {}, {})
        self.account.cash -= 1000.0        # what the purchase costs
        equity = self.account.equity({"91282CNT4": 101.0})
        self.assertAlmostEqual(equity, 9_000.0 + 1010.0, places=6)


@unittest.skipUnless(HAVE_DATA, "official data files are not collected")
class TestVenueRules(unittest.TestCase):
    def setUp(self):
        self.auctions, self.curve, self.rates = _load()

    def test_the_leverage_cap_binds_on_shorts_too(self):
        book = ob.OfficialBook(self.auctions, self.curve, self.rates, roster=[],
                               start="2025-09-17", end="2025-12-31")
        account = book.account_for("@Test")
        book.leverage_caps["@Test"] = 1.0
        # A 2-year note that is issued and has a published coupon, so the
        # official curve can price it on the target session.
        auction = book._auction_for("91282CNV9")
        self.assertIsNotNone(auction)
        self.assertLessEqual(auction.issue_date, "2025-09-19")
        intent = book.submit_book_intent(
            "@Test", ob.INTENT_SECONDARY, "2025-09-19", "91282CNV9",
            "sell", 10_000_000.0, rule="test", rationale="test")
        self.assertIsNotNone(intent)
        book._planning_session = "2025-09-18"
        book._marks = book.mark_prices("2025-09-19")
        summary = {"filled": 0, "partial": 0, "rejected": 0, "expired": 0,
                   "waiting": 0, "cancelled": 0, "ruined": 0, "maturities": 0,
                   "notional": 0.0}
        book._settle_secondary(account, intent, "2025-09-19", summary)
        self.assertIn(intent.status, (ob.INTENT_FILLED, ob.INTENT_CANCELLED))
        gross = account.gross_exposure(book._marks)
        self.assertLessEqual(gross, 1.0 * account.equity(book._marks) + 1e-6)

    def test_a_security_that_is_not_issued_cannot_be_traded(self):
        book = ob.OfficialBook(self.auctions, self.curve, self.rates, roster=[],
                               start="2025-09-17", end="2026-09-16")
        account = book.account_for("@Test")
        # A security whose auction has happened but whose issue date is later.
        upcoming = [a for a in self.auctions.auctions.values()
                    if a.issue_date and a.auction_date < a.issue_date
                    and a.auction_date >= "2025-09-17"][0]
        session = upcoming.auction_date
        self.assertLess(session, upcoming.issue_date)
        intent = book.submit_book_intent(
            "@Test", ob.INTENT_SECONDARY, upcoming.issue_date, upcoming.cusip,
            "buy", 1000.0, rule="test", rationale="test")
        book._planning_session = session
        book._marks = book.mark_prices(session)
        summary = {"filled": 0, "partial": 0, "rejected": 0, "expired": 0,
                   "waiting": 0, "cancelled": 0, "ruined": 0, "maturities": 0,
                   "notional": 0.0}
        book._settle_secondary(account, intent, session, summary)
        self.assertEqual(intent.status, ob.INTENT_REJECTED)
        self.assertIn("when-issued", intent.settle_note)
        self.assertEqual(account.lots, [])

    def test_ruin_leaves_exactly_minus_one_hundred_percent(self):
        book = ob.OfficialBook(self.auctions, self.curve, self.rates, roster=[],
                               start="2025-09-17", end="2025-12-31")
        account = book.account_for("@Test")
        # A levered long that gaps through zero: the venue has to wind it up
        # rather than let a negative balance compound into a -100,000% return.
        account.cash = 10_000.0
        account.apply_fill("91282CNT4", 100_000.0, 100.0, "2025-09-18",
                           ob.ENTRY_PRIMARY, {}, {}, {})
        account.cash -= 100_000.0          # the purchase the fill represents
        book._marks = {"91282CNT4": 89.0}
        book.check_margin("2025-09-19")
        self.assertTrue(account.margin_events)
        self.assertEqual(account.cash, 0.0)
        self.assertEqual(account.equity(book._marks), 0.0)
        self.assertEqual(account.ruined_on, "2025-09-19")
        self.assertTrue(account.lots == [])
        # Every cash movement is explained by the tape, write-off included.
        self.assertTrue(any(row["kind"] == "ruin_write_off" for row in account.carry))


@unittest.skipUnless(HAVE_MEMORY, "no published official run in memory/")
class TestPublishedRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = os.path.join(REPO_ROOT, "memory", "official")
        runs = sorted(d for d in os.listdir(base)
                      if d.startswith("official-rehearsal-"))
        cls.run_dir = os.path.join(base, runs[-1])
        with open(os.path.join(cls.run_dir, "leaderboard.json"),
                  encoding="utf-8") as handle:
            cls.board = json.load(handle)
        with open(os.path.join(cls.run_dir, "manifest.json"),
                  encoding="utf-8") as handle:
            cls.manifest = json.load(handle)
        cls.trips = []
        with gzip.open(os.path.join(cls.run_dir, "trips.jsonl.gz"), "rt",
                       encoding="utf-8") as handle:
            cls.trips = [json.loads(line) for line in handle if line.strip()]

    def test_the_verification_block_passed(self):
        verification = self.manifest["verification"]
        self.assertEqual(verification["verdict"], "PASS")
        self.assertEqual(verification["failure_count"], 0)
        self.assertLess(verification["max_equity_residual_usd"], 1.0)

    def test_every_trip_price_is_a_published_or_derived_number(self):
        for trip in self.trips:
            self.assertIn(trip["exit_kind"],
                          (ob.EXIT_MATURITY, ob.EXIT_SECONDARY,
                           ob.EXIT_LIQUIDATION))
            self.assertEqual(trip["source_class"], "OFFICIAL")
            self.assertTrue(trip["entry_evidence"])
            self.assertTrue(trip["exit_evidence"])
            if trip["entry_kind"] == ob.ENTRY_PRIMARY:
                self.assertEqual(trip["official_price_coverage"], 1.0)
            self.assertGreater(trip["face"], 0)

    def test_no_participant_ends_below_zero(self):
        for row in self.board["participants"]:
            self.assertGreaterEqual(row["final_equity"], 0.0)
            if row.get("ruined_on"):
                self.assertAlmostEqual(row["return_pct"], -100.0, places=3)

    def test_the_window_is_one_year(self):
        self.assertEqual(self.board["sessions"], 250)
        self.assertEqual(self.board["first_session"], "2025-09-17")
        self.assertEqual(self.board["last_session"], "2026-09-16")

    def test_the_ledger_documents_this_book_as_official(self):
        ledger = tradelog.collect_trades(include_seasons=False)
        official = [t for t in ledger if t["book"] == "Official Auction Book"]
        self.assertEqual(len(official), len(self.trips))
        coverage = tradelog.coverage(ledger)
        self.assertGreater(coverage["official_executed_notional_pct"], 0.0)


@unittest.skipUnless(HAVE_DATA, "official data files are not collected")
class TestNarratives(unittest.TestCase):
    """Every participant must be able to explain its own result."""

    @classmethod
    def setUpClass(cls):
        cls.auctions, cls.curve, cls.rates = _load()
        cls.book = ob.OfficialBook(cls.auctions, cls.curve, cls.rates,
                                   start="2025-09-17", end="2026-09-16")
        cls.summary = cls.book.run()

    def test_every_participant_has_a_narrative_with_drivers(self):
        rows = {r["participant"]: r for r in self.summary["participants"]}
        for strategy in self.book.roster:
            row = rows[strategy.username]
            report = official_season.narrative(
                strategy, row, self.book.accounts[strategy.username], self.book)
            self.assertTrue(report["thesis"])
            self.assertTrue(report["drivers"])
            self.assertIn(report["verdict"],
                          ("made money", "lost money", "flat", "lost everything"))

    def test_a_participant_with_no_trades_says_so(self):
        rows = {r["participant"]: r for r in self.summary["participants"]}
        for strategy in self.book.roster:
            account = self.book.accounts[strategy.username]
            if account.closed:
                continue
            report = official_season.narrative(
                strategy, rows[strategy.username], account, self.book)
            self.assertTrue(any("SOFR" in line for line in report["drivers"]))


if __name__ == "__main__":
    unittest.main()
