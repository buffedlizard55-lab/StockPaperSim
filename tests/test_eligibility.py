"""Tests for the strict official-price provenance gate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest

from sim.eligibility import audit_official_prices, require_official_prices


class TestOfficialPriceEligibility(unittest.TestCase):
    def _fixture(self, source_class="OFFICIAL", redistribution="LICENSED_FOR_REPOSITORY_REPRODUCTION"):
        root = tempfile.mkdtemp(prefix="price-eligibility-")
        os.makedirs(os.path.join(root, "fred"), exist_ok=True)
        os.makedirs(os.path.join(root, "prices", "nasdaq"), exist_ok=True)
        os.makedirs(os.path.join(root, "raw", "nasdaq"), exist_ok=True)
        start, end = "2025-01-02", "2025-01-03"
        with open(os.path.join(root, "fred", "SP500_2025-01-02_2025-01-03.csv"), "w",
                  encoding="utf-8") as handle:
            handle.write("observation_date,SP500\n2025-01-02,5800\n2025-01-03,5820\n")
        url = ("https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks"
               "&fromdate=2025-01-02&todate=2025-01-03&limit=5000")
        raw = b'{"data":{"totalRecords":2,"tradesTable":{"rows":[]}}}'
        raw_path = os.path.join(root, "raw", "nasdaq", "AAPL.json")
        with open(raw_path, "wb") as handle:
            handle.write(raw)
        raw_sha = hashlib.sha256(raw).hexdigest()
        dividend_url = ("https://api.nasdaq.com/api/quote/AAPL/dividends?assetclass=stocks"
                        "&limit=5000")
        dividend_raw = b'{"data":{"dividends":{"rows":[]}}}'
        dividend_path = os.path.join(root, "raw", "nasdaq", "AAPL_dividends.json")
        with open(dividend_path, "wb") as handle:
            handle.write(dividend_raw)
        dividend_sha = hashlib.sha256(dividend_raw).hexdigest()
        with open(os.path.join(root, "collection_manifest.json"), "w", encoding="utf-8") as handle:
            json.dump({"requests": [
                {"url": url, "ok": True, "status": 200, "sha256": raw_sha},
                {"url": dividend_url, "ok": True, "status": 200, "sha256": dividend_sha},
            ]}, handle)
        bars = [
            {"date": "2025-01-02", "open": 100, "high": 102, "low": 99,
             "close": 101, "volume": 1000},
            {"date": "2025-01-03", "open": 101, "high": 103, "low": 100,
             "close": 102, "volume": 1100},
        ]
        payload = {
            "symbol": "AAPL", "provider": "api.nasdaq.com", "source_class": source_class,
            "source": url, "retrieved_at": "2025-01-04T00:00:00Z",
            "access_status": "PUBLIC_ENDPOINT_RETRIEVED",
            "redistribution_status": redistribution, "raw_file": "raw/nasdaq/AAPL.json",
            "raw_sha256": raw_sha, "dividend_status": "NO_DECLARED_DIVIDENDS",
            "dividend_source": dividend_url, "dividend_raw_file": "raw/nasdaq/AAPL_dividends.json",
            "dividend_raw_sha256": dividend_sha, "bars": bars,
        }
        with open(os.path.join(root, "prices", "nasdaq", "AAPL.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return root, start, end

    def test_complete_licensed_fixture_is_eligible(self):
        root, start, end = self._fixture()
        report = audit_official_prices(root, ("AAPL",), start, end)
        self.assertTrue(report["eligible"], report)
        self.assertEqual(require_official_prices(root=root, symbols=("AAPL",),
                                                 start=start, end=end)["eligible"], True)

    def test_public_but_unlicensed_fixture_fails_closed(self):
        root, start, end = self._fixture(redistribution="NOT_AUTHORIZED_BY_TERMS")
        report = audit_official_prices(root, ("AAPL",), start, end)
        self.assertFalse(report["eligible"])
        self.assertIn("REDISTRIBUTION_NOT_CONFIRMED",
                      {issue["code"] for issue in report["issues"]})
        with self.assertRaises(RuntimeError):
            require_official_prices(root=root, symbols=("AAPL",), start=start, end=end)

    def test_secondary_class_is_not_promoted_by_agreement(self):
        root, start, end = self._fixture(source_class="SECONDARY")
        report = audit_official_prices(root, ("AAPL",), start, end)
        self.assertFalse(report["eligible"])
        self.assertIn("NON_OFFICIAL_SOURCE_CLASS",
                      {issue["code"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()
