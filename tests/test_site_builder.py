"""The static site generator: helpers, a full build, and HTML hygiene.

The deliverable is a GitHub Pages site a human can actually read, so these
tests check the things that break silently: unbalanced tags, unrendered format
placeholders, links that point at files that do not exist, absolute localhost
URLs (which would break the published site), and numbers that disagree with
memory.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from html import escape as html_escape
from html.parser import HTMLParser

from fixtures import REPO_ROOT

MEMORY_ROOT = os.path.join(REPO_ROOT, "memory")
SITE_PY = os.path.join(REPO_ROOT, "scripts", "build_site.py")

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr", "path", "circle", "rect",
        "line", "polyline", "polygon", "ellipse", "use", "stop"}


def load_site_module():
    spec = importlib.util.spec_from_file_location("build_site", SITE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


site = load_site_module()


class TagChecker(HTMLParser):
    """Collects unbalanced tags and every local/external reference."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.hrefs = []
        self.srcs = []
        self.text = []
        self.titles = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append((tag, self.getpos()))
        if tag == "title":
            self._in_title = True
        for k, v in attrs:
            if k == "href" and v:
                self.hrefs.append(v)
            if k == "src" and v:
                self.srcs.append(v)

    def handle_startendtag(self, tag, attrs):
        for k, v in attrs:
            if k == "href" and v:
                self.hrefs.append(v)
            if k == "src" and v:
                self.srcs.append(v)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag == "title":
            self._in_title = False
        if not self.stack:
            self.errors.append(f"stray </{tag}> at {self.getpos()}")
            return
        if self.stack[-1][0] != tag:
            self.errors.append(
                f"</{tag}> at {self.getpos()} closes <{self.stack[-1][0]}> "
                f"opened at {self.stack[-1][1]}")
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    return
            return
        self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)
        if self._in_title:
            self.titles.append(data.strip())

    def finish(self):
        for tag, pos in self.stack:
            self.errors.append(f"<{tag}> opened at {pos} was never closed")
        return self.errors


