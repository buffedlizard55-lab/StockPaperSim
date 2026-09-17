"""The verified-source register and the research registers.

The brief is explicit: verify line by line from official trusted sources, give
links for manual review, and flag irregularities.  These tests enforce that the
machine-readable registers are complete, honestly labelled (nothing claims to be
verified that was not actually retrieved), and that every claim points at a URL
a human can open.
"""

from __future__ import annotations

import csv
import json
import os
import re
import unittest
from urllib.parse import urlparse

from fixtures import REPO_ROOT

from sim import config, marketdata, strategies

RESEARCH = os.path.join(REPO_ROOT, "research")
REAL = os.path.join(REPO_ROOT, "data", "real")

# Statuses the project is allowed to claim, in descending order of strength.
STATUS_VOCABULARY = {
    config.FETCHED_VERIFIED, config.FETCHED, config.FETCHED_VIA_SEARCH,
    config.SECONDARY, config.KNOWN_NOT_FETCHED, "ADAPTER-DOCS",
}

# Hosts that were actually reached or are official publishers. Anything outside
# this list would mean a URL was invented.
ALLOWED_HOSTS = {
    "www.sec.gov", "sec.gov", "www.ecfr.gov", "ecfr.gov", "www.federalregister.gov",
    "www.federalreserve.gov", "fred.stlouisfed.org", "www.nasdaq.com",
    "www.nasdaqtrader.com", "otctransparency.finra.org", "www.finra.org",
    "www.nyse.com", "www.tradingview.com", "www.trade-ideas.com",
    "specials.candlecharts.com", "query1.finance.yahoo.com", "doi.org",
    "www.risk.net", "www.govinfo.gov", "stooq.com", "docs.alpaca.markets",
    "polygon.io", "finnhub.io", "www.tiingo.com", "www.cboe.com",
    "www.cftc.gov", "ir.thecorporatesecretary.com", "pages.stern.nyu.edu",
    "www.spglobal.com", "help.revolut.com", "www.cis.upenn.edu",
    # Official CPython documentation - the primary source for the hash
    # randomisation behaviour that IR-30 turns on.
    "docs.python.org",
}


def load_json(name):
    with open(os.path.join(RESEARCH, name), encoding="utf-8") as fh:
        return json.load(fh)


