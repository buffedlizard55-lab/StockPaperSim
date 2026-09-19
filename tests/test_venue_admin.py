"""Venue-admin tests: calendars, halts parser, corporate actions, fees.

The halts fixture models the REAL Nasdaq Trader RSS structure fetched on
2026-09-19 (RSS 2.0; per-item CDATA HTML table with the feed's own header
labels) using TEST-ONLY symbols - it exercises parser mechanics, never stands
in for an authority's halt list.
"""
import datetime as dt
import unittest

from sim import venue_admin as va


D = dt.date


class CalendarTests(unittest.TestCase):
    def test_documented_market_holidays(self):
        for closed in ("2026-11-26", "2026-04-03", "2026-12-25", "2026-07-03"):
            self.assertFalse(va.is_market_open(D.fromisoformat(closed)), closed)
        for open_day in ("2026-09-21", "2026-11-27", "2026-12-24"):
            self.assertTrue(va.is_market_open(D.fromisoformat(open_day)), open_day)

    def test_early_closes(self):
        self.assertTrue(va.is_early_close(D(2026, 11, 27)))
        self.assertTrue(va.is_early_close(D(2026, 12, 24)))
        self.assertFalse(va.is_early_close(D(2026, 12, 23)))

    def test_settlement_uses_fed_calendar_not_weekdays(self):
        # Thanksgiving Thu: T+1 is Friday (Fed open), even though markets close early.
        self.assertEqual(va.settlement_date(D(2026, 11, 25)).isoformat(), "2026-11-27")
        # Good Friday: market closed, Fed OPEN - settlement from Thursday lands Friday.
        self.assertEqual(va.settlement_date(D(2026, 4, 2)).isoformat(), "2026-04-03")
        # Independence Day observed: market closed Fri 07-03, Fed open (holiday is Sat).
        self.assertEqual(va.settlement_date(D(2026, 7, 2)).isoformat(), "2026-07-03")
        # Christmas Eve trade: Fri 12-25 closed, weekend, then Mon 12-28.
        self.assertEqual(va.settlement_date(D(2026, 12, 24)).isoformat(), "2026-12-28")

    def test_session_record_sources_and_times(self):
        rec = va.session_record(D(2026, 9, 21))
        self.assertEqual(rec["open"], "2026-09-21T13:30:00Z")
        self.assertEqual(rec["close"], "2026-09-21T20:00:00Z")
        self.assertFalse(rec["early_close"])
        self.assertEqual(rec["settlement_date"], "2026-09-22")
        self.assertTrue(rec["source_url"].startswith("https://"))
        self.assertTrue(rec["settlement_source_url"].startswith("https://"))
        early = va.session_record(D(2026, 12, 24))
        self.assertEqual(early["close"], "2026-12-24T18:00:00Z")  # 13:00 EST
        self.assertTrue(early["early_close"])

    def test_undocumented_year_fails_closed(self):
        with self.assertRaises(ValueError):
            va.is_market_open(D(2027, 1, 4))
        with self.assertRaises(ValueError):
            va.next_market_day(D(2026, 12, 31))

    def test_closed_session_refuses_record(self):
        with self.assertRaises(ValueError):
            va.session_record(D(2026, 11, 26))


FIXTURE_HALTS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
 <channel>
  <title>NASDAQTrader.com</title>
  <title>NASDAQ Trade Halts</title>
  <pubDate>Sat, 19 Sep 2026 19:18:03 GMT</pubDate>
  <item>
   <title>TESTX</title>
   <pubDate>Fri, 18 Sep 2026 04:00:00 GMT</pubDate>
   <description><![CDATA[<table><tr><td>Halt Date</td><td>Halt Time</td><td>Issue Symbol</td><td>Issue Name</td><td>Market</td><td>Reason Code</td><td>Pause Threshold Price</td><td>Resumption Date</td><td>Resumption Quote Time</td><td>Resumption Trade Time</td></tr><tr><td>09/18/2026</td><td>19:50:00.000</td><td>TESTX</td><td>Test Issue One</td><td>NASDAQ</td><td>T1</td><td></td><td></td><td></td><td></td></tr></table>]]></description>
  </item>
  <item>
   <title>TESTY</title>
   <pubDate>Wed, 01 Oct 2025 04:00:00 GMT</pubDate>
   <description><![CDATA[<table><tr><td>Halt Date</td><td>Halt Time</td><td>Issue Symbol</td><td>Issue Name</td><td>Market</td><td>Reason Code</td><td>Pause Threshold Price</td><td>Resumption Date</td><td>Resumption Quote Time</td><td>Resumption Trade Time</td></tr><tr><td>10/01/2025</td><td>11:28:10</td><td>TESTY</td><td>Test Issue Two</td><td>NASDAQ</td><td>T1</td><td>2</td><td>10/01/2025</td><td>15:50:00</td><td>15:55:00</td></tr></table>]]></description>
  </item>
 </channel>
