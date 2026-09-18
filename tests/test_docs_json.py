"""The published site in docs/ - what GitHub Pages actually serves.

test_site_builder builds into a throwaway directory; these tests audit the
committed artifact instead, because that is what a reviewer opens.  The two
things that matter most are that every number on the site agrees with run
memory (a stale site is a lie by omission) and that the disclosures a reader
needs are present on the page where they are needed.
"""

from __future__ import annotations

import html
import json
import os
import re
import unittest

from fixtures import (PUBLISHED_SESSIONS, PUBLISHED_SPX_RETURN_PCT,
                       PUBLISHED_TOP_RETURN_PCT, PUBLISHED_TOP_USERNAME,
                       PUBLISHED_VIX_MAX, PUBLISHED_VIX_MAX_DATE, REPO_ROOT)

from sim import config, memory

DOCS = os.path.join(REPO_ROOT, "docs")
MEMORY_ROOT = os.path.join(REPO_ROOT, "memory")
RUN = "season1-primary-seed20260917"


def _read(rel):
    with open(os.path.join(DOCS, rel), encoding="utf-8") as fh:
        return fh.read()


def _strict_json(rel):
    """Parse JSON, refusing the NaN/Infinity constants Python accepts."""
    return json.loads(_read(rel), parse_constant=lambda c: (_ for _ in ()).throw(
        ValueError(f"{rel} contains the non-JSON constant {c}")))


@unittest.skipUnless(os.path.isdir(DOCS) and os.listdir(DOCS),
                     "docs/ is not built; run `python3 -m sim.cli build-site`")
class TestPublishedSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = memory.MemoryStore(MEMORY_ROOT)
        cls.pages = []
        for base, _dirs, names in os.walk(DOCS):
            for n in names:
                rel = os.path.relpath(os.path.join(base, n), DOCS)
                if n.endswith(".html"):
                    cls.pages.append(rel)

    # ------------------------------------------------------------ inventory

    def test_pages_for_pages_jekyll_would_skip(self):
        self.assertTrue(os.path.exists(os.path.join(DOCS, ".nojekyll")),
                        "without .nojekyll, Pages skips assets/data/*.json")
        self.assertTrue(os.path.exists(os.path.join(DOCS, "assets", "site.css")))
        self.assertTrue(os.path.exists(os.path.join(DOCS, "assets", "site.js")))
        season1 = [p for p in self.pages if not p.startswith("season2/")]
        self.assertEqual(len(season1), 30)      # 10 top level + 20 + index
        # Season 2 publishes under docs/season2/: 7 index pages plus one per
        # participant, and it must not leak into Season 1's participant tree.
        season2 = [p for p in self.pages if p.startswith("season2/")]
        self.assertGreaterEqual(len(season2), 7 + 1)
        for name in ("index.html", "leaderboard.html", "masterfeed.html",
                     "ledger.html", "stress.html", "data.html",
                     "participants/index.html"):
            self.assertIn(f"season2/{name}", self.pages)

    def test_every_participant_has_a_published_page(self):
        board = _strict_json("assets/data/leaderboard.json")
        for row in board:
            slug = row["username"].lstrip("@")
            self.assertIn(f"participants/{slug}.html", self.pages,
                          f"{row['username']} has no page")
        # Same rule for Season 2, whose pages live one directory deeper.
        season2_board = _strict_json("assets/data/season2/leaderboard.json")
        for row in season2_board:
            slug = row["username"].lstrip("@")
            self.assertIn(f"season2/participants/{slug}.html", self.pages,
                          f"{row['username']} has no Season 2 page")
            self.assertNotIn(f"participants/{slug}.html", self.pages,
                             f"{row['username']} leaked into Season 1's pages")

    # ------------------------------------------- site agrees with run memory

    def test_leaderboard_json_is_identical_to_memory(self):
        published = _strict_json("assets/data/leaderboard.json")
        stored = self.store.load(RUN, "leaderboard.json")["leaderboard"]
        self.assertEqual(published, stored)

    def test_market_and_factor_json_are_identical_to_memory(self):
        self.assertEqual(_strict_json("assets/data/market.json"),
                         self.store.load(RUN, "market_report.json"))
        self.assertEqual(_strict_json("assets/data/factors.json"),
                         self.store.load(RUN, "factor_report.json"))

    def test_participants_json_carries_the_declared_factor_exposures(self):
        published = _strict_json("assets/data/participants.json")
        self.assertEqual(len(published), 20)
        for row in published:
            self.assertIn("factor_exposure", row,
                          f"{row.get('username')} lost its factor exposure")
            self.assertIsInstance(row["factor_exposure"], dict)
            self.assertGreater(len(row["entry_rules"]), 0, row["username"])
            self.assertGreater(len(row["why_return_seeking"]), 40, row["username"])

    def test_manifest_json_omits_the_checksum_block(self):
        published = _strict_json("assets/data/manifest.json")
        self.assertNotIn("files", published)
        self.assertEqual(published["run_id"], RUN)
        self.assertEqual(published["seed"], 20260917)
        self.assertEqual(published["session_count"], 251)
        self.assertEqual(published["participant_count"], 20)
        self.assertEqual(published["winner"], "@BetaChaser_3xProxy")

    def test_robustness_json_covers_all_six_scenarios(self):
        panel = _strict_json("assets/data/robustness.json")
        self.assertEqual(len(panel), 20)
        seeds = {str(s_) for s_ in config.SCENARIO_SEEDS}
        for user, row in panel.items():
            self.assertEqual(row["username"], user)
            self.assertEqual(row["scenarios"], 6, f"{user} is missing scenarios")
            # Keyed by scenario seed, so a reader can join back to memory.
            self.assertEqual(set(row["returns_by_scenario_pct"]), seeds, user)
            self.assertEqual(set(row["index_by_scenario_pct"]), seeds, user)
            for value in row["returns_by_scenario_pct"].values():
                self.assertIsInstance(value, float, user)
            self.assertAlmostEqual(row["mean_return_pct"],
                                   sum(row["returns_by_scenario_pct"].values())
                                   / 6.0, delta=0.01, msg=user)
            self.assertGreaterEqual(row["best_return_pct"],
                                    row["median_return_pct"] - 1e-9, user)
            self.assertLessEqual(row["worst_return_pct"],
                                 row["median_return_pct"] + 1e-9, user)
            self.assertEqual(row["positive_scenarios"],
                             sum(1 for v in row["returns_by_scenario_pct"].values()
                                 if v > 0), user)
            self.assertEqual(row["beat_index_scenarios"],
                             sum(1 for k, v in row["returns_by_scenario_pct"].items()
                                 if v > row["index_by_scenario_pct"][k]), user)
            # Every scenario replays the SAME real index path - that is the
            # point of the panel: it isolates idiosyncratic luck.
            self.assertEqual(set(row["index_by_scenario_pct"].values()),
                             {14.415}, user)

    def test_the_published_numbers_are_the_season_numbers(self):
        """Guard against a site built from an older run."""
        board = _strict_json("assets/data/leaderboard.json")
        top = board[0]
        self.assertEqual(top["username"], PUBLISHED_TOP_USERNAME)
        self.assertAlmostEqual(top["total_return_pct"],
                               PUBLISHED_TOP_RETURN_PCT, delta=0.05)
        market = _strict_json("assets/data/market.json")
        self.assertAlmostEqual(market["spx_return_pct"],
                               PUBLISHED_SPX_RETURN_PCT, delta=0.01)
        self.assertAlmostEqual(market["vix"]["max"],
                               PUBLISHED_VIX_MAX, delta=0.01)
        self.assertEqual(market["vix"]["max_date"], PUBLISHED_VIX_MAX_DATE)
        self.assertEqual(market["window"]["sessions"], PUBLISHED_SESSIONS)

    # ---------------------------------------------------------------- hygiene

    def test_no_page_contains_nan_inf_or_python_reprs(self):
        # `{symbol}` is a legitimate URL template quoted in IR-02 (the Yahoo
        # endpoint that requires a crumb); "None of the failure modes..." is
        # legitimate prose in the post-mortems.
        bad = re.compile(r"(?i)(\bnan\b|\binf\b|\bnull\b|\bNone\b|\['|\{'"
                         r"|Traceback \(most recent"
                         r"|\{(?!(symbol|interval|range)\})[a-z_]+\})")
        allowed_words = {"none", "inf"}
        for rel in sorted(self.pages):
            text = re.sub(r"<[^>]+>", " ", _read(rel))
            hits = {m.group(0) for m in bad.finditer(text)}
            hits = {h for h in hits
                    if h.lower() not in allowed_words and h != "{'"}
            self.assertFalse(hits, f"{rel}: {sorted(hits)[:6]}")

    def test_no_json_file_contains_nan_or_infinity(self):
        for base, _dirs, names in os.walk(os.path.join(DOCS, "assets", "data")):
            for n in names:
                if n.endswith(".json"):
                    rel = os.path.relpath(os.path.join(base, n), DOCS)
                    _strict_json(rel)          # raises on NaN/Infinity

    def test_every_local_link_and_asset_resolves(self):
        missing = []
        for rel in sorted(self.pages):
            base = os.path.dirname(os.path.join(DOCS, rel))
            for m in re.finditer(r'(?:href|src)="([^"#]+)(?:#[^"]*)?"', _read(rel)):
                url = m.group(1)
                if url.startswith(("https://", "mailto:")):
                    continue
                self.assertTrue(not url.startswith(("http://", "/")),
                                f"{rel}: {url} would break on Pages")
                if not os.path.exists(os.path.normpath(os.path.join(base, url))):
                    missing.append(f"{rel} -> {url}")
        self.assertEqual(missing, [], f"broken links in docs/: {missing[:8]}")

    def test_no_page_points_at_localhost_or_the_sandbox(self):
        for rel in sorted(self.pages):
            text = _read(rel)
            for needle in ("localhost", "127.0.0.1", "0.0.0.0", "file://",
                           "/home/user", "e2b.app"):
                self.assertNotIn(needle, text, f"{rel} references {needle}")

    # --------------------------------------------------------- disclosures

    def test_the_simulation_disclosure_is_unmissable(self):
        for rel in ("index.html", "market.html", "data.html",
                    "methodology.html", "leaderboard.html"):
            text = _read(rel)
            self.assertRegex(text, r"(?i)simulat", f"{rel} never says 'simulated'")
        # The one sentence a reader must not miss, on the front page.
        self.assertRegex(_read("index.html"),
                         r"(?i)not investment advice|no result on this site")

    def test_the_real_versus_simulated_split_is_stated_per_field(self):
        text = _read("data.html")
        self.assertIn("real", text)
        self.assertIn("scenario", text)
        self.assertIn("FRED", text)
        self.assertIn("data/real/fred/SP500_2025-09-17_2026-09-16.csv", text)
        self.assertIn("data/real/yahoo/SPY_monthly_1y.json", text)

    def test_the_competition_rules_page_cites_the_three_reference_sites(self):
        text = _read("methodology.html") + _read("sources.html")
        self.assertIn("tradingview.com/the-leap", text)
        self.assertIn("trade-ideas.com/stock-trading-competition", text)
        self.assertIn("candlecharts.com/contest", text)
        # The rules this project copied from them.
        for needle in ("100,000", "forced", "ranked", "251"):
            self.assertIn(needle, text, f"methodology does not state {needle}")

    def test_the_fee_schedules_are_published_with_their_dates(self):
        text = _read("methodology.html")
        self.assertIn("$20.60 per $1,000,000", text)
        self.assertIn("2026-04-04", text)
        self.assertIn("0.000195", text)
        self.assertIn("$8.30 per trade from 2025-01-01", text)
        self.assertIn("$9.79 per trade from 2026-01-01", text)
        self.assertIn(config.fee_coverage_start(), text)
        self.assertRegex(text, r"(?i)secondary source")   # the TAF caveat

    def test_the_tick_size_exemption_is_explained_on_the_site(self):
        text = _read("methodology.html") + _read("irregularities.html")
        self.assertIn("0.005", text)
        self.assertIn("November 2027", text)
        self.assertIn("34-105656", text)

    def test_every_irregularity_and_limitation_is_published(self):
        ir_html = _read("irregularities.html")
        lim_html = _read("limitations.html")
        for name, html, prefix, minimum in (
                ("IRREGULARITIES", ir_html, "IR-", 27),
                ("LIMITATIONS", lim_html, "L-", 16)):
            with open(os.path.join(REPO_ROOT, "research", f"{name}.json"),
                      encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertGreaterEqual(len(rows), minimum)
            for row in rows:
                self.assertTrue(row["id"].startswith(prefix))
                self.assertIn(row["id"], html, f"{row['id']} is not on the site")
                for link in row.get("links", []):
                    self.assertIn(link.replace("&", "&amp;"), html,
                                  f"{row['id']}: link {link} not published")

    def test_remaining_work_is_published(self):
        with open(os.path.join(REPO_ROOT, "research", "REMAINING_WORK.json"),
                  encoding="utf-8") as fh:
            rows = json.load(fh)
        # Compare the rendered *text*: the page escapes apostrophes and
        # ampersands, and a title that says "collector's" must not fail this
        # check because the markup says &#x27;. Unescaping the page is the
        # honest comparison; escaping the expected string was the previous
        # approach and it only worked while no title contained an apostrophe.
        page = html.unescape(_read("limitations.html"))
        for row in rows:
            self.assertIn(row["title"][:30], page,
                          f"remaining work not published: {row['title'][:40]}")

    def test_every_participant_page_has_the_why_it_worked_section(self):
        board = _strict_json("assets/data/leaderboard.json")
        for row in board:
            rel = f"participants/{row['username'].lstrip('@')}.html"
            text = _read(rel)
            self.assertIn(row["username"], text)
            self.assertRegex(text, r"(?i)why it (worked|did not)|what caused")
            self.assertIn(row["verdict"], text)
            for heading in ("Post-mortem: why it worked, or why it did not",
                            "Equity path", "Monthly returns",
                            "Scenario robustness", "Where the money came from",
                            "P&amp;L decomposition", "Execution cost breakdown",
                            "Implementation shortfall (Perold 1988)",
                            "Risk realised", "Trade statistics",
                            "Declared failure modes",
                            "The strategy as declared before the season"):
                self.assertIn(heading, text, f"{rel} is missing '{heading}'")
            self.assertRegex(text, r"(?i)simulat")

    def test_the_leaderboard_page_shows_the_benchmark_and_the_metric(self):
        text = _read("leaderboard.html")
        self.assertIn("total return", text.lower())
        # A reader must be able to see what "beating the market" meant here
        # without leaving the page.
        self.assertIn("+14.41%", text)         # S&P 500 over the same window
        self.assertIn("Benchmark", text)
        self.assertIn("Participants beating the index", text)
        self.assertIn("Season winner", text)
        self.assertIn("2025-09-17", text)
        self.assertIn("2026-09-16", text)
        self.assertIn("251", text)
        self.assertIn("S&amp;P 500", text)
        self.assertIn("@BetaChaser_3xProxy", text)
        self.assertIn("@GapAndGo_YOLO", text)
        market = _strict_json("assets/data/market.json")
        beat = sum(1 for r in _strict_json("assets/data/leaderboard.json")
                   if r["total_return_pct"] > market["spx_return_pct"])
        self.assertIn(f"{beat} / 20", text)

    def test_the_published_config_fingerprint_matches_the_code(self):
        """The site claims a fingerprint; if config.py changes without a rebuild
        the published number is a lie, so the test fails instead."""
        text = _read("data.html")
        self.assertIn(config.DEFAULT_COMPETITION.fingerprint(), text,
                      "docs/data.html shows a config fingerprint that the "
                      "current code does not produce - rebuild the site")

    def test_the_source_register_page_lists_every_registered_claim(self):
        """Every registered source must be reachable from the published page,
        and the page must not claim a row count it does not have."""
        # `&` is escaped to `&amp;` in HTML, so compare against the unescaped
        # text - otherwise every URL with a query string looks missing.
        text = html.unescape(_read("sources.html"))
        rows = config.all_verified_sources()
        for row in rows:
            self.assertIn(row["url"], text,
                          f"register URL missing from sources.html: {row['url']}")
            self.assertIn(row["status"], text, row["status"])
        published = re.search(r"The register \((\d+) rows\)", text)
        self.assertIsNotNone(published, "sources.html no longer states its row count")
        self.assertGreaterEqual(int(published.group(1)), len(rows),
                                "the page lists fewer rows than the register")
        # The page also carries the live-data adapter rows, so it is a superset.
        self.assertLessEqual(int(published.group(1)), len(rows) + 20)

    def test_the_site_is_reasonably_light_for_pages(self):
        total = 0
        for base, _dirs, names in os.walk(DOCS):
            for n in names:
                total += os.path.getsize(os.path.join(base, n))
        self.assertLess(total, 20 * 1024 * 1024,
                        f"docs/ is {total / 1024 / 1024:.1f} MB - Pages serves "
                        f"this to every visitor")
        self.assertGreater(total, 200 * 1024, "docs/ looks empty")


if __name__ == "__main__":
    unittest.main()
