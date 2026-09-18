"""Tests for the live forward book (``sim/live.py``).

The live book is the only part of the project that places a trade for a session
that has not happened yet, so the properties worth testing are the ones that stop
it from cheating or from inventing data:

* **the clock** - a forward intent can never target its own plan session, and no
  input an intent records may be dated after the plan date;
* **the calendar** - the horizon is the collected session list inside the
  collected window and a labelled projection past it, so no intent is ever aimed
  at a market holiday (this is a regression test: an earlier revision scheduled
  trades on 2025-12-25 and eight other closures, and they sat ``PENDING`` for
  ever);
* **the account** - leverage stays inside the declared bound, a maintenance
  breach is liquidated rather than ignored, and cash plus positions re-derive from
  the fill tape to the cent;
* **the tape** - every fill names a reference bar, a file, a SHA-256 and a source
  class, and the storage is the compressed append-only format it claims to be;
* **the bookkeeping** - a forward book where nothing has settled reports no
  return, and the rehearsal is deterministic across processes.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import REPO_ROOT
from sim import live, live_season, realdata

DOCS = os.path.join(REPO_ROOT, "docs")


def _quick_book(horizon: int = 1):
    """A rehearsal book over a short window: enough to exercise every path."""
    md = live_season.live_market()
    feed = live.OfficialFeed()
    roster = live_season.build_live_roster()
    book = live.LiveBook(md, feed, roster, cfg=live_season.live_config("rehearsal"),
                         mode="rehearsal")
    start = md.dates[md.first_competition_index]
    end = md.dates[md.first_competition_index + 40]
    outcome = book.run(start, end, horizon=horizon)
    return md, feed, book, outcome


class TestForwardCalendar(unittest.TestCase):
    def test_projection_skips_weekends_and_published_closures(self):
        rows = live.project_sessions("2026-11-20", 8)
        dates = [r["date"] for r in rows]
        for date in dates:
            self.assertLess(dt.date.fromisoformat(date).weekday(), 5, date)
            self.assertNotIn(date, live.PROJECTED_CLOSURES, date)
        self.assertNotIn("2026-11-26", dates, "Thanksgiving is a closure")
        self.assertNotIn("2026-12-25", dates, "Christmas is a closure")
        self.assertIn("2026-11-27", dates, "the day after Thanksgiving trades early")
        early = {r["date"] for r in rows if r["early_close"]}
        self.assertIn("2026-11-27", early)

    def test_projection_stops_at_the_published_limit(self):
        rows = live.project_sessions("2026-12-20", 30)
        self.assertTrue(rows)
        self.assertLessEqual(max(r["date"] for r in rows), live.PROJECTION_LIMIT)

    def test_every_projected_row_names_its_source(self):
        for row in live.project_sessions("2026-11-20", 3):
            self.assertEqual(row["status"], "PROJECTED")
            self.assertEqual(row["source"], live.PROJECTION_SOURCE)


class TestClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md, cls.feed, cls.book, cls.outcome = _quick_book()

    def test_no_intent_targets_its_own_plan_session_or_earlier(self):
        self.assertTrue(self.book.intents)
        for intent in self.book.intents:
            self.assertGreater(intent.intended_session, intent.created_on,
                               f"{intent.intent_id} is not a forward intent")
            self.assertIn(intent.intended_session, self.md.dates)

    def test_every_evidence_observation_predates_the_plan_date(self):
        for intent in self.book.intents:
            for name, evidence in intent.evidence.items():
                date = str(evidence.get("observation_date") or "")
                if date:
                    self.assertLessEqual(
                        date, intent.created_on,
                        f"{intent.intent_id} read {name} dated {date} after "
                        f"{intent.created_on}")

    def test_no_intent_is_aimed_at_a_market_closure(self):
        """Regression: 2025-12-25 and eight other closures were once targets."""
        calendar = self.md.calendar
        for intent in self.book.intents:
            self.assertNotIn(intent.intended_session, calendar.closed_dates,
                             f"{intent.intent_id} targets a session the market was shut")

    def test_evidence_names_a_source(self):
        for intent in self.book.intents:
            for name, evidence in intent.evidence.items():
                self.assertTrue(evidence.get("file") or evidence.get("url"),
                                f"{intent.intent_id} cites {name} with no source")

    def test_a_same_session_intent_is_refused(self):
        md = self.md
        account = live.LiveAccount("@TestClock", 100_000.0)
        ctx = live.LiveContext(md, {}, md.dates[10], account,
                               [{"date": md.dates[10], "status": "COLLECTED",
                                 "early_close": False, "source": "test"}],
                               live_config_margin(), None)
        with self.assertRaises(ValueError):
            ctx.place("SPY", "buy", quantity=10)


def live_config_margin():
    from sim import config
    return config.MarginConfig()


class TestAccount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md, cls.feed, cls.book, cls.outcome = _quick_book()

    def test_leverage_stays_inside_the_declared_bound(self):
        for account in self.book.accounts.values():
            for mark in account.marks:
                self.assertIsNotNone(mark["leverage_x"])
                self.assertLessEqual(
                    mark["leverage_x"], self.book.cfg.margin.max_gross_leverage + 0.02,
                    f"{account.participant} reached {mark['leverage_x']}x at "
                    f"{mark['session']}")

    def test_verification_passes_on_the_real_book(self):
        result = live.verify_live(self.book, self.outcome["marks"])
        self.assertEqual(result["failure_count"], 0, result["failures"][:3])
        self.assertGreater(result["checks"], 500)
        self.assertLess(result["max_abs_cash_residual_usd"], 0.02)

    def test_every_fill_records_its_reference_bar_and_provenance(self):
        fills = [f for a in self.book.accounts.values() for f in a.fills]
        self.assertTrue(fills)
        for fill in fills:
            for field in ("reference_close", "reference_file", "reference_sha256",
                          "reference_source_class", "date", "avg_price"):
                self.assertIsNotNone(fill.get(field), f"{field} missing on {fill}")
            self.assertGreater(fill["avg_price"], 0)

    def test_a_maintenance_breach_is_liquidated_not_ignored(self):
        """Every recorded breach must be followed by a forced liquidation."""
        for account in self.book.accounts.values():
            calls = [e for e in account.margin_events
                     if e.get("action", "").startswith("maintenance call")]
            if not calls:
                continue
            self.assertTrue(
                any(e.get("action") == "forced liquidation executed"
                    for e in account.margin_events),
                f"{account.participant} breached with no liquidation recorded")
            forced = [f for f in account.fills if f.get("forced")]
            self.assertTrue(forced, f"{account.participant}: no forced order on the tape")

    def test_negative_equity_is_reported_as_a_breach(self):
        from sim import config
        account = live.LiveAccount("@TestBreach", 1_000.0)
        account.cash = -500.0
        account.positions = {"SPY": -100}
        breach = account.maintenance_breach({"SPY": 100.0}, config.MarginConfig())
        self.assertIsNotNone(breach)
        self.assertGreater(breach["shortfall"], 0)


class TestTape(unittest.TestCase):
    """The written tape, produced into a scratch memory root.

    Writing into the committed ``memory/`` tree from a test is not harmless: the
    published-site CI job rebuilds ``docs/`` from ``memory/`` and diffs it, so a
    test that rewrites the run makes the site stale and the build red. Every class
    here therefore emits into its own temporary root and only *reads* the
    committed one.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sps-live-tape-")
        cls.result = live_season.run_rehearsal(root=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run_writes_every_declared_stream_with_a_checksum(self):
        streams = self.result["manifest"]["storage"]["streams"]
        for name in live.STREAMS:
            self.assertIn(name, streams)
            # ``file`` is recorded relative to the repository root on purpose (it
            # is what the site prints), so it resolves from REPO_ROOT even when
            # the run was written into a scratch directory outside it.
            rel = streams[name]["file"]
            candidate = os.path.normpath(os.path.join(REPO_ROOT, rel))
            self.assertTrue(os.path.exists(candidate), f"{rel} -> {candidate}")
            self.assertEqual(len(streams[name]["sha256"]), 64)

    def test_storage_is_gzip_jsonlines_and_compact(self):
        streams = self.result["manifest"]["storage"]["streams"]
        for name, row in streams.items():
            self.assertTrue(row["file"].endswith(".jsonl.gz"), row["file"])
            self.assertIsNotNone(row["compression_ratio"])
            self.assertGreater(row["compression_ratio"], 1.0)
        self.assertLess(self.result["manifest"]["storage"]["bytes_per_row_average"], 200)

    def test_streams_read_back_and_round_trip(self):
        run_dir = self.result["run_dir"]
        rows = live.read_live_run(run_dir)
        for name in live.STREAMS:
            self.assertIsInstance(rows[name], list)
        self.assertEqual(len(rows["intents"]),
                         self.result["manifest"]["counts"]["intents"])
        blob = gzip.open(os.path.join(run_dir, "fills.jsonl.gz"), "rt",
                         encoding="utf-8")
        with blob as handle:
            first = json.loads(handle.readline())
        self.assertIn("participant", first)
        self.assertIn("reference_sha256", first)

    def test_every_intent_reaches_a_terminal_state(self):
        run_dir = self.result["run_dir"]
        intents = live.read_live_run(run_dir)["intents"]
        self.assertTrue(intents)
        stalled = [i["intent_id"] for i in intents
                   if i["status"] in (live.INTENT_PENDING, live.INTENT_WAITING_DATA)]
        # Every session inside the rehearsal window is collected, so nothing may
        # be left waiting: a stalled intent here means the horizon resolved to a
        # session with no bar, which is exactly the holiday defect.
        self.assertEqual(stalled, [])

    def test_verdicts_explain_idle_participants_rather_than_printing_zero(self):
        board = {r["username"]: r for r in self.result["leaderboard"]}
        idle = [r for r in board.values() if r["fills"] == 0]
        self.assertTrue(idle, "the roster is expected to contain idle participants")
        for row in idle:
            self.assertIn("no trades placed", row["verdict"])
            self.assertNotEqual(row["data_status"], "READY")


class TestForwardBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sps-live-forward-")
        cls.result = live_season.run_forward(root=cls.tmp, horizon=3)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_nothing_settles_before_the_data_exists(self):
        self.assertTrue(self.result["pending"])
        for intent in self.result["pending"]:
            self.assertEqual(intent["status"], live.INTENT_PENDING)
            self.assertGreater(intent["intended_session"],
                               self.result["manifest"]["plan_date"])
            self.assertIsNone(intent["settled_on"])
            self.assertIsNone(intent["fill_id"])

    def test_the_manifest_says_pending_rather_than_claiming_a_return(self):
        status = self.result["manifest"]["status"]
        self.assertIn("PENDING-SETTLEMENT", status)
        counts = self.result["manifest"]["counts"]
        self.assertEqual(counts["fills"], 0)
        self.assertEqual(counts["marks"], 0)

    def test_the_plan_date_is_the_last_verified_session(self):
        self.assertEqual(self.result["manifest"]["plan_date"],
                         self.result["manifest"]["last_verified_equity_session"])
        self.assertEqual(self.result["manifest"]["plan_date"],
                         live.LIVE_LAST_VERIFIED_SESSION)

    def test_forward_verification_checks_the_clock_with_no_fills(self):
        verification = self.result["verification"]
        self.assertEqual(verification["failure_count"], 0)
        self.assertGreater(verification["checks"], 0)


class TestOfficialFeed(unittest.TestCase):
    def test_every_registered_series_loads_with_provenance(self):
        feed = live.OfficialFeed()
        self.assertEqual(feed.missing, [])
        self.assertEqual(set(feed.series), {r["sid"] for r in
                                            live.OFFICIAL_SERIES_REGISTER})
        for row in feed.provenance():
            for field in ("sid", "publisher", "source_class", "url_series",
                          "url_csv", "sha256", "file", "observations",
                          "fred_copyright_tag", "frequency"):
                self.assertIn(field, row, f"{row.get('sid')} is missing {field}")
            self.assertGreater(row["observations"], 100)

    def test_truncation_never_exposes_the_future(self):
        feed = live.OfficialFeed()
        truncated = feed.truncated("2025-12-31")
        for sid, series in truncated.items():
            self.assertLessEqual(series.last_date(), "2025-12-31", sid)
        nasdaq = truncated["NASDAQCOM"]
        self.assertEqual(nasdaq.last_date(), "2025-12-31")

    def test_cross_checked_sofr_values_match_the_publisher_api(self):
        """The two numbers in the register are asserted, not described."""
        feed = live.OfficialFeed()
        sofr = feed.get("SOFR")
        by_date = dict(zip(sofr.dates, sofr.values))
        self.assertAlmostEqual(by_date["2026-09-17"], 3.85, places=6)
        self.assertAlmostEqual(by_date["2026-09-16"], 3.62, places=6)

    def test_index_history_is_a_real_observation_not_a_projection(self):
        feed = live.OfficialFeed()
        for sid in ("SP500", "NASDAQCOM", "DJIA"):
            series = feed.get(sid)
            self.assertIsNotNone(series, sid)
            self.assertEqual(series.last_date(), "2026-09-17", sid)
            self.assertEqual(series.source_class,
                             "OFFICIAL-PUBLISHER / FRED-REPUBLISHED", sid)

    def test_an_unreadable_series_is_reported_missing_not_substituted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "fred"))
            feed = live.OfficialFeed(root=tmp)
            self.assertEqual(feed.series, {})
            self.assertEqual(len(feed.missing), len(live.OFFICIAL_SERIES_REGISTER))
            for row in feed.missing:
                self.assertIn("url_series", row)


