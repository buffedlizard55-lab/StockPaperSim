"""The rendered-Form-4 lane parses SEC content, and only SEC content.

The fixture is the verbatim rendered view (agent page-fetch route) of Apple's
Form 4 accession 0001140361-26-036226 (filed 2026-09-10, Jennifer Newstead,
sale of 1,438 shares at $317.23 on 2026-09-08).  The expected values below
are the same values the SEC's own XML for that accession carries, so the test
pins the extraction against the filing's published facts rather than against
the parser's own opinion.
"""

from __future__ import annotations

import os
import unittest

from sim.edgar_rendered import parse_rendered_form4, sha256_text

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures",
                       "rendered_form4_aapl_newstead_2026-09-10.md")


def _fixture_text() -> str:
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        return handle.read()


class RenderedForm4Tests(unittest.TestCase):
    def test_real_filing_extracts_the_published_values(self) -> None:
        markdown = _fixture_text()
        result = parse_rendered_form4(
            markdown, expected_ticker="AAPL",
            accession="0001140361-26-036226", cik=320193,
            filed_date="2026-09-10",
            source_url=("https://www.sec.gov/Archives/edgar/data/320193/"
                        "000114036126036226/xslF345X06/form4.xml"),
            filing_index=("https://www.sec.gov/Archives/edgar/data/320193/"
                          "000114036126036226/0001140361-26-036226-index.htm"))
        rows, flags = result["rows"], result["flags"]
        self.assertEqual(len(rows), 1, flags)
        row = rows[0]
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["cik"], 320193)
        self.assertEqual(row["insider"], "Newstead Jennifer")
        self.assertEqual(row["owner_cik"], "0001780525")
        self.assertIn("officer", row["roles"])
        self.assertEqual(row["title"], "SVP, GC and Government Affairs")
        self.assertEqual(row["transaction_date"], "2026-09-08")
        self.assertEqual(row["filed_date"], "2026-09-10")
        self.assertEqual(row["code"], "S")
        self.assertEqual(row["security"], "Common Stock")
        self.assertEqual(row["acquired_or_disposed"], "D")
        self.assertEqual(row["shares"], 1438.0)
        self.assertEqual(row["price"], 317.23)
        self.assertEqual(row["shares_after"], 34352.0)
        self.assertEqual(row["ownership"], "D")
        self.assertTrue(row["tenb5_1_plan"])
        self.assertEqual(row["accession"], "0001140361-26-036226")
        self.assertEqual(row["source_class"], "OFFICIAL")
        self.assertEqual(row["channel"], "agent-rendered-extract")
        self.assertEqual(row["source_sha256"], sha256_text(markdown))

    def test_wrong_expected_ticker_is_rejected(self) -> None:
        result = parse_rendered_form4(_fixture_text(), expected_ticker="MSFT",
                                      accession="0001140361-26-036226")
        self.assertEqual(result["rows"], [])
        self.assertTrue(any("rendered issuer ticker" in f
                            for f in result["flags"]))

    def test_unparseable_content_yields_no_rows_and_a_flag(self) -> None:
        result = parse_rendered_form4("not a form at all",
                                      expected_ticker="AAPL",
                                      accession="0000000000-00-000000")
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["flags"])

    def test_derivative_only_filing_has_no_table_one_rows(self) -> None:
        markdown = ("2. Issuer Name and Ticker \\[ MSFT \\]\n"
                    "| **Table I - Non-Derivative Securities Acquired** |\n"
                    "| 1. Title | 2. Date |\n"
                    "| **Table II - Derivative Securities** |\n")
        result = parse_rendered_form4(markdown, expected_ticker="MSFT",
                                      accession="0000000000-00-000001")
        self.assertEqual(result["rows"], [])
        self.assertTrue(any("no Table I data rows" in f
                            for f in result["flags"]))

    def test_malformed_data_row_is_discarded_not_guessed(self) -> None:
        markdown = _fixture_text().replace(
            "| Common Stock(1) | 09/08/2026 |  | S |  | 1,438 | D | $317.23 | 34,352 | D |  |",
            "| Common Stock(1) | 09/08/2026 |  | XX |  | 1,438 | D | $317.23 | 34,352 | D |  |")
        result = parse_rendered_form4(markdown, expected_ticker="AAPL",
                                      accession="0001140361-26-036226")
        self.assertEqual(result["rows"], [])
        self.assertTrue(any("matched no" in f or "no Table I" in f
                            for f in result["flags"]))


