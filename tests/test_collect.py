"""The collector's HTTP client, exercised against a server on this machine.

``scripts/collect_real_data.py`` normally runs only where there is an outbound
network (the GitHub runner), which is exactly why its behaviour has to be
testable without one: the collection runs for twenty-five minutes and what it
records is the published provenance.  Everything below is driven against a
local ``http.server`` in-process, so the code path is the real one and the URL
cannot leave the machine.

The two things these tests exist to protect:

* the request must declare itself the way SEC's own instructions say, because a
  refusal cannot be diagnosed from a record that never said what was sent; and
* a refusal must land in the manifest with its status, the publisher's own
  explanation and the number of attempts, because "recorded rather than guessed
  at" is the property the whole data page rests on.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from fixtures import REPO_ROOT

COLLECTOR_PY = os.path.join(REPO_ROOT, "scripts", "collect_real_data.py")

#: A company feed as SEC serves it: the CIK of the company, the tickers it
#: trades under, and (as a second copy of the same number) a link that carries
#: it in the query string.  Both forms are what the parser is allowed to use.
COMPANY_FEED = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <company-info>
  <cik>0000320193</cik>
  <conformed-name>Apple Inc.</conformed-name>
  <tickers>AAPL</tickers>
 </company-info>
 <entry>
  <accession-number>0001140361-26-037020</accession-number>
  <filing-date>2026-09-17</filing-date>
  <filing-href>https://www.sec.gov/Archives/edgar/data/320193/000114036126037020/0001140361-26-037020-index.htm</filing-href>
 </entry>
</feed>
"""

UNDECLARED = (b"<html><body>Your Request Originates from an Undeclared Automated "
              b"Tool. Please declare your user agent.</body></html>")

_seen: dict = {}


class _Handler(BaseHTTPRequestHandler):
    """One handler for every case the tests need, dispatched by path."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output readable
        return

    def _send(self, status: int, body: bytes, headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server's spelling
        _seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
        path = self.path.split("?", 1)[0]
        if path == "/ok.json":
            self._send(200, b'{"ok": true}', {"Content-Type": "application/json"})
            return
        if path == "/gzip.json":
            import gzip
            self._send(200, gzip.compress(b'{"hello": "world"}'),
                       {"Content-Type": "application/json",
                        "Content-Encoding": "gzip"})
            return
        if path == "/refused":
            self._send(403, UNDECLARED, {"Content-Type": "text/html"})
            return
        if path == "/flaky":
            _seen["flaky"] = _seen.get("flaky", 0) + 1
            if _seen["flaky"] < 3:
                self._send(503, b"try again", {"Content-Type": "text/plain"})
            else:
                self._send(200, b'{"ok": true}', {"Content-Type": "application/json"})
            return
        if path.startswith("/ticker/"):
            self._send(200, COMPANY_FEED, {"Content-Type": "application/atom+xml"})
            return
        self._send(404, b"no such document", {"Content-Type": "text/plain"})


def load_collector_module():
    spec = importlib.util.spec_from_file_location("collect_real_data", COLLECTOR_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = load_collector_module()


class FetcherBehaviour(unittest.TestCase):
    """What the fetcher sends, and what it records when the answer is no."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _seen.clear()
        self.manifest = []
        self.fetcher = collector.Fetcher(self.manifest, max_seconds=60)
        # The retry backoff is real in production and pointless here; the
        # attempt *count* is what the manifest has to get right.
        patcher = mock.patch.object(collector.time, "sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_declared_headers_are_the_ones_sec_publishes(self):
        """User-Agent with a contact address, and Accept-Encoding gzip, deflate.

        SEC's "Accessing EDGAR Data" prints the header set it expects from an
        automated client.  The collector sent a self-identifying User-Agent but
        urllib's default ``Accept-Encoding: identity``, and its first collection
        run was answered HTTP 403; this test pins the headers to the published
        set so the record always shows what was actually sent.
        """
        body = self.fetcher.get(f"{self.base}/ok.json", "sec")
        self.assertIsNotNone(body)
        sent = _seen["headers"]
        self.assertEqual(sent.get("user-agent"), collector.SEC_UA)
        self.assertIn("gzip", sent.get("accept-encoding", ""))
        self.assertIn("deflate", sent.get("accept-encoding", ""))
        # A name followed by a contact address, which is the shape the page
        # gives: "Sample Company Name AdminContact@<sample company domain>.com"
        self.assertRegex(collector.SEC_UA, r"^\S+ \S+@\S+\.\S+$")

    def test_a_gzip_response_is_decoded_and_hashed_as_the_representation(self):
        body = self.fetcher.get(f"{self.base}/gzip.json", "sec")
        self.assertEqual(body, b'{"hello": "world"}')
        entry = self.manifest[-1]
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["bytes"], len(body))
        self.assertEqual(entry["sha256"], collector.hashlib.sha256(body).hexdigest())
        self.assertEqual(entry["attempts"], 1)

    def test_a_refusal_records_the_status_and_the_publishers_own_words(self):
        body = self.fetcher.get(f"{self.base}/refused", "sec", note="ticker map")
        self.assertIsNone(body)
        entry = self.manifest[-1]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["status"], 403)          # not 0: the reader needs
        self.assertEqual(entry["error"], "HTTP 403")    # the code a re-fetch sees
        self.assertEqual(entry["attempts"], 1)          # a refusal is not transient
        self.assertIn("Undeclared Automated Tool", entry["error_body"])
        self.assertEqual(entry["note"], "ticker map")

    def test_a_transient_failure_is_retried_and_the_attempts_are_recorded(self):
        body = self.fetcher.get(f"{self.base}/flaky", "sec")
        self.assertEqual(body, b'{"ok": true}')
        entry = self.manifest[-1]
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["attempts"], 3)
        self.assertEqual(_seen["flaky"], 3)

    def test_a_missing_document_is_not_retried(self):
        self.assertIsNone(self.fetcher.get(f"{self.base}/nope", "sec"))
        self.assertEqual(self.manifest[-1]["status"], 404)
        self.assertEqual(self.manifest[-1]["attempts"], 1)

    def test_the_nasdaq_policy_bounds_a_host_that_only_times_out(self):
        """The cross-check host has timed out on every run.

        Three attempts at 45 seconds for eight symbols is eighteen of the
        collection's twenty-five minutes spent proving the same timeout, and it
        is why the SEC section began at minute nineteen.  The policy is a bound
        on that, not a change to what the manifest records.
        """
        self.assertEqual(collector.FETCH_POLICY["nasdaq"]["tries"], 2)
        self.assertLess(collector.FETCH_POLICY["nasdaq"]["timeout"], 45.0)
        # The kind keeps its source class: nothing about the bound hides it.
        self.assertEqual(collector.SOURCE_CLASS["nasdaq"], "OFFICIAL")