class TestHelpers(unittest.TestCase):
    def test_number_formatters(self):
        self.assertEqual(site.num(1234.5678, site.FC2), "1,234.57")
        self.assertEqual(site.num(None), site.NA)
        self.assertEqual(site.num("abc"), "abc")
        self.assertEqual(site.signed(1.5), '<span class="pos">+1.50%</span>')
        self.assertEqual(site.signed(-1.5), '<span class="neg">-1.50%</span>')
        self.assertEqual(site.signed(0.0), '<span class="zero">+0.00%</span>')
        self.assertEqual(site.signed(None), site.NA)
        self.assertEqual(site.money(1234.5), '<span class="pos">+1,234</span>')
        self.assertEqual(site.money(-1234.5), '<span class="neg">-1,234</span>')
        self.assertEqual(site.money(None), site.NA)
        # None profit factor means "no losing round trips" and renders as infinity.
        self.assertIn("&#8734;", site.pf_cell(None))
        self.assertEqual(site.pf_cell(1.5), "1.50")
        self.assertEqual(site.pf_cell(float("nan")), site.NA)
        self.assertEqual(site.pct_cls(1.0), "pos")
        self.assertEqual(site.pct_cls(-1.0), "neg")
        self.assertEqual(site.pct_cls(0.0), "zero")
        self.assertEqual(site.pct_cls(None), "na")

    def test_no_formatter_can_emit_nan_or_inf(self):
        """A non-finite float must never reach the published HTML: a reader
        cannot tell '+nan%' apart from a real number."""
        for value in (float("nan"), float("inf"), float("-inf")):
            for fn in (site.signed, site.money, site.num, site.pf_cell):
                out = fn(value)
                self.assertNotRegex(out, r"(?i)\bnan\b|\binf\b",
                                    f"{fn.__name__}({value}) -> {out}")
                self.assertIn("n/a", out)
            self.assertEqual(site.pct_cls(value), "na")
        self.assertTrue(site._is_finite(0))
        self.assertFalse(site._is_finite(True))     # bools are not numbers here
        self.assertTrue(site._is_finite("1.5"))     # numeric strings are fine
        self.assertFalse(site._is_finite("abc"))
        self.assertTrue(site._numeric(float("nan")))

    def test_table_rejects_a_joined_html_string(self):
        """`table()` must take a list of row-lists; a string is a bug."""
        with self.assertRaises((TypeError, ValueError, AssertionError)):
            site.table(["a", "b"], "<tr><td>x</td></tr>")

    def test_table_passes_cells_through_and_headers_are_trusted(self):
        """Contract: cells may contain markup (links, badges, spans), so callers
        escape with ESC() themselves. What table() must do is refuse a string."""
        html = site.table(["A", "B"], [["1", '<a href="x">y</a>'], ["2", "3"]])
        self.assertIn("<thead>", html)
        self.assertIn("<th>A</th>", html)
        self.assertIn('<a href="x">y</a>', html)
        self.assertEqual(html.count("<tr>"), 3)
        self.assertEqual(html.count("<td>"), 4)
        with self.assertRaises(TypeError):
            site.table(["A"], ["a string row"])

    def test_table_handles_an_empty_body(self):
        html = site.table(["A"], [])
        self.assertIn("<table", html)

    def test_badges_and_status_badges(self):
        self.assertIn("badge-ok", site.badge("good", "ok"))
        self.assertIn("badge-neg", site.badge("bad", "neg"))
        self.assertIn("badge", site.status_badge("FETCHED-VERIFIED"))
        self.assertIn("badge", site.status_badge("KNOWN-NOT-FETCHED"))
        self.assertIn("badge", site.status_badge("SECONDARY"))

    def test_slug_is_filesystem_safe(self):
        self.assertEqual(site._slug("@BetaChaser_3xProxy"), "BetaChaser_3xProxy")
        self.assertNotIn("@", site._slug("@a/b.c"))
        self.assertRegex(site._slug("@a/b.c"), r"^[A-Za-z0-9_.-]+$")

    def test_nice_ticks_are_monotonic_and_bracket_the_range(self):
        for lo, hi in ((0.0, 1.0), (-50.0, 250.0), (1000.0, 1001.0),
                       (-1e6, 3e6), (0.0, 0.001), (-0.5, 0.5)):
            ticks = site._nice_ticks(lo, hi)
            self.assertEqual(ticks, sorted(ticks))
            self.assertGreater(len(ticks), 1)
            self.assertLessEqual(ticks[0], lo)
            self.assertGreaterEqual(ticks[-1], hi - (ticks[1] - ticks[0]))
        # A degenerate (flat) range yields a single tick rather than crashing.
        self.assertEqual(site._nice_ticks(5.0, 5.0), [5.0])
        self.assertEqual(site._nice_ticks(5.0, 4.0), [5.0])

    def test_charts_survive_a_flat_series(self):
        """A participant that never trades has a perfectly flat equity curve."""
        flat = [("flat", [(f"2026-01-{i:02d}", 100_000.0) for i in range(1, 6)])]
        html = site.line_chart(flat, title="flat")
        self.assertIn("<svg", html)
        self.assertNotRegex(html, r"(?i)\bnan\b|\binf\b")
        self.assertIn("<svg", site.bar_chart([("A", 0.0), ("B", 0.0)]))
        self.assertIn("<svg", site.sparkline([1.0, 1.0, 1.0]))

    def test_charts_emit_svg_with_no_nan(self):
        series = [("a", [("2026-01-01", 100.0), ("2026-01-02", 101.5),
                         ("2026-01-03", 99.0)])]
        for html in (site.line_chart(series, title="t"),
                     site.bar_chart([("SPY", 1.0), ("QQQ", -2.0)]),
                     site.hbar_chart([("SPY", 1.0), ("QQQ", -2.0)]),
                     site.sparkline([1.0, 2.0, 3.0, 2.5])):
            self.assertIn("<svg", html)
            self.assertIn("</svg>", html)
            self.assertNotRegex(html, r"(?i)\bnan\b|\binf\b|\bnull\b")

    def test_page_sets_depth_relative_links(self):
        root = site.page("T", "<p>x</p>", "index.html", depth=0)
        deep = site.page("T", "<p>x</p>", "participants/index.html", depth=1)
        self.assertIn('href="assets/site.css"', root)
        self.assertIn('href="../assets/site.css"', deep)
        self.assertIn("<title>T", root)
        self.assertIn("</html>", root)
        self.assertIn(".nojekyll", os.listdir(os.path.join(REPO_ROOT, "docs")))


@unittest.skipUnless(os.path.isdir(MEMORY_ROOT) and os.listdir(MEMORY_ROOT),
                     "no run memory; run `python3 -m sim.cli run` first")
class TestFullBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="sps-site-")
        cls.rc = site.main(["--memory-root", MEMORY_ROOT, "--out", cls.out])
        cls.files = []
        for base, _dirs, names in os.walk(cls.out):
            for n in names:
                full = os.path.join(base, n)
                cls.files.append(os.path.relpath(full, cls.out))
        cls.html = {}
        for f in cls.files:
            if f.endswith(".html"):
                with open(os.path.join(cls.out, f), "r", encoding="utf-8") as fh:
                    cls.html[f] = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_build_succeeds_and_writes_the_expected_inventory(self):
        self.assertEqual(self.rc, 0)
        for page_name in ("index.html", "leaderboard.html", "strategies.html",
                          "simulator.html", "market.html", "methodology.html",
                          "data.html", "sources.html", "irregularities.html",
                          "limitations.html", "participants/index.html",
                          "assets/site.css", "assets/site.js", ".nojekyll"):
            self.assertIn(page_name, self.files, f"missing {page_name}")
        for name in ("leaderboard", "market", "factors", "robustness",
                     "participants", "manifest"):
            self.assertIn(f"assets/data/{name}.json", self.files)
        participants = [f for f in self.files
                        if f.startswith("participants/") and f.endswith(".html")]
        self.assertEqual(len(participants), 21)      # 20 + index

    def test_every_html_file_is_well_formed(self):
        for rel, html in sorted(self.html.items()):
            checker = TagChecker()
            checker.feed(html)
            checker.close()
            errs = checker.finish()
            self.assertFalse(errs, f"{rel}: {errs[:4]}")

    def test_every_page_has_a_title_and_a_doctype(self):
        for rel, html in sorted(self.html.items()):
            self.assertTrue(html.lstrip().lower().startswith("<!doctype html"),
                            f"{rel} has no doctype")
            checker = TagChecker()
            checker.feed(html)
            self.assertTrue(checker.titles, f"{rel} has no <title>")
            self.assertGreater(len(checker.titles[0]), 5, rel)

    def test_no_unrendered_placeholders_or_missing_values(self):
        # `{symbol}` is a legitimate URL template in the provider-adapter docs,
        # and the word "traceback" appears in prose about what memory stores;
        # what must never appear is a real Python repr or an unrendered format.
        bad = re.compile(r"(?i)(\bnan\b|\binf\b|\bnull\b|\bnone\b|>n/a<"
                         r"|\{(?!(symbol|interval|range)\})[a-z_]+\}|\{:\."
                         r"|%\(|TODO|FIXME|Traceback \(most recent call last\)"
                         r"|\['|\bNone\b)")
        for rel, html in sorted(self.html.items()):
            text = re.sub(r"<[^>]+>", " ", html)
            hits = sorted(set(m.group(0) for m in bad.finditer(text)))
            allowed = {"none"}            # legitimate English, e.g. "none fired"
            hits = [h for h in hits if h.lower() not in allowed]
            self.assertFalse(hits, f"{rel}: suspicious tokens {hits[:6]}")

    def test_no_absolute_or_localhost_urls(self):
        for rel, html in sorted(self.html.items()):
            self.assertNotIn("localhost", html, rel)
            self.assertNotIn("127.0.0.1", html, rel)
            self.assertNotIn("file://", html, rel)
            for m in re.finditer(r'(?:href|src)="([^"]+)"', html):
                url = m.group(1)
                if url.startswith(("http://", "https://")):
                    self.assertTrue(url.startswith("https://"),
                                    f"{rel}: insecure URL {url}")
                else:
                    self.assertFalse(url.startswith("/"),
                                     f"{rel}: root-relative URL {url} breaks "
                                     f"GitHub Pages project sites")
                    self.assertNotIn("://", url, rel)

    def test_every_local_link_resolves_to_a_written_file(self):
        missing = []
        for rel, html in sorted(self.html.items()):
            base = os.path.dirname(os.path.join(self.out, rel))
            for m in re.finditer(r'(?:href|src)="([^"#]+)(?:#[^"]*)?"', html):
                url = m.group(1)
                if url.startswith(("https://", "http://", "mailto:")):
                    continue
                target = os.path.normpath(os.path.join(base, url))
                if not os.path.exists(target):
                    missing.append(f"{rel} -> {url}")
        self.assertFalse(missing, f"broken links: {missing[:10]}")

    def test_navigation_is_present_and_consistent_on_every_page(self):
        # The evidence-first desk is a separate section with its own accessible navigation.
        self.assertEqual(len(site.NAV), 15)
        self.assertEqual([h for h, _ in site.NAV][-1], "participants/index.html")
        for rel, html in sorted(self.html.items()):
            if rel.startswith("desk/"):
                self.assertIn('<nav aria-label="Desk">', html)
                self.assertIn('href="../index.html"', html)
                self.assertIn('src="desk.js"', html)
                continue
            self.assertRegex(html, r'<nav[^>]*class="[^"]*\bnav\b[^"]*"', rel)
            for _href, label in site.NAV:
                self.assertIn(label, html, f"{rel} nav is missing {label}")
            for label in ("Leaderboard", "Strategies", "Trade Simulator", "Market",
                          "Venue sensitivity", "Methodology", "Data", "Sources",
                          "Irregularities", "Limitations", "Participants"):
                self.assertIn(label, html, f"{rel} nav is missing {label}")
            self.assertIn('class="site-footer"', html, rel)
            self.assertIn("assets/site.js" if rel.count("/") == 0
                          else "../assets/site.js", html, rel)

    def test_participant_pages_link_back_with_the_right_depth(self):
        page = self.html["participants/BetaChaser_3xProxy.html"]
        self.assertIn('href="../index.html"', page)
        self.assertIn('href="../leaderboard.html"', page)
        self.assertIn("../assets/site.css", page)
        self.assertIn("../assets/site.js", page)
        # A one-level-deep page must never link with a bare relative path.
        self.assertNotIn('href="leaderboard.html"', page)
        self.assertNotIn('href="assets/', page)

    def test_the_leaderboard_page_matches_memory(self):
        with open(os.path.join(MEMORY_ROOT, "runs", "season1-primary-seed20260917", "leaderboard.json"), "r", encoding="utf-8") as fh:
            store_board = json.load(fh)["leaderboard"]
        html = self.html["leaderboard.html"]
        for row in store_board:
            self.assertIn(row["username"], html, f"{row['username']} not on the page")
            self.assertIn(f"{row['total_return_pct']:+,.2f}%", html)
        self.assertEqual(html.index(store_board[0]["username"]),
                         min(html.index(r["username"]) for r in store_board),
                         "the winner is not rendered first")

    def test_json_data_files_round_trip_and_reject_nan(self):
        for name in ("leaderboard", "market", "factors", "robustness",
                     "participants", "manifest"):
            path = os.path.join(self.out, "assets", "data", f"{name}.json")
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotRegex(raw, r"(?i)\bNaN\b|\bInfinity\b")
            data = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(
                ValueError(f"{name}.json contains {c}")))
            self.assertTrue(data, f"{name}.json is empty")
        with open(os.path.join(self.out, "assets", "data", "manifest.json"), "r", encoding="utf-8") as fh:
            manifest = json.loads(fh.read())
        self.assertNotIn("files", manifest)      # checksums stay in memory only
        self.assertEqual(manifest["run_id"], "season1-primary-seed20260917")

    def test_the_index_page_states_the_season_and_the_winner(self):
        html = self.html["index.html"]
        self.assertIn("2025-09-17", html)
        self.assertIn("2026-09-16", html)
        self.assertIn("@BetaChaser_3xProxy", html)
        self.assertIn("100,000", html.replace(",", ","))
        self.assertIn("S&amp;P 500", html.replace("S&P 500", "S&amp;P 500"))

    def test_dates_are_rendered_as_prose_not_as_python_reprs(self):
        html = self.html["data.html"]
        self.assertNotIn("['2025", html)
        self.assertNotIn("[&#x27;2025", html)
        self.assertIn("10 dates:", html)      # market closures detected
        self.assertIn("2 dates:", html)       # early closes
        self.assertIn("(IR-01)", html)

    def test_the_simulation_disclosure_is_on_every_page_that_shows_prices(self):
        for rel in ("index.html", "market.html", "data.html", "methodology.html",
                    "participants/BetaChaser_3xProxy.html"):
            self.assertRegex(self.html[rel], r"(?i)simulat", f"{rel} lacks disclosure")

    def test_sources_page_lists_the_whole_register(self):
        from sim import config
        rows = config.all_verified_sources()
        html = self.html["sources.html"]
        for row in rows:
            # URLs are HTML-escaped on the page (& -> &amp;).
            escaped = row["url"].replace("&", "&amp;")
            self.assertTrue(escaped in html or row["url"] in html,
                            f"source URL missing: {row['url']}")
            self.assertIn(row["status"], html)
            probe = row["claim"][:40]
            # ``html.escape`` is what the page is rendered with, and it escapes
            # quotes as well as the three markup characters - so a claim whose
            # first 40 characters contain an apostrophe ("FINRA's Trading
            # Activity Fee guidance page: ...") is published as &#x27; and a
            # comparison that only handles &, < and > reports it missing. The
            # probe set below is generated the same way the page renders.
            for esc in (probe, html_escape(probe),
                        probe.replace("&", "&amp;").replace("<", "&lt;")
                        .replace(">", "&gt;")):
                if esc in html:
                    break
            else:
                self.fail(f"claim missing from sources.html: {probe}")
        for status in ("FETCHED-VERIFIED", "FETCHED", "FETCHED-VIA-SEARCH",
                       "SECONDARY", "KNOWN-NOT-FETCHED"):
            self.assertIn(status, html)

    def test_irregularities_and_limitations_pages_render_the_registers(self):
        with open(os.path.join(REPO_ROOT, "research", "IRREGULARITIES.json"), "r", encoding="utf-8") as fh:
            ir = json.load(fh)
        with open(os.path.join(REPO_ROOT, "research", "LIMITATIONS.json"), "r", encoding="utf-8") as fh:
            lim = json.load(fh)
        with open(os.path.join(REPO_ROOT, "research", "REMAINING_WORK.json"), "r", encoding="utf-8") as fh:
            work = json.load(fh)
        ir_html = self.html["irregularities.html"]
        for row in (ir["irregularities"] if isinstance(ir, dict) else ir):
            self.assertIn(row["id"], ir_html)
        lim_rows = lim["limitations"] if isinstance(lim, dict) else lim
        lim_html = self.html["limitations.html"]
        for row in lim_rows:
            self.assertIn(row["id"], lim_html)
        work_rows = work["remaining_work"] if isinstance(work, dict) else work
        self.assertGreater(len(work_rows), 5)
        for row in work_rows[:6]:
            self.assertIn(row.get("id", row.get("title", ""))[:12], lim_html)

    def test_a_second_build_is_byte_identical(self):
        out2 = tempfile.mkdtemp(prefix="sps-site2-")
        try:
            site.main(["--memory-root", MEMORY_ROOT, "--out", out2])
            for rel in sorted(self.files):
                if rel.endswith((".html", ".json", ".css", ".js")):
                    with open(os.path.join(self.out, rel), "r", encoding="utf-8") as fh_a:
                        a = fh_a.read()
                    with open(os.path.join(out2, rel), "r", encoding="utf-8") as fh_b:
                        b = fh_b.read()
                    self.assertEqual(a, b, f"{rel} is not reproducible")
        finally:
            shutil.rmtree(out2, ignore_errors=True)