if __name__ == "__main__":
    unittest.main()


class FootnoteMarkerTests(unittest.TestCase):
    """SEC renders inline footnote markers on codes and numbers (S(3),
    $218.0144(4), 438,000(3), 299,428(2)(3)); they must not defeat parsing."""

    ORIG_ROW = ("| Common Stock(1) | 09/08/2026 |  | S |  | 1,438 | D | "
                "$317.23 | 34,352 | D |  |")

    def _parse(self, row: str) -> dict:
        markdown = _fixture_text().replace(self.ORIG_ROW, row)
        self.assertNotEqual(markdown, _fixture_text(), "row substitution failed")
        result = parse_rendered_form4(markdown, expected_ticker="AAPL",
                                      accession="0001140361-26-036226",
                                      cik=320193, filed_date="2026-09-10")
        rows, flags = result["rows"], result["flags"]
        self.assertEqual(len(rows), 1, flags)
        self.assertEqual(flags, [])
        return rows[0]

    def test_footnoted_code_and_price_are_parsed(self) -> None:
        row = self._parse(self.ORIG_ROW.replace("|  | S |", "|  | S(3) |")
                                       .replace("$317.23", "$317.23(4)"))
        self.assertEqual(row["code"], "S")
        self.assertEqual(row["shares"], 1438.0)
        self.assertEqual(row["price"], 317.23)

    def test_footnoted_amounts_are_parsed(self) -> None:
        row = self._parse(self.ORIG_ROW.replace("1,438", "1,438(5)")
                                       .replace("34,352", "34,352(2)(3)"))
        self.assertEqual(row["shares"], 1438.0)
        self.assertEqual(row["shares_after"], 34352.0)


class IndexFactsTests(unittest.TestCase):
    """Filing dates come from an SEC document in either of the two shapes the
    SEC serves: the rendered ``-index.htm`` page or the SGML header page
    (``-index-headers.html``), which is what the lane falls back to when the
    rendered page answers with the SEC's "File Unavailable" apology."""

    RENDERED = ("**SEC Accession No.** 0000789019-26-000028\n\n\nFiling Date\n\n"
                "2026-02-18\n\nAccepted\n\n2026-02-18 18:14:22\n\nDocuments\n\n2\n\n"
                "Period of Report\n\n2026-02-18\n")
    HEADER = ("<SEC-DOCUMENT>0000789019-25-000120-index.html : 20251212\n"
              "<SEC-HEADER>0000789019-25-000120.hdr.sgml : 20251212\n"
              "<ACCEPTANCE-DATETIME>20251212182017\n"
              "ACCESSION NUMBER:\t\t0000789019-25-000120\n"
              "CONFORMED SUBMISSION TYPE:\t4\n"
              "CONFORMED PERIOD OF REPORT:\t20250423\n"
              "FILED AS OF DATE:\t\t20251212\n")
    APOLOGY = "# This page is temporarily unavailable.\n\nSEC.gov is undergoing maintenance."

    def test_rendered_index_page(self) -> None:
        from sim.edgar_rendered import _index_facts
        self.assertEqual(_index_facts(self.RENDERED), {
            "filing_date": "2026-02-18", "accepted": "2026-02-18 18:14:22",
            "period_of_report": "2026-02-18"})

    def test_sgml_header_page(self) -> None:
        from sim.edgar_rendered import _index_facts
        self.assertEqual(_index_facts(self.HEADER), {
            "filing_date": "2025-12-12", "accepted": "2025-12-12 18:20:17",
            "period_of_report": "2025-04-23"})

    def test_apology_page_yields_no_facts(self) -> None:
        from sim.edgar_rendered import _index_facts
        self.assertEqual(_index_facts(self.APOLOGY), {
            "filing_date": None, "accepted": None, "period_of_report": None})