</rss>
"""


class HaltsParserTests(unittest.TestCase):
    def test_parse_real_shaped_document(self):
        rows = va.parse_halts_feed(FIXTURE_HALTS)
        self.assertEqual(len(rows), 2)
        one, two = rows
        self.assertEqual(one["symbol"], "TESTX")
        self.assertEqual(one["reason_code"], "T1")
        self.assertIsNone(one["resume_time"])
        self.assertEqual(one["market"], "NASDAQ")
        self.assertEqual(two["resume_time"], "15:55:00")
        self.assertEqual(va.halted_symbols(rows), ["TESTX"])

    def test_rejects_non_feed_bytes(self):
        with self.assertRaises(ValueError):
            va.parse_halts_feed(b"<html><body>proxy error</body></html>")

    def test_rejects_wrong_document(self):
        with self.assertRaises(ValueError):
            va.parse_halts_feed(b'<rss version="2.0"><channel><title>Some Other Feed</title></channel></rss>')

    def test_empty_channel_is_zero_halts(self):
        doc = (b'<rss version="2.0"><channel><title>NASDAQ Trade Halts</title>'
               b'<title>NASDAQTrader.com</title></channel></rss>')
        self.assertEqual(va.parse_halts_feed(doc), [])


class CorporateActionTests(unittest.TestCase):
    def _table(self):
        return va.CorporateActionTable(
            [
                va.CorporateAction(
                    symbol="TESTX", action_type="SPLIT", ex_date="2026-10-05",
                    description="2-for-1 test split",
                    source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
                    checked_on="2026-09-19",
                )
            ],
            coverage_note="Only rows with cited primary sources; not a claim of completeness.",
        )

    def test_roundtrip_and_lookup(self):
        table = self._table()
        payload = table.to_json()
        again = va.CorporateActionTable.from_json(payload)
        hits = again.actions_for("TESTX", D(2026, 10, 1), D(2026, 10, 31))
        self.assertEqual([h.action_type for h in hits], ["SPLIT"])
        self.assertEqual(again.actions_for("TESTX", D(2026, 11, 1), D(2026, 11, 30)), [])

    def test_unsourced_row_refused(self):
        with self.assertRaises(ValueError):
            va.CorporateActionTable(
                [va.CorporateAction("TESTX", "DIVIDEND", "2026-10-05", "x", "ftp://invented", "2026-09-19")],
                "note",
            )

    def test_coverage_note_required(self):
        with self.assertRaises(ValueError):
            va.CorporateActionTable([], "")


class RegulatorFeeTests(unittest.TestCase):
    def test_2026_rates(self):
        fees = va.regulator_fees("2026-12-01", 1000, 50.0)
        self.assertAlmostEqual(fees["sec31_fee"], 50000 * 20.60 / 1_000_000, places=6)
        self.assertAlmostEqual(fees["finra_taf"], 1000 * 0.000195, places=6)
        self.assertAlmostEqual(fees["total"], fees["sec31_fee"] + fees["finra_taf"], places=6)

    def test_zero_sec31_before_2026_04_04(self):
        fees = va.regulator_fees("2026-03-20", 1000, 50.0)
        self.assertEqual(fees["sec31_fee"], 0.0)
        self.assertEqual(fees["finra_taf"], 0.195)

    def test_taf_cap_applies(self):
        fees = va.regulator_fees("2026-12-01", 1_000_000, 100.0)
        self.assertEqual(fees["finra_taf"], 9.79)

    def test_small_execution_exemption(self):
        # Per-share execution price below the per-share rate: no fee assessed.
        fees = va.regulator_fees("2026-12-01", 10, 0.0001)
        self.assertEqual(fees["finra_taf"], 0.0)

    def test_sources_attached(self):
        fees = va.regulator_fees("2026-12-01", 1, 1.0)
        self.assertTrue(all(u.startswith("https://") for u in fees["sources"]))


if __name__ == "__main__":
    unittest.main()
