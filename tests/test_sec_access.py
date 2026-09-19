"""Tests for the SEC EDGAR access rules (``sim/sec.py``).

The insider participants are gated on the Form 4 stream, and the first collection
run was refused with HTTP 403 because the client did not identify itself the way
the SEC publishes. The module under test is therefore a citation, and these tests
check the citation is implemented exactly:

* the declared header set is the SEC's published one, verbatim;
* the User-Agent names a client and a contact, and an operator-supplied contact
  wins over the default;
* the request rate cannot exceed the published maximum;
* ``Accept-Encoding: gzip, deflate`` is declared and the compressed bodies are
  actually decoded, because declaring an encoding the client cannot read is how
  a collector ends up storing bytes it cannot parse;
* one request records exactly what was sent, so the manifest can be compared
  with the policy.
"""

from __future__ import annotations

import gzip
import os
import unittest
import zlib
from unittest import mock

from fixtures import REPO_ROOT  # noqa: F401 - puts the repository on sys.path
from sim import sec


class DeclaredHeadersTest(unittest.TestCase):
    def test_header_set_is_the_published_one(self):
        headers = sec.sec_headers()
        self.assertEqual(headers["Accept-Encoding"], "gzip, deflate")
        self.assertEqual(headers["Host"], "www.sec.gov")
        self.assertEqual(sec.SEC_HEADER_NAMES,
                         ("User-Agent", "Accept-Encoding", "Host"))
        for name in sec.SEC_HEADER_NAMES:
            self.assertTrue(headers.get(name), f"{name} must be sent")

    def test_user_agent_names_a_client_and_a_contact(self):
        agent = sec.sec_user_agent()
        self.assertIn("@", agent, "the SEC's rule is that the header names a contact")
        self.assertIn("StockPaperSim", agent)
        # The published shape is "Name contact@host" - a bare address is not a
        # declared client and a bare name is not a contact.
        self.assertGreater(len(agent.split()), 1)

    def test_environment_supplies_the_operator_contact(self):
        with mock.patch.dict(os.environ, {"SPS_SEC_USER_AGENT": "Acme Research ops@acme.example"},
                             clear=False):
            self.assertEqual(sec.sec_user_agent(), "Acme Research ops@acme.example")
            self.assertEqual(sec.sec_headers()["User-Agent"], "Acme Research ops@acme.example")
            self.assertEqual(sec.declared_headers_record()["policy"]["contact_source"],
                             "SPS_SEC_USER_AGENT")

    def test_default_contact_is_used_when_no_environment_is_set(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn(sec.DEFAULT_SEC_CONTACT, sec.sec_user_agent())
            self.assertEqual(sec.declared_headers_record()["policy"]["contact_source"],
                             "repository default")

    def test_policy_record_carries_the_rate_and_the_source(self):
        record = sec.declared_headers_record()
        self.assertEqual(record["policy"]["max_requests_per_second"], 10.0)
        self.assertIn("accessing-edgar-data", record["policy"]["source"])


class RateLimiterTest(unittest.TestCase):
    def test_the_limiter_keeps_the_run_under_the_published_maximum(self):
        clock = {"now": 0.0}
        slept: list = []

        def fake_clock():
            return clock["now"]

        def fake_sleep(seconds):
            slept.append(seconds)
            clock["now"] += seconds

        limiter = sec.SecRateLimiter(clock=fake_clock, sleep=fake_sleep)
        # Ten requests, issued as fast as the limiter allows.
        for _ in range(10):
            limiter.wait()
        elapsed = clock["now"]
        self.assertGreaterEqual(elapsed, 1.0,
                                "ten requests must not fit inside one second")
        self.assertEqual(limiter.requests, 10)
        self.assertLessEqual(limiter.max_per_second, sec.SEC_MAX_REQUESTS_PER_SECOND)
        self.assertTrue(all(s > 0 for s in slept))

    def test_interval_is_derived_from_the_published_limit(self):
        limiter = sec.SecRateLimiter(max_per_second=sec.SEC_MAX_REQUESTS_PER_SECOND,
                                     clock=lambda: 0.0, sleep=lambda _s: None)
        self.assertAlmostEqual(1.0 / limiter.min_interval, 9.0, places=6)
        self.assertLess(1.0 / limiter.min_interval, sec.SEC_MAX_REQUESTS_PER_SECOND)
        self.assertAlmostEqual(sec.SEC_MIN_INTERVAL_EFFECTIVE, limiter.min_interval)

    def test_a_rejected_rate_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            sec.SecRateLimiter(max_per_second=0)


class DecodeBodyTest(unittest.TestCase):
    def test_gzip_is_decoded_when_declared(self):
        payload = b'{"filings": 3}'
        self.assertEqual(sec.decode_body(gzip.compress(payload), "gzip"), payload)

    def test_gzip_is_detected_from_the_magic_bytes(self):
        payload = b"Form 4"
        self.assertEqual(sec.decode_body(gzip.compress(payload), ""), payload)

    def test_deflate_and_raw_deflate_are_both_handled(self):
        payload = b"CIK0000320193"
        self.assertEqual(sec.decode_body(zlib.compress(payload), "deflate"), payload)
        raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        self.assertEqual(sec.decode_body(raw.compress(payload) + raw.flush(), "deflate"),
                         payload)

    def test_an_uncompressed_body_is_returned_unchanged(self):
        payload = b"<html>Undeclared Automated Tool</html>"
        self.assertEqual(sec.decode_body(payload, ""), payload)
        self.assertEqual(sec.decode_body(payload, "identity"), payload)


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200, encoding: str = ""):
        self._body = body
        self.status = status
        self.headers = {"Content-Encoding": encoding}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class EdgarRequestTest(unittest.TestCase):
    def test_one_request_declares_the_policy_and_records_it(self):
        seen = {}

        def opener(request, timeout=30.0):
            seen["headers"] = dict(request.headers)
            seen["url"] = request.full_url
            return _FakeResponse(gzip.compress(b"ok"), encoding="gzip")

        status, body, record = sec.edgar_request(
            "https://www.sec.gov/files/company_tickers.json",
            limiter=sec.SecRateLimiter(clock=lambda: 0.0, sleep=lambda _s: None),
            opener=opener)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")
        self.assertEqual(record["bytes"], 2)
        self.assertEqual(record["headers"]["Accept-Encoding"], "gzip, deflate")
        self.assertEqual(record["headers"]["Host"], "www.sec.gov")
        # urllib capitalises header names; compare case-insensitively.
        lowered = {k.lower(): v for k, v in seen["headers"].items()}
        self.assertEqual(lowered["accept-encoding"], "gzip, deflate")
        self.assertIn("@", lowered["user-agent"])
        self.assertEqual(record["url"], "https://www.sec.gov/files/company_tickers.json")

    def test_an_http_error_is_returned_as_a_record_not_raised(self):
        class _Boom(Exception):
            code = 403

        def opener(request, timeout=30.0):
            raise _Boom("refused")

        status, body, record = sec.edgar_request(
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
            limiter=sec.SecRateLimiter(clock=lambda: 0.0, sleep=lambda _s: None),
            opener=opener)
        self.assertEqual(status, 403)
        self.assertEqual(body, b"")
        self.assertIn("refused", record["error"])
        self.assertEqual(record["headers"]["Host"], "www.sec.gov")

    def test_documented_endpoints_are_https_and_on_a_sec_host(self):
        for key, url in sec.SEC_ENDPOINTS.items():
            if key == "insider_sets_page":
                continue
            self.assertTrue(url.startswith("https://"), key)
            host = url.split("/")[2]
            self.assertTrue(host.endswith("sec.gov"), (key, host))


if __name__ == "__main__":
    unittest.main()