#: A settled market as the venue serves it now: numbers as fixed-point strings
#: (contract counts) and dollar strings (prices), and no legacy integer fields.
KALSHI_CURRENT = {
    "ticker": "KXNFLGAME-26SEP17DETBUF-DET",
    "event_ticker": "KXNFLGAME-26SEP17DETBUF",
    "title": "Detroit wins",
    "status": "finalized",
    "result": "no",
    "close_time": "2026-09-18T03:29:53Z",
    "volume_fp": "30421098.89",
    "volume_24h_fp": "29599985.21",
    "open_interest_fp": "16869704.61",
    "last_price_dollars": "0.0100",
    "yes_bid_dollars": "0.0000",
    "yes_ask_dollars": "1.0000",
    "settlement_value_dollars": "0.0000",
}

#: The same market in the legacy spelling, which the reader still accepts.
KALSHI_LEGACY = {
    "ticker": "KXNFLGAME-26SEP17DETBUF-DET",
    "event_ticker": "KXNFLGAME-26SEP17DETBUF",
    "title": "Detroit wins",
    "close_time": "2026-09-18T03:29:53Z",
    "volume": 1234,
    "open_interest": 567,
    "last_price": 1,
    "yes_bid": 0,
    "yes_ask": 100,
    "settlement_value": 0,
}


