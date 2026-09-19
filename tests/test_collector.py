"""Pure tests for the official Nasdaq collection adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

from fixtures import REPO_ROOT


SPEC = importlib.util.spec_from_file_location(
    "collect_real_data", os.path.join(REPO_ROOT, "scripts", "collect_real_data.py"))
COLLECTOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(COLLECTOR)


class FakeFetcher:
    def __init__(self):
        self.manifest = []
        self.budget_exhausted = False

    def get(self, url, kind, headers=None, note="", **kwargs):
        if "/dividends?" in url:
            body = {
                "data": {"dividends": {"rows": [
                    {"exOrEffDate": "01/02/2025", "type": "Cash",
                     "amount": "$0.25", "currency": "USD", "paymentDate": "01/15/2025"}
                ]}}
            }
        else:
            body = {
                "data": {"totalRecords": 2, "tradesTable": {"rows": [
                    {"date": "01/03/2025", "close": "$102.00", "volume": "1,100",
                     "open": "$101.00", "high": "$103.00", "low": "$100.00"},
                    {"date": "01/02/2025", "close": "$101.00", "volume": "1,000",
                     "open": "$100.00", "high": "$102.00", "low": "$99.00"},
                ]}}
            }
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        import hashlib
        self.manifest.append({"url": url, "kind": kind, "source_class": "OFFICIAL",
                              "status": 200, "bytes": len(raw),
                              "sha256": hashlib.sha256(raw).hexdigest(), "ok": True,
                              "fetched_at": "2026-09-18T00:00:00Z", "note": note})
        return raw


class TestNasdaqCollector(unittest.TestCase):
    def test_normalized_files_keep_raw_response_and_explicit_status(self):
        with tempfile.TemporaryDirectory(prefix="collector-") as root:
            fetcher = FakeFetcher()
            summary = COLLECTOR.collect_nasdaq(fetcher, root)
            self.assertEqual(len(summary["ok"]), len(COLLECTOR.NASDAQ_ASSETCLASS))
            self.assertFalse(summary["failed"])
            self.assertEqual(summary["dividends_failed"], [])
            path = os.path.join(root, "prices", "nasdaq", "AAPL.json")
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["source_class"], "OFFICIAL")
            self.assertEqual(payload["redistribution_status"], "NOT_AUTHORIZED_BY_TERMS")
            self.assertEqual(payload["dividend_status"], "AVAILABLE")
            self.assertEqual(payload["bars"][0]["date"], "2025-01-02")
            self.assertEqual(payload["dividends"][0]["date"], "2025-01-02")
            self.assertTrue(os.path.exists(os.path.join(root, "raw", "nasdaq", "AAPL.json")))
            self.assertTrue(payload["raw_sha256"])
            self.assertEqual(payload["retrieved_at"], "2026-09-18T00:00:00Z")


if __name__ == "__main__":
    unittest.main()


class EspnRequestFormTest(unittest.TestCase):
    """The scoreboard request form must match what each sport's calendar is.

    Verified against the live endpoint on 2026-09-19: the weekly form fills
    the football files, but basketball and baseball IGNORE the week parameter
    and answer a bare season year with the whole season - the week form
    returned zero basketball events on every prior run, which is why
    nba_scoreboard.jsonl stayed empty and the NBA personas reported
    DATA-MISSING.
    """

    def test_football_uses_the_week_form(self):
        for key, path in (("nfl", "football/nfl"), ("ncaaf", "football/college-football")):
            urls = [u for u, _ in COLLECTOR._espn_requests(key, path)]
            self.assertTrue(urls, key)
            for url in urls:
                self.assertIn("seasontype=", url)
                self.assertIn("week=", url)
                self.assertNotIn("dates=2026&limit=5000", url)

    def test_basketball_and_baseball_use_the_whole_season_form(self):
        for key, path in (("nba", "basketball/nba"), ("mlb", "baseball/mlb")):
            urls = [u for u, _ in COLLECTOR._espn_requests(key, path)]
            self.assertTrue(urls, key)
            for url in urls:
                self.assertNotIn("week=", url, url)
                self.assertNotIn("seasontype=", url, url)
                self.assertRegex(url, r"dates=\d{4}&limit=5000$")

    def test_the_nba_season_year_is_the_one_inside_the_trading_window(self):
        # dates=2026 is the 2025-26 NBA season (the year names the season that
        # ends in it); dates=2025 would be 2024-25, which is before the window.
        urls = [u for u, _ in COLLECTOR._espn_requests("nba", "basketball/nba")]
        self.assertEqual(urls, [
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
            "scoreboard?dates=2026&limit=5000"])

    def test_every_request_carries_an_explanatory_note(self):
        for key, path in (("nfl", "football/nfl"), ("nba", "basketball/nba"),
                          ("mlb", "baseball/mlb")):
            for url, note in COLLECTOR._espn_requests(key, path):
                self.assertTrue(note, (key, url))

    def test_the_parser_drops_preseason_events(self):
        """Whole-season payloads mix spring training in; weeks never did."""

        class WholeSeasonFetcher:
            def __init__(self):
                self.manifest = []

            def get(self, url, kind, headers=None, note="", **kwargs):
                def event(type_, date, status="STATUS_FINAL"):
                    return {
                        "date": date, "id": f"e{type_}{date}", "name": "A at B",
                        "season": {"type": type_},
                        "competitions": [{"status": {"type": {"name": status}},
                                          "venue": {"fullName": "V"},
                                          "competitors": [
                                              {"homeAway": "home", "score": "110",
                                               "team": {"displayName": "Home Team"}},
                                              {"homeAway": "away", "score": "99",
                                               "team": {"displayName": "Away Team"}}]}]}
                body = {"events": [event(1, "2025-10-05T23:00Z"),
                                   event(2, "2025-10-21T23:00Z"),
                                   event(3, "2026-04-20T23:00Z"),
                                   event(None, "2025-10-22T23:00Z")]}
                raw = json.dumps(body).encode("utf-8")
                self.manifest.append({"url": url})
                return raw

        with tempfile.TemporaryDirectory() as tmp:
            COLLECTOR.collect_espn(WholeSeasonFetcher(), tmp)
            # Only basketball gets rows here: the fetcher answers every URL
            # with the same body, and football's week list is non-empty too,
            # so nfl/ncaaf files also exist.  The assertion is on basketball.
            rows = [json.loads(line) for line in open(
                os.path.join(tmp, "sports", "nba_scoreboard.jsonl"))]
        # Preseason (type 1) is dropped; regular season, postseason and an
        # event with no season block at all are kept, in payload order.
        self.assertEqual([r["date"] for r in rows],
                         ["2025-10-21", "2026-04-20", "2025-10-22"])


class InsiderBulkBudgetTest(unittest.TestCase):
    """The insider ZIP walk must be bounded by its own sub-budget.

    The 2026-09-19 run proved a throttled www.sec.gov burns ~40 seconds per
    URL with nothing to show for it; unbounded, that consumed the minutes the
    sports section needed, and the sections after it never started.
    """

    def test_an_expired_sub_budget_records_the_skipped_quarters(self):
        fetcher = COLLECTOR.Fetcher([], max_seconds=60.0)
        # A deadline already in the past: every quarter must be skipped and
        # no URL may be requested at all.
        COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS_BACKUP = (
            COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS)
        try:
            COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS = -1.0
            with tempfile.TemporaryDirectory() as tmp:
                summary = COLLECTOR.collect_insider_bulk(fetcher, tmp)
        finally:
            COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS = (
                COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS_BACKUP)
        self.assertTrue(summary["sub_budget_exhausted"])
        self.assertEqual(summary["rows"], 0)
        self.assertEqual(summary["quarters_skipped"], summary["quarters"])
        self.assertEqual(fetcher.manifest, [])

    def test_the_sub_budget_is_a_slice_not_the_whole_run(self):
        self.assertLessEqual(COLLECTOR.INSIDER_BULK_SUB_BUDGET_SECONDS, 300.0)
