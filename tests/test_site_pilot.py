"""Pilot site builder: pages exist, say the honest state, link official sources."""
import tempfile
import unittest
from pathlib import Path

from scripts.build_site_pilot import build


class PilotSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.stats = build("memory/pilot", cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _page(self, name):
        path = Path(self.temp.name, "pilot", name)
        self.assertTrue(path.exists(), name)
        return path.read_text()

    def test_five_pages_and_snapshot(self):
        self.assertEqual(self.stats["pages"], 5)
        for name in ("index.html", "upcoming.html", "blotter.html", "method.html", "sources.html"):
            html = self._page(name)
            self.assertIn("<title>", html)
            self.assertIn("PAPER ONLY", html)
        snap = Path(self.temp.name, "pilot", "snapshot.json")
        self.assertTrue(snap.exists())

    def test_standard_shell_and_stylesheet(self):
        html = self._page("index.html")
        self.assertIn("../desk/desk.css", html)
        self.assertIn('href="../desk/index.html"', html)

    def test_states_zero_fills_and_mm_gate_closed(self):
        index = self._page("index.html")
        # With the seeded rehearsal run archived the numbers are counts, not prose.
        self.assertIn("Reconciliation verdict: PASS", index)
        self.assertIn("Market making allowed: False", index)
        self.assertIn("NO_APPROVED_FEEDS_AT_ALL", index)
        self.assertNotIn("Leaderboard", index)

    def test_upcoming_rows_are_plan_only(self):
        page = self._page("upcoming.html")
        self.assertIn("PLAN ONLY", page)
        self.assertIn("SCHEDULED_FOR_EVALUATION", page)
        self.assertIn("nyse.com/trade/hours-calendars", page)

    def test_blotter_lists_orders_with_timestamps_and_refusals(self):
        page = self._page("blotter.html")
        self.assertIn("2026-09-21T21:10:00Z", page)
        self.assertIn("NO_APPROVED_OFFICIAL_FEED", page)
        self.assertIn("SECONDARY", page)

    def test_sources_page_links_official_pages(self):
        page = self._page("sources.html")
        for needle in (
            "https://www.nyse.com/trade/hours-calendars",
            "https://www.frbservices.org/about/holiday-schedules",
            "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule",
            "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
            "research/strict/registry.json",
        ):
            self.assertIn(needle, page, needle)

    def test_method_documents_fees_without_minimum(self):
        page = self._page("method.html")
        self.assertIn("$0.000195", page)
        self.assertIn("$9.79", page)
        self.assertIn("no $0.01 minimum on covered equity", page)

    def test_deterministic_rebuild(self):
        temps2 = tempfile.TemporaryDirectory()
        self.addCleanup(temps2.cleanup)
        stats2 = build("memory/pilot", temps2.name)
        for name in ("index.html", "upcoming.html", "blotter.html", "snapshot.json"):
            self.assertEqual(
                Path(self.temp.name, "pilot", name).read_bytes(),
                Path(temps2.name, "pilot", name).read_bytes(),
                name,
            )
        self.assertEqual(self.stats["runs"], stats2["runs"])


if __name__ == "__main__":
    unittest.main()