class KalshiSchema(unittest.TestCase):
    """The venue re-spelled its numbers; the reader follows the payload.

    The collection run of 2026-09-18 wrote a file whose every numeric column was
    null, and the site published that as the venue leaving the fields empty. The
    venue had not: it serves fixed-point contract counts and dollar strings now
    and the collector was reading legacy integer names, so the absence was in the
    reader (IR-41). These tests pin both spellings, and pin that a number the
    payload does not carry is reported as absent rather than as zero.
    """

    def test_the_current_spelling_is_read_and_the_source_key_is_recorded(self):
        row = collector.kalshi_market_row(KALSHI_CURRENT)
        self.assertEqual(row["volume"], 30421098.89)
        self.assertEqual(row["open_interest"], 16869704.61)
        self.assertEqual(row["last_price"], 0.01)
        self.assertEqual(row["yes_ask"], 1.0)
        self.assertEqual(row["settlement_value"], 0.0)
        self.assertEqual(row["source_keys"]["volume"], "volume_fp")
        self.assertEqual(row["source_keys"]["last_price"], "last_price_dollars")
        self.assertEqual(row["result"], "no")
        self.assertEqual(row["source_class"], "OFFICIAL-VENDOR")

    def test_the_legacy_spelling_is_still_read(self):
        row = collector.kalshi_market_row(KALSHI_LEGACY)
        self.assertEqual(row["volume"], 1234.0)
        self.assertEqual(row["open_interest"], 567.0)
        self.assertEqual(row["source_keys"]["volume"], "volume")
        self.assertEqual(row["source_keys"]["last_price"], "last_price")

    def test_an_absent_number_is_none_and_never_zero(self):
        row = collector.kalshi_market_row({"ticker": "X", "volume_fp": ""})
        self.assertIsNone(row["volume"])
        self.assertIsNone(row["last_price"])
        self.assertNotIn("volume", row["source_keys"])
        # ...and the consumer's fallback still has something to read when only
        # open interest is present.
        self.assertIsNone(row["open_interest"])

    def test_the_field_map_names_the_newest_spelling_first(self):
        for canonical, keys in collector.KALSHI_NUMERIC_FIELDS:
            self.assertTrue(keys, canonical)
            self.assertTrue(keys[0].endswith(("_fp", "_dollars")), keys)


class CompanyFeedFallback(unittest.TestCase):
    """ticker -> CIK when the bulk map is refused: the same publisher, another
    endpoint, and the substitution written down where the study can be read."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sps-collect-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.manifest = []
        self.fetcher = collector.Fetcher(self.manifest, max_seconds=60)
        patcher = mock.patch.object(collector.time, "sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_parser_reads_the_cik_and_rejects_a_different_company(self):
        self.assertEqual(collector.cik_from_company_feed(COMPANY_FEED, "AAPL"), 320193)
        self.assertIsNone(collector.cik_from_company_feed(COMPANY_FEED, "MSFT"))
        self.assertIsNone(collector.cik_from_company_feed(b"<feed/>", "AAPL"))

    def test_a_refused_map_falls_back_to_the_company_feeds(self):
        summary = {"errors": [], "cik_map_source": ""}
        patches = [
            mock.patch.object(collector, "SEC_TICKER_MAP", f"{self.base}/refused"),
            mock.patch.object(collector, "SEC_COMPANY_FEED",
                              self.base + "/ticker/{ticker}.atom"),
            mock.patch.object(collector, "INSIDER_TICKERS", ("AAPL",)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        mapping = collector._sec_cik_map(self.fetcher, self.tmp, summary)
        self.assertEqual(mapping, {"AAPL": 320193})
        self.assertIn("browse-edgar", summary["cik_map_source"])
        self.assertTrue(summary["errors"])                 # the refusal is kept
        written = os.path.join(self.tmp, "sec", "cik_map.json")
        self.assertTrue(os.path.exists(written))
        with open(written, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["map"], {"AAPL": 320193})
        self.assertIn("browse-edgar", record["source"])
        self.assertIn("refused", record["why"])
        # The manifest shows one refusal and one successful fallback, in order.
        self.assertEqual([e["ok"] for e in self.manifest], [False, True])
        self.assertEqual(self.manifest[0]["status"], 403)

    def test_a_refused_map_and_refused_feeds_leave_the_study_missing(self):
        summary = {"errors": [], "cik_map_source": "",
                   "tickers_ok": [], "tickers_failed": [], "filings": 0,
                   "transactions": 0, "skipped_tickers": []}
        patches = [
            mock.patch.object(collector, "SEC_TICKER_MAP", f"{self.base}/refused"),
            mock.patch.object(collector, "SEC_COMPANY_FEED", f"{self.base}/refused"),
            mock.patch.object(collector, "INSIDER_TICKERS", ("AAPL", "MSFT")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        result = collector.collect_sec(self.fetcher, self.tmp)
        self.assertEqual(result["transactions"], 0)
        self.assertEqual(result["cik_map_source"], "")
        self.assertTrue(result["errors"])
        # The failure is reported, not exported: no half-built file is left for
        # the site to read as a collected dataset.
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "sec", "form4_transactions.jsonl")))
        self.assertEqual(summarise(self.manifest)["sec"],
                         {"ok": 0, "failed": 3})       # map + two feeds


def summarise(manifest) -> dict:
    """ok/failed counts per kind, the shape the workflow prints."""
    out: dict = {}
    for entry in manifest:
        row = out.setdefault(entry["kind"], {"ok": 0, "failed": 0})
        row["ok" if entry["ok"] else "failed"] += 1
    return out


if __name__ == "__main__":
    unittest.main()
