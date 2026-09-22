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


class TestInsiderSignalsAreDatedByFilingDate(unittest.TestCase):
    """A purchase is knowable on its EDGAR filing date, not its trade date.

    Section 16(a) allows two business days between the two and a late filing
    can separate them by months (an MSFT officer's 2025-04-23 trade was filed
    on 2025-12-12, accession 0000789019-25-000120).  Windowing by the trade
    date would let a strategy react before the filing existed (IR-82).
    """

    # Sessions around a trade on 07-15 that was only filed on 07-28.
    DATES = ["2026-07-01", "2026-07-16", "2026-07-27", "2026-07-29", "2026-09-01"]

    def _build(self, rows):
        with tempfile.TemporaryDirectory() as root:
            _write_agent_rows(root, rows)
            book = masterfeed.SignalBook(self.DATES)
            masterfeed._insider_signals(book, _StubMarketData(self.DATES), root)
            return book

    def test_purchase_counts_only_after_its_filing_date(self):
        row = _row("P")
        row["transaction_date"] = "2026-07-15"
        row["filed_date"] = "2026-07-28"
        book = self._build([row])
        buys = book.arrays["insider_buys_30d::AAPL"]
        ceo = book.arrays["insider_ceo_buys_30d::AAPL"]
        # 07-16 and 07-27: the trade has happened but nobody outside knows.
        self.assertEqual(buys[1], 0.0)
        self.assertEqual(buys[2], 0.0)
        self.assertEqual(ceo[2], 0.0)
        # 07-29: the filing is public (filed 07-28 < 07-29) -> visible.
        self.assertEqual(buys[3], 1.0)
        self.assertEqual(ceo[3], 1.0)
        self.assertEqual(book.arrays["insider_buys_30d"][3], 1.0)
        # 09-01: 35 days after filing -> outside the trailing 30-day window.
        self.assertEqual(buys[4], 0.0)
        self.assertIn("dated by EDGAR filing date",
                      book.availability["insider_buys_30d::AAPL"]["note"])

    def test_sales_use_the_filing_date_too(self):
        row = _row("S")
        row["transaction_date"] = "2026-07-15"
        row["filed_date"] = "2026-07-28"
        buy = _row("P", date="2026-07-02")
        book = self._build([row, buy])
        ratio = book.arrays["insider_buy_ratio_30d"]
        # 07-16: one buy known (filed 07-02), the sale is not yet filed.
        self.assertEqual(ratio[1], 1.0)
        # 07-29: the sale is now public -> 1 buy / 1 sale.
        self.assertEqual(ratio[3], 1.0)
        self.assertEqual(book.arrays["insider_buys_30d"][1], 1.0)

    def test_row_without_filing_date_falls_back_and_is_reported(self):
        row = _row("P")
        row["transaction_date"] = "2026-07-15"
        row["filed_date"] = ""
        book = self._build([row])
        # Fallback: counted from the trade date, and the note says so.
        self.assertEqual(book.arrays["insider_buys_30d::AAPL"][1], 1.0)
        note = book.availability["insider_buys_30d::AAPL"]["note"]
        self.assertIn("1 row(s) carried no filing date", note)

    def test_bulk_rows_carry_their_filing_date(self):
        with tempfile.TemporaryDirectory() as root:
            lane = os.path.join(root, "insider_bulk")
            os.makedirs(lane, exist_ok=True)
            with open(os.path.join(lane, "insider_transactions.jsonl"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "symbol": "AAPL", "transaction_code": "P",
                    "transaction_date": "2026-07-15", "filing_date": "2026-07-28",
                    "accession_number": "0000000000-26-000001", "shares": 10,
                    "price_per_share": 100.0,
                    "owners": [{"relationship": "officer",
                                "title": "Chief Executive Officer"}],
                }) + "\n")
            rows, files, _note = masterfeed._load_insider_rows(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filed_date"], "2026-07-28")
        self.assertEqual(rows[0]["transaction_date"], "2026-07-15")


if __name__ == "__main__":
    unittest.main()


class TestDistinctBuyerCount(unittest.TestCase):
    """``insider_buyers_30d`` counts reporting *people*, not lots.

    One Form 4 can report dozens of same-day lots by one person (Tesla's
    2025-09-15 filing reports 25 code-P lots by one reporting person); the
    cluster thesis is about several insiders buying, so the cluster rule must
    read the distinct-buyer series and not the lot count.
    """

    DATES = ["2026-07-01", "2026-07-20", "2026-09-01"]

    def _build(self, rows):
        with tempfile.TemporaryDirectory() as root:
            _write_agent_rows(root, rows)
            book = masterfeed.SignalBook(self.DATES)
            masterfeed._insider_signals(book, _StubMarketData(self.DATES), root)
            return book

    def test_many_lots_by_one_person_is_one_buyer(self):
        rows = []
        for i in range(25):
            row = _row("P", shares=100.0 + i, price=300.0 + i)
            row["owner_cik"] = "0001494730"
            row["accession"] = "0001104659-25-089693"
            rows.append(row)
        book = self._build(rows)
        self.assertEqual(book.arrays["insider_buys_30d::AAPL"][1], 25.0)
        self.assertEqual(book.arrays["insider_buyers_30d::AAPL"][1], 1.0)
        self.assertEqual(book.arrays["insider_buyers_30d"][1], 1.0)
        self.assertEqual(
            book.availability["insider_buyers_30d::AAPL"]["state"], "AVAILABLE")

    def test_two_people_is_a_cluster_of_two(self):
        a = _row("P", shares=100.0)
        a["owner_cik"] = "0000000001"
        a["accession"] = "0000000000-26-000001"
        b = _row("P", shares=200.0)
        b["owner_cik"] = "0000000002"
        b["insider"] = "OTHER PERSON"
        b["accession"] = "0000000000-26-000002"
        book = self._build([a, b])
        self.assertEqual(book.arrays["insider_buyers_30d::AAPL"][1], 2.0)
        # The unqualified total counts (symbol, person) pairs across names.
        c = _row("P", ticker="MSFT", shares=300.0)
        c["owner_cik"] = "0000000001"
        c["accession"] = "0000000000-26-000003"
        book = self._build([a, b, c])
        self.assertEqual(book.arrays["insider_buyers_30d"][1], 3.0)
        self.assertEqual(book.arrays["insider_buyers_30d::MSFT"][1], 1.0)

    def test_missing_collection_registers_buyers_missing(self):
        with tempfile.TemporaryDirectory() as root:
            book = masterfeed.SignalBook(self.DATES)
            masterfeed._insider_signals(book, _StubMarketData(self.DATES), root)
        self.assertEqual(book.availability["insider_buyers_30d"]["state"], "MISSING")
        self.assertIn("insider_buyers_30d::AAPL", book.arrays)
