"""Registration state of insider buy signals under partial SEC coverage.

A landed SEC insider collection does not guarantee that *purchase* signals
have observations: a walk of recent Form 4 filings can legitimately hold
only S/A/F/M/G codes and zero open-market purchases (code P).  The signal
book must then register the buy arrays as ``NO-OBSERVATIONS`` (observed but
empty) instead of ``AVAILABLE`` - otherwise a strategy would read an
all-zero series that claims to be real data, the exact shape the Season 2
availability invariant forbids.
"""

import json
import os
import tempfile
import unittest

from sim import masterfeed


class _StubInstrument:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _StubMarketData:
    def __init__(self, dates):
        self.dates = list(dates)
        self.instruments = {s: _StubInstrument(s) for s in ("AAPL", "MSFT")}


def _write_agent_rows(root, rows):
    lane = os.path.join(root, "sec_agent")
    os.makedirs(lane, exist_ok=True)
    with open(os.path.join(lane, "form4_transactions.jsonl"), "w",
              encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _row(code, ticker="AAPL", date="2026-07-15", shares=100.0, price=100.0,
         title="Chief Executive Officer"):
    return {
        "ticker": ticker, "cik": "0000320193", "issuer": "Apple Inc.",
        "insider": "TEST PERSON", "roles": ["officer"], "title": title,
        "transaction_date": date, "filed_date": date, "code": code,
        "security": "Common Stock", "shares": shares, "price": price,
        "shares_after": 1000.0, "ownership": "D", "tenb5_1_plan": False,
        "accession": f"0000000000-26-00{ord(code) if code.isalpha() else 0:04d}",
        "filing_index": "https://www.sec.gov/", "source_url": "https://www.sec.gov/",
        "source_sha256": "x" * 64, "source_class": "OFFICIAL",
        "channel": "agent-rendered-extract",
    }


DATES = ["2026-07-01", "2026-07-15", "2026-07-16", "2026-08-03"]


class TestInsiderSignalRegistration(unittest.TestCase):
    def _build(self, rows):
        with tempfile.TemporaryDirectory() as root:
            _write_agent_rows(root, rows)
            book = masterfeed.SignalBook(DATES)
            masterfeed._insider_signals(book, _StubMarketData(DATES), root)
            return book

    def test_buy_signal_with_a_real_purchase_is_available(self):
        book = self._build([_row("P"), _row("S", ticker="MSFT")])
        self.assertEqual(
            book.availability["insider_buys_30d::AAPL"]["state"], "AVAILABLE")
        self.assertEqual(
            book.availability["insider_ceo_buys_30d::AAPL"]["state"], "AVAILABLE")
        self.assertEqual(
            book.availability["insider_buys_30d"]["state"], "AVAILABLE")
        self.assertEqual(
            book.availability["insider_buy_ratio_30d"]["state"], "AVAILABLE")
        self.assertTrue(book.available("insider_buys_30d::AAPL"))

    def test_collection_without_purchases_registers_no_observations(self):
        book = self._build([_row("S"), _row("A"), _row("F")])
        self.assertEqual(
            book.availability["insider_buys_30d::AAPL"]["state"], "NO-OBSERVATIONS")
        self.assertEqual(
            book.availability["insider_ceo_buys_30d::AAPL"]["state"],
            "NO-OBSERVATIONS")
        self.assertEqual(
            book.availability["insider_buys_30d"]["state"], "NO-OBSERVATIONS")
        self.assertFalse(book.available("insider_buys_30d"))
        # With no purchases the ratio is 0.0 at every session too - real, but
        # indistinguishable from a missing signal, so it registers the same way.
        self.assertEqual(
            book.availability["insider_buy_ratio_30d"]["state"], "NO-OBSERVATIONS")
        # The invariant the signal book exists to protect: an array that is
        # all zeros must never be marked AVAILABLE.
        for name, meta in book.availability.items():
            if meta["state"] == "AVAILABLE":
                self.assertTrue(any(v != 0.0 for v in book.arrays[name]), name)

    def test_missing_collection_still_registers_missing(self):
        with tempfile.TemporaryDirectory() as root:
            book = masterfeed.SignalBook(DATES)
            masterfeed._insider_signals(book, _StubMarketData(DATES), root)
        self.assertEqual(book.availability["insider_buys_30d"]["state"], "MISSING")


if __name__ == "__main__":
    unittest.main()
