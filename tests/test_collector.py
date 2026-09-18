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