class TestRunProvenanceFooter(unittest.TestCase):
    """A commit id in the footer must not promise more than it can deliver.

    The season published on GitHub Pages was generated from a dirty working tree,
    which made the footer's "git commit <sha>" a statement about a commit that did
    not contain the code which ran (IR-35). These tests pin the three footer
    states and - the one that actually caught the bug - compare the committed
    site's footer against the committed manifest it was generated from.
    """

    def _foot(self, manifest):
        return site.provenance_sentence(manifest)

    def test_a_dirty_run_says_so_instead_of_leaning_on_the_commit(self):
        html = self._foot({"code": {"git": {"commit": "a" * 40, "branch": "arena/x",
                                            "dirty": True},
                                    "python_module_hashes": {"sim/engine.py": "h"}}})
        self.assertIn("modified working tree", html)
        self.assertIn("1 per-module SHA-256 source hashes", html)
        self.assertIn("arena/x", html)

    def test_a_clean_run_points_at_the_module_hashes(self):
        html = self._foot({"code": {"git": {"commit": "b" * 40, "branch": "main",
                                            "dirty": False},
                                    "python_module_hashes": {"sim/engine.py": "h",
                                                              "sim/cli.py": "h2"}}})
        self.assertNotIn("modified working tree", html)
        self.assertIn("2 per-module SHA-256 source hashes", html)

    def test_a_manifest_without_git_metadata_degrades_explicitly(self):
        # A run whose memory root sat outside the repository used to record
        # commit=None *and* dirty=False, i.e. it reported "clean" for a tree it
        # had never looked at. Unknown must never render as clean.
        html = self._foot({})
        self.assertIn("no git provenance recorded", html)
        self.assertIn("no per-module source hashes", html)
        for banned in ("None", "{", "}"):
            self.assertNotIn(banned, html, f"raw python value leaked into the footer: {banned}")

    def test_the_published_footer_matches_the_published_manifest(self):
        index = os.path.join(REPO_ROOT, "docs", "index.html")
        if not os.path.exists(index):
            self.skipTest("docs/ not built yet")
        run = "season1-primary-seed20260917"
        with open(os.path.join(MEMORY_ROOT, "runs", run, "manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        with open(index, encoding="utf-8") as fh:
            html = fh.read()
        commit = str((man.get("code") or {}).get("git", {}).get("commit") or "unknown")[:12]
        self.assertIn(commit, html, "the footer does not name the manifest's commit")
        dirty = bool((man.get("code") or {}).get("git", {}).get("dirty"))
        self.assertEqual("modified working tree" in html, dirty,
                         f"footer says dirty={not dirty} but the manifest says dirty={dirty}")


if __name__ == "__main__":
    unittest.main()
