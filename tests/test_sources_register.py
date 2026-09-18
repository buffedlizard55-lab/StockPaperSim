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

from sim import config, marketdata, masterfeed, realdata, strategies, strategies_mf

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
    # Added 2026-09-17 while fixing IR-31/IR-32: the IRS publication is the
    # primary source for payments in lieu of dividends on borrowed stock, and
    # investor.gov is the SEC's own education portal. Both were reached live;
    # investor.gov is here so a future citation is not silently rejected, and
    # it is NOT currently used by any register row.
    "www.irs.gov", "www.investor.gov",
    # Official CPython documentation - the primary source for the hash
    # randomisation behaviour that IR-30 turns on.
    "docs.python.org",
    # Added 2026-09-18 for Season 2's collected data sources: the official
    # endpoints the strategies read, plus the two aggregators that are classed
    # SECONDARY and cross-checked against an independent publisher.
    "api.fda.gov", "statsapi.mlb.com", "www.ncei.noaa.gov",
    "api.elections.kalshi.com", "api.nasdaq.com", "www.nfl.com",
    "official.nba.com", "site.api.espn.com", "buffedlizard55-lab.github.io",
    # Added 2026-09-18 with the rest of the Season 2 register: the SEC's
    # structured-data host and archive, the attempted second publishers (NBA's
    # three hosts, Stooq), and the repository itself, which the provenance notes
    # cite. Every one is either a source the collector calls or a page it cites
    # for the access policy it follows.
    "data.sec.gov", "cdn.nba.com", "stats.nba.com", "www.nba.com",
    "github.com",
    # Added 2026-09-18 with IR-41's correction: the venue's own migration note,
    # which is the primary source for the *_fp / *_dollars field names the
    # collector now reads. Unknown-host rejection is the point of this set, so a
    # citation is never allow-listed without opening the page.
    "docs.kalshi.com",
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
            # "note" is optional and, when present, must say something: it is
            # where the caveat that does not fit the one-line claim goes (e.g.
            # "the rate itself is corroborated only by broker schedules"), so
            # the site can render it next to the link for a reviewer.
            self.assertLessEqual(set(row),
                                 {"claim", "url", "publisher", "status", "note"},
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

    def test_notes_when_present_are_specific(self):
        for row in self.rows:
            note = row.get("note")
            if note is None:
                continue
            self.assertIsInstance(note, str)
            self.assertGreater(len(note), 40,
                               f"note on {row['url']} is too short to help a reviewer")
            # A note that does not mention a date is making a claim about the
            # state of the world that will silently go stale (IR-33).
            self.assertTrue(any(ch.isdigit() for ch in note),
                            f"note on {row['url']} cites no date or number")

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
        # Two kinds of evidence count as "stored", because the two kinds of
        # source are different: a market-data claim is backed by the CSV or JSON
        # that came out of the API (data/real/fred, data/real/yahoo), while a
        # legal or tax claim is backed by an excerpt of the page kept in
        # data/real/regulatory with the SOURCE url in it. A FETCHED-VERIFIED row
        # with neither is a claim that cannot be re-checked once the site moves.
        regulatory = os.path.join(REAL, "regulatory")
        snapshots = {}
        if os.path.isdir(regulatory):
            for name in sorted(os.listdir(regulatory)):
                with open(os.path.join(regulatory, name), encoding="utf-8") as fh:
                    snapshots[name] = fh.read()
        for row in verified:
            host = urlparse(row["url"]).netloc
            if host.endswith("fred.stlouisfed.org") or host.endswith("finance.yahoo.com"):
                continue
            hits = [n for n, text in snapshots.items() if row["url"] in text]
            self.assertTrue(hits,
                            f"{row['url']} claims VERIFIED but no stored evidence "
                            f"exists for it: neither a data/real download from "
                            f"that host nor a data/real/regulatory excerpt citing "
                            f"the URL")

    def test_regulatory_snapshots_are_traceable_and_dated(self):
        """Every excerpt under data/real/regulatory names its URL and its date.

        These files are what makes a legal citation checkable after the publisher
        moves the page (IR-33 is two such moves in one pass). Without a retrieval
        date they assert something about "now" forever, which is how the register
        accumulated a dead link in the first place.
        """
        regulatory = os.path.join(REAL, "regulatory")
        names = sorted(os.listdir(regulatory)) if os.path.isdir(regulatory) else []
        self.assertGreaterEqual(len(names), 5,
                                "the regulatory excerpts are the evidence trail "
                                "for the fee, margin and securities-lending "
                                "figures; there should be at least one per "
                                "non-data FETCHED-VERIFIED row")
        for name in names:
            with open(os.path.join(regulatory, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("SOURCE: http", text,
                          f"{name} does not record the URL it came from")
            self.assertRegex(text, r"[Rr]etrieved: 20\d\d-\d\d-\d\d",
                             f"{name} records no retrieval date")
            self.assertRegex(text, r"[Ww][Hh][YwWY] THIS FILE EXISTS",
                             f"{name} does not say which decision it supports")

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
        # Kalshi's API reference and its fixed-point migration note: the schema
        # the collector reads, not data itself. The endpoint they document is
        # registered (sim/realdata.py, and IR-41 links it), which is the link a
        # reviewer needs to re-fetch the payload.
        "https://docs.kalshi.com/getting_started/fixed_point_migration",
        "https://docs.kalshi.com/api-reference/market/get-markets",
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
        # Season 2's participants declare their own primary sources, and
        # sim/realdata.py carries the register of every endpoint the collector
        # calls. Both count as registers: a reader can follow either one.
        urls |= {e["url"] for s in strategies_mf.build_roster_mf()
                 for e in s.spec.academic_basis}
        urls |= {row["url"] for row in realdata.collected_sources()}
        # The MasterFeed register is itself published (Season 2's masterfeed
        # page renders it row by row), so the official URL it names for each
        # project counts as a register entry a reader can follow.
        urls |= {row["official_url"] for row in masterfeed.signal_register()}
        # ...and the MasterSite project page it links to for the project itself.
        urls |= {row["site_url"] for row in masterfeed.signal_register()}
        urls.add(masterfeed.MASTER_SITE_URL)
        for name in ("IRREGULARITIES", "LIMITATIONS", "REMAINING_WORK"):
            for row in load_json(f"{name}.json"):
                urls |= set(row.get("links") or [])
        return urls

    @staticmethod
    def _normalise(url: str) -> str:
        """Reduce a URL to the endpoint it names, for comparison purposes.

        Code builds URLs (".../chart/" + symbol, "...?search=" + query), so a
        literal match would demand that every concatenation be registered
        separately. Stripping the query and any template fragment, and comparing
        by prefix, means one register row for an endpoint covers the URLs the
        collector derives from it - while an unregistered *host or path* still
        fails the test, which is the property that matters.
        """
        url = url.split("#", 1)[0].split("?", 1)[0]
        for cut in ("{", "("):
            url = url.split(cut, 1)[0]
        return url.rstrip("/.,;")

    def test_no_code_cites_an_unregistered_url(self):
        registered = {self._normalise(u) for u in self.registered_urls()}
        allowed = self.ALLOWED_UNREGISTERED
        offenders = {}
        for base in ("sim", "scripts"):
            d = os.path.join(REPO_ROOT, base)
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py"):
                    continue
                text = open(os.path.join(d, fn), encoding="utf-8").read()
                for m in re.finditer(r"https?://[^\s'\"\)\],>]+", text):
                    url = m.group(0).rstrip(".,;")
                    if url in allowed:
                        continue
                    norm = self._normalise(url)
                    if any(norm == r or norm.startswith(r + "/") for r in registered):
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
        # 16 limitations were registered for Season 1; Season 2 adds its own
        # (one real history, asserted mappings, forward-only sources, borrow
        # availability, ledger independence). The register grows, the ids stay
        # sequential and every row keeps the same shape.
        self.assertGreaterEqual(len(rows), 16)
        self.assertEqual([r["id"] for r in rows],
                         [f"L-{i:02d}" for i in range(1, len(rows) + 1)])
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



class TestRuntimeRegisterClaims(unittest.TestCase):
    """Counts the register quotes from a run must be that run's counts.

    IR-13, IR-14 and IR-15 each state how many events the published season
    produced. All three were quietly wrong for two full re-runs: every number
    described whichever memory happened to exist when the sentence was written, and
    each later fix moved the season underneath the prose without a single test
    noticing. The run's own irregularities file is the authority for those counts,
    so it is compared against the prose here - a register may quote a peak, but it
    may not quote a stale one.
    """

    RUN = "season1-primary-seed20260917"

    def _runtime_rows(self):
        path = os.path.join(REPO_ROOT, "memory", "runs", self.RUN, "irregularities.json")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload["irregularities"] if isinstance(payload, dict) else payload

    def _peak(self, code):
        """(largest per-participant count, total) for one runtime IR code.

        The run's rows are already aggregated per participant by the engine, and
        the participant is part of the message rather than a field of its own, so
        "@Name: 67 order(s) REJECTED..." is the unit being read here.
        """
        counts: dict = {}
        for r in self._runtime_rows():
            if r.get("id") != code:
                continue
            msg = r.get("message", "")
            m = re.match(r"(@[\w.\-]+): (\d+) order\(s\)", msg)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2))
            else:
                counts["unaggregated"] = counts.get("unaggregated", 0) + 1
        return (max(counts.values()) if counts else 0,
                sum(counts.values()) if counts else 0)

    def _entry_text(self, ident):
        row = {r["id"]: r for r in load_json("IRREGULARITIES.json")}[ident]
        return row["detail"] + " " + row["resolution"]

    def test_the_rejection_and_clip_counts_are_the_published_ones(self):
        peak, total = self._peak("IR-14")
        text = self._entry_text("IR-14")
        self.assertIn(f"{peak} such rejections", text,
                      f"IR-14 does not state the published peak of {peak} rejected orders")
        self.assertIn(f"{total} across all participants", text,
                      f"IR-14 quotes a peak but not the {total} events it sits inside")
        clip, clip_total = self._peak("IR-15")
        text = self._entry_text("IR-15")
        self.assertIn(f"{clip} clips", text,
                      f"IR-15 does not state the published peak of {clip} clips")
        self.assertIn(f"{clip_total} clips in total", text,
                      f"IR-15 does not state the published total of {clip_total} clips")

    def test_the_blowup_entry_quotes_the_published_worst_return(self):
        with open(os.path.join(REPO_ROOT, "memory", "runs", self.RUN,
                               "leaderboard.json"), encoding="utf-8") as fh:
            board = json.load(fh)["leaderboard"]
        worst = min(r["total_return_pct"] for r in board)
        self.assertLess(worst, 0.0, "the worst participant is not meant to be profitable")
        self.assertIn(f"{worst:.2f}%", self._entry_text("IR-13"),
                      f"IR-13 does not quote the published worst return {worst:.2f}%")

if __name__ == "__main__":
    unittest.main()