class TestVerifiedSourceRegister(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = config.all_verified_sources()

    def test_the_register_is_substantial(self):
        self.assertGreaterEqual(len(self.rows), 47)

    def test_every_row_has_the_four_required_fields(self):
        for i, row in enumerate(self.rows):
            self.assertEqual(set(row), {"claim", "url", "publisher", "status"},
                             f"row {i} has the wrong shape: {sorted(row)}")
            for field in ("claim", "url", "publisher", "status"):
                self.assertTrue(str(row[field]).strip(),
                                f"row {i} has an empty {field}")

    def test_every_claim_is_specific_enough_to_check(self):
        for row in self.rows:
            self.assertGreater(len(row["claim"]), 25,
                               f"claim too vague to verify: {row['claim']!r}")

    def test_every_url_is_https_and_on_a_known_host(self):
        for row in self.rows:
            parsed = urlparse(row["url"])
            self.assertEqual(parsed.scheme, "https",
                             f"insecure or relative URL: {row['url']}")
            self.assertIn(parsed.netloc, ALLOWED_HOSTS,
                          f"unknown host in the register: {row['url']}")
            # A few official portals are cited at their root (FINRA's
            # OTC transparency search); everything else must name a page.
            if parsed.netloc not in ("otctransparency.finra.org",):
                self.assertGreater(len(parsed.path), 1,
                                   f"bare domain, not a checkable page: {row['url']}")

    def test_statuses_are_honest_and_from_the_vocabulary(self):
        seen = set()
        for row in self.rows:
            self.assertIn(row["status"], STATUS_VOCABULARY,
                          f"unknown status {row['status']!r}")
            seen.add(row["status"])
        # The register must actually distinguish strength of evidence.
        self.assertGreaterEqual(len(seen), 3)
        # FETCHED-VERIFIED means "retrieved here AND saved under data/real/".
        verified = [r for r in self.rows if r["status"] == config.FETCHED_VERIFIED]
        self.assertGreater(len(verified), 0)
        for row in verified:
            host = urlparse(row["url"]).netloc
            self.assertTrue(host.endswith("fred.stlouisfed.org")
                            or host.endswith("finance.yahoo.com"),
                            f"{row['url']} claims VERIFIED but nothing from that "
                            f"host is stored under data/real/")

    def test_nothing_in_the_register_is_a_duplicate(self):
        urls = [r["url"] for r in self.rows]
        claims = [r["claim"] for r in self.rows]
        self.assertEqual(len(set(urls)), len(urls), "duplicate URL in the register")
        self.assertEqual(len(set(claims)), len(claims), "duplicate claim")

    def test_the_three_reference_competitions_are_all_cited(self):
        blob = " ".join(r["url"] for r in self.rows)
        self.assertIn("tradingview.com/the-leap", blob)
        self.assertIn("trade-ideas.com/stock-trading-competition", blob)
        self.assertIn("candlecharts.com/contest", blob)

    def test_the_market_microstructure_rules_are_all_cited(self):
        blob = " ".join(r["claim"] + " " + r["url"] for r in self.rows).lower()
        for needle in ("612", "610", "611", "round lot", "31", "access fee",
                       "reg t", "pattern day trad", "short sale", "0.005",
                       "november 2027"):
            self.assertIn(needle, blob, f"no source covers {needle!r}")

    def test_the_fee_rates_used_by_the_code_match_the_register(self):
        """The numbers in code must be the numbers the sources state."""
        sec31 = dict(config.SEC31_PER_MILLION)
        dates = sorted(sec31)
        self.assertEqual(dates[0], "2025-09-01")   # not a 1970 sentinel
        self.assertEqual(sec31["2025-09-01"], 0.0)
        self.assertEqual(dates[-1], "2026-04-04")
        self.assertAlmostEqual(sec31["2026-04-04"], 20.60, places=9)
        self.assertEqual(config.rate_for(config.SEC31_PER_MILLION, "2026-03-01"), 0.0)
        # The schedule is quoted in dollars per $1,000,000 of sell notional.
        self.assertAlmostEqual(config.rate_for(config.SEC31_PER_MILLION,
                                               "2026-05-01"), 20.60, places=9)
        taf = dict(config.FINRA_TAF_PER_SHARE)
        self.assertAlmostEqual(taf["2025-01-01"], 0.000166, places=9)
        self.assertAlmostEqual(taf["2026-01-01"], 0.000195, places=9)
        cap = dict(config.FINRA_TAF_MAX_PER_TRADE)
        self.assertAlmostEqual(cap["2025-01-01"], 8.30, places=6)
        self.assertAlmostEqual(cap["2026-01-01"], 9.79, places=6)
        self.assertEqual(config.rate_for(config.FINRA_TAF_MAX_PER_TRADE,
                                         "2025-06-01"), 8.30)
        self.assertEqual(config.rate_for(config.FINRA_TAF_MAX_PER_TRADE,
                                         "2026-06-01"), 9.79)
        self.assertEqual(config.ACCESS_FEE_CAP_PER_SHARE, 0.003)

    def test_a_season_opening_before_the_fee_documentation_is_refused(self):
        """No trade may ever be costed at a rate this project cannot cite."""
        self.assertEqual(config.fee_coverage_start(), "2025-09-01")
        config.validate_fee_coverage("2025-09-17", "2026-09-16")   # Season 1
        with self.assertRaises(ValueError):
            config.validate_fee_coverage("2024-01-02", "2024-12-31")
        with self.assertRaises(ValueError):
            config.validate_fee_coverage("2026-09-16", "2025-09-17")
        with self.assertRaises(ValueError):
            config.CompetitionConfig(start="2024-01-02", end="2024-12-31")
        # The shipped default config is inside coverage.
        self.assertEqual(config.CompetitionConfig().start, "2025-09-17")


class TestProviderAdapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = marketdata.provider_catalogue()

    def test_six_adapters_are_documented(self):
        self.assertEqual(len(self.rows), 6)
        self.assertEqual([r["name"] for r in self.rows],
                         ["yahoo", "stooq", "alpaca", "polygon", "finnhub",
                          "tiingo"])

    def test_each_adapter_states_what_it_can_and_cannot_do(self):
        for row in self.rows:
            self.assertEqual(set(row), {"name", "class", "realtime_capable",
                                        "key_env", "docs_url", "notes",
                                        "attempted_in_this_sandbox",
                                        "succeeded_in_this_sandbox"})
            self.assertRegex(row["class"], r"^[A-Za-z]+Provider$",
                             f"{row['name']} names no provider class")
            self.assertIsInstance(row["realtime_capable"], bool)
            self.assertTrue(row["docs_url"].startswith("https://"), row["name"])
            self.assertGreater(len(row["notes"]), 20, row["name"])
            self.assertIsInstance(row["attempted_in_this_sandbox"], bool)
            self.assertIsInstance(row["succeeded_in_this_sandbox"], bool)
            if row["succeeded_in_this_sandbox"]:
                self.assertTrue(row["attempted_in_this_sandbox"],
                                f"{row['name']} claims success without an attempt")

    def test_only_yahoo_actually_returned_data_here(self):
        """Honesty check: the sandbox could not reach the key-based vendors."""
        succeeded = [r["name"] for r in self.rows
                     if r["succeeded_in_this_sandbox"]]
        self.assertEqual(succeeded, ["yahoo"])
        refused = [r["name"] for r in self.rows
                   if r["attempted_in_this_sandbox"]
                   and not r["succeeded_in_this_sandbox"]]
        self.assertIn("stooq", refused)

    def test_key_based_adapters_name_their_environment_variable(self):
        for row in self.rows:
            if row["name"] in ("alpaca", "polygon", "finnhub", "tiingo"):
                self.assertRegex(row["key_env"], r"^[A-Z][A-Z0-9_]*$",
                                 f"{row['name']} has no key env var")
            else:
                self.assertIn(row["key_env"], ("", "-", None))


class TestAcademicBasis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = [e for s in strategies.build_roster()
                       for e in s.spec.academic_basis]

    def test_every_entry_is_cited_properly(self):
        self.assertGreaterEqual(len(self.entries), 20)
        for e in self.entries:
            self.assertEqual(set(e), {"claim", "url", "ref", "status"})
            self.assertGreater(len(e["claim"]), 25, e)
            self.assertRegex(e["status"],
                             r"^[A-Z][A-Z -]*(-\d{4}-\d{2}-\d{2})?$", e["status"])
            self.assertTrue(e["url"].startswith("https://"), e["url"])
            self.assertIn(urlparse(e["url"]).netloc, ALLOWED_HOSTS, e["url"])
            self.assertGreater(len(e["ref"]), 8, e)

    def test_no_entry_pretends_to_be_verified(self):
        for e in self.entries:
            base = e["status"].split("-20")[0]
            self.assertIn(base, ("FETCHED", "KNOWN-NOT-FETCHED", "SECONDARY",
                                 "FETCHED-VIA-SEARCH"),
                          f"unexpected academic status {e['status']}")
            if base == "KNOWN-NOT-FETCHED":
                # Cited from the literature, so it must resolve somewhere a
                # reader can actually open: a DOI, or an official publisher.
                host = urlparse(e["url"]).netloc
                self.assertTrue(e["url"].startswith("https://doi.org/")
                                or host in ("www.sec.gov", "sec.gov",
                                            "www.cboe.com", "www.risk.net",
                                            "fred.stlouisfed.org",
                                            "pages.stern.nyu.edu",
                                            "www.cis.upenn.edu"),
                                f"{e['ref']} cites an unopenable URL {e['url']}")

    def test_the_premia_papers_behind_the_roster_are_cited(self):
        blob = " ".join(e["ref"] + " " + e["claim"] for e in self.entries).lower()
        for needle in ("jegadeesh", "titman", "bernard", "thomas", "ang",
                       "bondt", "thaler", "amihud", "avellaneda", "almgren",
                       "moskowitz", "gatev", "lou", "frazzini", "damodaran"):
            self.assertIn(needle, blob, f"{needle} is not cited by any strategy")

    def test_the_cost_and_liquidity_literature_is_in_the_source_register(self):
        blob = " ".join(r["claim"] + " " + r["publisher"]
                        for r in config.all_verified_sources()).lower()
        for needle in ("perold", "implementation shortfall", "round lot",
                       "maker-taker", "almgren", "avellaneda", "admati"):
            self.assertIn(needle, blob, f"{needle} is not in the source register")

    def test_no_doi_in_the_code_is_misattributed(self):
        """A citation audit: every DOI named in sim/ must sit next to the paper
        it actually belongs to.  Roll (1984) is 10.2307/2328616 and was once
        cited here as Perold (1988); Perold is 10.2469/faj.v44.5.28."""
        known = {
            "10.2469/faj.v44.5.28": "Perold",
            "10.2307/2328616": "Roll",
            "10.1093/rfs/1.1.3": "Admati",
            "10.1080/14697680701381228": "Avellaneda",
            "10.1088/1469-7688/5/8/005": "Almgren",
            "10.1080/713665679": "Cont",
            "10.1111/1467-9965.00068": "Artzner",
            "10.1111/j.1540-6261.1993.tb04681.x": "Jegadeesh",
            "10.1111/j.1540-6261.1990.tb05088.x": "Jegadeesh",
            "10.2307/2490899": "Bernard",
            "10.1111/j.1540-6261.2006.00836.x": "Ang",
            "10.1111/j.1540-6261.1985.tb05002.x": "Bondt",
            "10.1016/j.jfineco.2004.08.011": "Carr",
        }
        sim_dir = os.path.join(REPO_ROOT, "sim")
        found = set()
        for fn in sorted(os.listdir(sim_dir)):
            if not fn.endswith(".py"):
                continue
            text = open(os.path.join(sim_dir, fn), encoding="utf-8").read()
            for m in re.finditer(r"10\.\d{4,5}/[^\s\)\"'`,;>]+", text):
                doi = m.group(0).rstrip(".,;")
                found.add(doi)
                if doi not in known:
                    continue
                # The author named nearest the DOI must be the right one.
                window = text[max(0, m.start() - 220):m.end() + 220]
                self.assertIn(known[doi], window,
                              f"{fn}: DOI {doi} is not next to {known[doi]}")
                for other_doi, other_author in known.items():
                    if other_author == known[doi]:
                        continue
                    if other_doi in window and other_author in window:
                        continue
                    if other_author in window and other_doi == doi:
                        self.fail(f"{fn}: DOI {doi} attributed to {other_author}")
        self.assertIn("10.2469/faj.v44.5.28", found,
                      "Perold's implementation-shortfall DOI vanished from sim/")
        # The corrected attribution must be documented where the error was.
        micro = open(os.path.join(sim_dir, "microstructure.py"),
                     encoding="utf-8").read()
        self.assertIn("Roll (1984)", micro)
        self.assertRegex(micro,
                         r"Perold 1988[\s\S]{0,200}?10\.2469/faj\.v44\.5\.28")


class TestEveryCitedUrlIsRegistered(unittest.TestCase):
    """The brief: verify line by line and PROVIDE LINKS FOR MANUAL REVIEW.

    A URL that appears in the code but nowhere in the machine-readable register
    is a link a reviewer cannot follow from the site, so this test closes that
    gap: every http(s) URL in sim/ and scripts/ must be reachable from the
    source register, the provider catalogue, or a strategy's academic_basis.
    """

    #: URLs that are code-internal endpoints/templates rather than citations.
    ALLOWED_UNREGISTERED = {
        # Live-data adapter request endpoints (the catalogue registers the docs
        # page for each provider, which is what a reviewer needs).
        "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        "https://query1.finance.yahoo.com/v8/finance/chart/SPY",
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        "https://stooq.com/q/d/l/?s={s}",
        "https://stooq.com/q/d/l/?s=aapl.us&i=d",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/",
        "https://finnhub.io/api/v1/stock/candle?symbol={symbol}",
        "https://api.tiingo.com/tiingo/daily/{symbol}/prices",
        "https://polygon.io/pricing",
        "https://docs.alpaca.markets/docs/market-data",
        # HTML-escaped form of a URL that IS registered.
        "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&amp;range=1y",
        # The secondary source behind IR-05, registered as SECONDARY at the
        # FINRA rule page and named in the irregularity entry.
        "https://help.revolut.com/help/wealth/order-execution-fees-and-limits/trading-regulatory-fees/",
    }

    @staticmethod
    def registered_urls():
        urls = {r["url"] for r in config.all_verified_sources()}
        urls |= {p["docs_url"] for p in marketdata.provider_catalogue()}
        urls |= {e["url"] for s in strategies.build_roster()
                 for e in s.spec.academic_basis}
        for name in ("IRREGULARITIES", "LIMITATIONS", "REMAINING_WORK"):
            for row in load_json(f"{name}.json"):
                urls |= set(row.get("links") or [])
        return urls

    def test_no_code_cites_an_unregistered_url(self):
        registered = self.registered_urls()
        offenders = {}
        for base in ("sim", "scripts"):
            d = os.path.join(REPO_ROOT, base)
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py"):
                    continue
                text = open(os.path.join(d, fn), encoding="utf-8").read()
                for m in re.finditer(r"https?://[^\s'\"\)\],>]+", text):
                    url = m.group(0).rstrip(".,;")
                    if url in registered or url in self.ALLOWED_UNREGISTERED:
                        continue
                    offenders.setdefault(url, set()).add(f"{base}/{fn}")
        self.assertEqual(
            {}, {u: sorted(f) for u, f in offenders.items()},
            "URLs cited in code but absent from every register - a reviewer "
            "cannot follow them from the site")

    def test_the_register_urls_are_all_reachable_from_the_site(self):
        """Every registered URL must be printed somewhere in docs/."""
        docs = os.path.join(REPO_ROOT, "docs")
        if not os.path.isdir(docs):
            self.skipTest("docs/ not built yet")
        blob = []
        for base, _dirs, names in os.walk(docs):
            for n in names:
                if n.endswith((".html", ".json")):
                    with open(os.path.join(base, n), encoding="utf-8") as fh:
                        blob.append(fh.read())
        blob = "\n".join(blob).replace("&amp;", "&")
        missing = [r["url"] for r in config.all_verified_sources()
                   if r["url"] not in blob]
        self.assertEqual(missing, [], "register rows not published on the site")


class TestResearchRegisters(unittest.TestCase):
    def test_irregularities_register(self):
        rows = load_json("IRREGULARITIES.json")
        self.assertIsInstance(rows, list)
        self.assertGreaterEqual(len(rows), 27)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, [f"IR-{i:02d}" for i in range(1, len(rows) + 1)])
        for r in rows:
            self.assertEqual(set(r), {"id", "topic", "severity", "detail",
                                      "resolution", "links"})
            self.assertIn(r["severity"], ("low", "medium", "high"))
            self.assertGreater(len(r["topic"]), 10, r["id"])
            self.assertGreater(len(r["detail"]), 80, r["id"])
            self.assertGreater(len(r["resolution"]), 20, r["id"])
            self.assertIsInstance(r["links"], list)
            for link in r["links"]:
                self.assertTrue(link.startswith("https://"), f"{r['id']}: {link}")
                self.assertIn(urlparse(link).netloc, ALLOWED_HOSTS,
                              f"{r['id']}: unknown host {link}")

    def test_limitations_register(self):
        rows = load_json("LIMITATIONS.json")
        self.assertEqual(len(rows), 16)
        self.assertEqual([r["id"] for r in rows],
                         [f"L-{i:02d}" for i in range(1, 17)])
        for r in rows:
            self.assertEqual(set(r), {"id", "title", "severity", "detail", "fix"})
            self.assertIn(r["severity"], ("low", "medium", "high", "critical"))
            self.assertGreater(len(r["title"]), 10, r["id"])
            self.assertGreater(len(r["detail"]), 80, r["id"])
            self.assertGreater(len(r["fix"]), 20, r["id"])

    def test_remaining_work_register(self):
        rows = load_json("REMAINING_WORK.json")
        self.assertGreaterEqual(len(rows), 14)
        for r in rows:
            self.assertEqual(set(r), {"title", "detail", "priority", "effort",
                                      "where"})
            self.assertIn(r["priority"], ("P0", "P1", "P2"))
            self.assertGreater(len(r["detail"]), 60, r["title"])
            self.assertGreater(len(r["where"]), 3, r["title"])

    def test_the_registers_are_cross_referenced_by_the_code(self):
        """Every IR-xx the engine can raise must exist in the register."""
        import subprocess
        out = subprocess.run(
            ["grep", "-rhoE", r"IR-[0-9]{2}", os.path.join(REPO_ROOT, "sim")],
            capture_output=True, text=True, check=True).stdout
        raised = set(out.split())
        registered = {r["id"] for r in load_json("IRREGULARITIES.json")}
        self.assertTrue(raised, "no IR-xx codes found in sim/")
        self.assertEqual(raised - registered, set(),
                         f"code raises unregistered irregularities: "
                         f"{sorted(raised - registered)}")


