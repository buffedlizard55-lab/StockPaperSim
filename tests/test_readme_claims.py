"""The README's claims about itself must be true.

Why this file exists: three separate passes of this project ended with the
README quoting a stale test count, a stale register size and a leaderboard table
that no longer matched the memory it was supposedly generated from. Every one of
those numbers was hand-typed at least once. Hand-typed self-description rots the
moment the thing it describes changes, and a README that overstates its own
evidence is the one kind of error this project is explicitly built to catch. So
the counts are computed here from the same artefacts the site is built from, and
the table is compared cell by cell against the published leaderboard.

These tests read only committed files, so they cost milliseconds and run in CI
without a re-run of the season.
"""

from __future__ import annotations

import json
import os
import re
import unittest

from fixtures import REPO_ROOT

README = os.path.join(REPO_ROOT, "README.md")
RUN = "season1-primary-seed20260917"
MEMORY = os.path.join(REPO_ROOT, "memory", "runs", RUN)
MINUS = "\u2212"


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _count_tests() -> int:
    loader = unittest.defaultTestLoader
    suite = loader.discover(os.path.join(REPO_ROOT, "tests"),
                            pattern="test_*.py")
    return suite.countTestCases()


def _num(cell: str) -> float:
    return float(cell.strip().replace("*", "").replace(",", "")
                 .replace("%", "").replace(MINUS, "-"))


class TestReadmeCounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = open(README, encoding="utf-8").read()

    def test_claimed_test_count_matches_the_suite(self):
        claimed = {int(m) for m in
                   re.findall(r"(\d+) tests - engine, venue", self.text)}
        claimed |= {int(m) for m in
                    re.findall(r"discover -s tests   # (\d+) tests", self.text)}
        self.assertEqual(len(claimed), 1,
                         f"the README states its test count in more than one "
                         f"place and they disagree: {sorted(claimed)}")
        got = _count_tests()
        self.assertEqual(claimed.pop(), got,
                         f"README claims a different number of tests than the "
                         f"suite collects ({got}); update both mentions")

    def test_claimed_register_size_matches_the_register(self):
        from sim import config
        rows = config.all_verified_sources()
        m = re.search(r"(\d+)-row verified-source register", self.text)
        self.assertIsNotNone(m, "README no longer states the register size")
        self.assertEqual(int(m.group(1)), len(rows),
                         f"README says {m.group(1)} rows, the register has "
                         f"{len(rows)}")
        # The site renders the source register AND the provider register on one
        # page; the README quotes that combined figure, so check it too rather
        # than letting a second number drift.
        from sim import marketdata
        providers = len(marketdata.provider_catalogue())
        m2 = re.search(r"(\d+)-row register \((\d+) source rows plus (\d+) "
                       r"provider rows\)", self.text)
        self.assertIsNotNone(m2, "README no longer breaks the site register down")
        self.assertEqual(int(m2.group(1)), len(rows) + providers)
        self.assertEqual(int(m2.group(2)), len(rows))
        self.assertEqual(int(m2.group(3)), providers)

    def test_claimed_research_counts_match_the_files(self):
        for key, fname in (("IRREGULARITIES.json", "IRREGULARITIES.json"),
                           ("LIMITATIONS.json", "LIMITATIONS.json"),
                           ("REMAINING_WORK.json", "REMAINING_WORK.json")):
            with open(os.path.join(REPO_ROOT, "research", fname),
                      encoding="utf-8") as fh:
                n = len(json.load(fh))
            m = re.search(re.escape(key) + r" \((\d+)\)", self.text)
            self.assertIsNotNone(m, f"README no longer counts {key}")
            self.assertEqual(int(m.group(1)), n,
                             f"README says {key} has {m.group(1)} entries, it "
                             f"has {n}")
        # The prose that points at the published irregularities page must carry
        # the same number as the layout block, or one of them is already stale.
        with open(os.path.join(REPO_ROOT, "research", "IRREGULARITIES.json"),
                  encoding="utf-8") as fh:
            n_ir = len(json.load(fh))
        prose = re.findall(r"all (\d+) flags", self.text) + \
            re.findall(r"carries the (\d+) flags", self.text)
        self.assertTrue(prose, "README no longer counts the flags it publishes")
        for claim in prose:
            self.assertEqual(int(claim), n_ir,
                             f"'{claim} flags' disagrees with the "
                             f"{n_ir} registered irregularities")

    def test_leaderboard_table_agrees_with_the_published_memory(self):
        with open(os.path.join(MEMORY, "leaderboard.json"), encoding="utf-8") as fh:
            board = json.load(fh)["leaderboard"]
        rows = [line for line in self.text.split("\n")
                if re.match(r"\| \d+ \| `@", line)]
        self.assertEqual(len(rows), len(board),
                         f"README table has {len(rows)} rows, the leaderboard "
                         f"has {len(board)}")
        for line, exp in zip(rows, board):
            c = line.split("|")
            self.assertEqual(int(c[1]), exp["rank"], f"rank column out of order: {line}")
            self.assertEqual(c[2].strip().strip("`").lstrip("@"),
                             exp["username"].lstrip("@"),
                             f"README and leaderboard disagree on who is rank "
                             f"{exp['rank']}: {line}")
            for idx, key, tol in ((3, "total_return_pct", 0.051),
                                  (4, "max_drawdown_pct", 0.051),
                                  (5, "sharpe", 0.006),
                                  (6, "beta", 0.006),
                                  (7, "closed_trades", 0.001),
                                  (8, "execution_cost_pct", 0.006)):
                self.assertAlmostEqual(_num(c[idx]), exp[key], delta=tol,
                                       msg=f"{line}: {key} is {c[idx].strip()}, "
                                           f"memory says {exp[key]}")
            self.assertEqual(c[9].strip(), exp["verdict"],
                              f"{line}: verdict text is stale")

    def test_benchmark_sentence_agrees_with_the_market_report(self):
        with open(os.path.join(MEMORY, "market_report.json"), encoding="utf-8") as fh:
            spx = json.load(fh)["spx_return_pct"]
        m = re.search(r"the real S&P 500 returned \*\*(.{1,10}?)\*\*", self.text)
        self.assertIsNotNone(m, "README no longer states the benchmark return")
        self.assertAlmostEqual(_num(m.group(1)), spx, delta=0.006)
        n_beat = sum(1 for r in json.load(open(os.path.join(
            MEMORY, "leaderboard.json"), encoding="utf-8"))["leaderboard"]
            if r["total_return_pct"] > spx)
        m2 = re.search(r"\*\*(\d+) of (\d+) participants beat it\*\*", self.text)
        self.assertIsNotNone(m2)
        self.assertEqual((int(m2.group(1)), int(m2.group(2))), (n_beat, 20),
                         f"README says {m2.group(1)} of {m2.group(2)} beat the "
                         f"index; the published memory says {n_beat} of 20")

    def test_no_replaced_placeholder_left_in_the_readme(self):
        for bad in ("TODO", "FIXME", "XXX", "{", "None%", "nan%"):
            if bad in ("{",):
                continue
            self.assertNotIn(bad, self.text, f"README contains {bad!r}")