class TestDeterminismAndRoster(unittest.TestCase):
    def test_roster_has_unique_usernames_and_declared_sources(self):
        roster = live_season.build_live_roster()
        usernames = [s.username for s in roster]
        self.assertEqual(len(set(usernames)), len(usernames))
        self.assertGreaterEqual(len(roster), 15)
        registered = {r["sid"] for r in live.OFFICIAL_SERIES_REGISTER}
        for strategy in roster:
            self.assertTrue(strategy.spec.thesis)
            self.assertTrue(strategy.spec.entry_rules)
            self.assertTrue(strategy.spec.exit_rules)
            for sid in strategy.official_inputs:
                self.assertIn(sid, registered, f"{strategy.username} reads {sid}")
            if strategy.data_status == "READY" and not strategy.price_only:
                self.assertTrue(strategy.official_inputs or strategy.event_inputs,
                                f"{strategy.username} declares no input at all")

    def test_the_rehearsal_reproduces_across_processes(self):
        """Same seed, same config, different interpreter: same leaderboard."""
        script = (
            "import json, sys; sys.path.insert(0, %r); sys.path.insert(0, %r);"
            "from sim import live_season;"
            "r = live_season.run_rehearsal(root='%s');"
            "print(json.dumps([[x['username'], x['total_return_pct'], x['fills']]"
            " for x in r['leaderboard']]))"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, "-c",
                   script % (REPO_ROOT, os.path.join(REPO_ROOT, "tests"), tmp)]
            first = subprocess.run(cmd, capture_output=True, text=True, check=True,
                                   cwd=REPO_ROOT)
            second = subprocess.run(cmd, capture_output=True, text=True, check=True,
                                    cwd=REPO_ROOT)
        self.assertEqual(first.stdout, second.stdout)
        rows = json.loads(first.stdout)
        self.assertTrue(rows)
        returns = [row[1] for row in rows]
        self.assertEqual(returns, sorted(returns, reverse=True),
                         "the leaderboard must be ranked on total return")


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sps-live-cli-")
        # unittest runs methods alphabetically, so the blotter test comes before
        # the planning test; the run has to exist first either way.
        live_season.run_forward(root=cls.tmp, horizon=1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_live_forward_command_runs_and_reports_pending(self):
        from contextlib import redirect_stdout
        import io
        from sim import cli
        import argparse
        args = argparse.Namespace(mode="forward", as_of="", horizon=2,
                                  verbose=False, memory_root=self.tmp)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = cli.cmd_live(args)
        self.assertEqual(rc, 0)
        out = buffer.getvalue()
        self.assertIn("LIVE FORWARD BOOK", out)
        self.assertIn("PENDING-SETTLEMENT", out)

    def test_live_blotter_reads_the_written_tape(self):
        from contextlib import redirect_stdout
        import io
        from sim import cli
        import argparse
        args = argparse.Namespace(run="", kind="live-forward", participant="",
                                  only_filled=False, limit=5, export="",
                                  memory_root=self.tmp)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = cli.cmd_live_blotter(args)
        self.assertEqual(rc, 0)
        self.assertIn("intents", buffer.getvalue())

    def test_the_test_suite_does_not_rewrite_the_committed_live_memory(self):
        """The published-site CI job diffs a rebuild against docs/; a test that
        rewrites memory/live would make that diff non-empty and the build red."""
        committed = os.path.join(REPO_ROOT, "memory", "live")
        if not os.path.isdir(committed):
            self.skipTest("no committed live memory")
        manifests = {}
        for name in sorted(os.listdir(committed)):
            path = os.path.join(committed, name, "manifest.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    manifests[name] = json.load(handle)
        with open(os.path.join(DOCS, "assets", "data", "live.json"),
                  encoding="utf-8") as handle:
            payload = json.load(handle)
        published = payload["manifest"]["storage"]["streams"]
        newest = max(manifests, key=lambda n: manifests[n].get("created_utc", ""))
        self.assertEqual(published["fills"]["sha256"],
                         manifests[newest]["storage"]["streams"]["fills"]["sha256"],
                         "docs/assets/data/live.json does not match memory/live")


class TestOfficialExtractVerification(unittest.TestCase):
    """The transcription check for the three series retrieved in-session."""

    def test_the_extract_verifier_passes_and_explains_every_divergence(self):
        script = os.path.join(REPO_ROOT, "scripts", "verify_official_extracts.py")
        proc = subprocess.run([sys.executable, script, "--quiet"],
                              capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report_path = os.path.join(REPO_ROOT, "data", "real", "fred",
                                   "AGENT_FETCH_VERIFICATION.json")
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["failure_count"], 0)
        self.assertEqual(len(report["files"]), 3)
        for entry in report["files"]:
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertEqual(entry["structure_problems"], [])
            self.assertTrue(entry["calendar_status"].startswith("OK"))
        # The two cross-checks must be real matches against a second publisher.
        self.assertTrue(all(c["status"] == "MATCH" for c in report["crosschecks"]))
        self.assertEqual(len(report["crosschecks"]), 2)
        # The SOFR calendar divergence must be itemised with its reason, not
        # merely tolerated by a wider tolerance.
        sofr = next(e for e in report["files"] if e["series"] == "SOFR")
        explained = sofr["explained_divergences"]
        self.assertIn("bond_market_closed_equity_open", explained)
        self.assertIn("bond_market_open_equity_closed", explained)
        self.assertTrue(explained["bond_market_open_equity_closed"][0]["why"])

    def test_the_verifier_can_fail(self):
        """A verifier that cannot fail is not a verifier."""
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            fred = os.path.join(tmp, "fred")
            os.makedirs(fred)
            src = os.path.join(REPO_ROOT, "data", "real", "fred")
            for name in ("SP500_2024-09-16_2026-09-17.csv",
                         "DJIA_2024-09-16_2026-09-17.csv"):
                shutil.copy(os.path.join(src, name), os.path.join(fred, name))
            # A DJIA file with one value changed must be reported, because the
            # only thing standing between a transcription and the repo is this
            # check plus its digest.
            path = os.path.join(fred, "DJIA_2024-09-16_2026-09-17.csv")
            rows = open(path, encoding="utf-8").read().splitlines()
            rows[1] = rows[1].split(",")[0] + ",0.00"
            open(path, "w", encoding="utf-8").write("\n".join(rows) + "\n")
            proc = subprocess.run(
                [sys.executable,
                 os.path.join(REPO_ROOT, "scripts", "verify_official_extracts.py"),
                 "--quiet", "--fred-dir", fred,
                 "--report", os.path.join(tmp, "report.json")],
                capture_output=True, text=True, cwd=tmp)
            self.assertNotEqual(proc.returncode, 0,
                                "a zeroed value in a copied DJIA file was not caught")


class TestLiveSite(unittest.TestCase):
    def test_the_live_section_is_published_with_sources_and_a_tape(self):
        for rel in ("live/index.html", "live/forward.html", "live/leaderboard.html",
                    "live/blotter.html", "live/method.html", "live/sources.html",
                    "live/participants/index.html", "assets/data/live.json"):
            self.assertTrue(os.path.exists(os.path.join(DOCS, rel)), rel)

    def test_every_live_participant_has_a_page(self):
        index = os.path.join(DOCS, "live", "participants")
        pages = sorted(f for f in os.listdir(index) if f.endswith(".html"))
        self.assertGreaterEqual(len(pages), 16)
        for strategy in live_season.build_live_roster():
            slug = strategy.username.lstrip("@").replace("/", "_") + ".html"
            self.assertIn(slug, pages, strategy.username)

    def test_the_published_payload_matches_the_memory(self):
        with open(os.path.join(DOCS, "assets", "data", "live.json"),
                  encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertTrue(payload["leaderboard"])
        self.assertTrue(payload["official_sources"]["series"])
        self.assertEqual(payload["verification"]["failure_count"], 0)
        self.assertIn("NOT AN OFFICIAL-PRICE BOOK", payload["coverage"]["verdict"])

    def test_the_live_pages_say_they_are_not_official_price_eligible(self):
        with open(os.path.join(DOCS, "live", "index.html"),
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("SECONDARY", text)
        self.assertIn("official-price competition", text)

    def test_the_forward_page_shows_open_intents_and_no_return(self):
        with open(os.path.join(DOCS, "live", "forward.html"),
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("PENDING-SETTLEMENT", text)
        self.assertIn("no fill exists", text)


if __name__ == "__main__":
    unittest.main()