class TestRealDataFiles(unittest.TestCase):
    """The files behind every FETCHED-VERIFIED claim must exist and parse."""

    def test_fred_csv_files_are_present_and_complete(self):
        for name, series in (("SP500", "SP500"), ("VIXCLS", "VIXCLS")):
            path = os.path.join(REAL, "fred",
                                f"{name}_2025-09-17_2026-09-16.csv")
            self.assertTrue(os.path.exists(path), f"missing {path}")
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0], ["observation_date", series],
                             f"{name} header is not the fredgraph.csv export")
            self.assertGreater(len(rows), 250)
            dates = [r[0] for r in rows[1:]]
            self.assertEqual(dates, sorted(dates))
            self.assertEqual(dates[0], "2025-09-17")
            self.assertEqual(dates[-1], "2026-09-16")
            # Blanks are market closures, and SP500 has exactly ten of them.
            blanks = [r for r in rows[1:] if not r[1].strip()]
            if name == "SP500":
                self.assertEqual(len(blanks), 10, [b[0] for b in blanks])

    def test_yahoo_anchors_are_present_and_parse(self):
        spy = os.path.join(REAL, "yahoo", "SPY_monthly_1y.json")
        aapl = os.path.join(REAL, "yahoo", "AAPL_snapshot_2026-09-17.json")
        for path in (spy, aapl):
            self.assertTrue(os.path.exists(path), f"missing {path}")
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertTrue(doc)
        with open(spy, encoding="utf-8") as fh:
            doc = json.load(fh)
        blob = json.dumps(doc)
        self.assertIn("SPY", blob)
        self.assertIn("chart", blob.lower())
        with open(aapl, encoding="utf-8") as fh:
            doc = json.load(fh)
        blob = json.dumps(doc)
        self.assertIn("AAPL", blob)
        self.assertRegex(blob, r"dividend")

    def test_the_stored_anchors_match_the_numbers_the_site_quotes(self):
        """AAPL and SPY anchors are quoted on the data page; they must be real."""
        with open(os.path.join(REAL, "yahoo", "AAPL_snapshot_2026-09-17.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        blob = json.dumps(doc)
        self.assertIn("236.7", blob)      # AAPL start anchor
        self.assertIn("332.4", blob)      # AAPL end anchor


if __name__ == "__main__":
    unittest.main()