if __name__ == "__main__":
    unittest.main()


class TestReadmeMeasuredClaims(unittest.TestCase):
    """Every dollar figure and count quoted in the prose must be the published
    memory's own number, not the number that was true of some earlier run.

    This test exists because the registers and the README both carried figures
    measured on intermediate runs: a "$635.91 across 4 payments" that could not be
    reproduced from any event stream in the repository, and "$11,662.74" of
    dividends that was true right up until the next fix re-ran the season. Both
    were honest when written and both ended up published as if they described the
    released artefact. Prose that quotes a run's numbers has to be checked against
    the run that was actually committed, so it is checked here, on every push.
    """

    def _reports(self):
        out = {}
        root = os.path.join(MEMORY, "reports")
        for fn in sorted(os.listdir(root)):
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                out[fn[:-len(".json")]] = json.load(fh)
        return out

    def _fills(self):
        import gzip
        path = os.path.join(MEMORY, "events", "fills.jsonl.gz")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_quoted_carry_totals_match_the_published_reports(self):
        reports = self._reports()
        paid = round(sum(r["carry"]["dividends_received_usd"] for r in reports.values()), 2)
        in_lieu = round(sum(r["carry"]["dividends_in_lieu_paid_usd"] for r in reports.values()), 2)
        text = _read("README.md")
        # Only the two totals that exist in the committed memory are checked.
        # The README's third figure, the $1,551.09 *correction*, is a difference
        # against the pre-fix run and cannot be recomputed from committed files - it
        # lives in git history - so it is verified by hand at republication time.
        net = round(paid - in_lieu, 2)
        for claim in (f"${paid:,.2f}", f"${in_lieu:,.2f}"):
            self.assertIn(
                claim, text,
                f"README never quotes the season's published {claim} "
                f"(received ${paid:,.2f}, charged in lieu ${in_lieu:,.2f}, net ${net:,.2f})",
            )

    def test_a_book_that_is_flat_at_the_close_earns_no_dividend(self):
        # The one check that can only come out right if the at-close exit works:
        # a strategy that is genuinely out of everything by the close is never a
        # holder of record, so its dividend line must be exactly zero.
        reports = self._reports()
        self.assertEqual(
            reports["GapAndGo_YOLO"]["carry"]["dividends_received_usd"], 0.0,
            "GapAndGo documents being flat before every close but was paid a "
            "dividend; either the ticket or this claim is wrong",
        )
        self.assertGreater(
            reports["OvernightCarry_NO"]["carry"]["dividends_received_usd"], 0.0,
            "the other close-of-session strategy holds overnight by design and "
            "must still be paid, or the zero above proves nothing",
        )
        self.assertIn("**$0.00**", _read("README.md"))

    def test_fill_count_and_at_close_share_match_the_register_text(self):
        fills = self._fills()
        reports = self._reports()
        self.assertEqual(len(fills), sum(r["costs"]["fills"] for r in reports.values()),
                         "the fills stream and the reports disagree about how many fills there were")
        at_close = [f for f in fills if f.get("at_close")]
        share = 100.0 * len(at_close) / len(fills)
        registered = _read("research", "IRREGULARITIES.json")
        expected = f"{len(at_close)} of {len(fills):,} fills ({share:.1f}%)"
        self.assertIn(expected, registered,
                      f"IRREGULARITIES.json does not state the published {expected}")
        self.assertEqual({f["interval"] for f in at_close}, {13},
                         "an at-close fill was worked somewhere other than the last interval")
        self.assertEqual({f["participant"] for f in at_close},
                         {"@GapAndGo_YOLO", "@OvernightCarry_NO"},
                         "a strategy that does not document a close-of-session exit used one")
        self.assertTrue(all(abs(f["avg_price"] * 100 - round(f["avg_price"] * 100)) < 1e-6
                            for f in fills),
                        "a published fill is off the $0.01 Rule 612 grid")

    def test_ledger_closes_exactly_in_every_published_report(self):
        for user, r in self._reports().items():
            self.assertAlmostEqual(
                r["final_equity"] - r["starting_cash"] - r["net_pnl_usd"], 0.0,
                delta=0.005, msg=f"{user}'s published report does not close",
            )
