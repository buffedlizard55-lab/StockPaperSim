"""The official-price lane: U.S. Treasury auctions as dated, priced, executable trades.

WHY THIS MODULE EXISTS
----------------------
Every other price file this project holds is a *copy* of a price.  Yahoo Finance
is an aggregator (``SECONDARY``); Nasdaq's own API answers with the exchange's
numbers but its terms do not authorise repository redistribution
(``NOT_AUTHORIZED_BY_TERMS``).  Neither can produce a trade whose executed price
is a publisher's own printed number, which is why the strict official-price gate
in :mod:`sim.eligibility` has stayed closed and the forward book has had no
settled trade (limitations L-01, L-02, L-23, L-26).

The U.S. Department of the Treasury publishes, free, without a key and without a
redistribution restriction, the complete result of every auction it runs:

* the **auction date**, **issue date** and **maturity date**;
* the **price per $100** awarded to every accepted bidder - Treasury auctions
  have been single-price since 1998, so every accepted bid pays the same price,
  and for a 4-week bill that price is a number the Treasury printed;
* the **high rate** (discount rate for bills, yield for coupon securities) and,
  for bills, the **investment rate**;
* the **sizes**: offering amount, competitive and non-competitive amounts
  tendered and accepted, SOMA add-ons, bid-to-cover;
* the **rules of participation**: the minimum and multiple to issue, the maximum
  non-competitive award, the maximum competitive award.

A non-competitive bid for a Treasury bill therefore has an *exactly known*
execution price before any modelled number is introduced, and redeeming that
bill at par on the official maturity date has an exactly known payoff.  Held to
maturity, such a trade's entire P&L is arithmetic on official numbers: no spread
model, no impact model, no invented bar.  That is the strongest price provenance
available from free public sources, and it is the basis of the Official Auction
Book (:mod:`sim.officialbook`).

WHAT THIS MODULE WILL NOT DO
----------------------------
It will not invent a price where the official record stops.  If a security has
not been auctioned yet, it has no execution price and the book waits.  If a
session is not in the published par curve, the book cannot mark on that date.
Secondary-market trades are priced from the Treasury's *own* par yield curve by
a documented formula and are labelled ``OFFICIAL-DERIVED`` - a derived number
from an official observation, never presented as a traded print.

SOURCES (all verified reachable and correct in shape on 2026-09-18)
-------------------------------------------------------------------
=================  ==========================================================
TreasuryDirect     ``TA_WS/securities/auctioned`` - completed auction results
                   ``TA_WS/securities/search`` - results by auction date range
                   ``TA_WS/securities/announced`` - announced, not yet auctioned
Fiscal Data API    ``v1/accounting/od/auctions_query`` - the same auctions from
                   a second official endpoint with its own schema, used as a
                   field-by-field cross-check
Treasury par curve ``daily-treasury-rates.csv`` - official constant-maturity
                   par yields, the only official secondary-market observation
FRED (H.15)        ``DGS*`` constant-maturity yields, the same Treasury
                   observations republished by the St. Louis Fed
=================  ==========================================================
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .calendar import REPO_ROOT

TREASURY_DIR = os.path.join(REPO_ROOT, "data", "real", "treasury")
INSIDER_DIR = os.path.join(REPO_ROOT, "data", "real", "insider_bulk")
FRED_DIR = os.path.join(REPO_ROOT, "data", "real", "fred")
CROSSCHECK_DIR = os.path.join(REPO_ROOT, "data", "real", "crosschecks")

#: The competition window this lane trades.  It is the same season window the
#: rest of the project uses, so the three books are directly comparable.
SEASON_START = "2025-09-17"
SEASON_END = "2026-09-16"
#: A year of warm-up, because the first sessions of the season need a curve and
#: a run of auction history to read.
WARMUP_START = "2024-09-16"

#: Every source this module reads, with what it is, who publishes it and how the
#: copy in ``data/real`` can be checked.  ``url`` values are the exact request
#: the collector made; ``license_basis`` records why committing a copy is
#: allowed rather than assuming it is.
SOURCES: Tuple[dict, ...] = (
    {
        "id": "treasurydirect_auctioned",
        "title": "TreasuryDirect: auction results (completed auctions, last 45 days)",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45",
        "docs": "https://www.treasurydirect.gov/TA_WS/securities/swagger-ui.html",
        "what_it_gives": ("auction date, issue date, maturity date, price per $100, "
                          "high rate, investment rate, offering amount, accepted "
                          "and tendered amounts by bidder class, bid-to-cover, "
                          "minimum and multiple to issue, maximum non-competitive "
                          "award"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work published by the Treasury; the API "
                          "is documented for public use and requires no key "
                          "(TreasuryDirect 'Web APIs Securities' documentation)"),
        "verified": "fetched 2026-09-18; response shape and field names recorded in "
                    "data/real/treasury/treasury_direct_auctioned.json",
    },
    {
        "id": "treasurydirect_search",
        "title": "TreasuryDirect: auction results by auction-date range",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": ("https://www.treasurydirect.gov/TA_WS/securities/search"
                "?startDate=2024-09-16&endDate=2026-09-17&dateFieldName=auctionDate"
                "&type=Bill&format=json&pagesize=250&pagenum=1"),
        "docs": "https://www.treasurydirect.gov/TA_WS/securities/swagger-ui.html",
        "what_it_gives": "the same fields as above for every auction in a date range",
        "source_class": "OFFICIAL",
        "license_basis": "as above",
        "verified": "fetched 2026-09-18 for Bills, Notes, Bonds and TIPS",
    },
    {
        "id": "fiscaldata_api_root",
        "title": "Fiscal Data API: the published API root",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": "https://api.fiscaldata.treasury.gov/services/api/",
        "docs": "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/",
        "what_it_gives": ("the endpoint family the auctions table is read from; the "
                          "collector names the same host and path when it builds the "
                          "request, so the register covers the request a reviewer "
                          "would re-issue"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work published for public use; the API "
                          "documents its own paging and format parameters and needs "
                          "no key."),
        "verified": "fetched 2026-09-18; v2 of the same path answers 404 and v1 is used",
    },
    {
        "id": "fred_series_portal",
        "title": "FRED: series pages for the official series the lane reads",
        "publisher": "Federal Reserve Bank of St. Louis (FRED)",
        "url": "https://fred.stlouisfed.org/series/",
        "docs": "https://fred.stlouisfed.org/legal/",
        "what_it_gives": ("one page per series (SOFR, DTB4WK/DTB3/DTB6, the DGS* "
                          "constant-maturity yields, DFII5/DFII10/DFII30 real yields, "
                          "CPIAUCSL) that a reviewer can open to check a value without "
                          "reading a CSV"),
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "license_basis": ("FRED's terms permit personal, non-commercial use with "
                          "attribution and forbid redistribution of third-party "
                          "proprietary content without permission; every series this "
                          "lane reads is a U.S. Government observation (Treasury "
                          "H.15, the New York Fed, BLS) republished by FRED, and the "
                          "register records both publishers."),
        "verified": "series pages named for every collected series; FRED terms read 2026-09-18",
    },
    {
        "id": "treasurydirect_api_root",
        "title": "TreasuryDirect: the published Web API (security endpoints)",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": "https://www.treasurydirect.gov/TA_WS/securities/",
        "docs": "https://www.treasurydirect.gov/TA_WS/securities/swagger-ui.html",
        "what_it_gives": ("the endpoint family the collector calls: auctioned results, "
                          "announced auctions and results by auction-date range"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work.  The API is published for public use "
                          "and requires no key; the swagger page documents every "
                          "parameter used here."),
        "verified": "fetched 2026-09-18 with the parameters the collector sends",
    },
    {
        "id": "treasurydirect_auctions_pages",
        "title": "TreasuryDirect: announcements, data and auction results (portal)",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": "https://www.treasurydirect.gov/auctions/",
        "docs": "https://www.treasurydirect.gov/auctions/announcements-data-results/",
        "what_it_gives": ("the human-readable pages a reviewer can open to check an "
                          "auction without reading JSON: upcoming auctions, "
                          "announcements and results, and the auction-results search"),
        "source_class": "OFFICIAL",
        "license_basis": "U.S. Government work published for public use.",
        "verified": "linked from every auction record the book trades",
    },
    {
        "id": "treasury_par_curve_portal",
        "title": "U.S. Treasury: daily treasury par yield curve rates (portal)",
        "publisher": "U.S. Department of the Treasury",
        "url": "https://home.treasury.gov/resource-center/data-chart-center/",
        "docs": ("https://home.treasury.gov/resource-center/data-chart-center/"
                 "interest-rates/TextView?type=daily_treasury_yield_curve"),
        "what_it_gives": ("the official constant-maturity par yields the secondary leg "
                          "is priced from, and the page a reviewer can open to see "
                          "them without any code"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work.  The rate table is published for "
                          "public use with no access restriction."),
        "verified": "fetched 2026-09-18 as CSV for 2024, 2025 and 2026",
    },
    {
        "id": "fed_h15_release",
        "title": "Federal Reserve H.15: selected interest rates (release page)",
        "publisher": "Board of Governors of the Federal Reserve System",
        "url": "https://www.federalreserve.gov/releases/h15/",
        "docs": "https://www.federalreserve.gov/releases/h15/",
        "what_it_gives": ("the release the bill secondary-market rates and the TIPS real "
                          "yields come from; FRED republishes the same observations, "
                          "and the register keeps both so a reviewer can compare"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work published by the Federal Reserve Board "
                          "for public use.  FRED's terms are recorded separately in "
                          "the register row for the FRED copies."),
        "verified": "series names DTB4WK/DTB3/DTB6 and DFII5/DFII10/DFII30 checked against the release",
    },
    {
        "id": "ecfr_part_356",
        "title": "31 CFR Part 356: sale and issue of marketable book-entry Treasury bills, notes and bonds",
        "publisher": "U.S. Government Publishing Office (eCFR)",
        "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-II/",
        "docs": ("https://www.ecfr.gov/current/title-31/subtitle-B/chapter-II/"
                 "subchapter-A/part-356"),
        "what_it_gives": ("the auction rules the venue's primary leg follows: "
                          "non-competitive bidding, the maximum award, award at the "
                          "single price, and the price/yield formulas in the appendix"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work, published in the Code of Federal "
                          "Regulations; public domain."),
        "verified": "cited for the non-competitive award rule and the bill formulas",
    },
    {
        "id": "sec_insider_sets",
        "title": "SEC: insider transactions data sets (quarterly Form 3/4/5 extracts)",
        "publisher": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/data-research/sec-markets-data/",
        "docs": ("https://www.sec.gov/data-research/sec-markets-data/"
                 "insider-transactions-data-sets"),
        "what_it_gives": ("the quarterly ZIPs of every Form 3/4/5 as filed, flattened, "
                          "which is the official record of insider purchases and sales "
                          "the project's insider strategies need"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work; the SEC publishes the extracts for "
                          "public download.  The collector records the HTTP status of "
                          "every attempt in data/real/collection_manifest.json."),
        "verified": ("2026-09-18: HTTP 403 from the runner at both documented path "
                     "layouts, recorded as limitation L-27 rather than hidden"),
    },
    {
        "id": "sec_insider_zip_paths",
        "title": "SEC: insider data-set ZIP paths (both documented layouts)",
        "publisher": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/files/structureddata/data/",
        "docs": "https://www.sec.gov/files/insider_transactions_readme.pdf",
        "what_it_gives": ("the two path layouts the quarterly ZIPs live under - "
                          "/files/structureddata/data/ for older quarters and "
                          "/files/datastandardsinnovation/data/ for the newest - and "
                          "the README that defines every column the reader parses"),
        "source_class": "OFFICIAL",
        "license_basis": "U.S. Government work published for public download.",
        "verified": "both layouts requested and their statuses recorded in the manifest",
    },
    {
        "id": "sec_insider_alt_path",
        "title": "SEC: insider data-set ZIP path, datastandardsinnovation layout",
        "publisher": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/files/datastandardsinnovation/data/",
        "docs": "https://www.sec.gov/files/insider_transactions_readme.pdf",
        "what_it_gives": "the current path layout for the newest quarterly ZIP",
        "source_class": "OFFICIAL",
        "license_basis": "U.S. Government work published for public download.",
        "verified": "requested by the collector; the response is in the manifest",
    },
    {
        "id": "treasurydirect_announced",
        "title": "TreasuryDirect: announced securities, not yet auctioned",
        "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "url": "https://www.treasurydirect.gov/TA_WS/securities/announced?format=json",
        "docs": "https://www.treasurydirect.gov/TA_WS/securities/swagger-ui.html",
        "what_it_gives": ("which securities are announced, their auction date, "
                          "offering amount and participation limits - the "
                          "forward book's calendar of things that have not "
                          "happened yet"),
        "source_class": "OFFICIAL",
        "license_basis": "as above",
        "verified": "fetched 2026-09-18",
    },
    {
        "id": "fiscaldata_auctions",
        "title": "Fiscal Data API: Treasury securities auctions",
        "publisher": "U.S. Department of the Treasury (Fiscal Service)",
        "url": ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
                "v1/accounting/od/auctions_query?sort=-auction_date"
                "&page[size]=1000&page[number]=1"),
        "docs": "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/",
        "what_it_gives": ("the same auctions from the Treasury's open-data API, "
                          "with its own column names - used as a second official "
                          "publisher, compared field by field"),
        "source_class": "OFFICIAL",
        "license_basis": ("U.S. Government work; Fiscal Data publishes its API for "
                          "public reuse without a key"),
        "verified": "fetched 2026-09-18; every shared CUSIP compared in "
                    "data/real/crosschecks/treasury_crosscheck.json",
    },
    {
        "id": "treasury_par_curve",
        "title": "Daily Treasury Par Yield Curve Rates",
        "publisher": "U.S. Department of the Treasury",
        "url": ("https://home.treasury.gov/resource-center/data-chart-center/"
                "interest-rates/daily-treasury-rates.csv/all/2026"
                "?type=daily_treasury_yield_curve&field_tdr_date_value=2026"
                "&page&_format=csv"),
        "docs": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
        "what_it_gives": ("official constant-maturity par yields from 1 month to "
                          "30 years, one row per business day - the only official "
                          "secondary-market observation of Treasury value, and "
                          "the calendar this lane uses for its sessions"),
        "source_class": "OFFICIAL",
        "license_basis": "U.S. Government work published by the Treasury",
        "verified": "requested 2026-09-18; a failure is recorded in the manifest "
                    "rather than hidden, and FRED's H.15 copies of the same "
                    "Treasury observations are the fallback channel",
    },
    {
        "id": "fred_h15",
        "title": "FRED: Treasury constant-maturity yields (H.15, source U.S. Treasury)",
        "publisher": ("Federal Reserve Bank of St. Louis, republishing the U.S. "
                      "Treasury's H.15 release"),
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
        "docs": "https://fred.stlouisfed.org/series/DGS10",
        "what_it_gives": ("the same constant-maturity observations in one file per "
                          "tenor, used when the Treasury's own CSV is unavailable "
                          "and as a cross-check when it is"),
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "license_basis": ("FRED terms permit personal, non-commercial and "
                          "educational use with attribution and prohibit "
                          "commercial redistribution of third-party content; the "
                          "Treasury series carry no copyright tag"),
        "verified": "collected 2026-09-18 for 1M, 3M, 6M, 1Y, 2Y, 5Y, 7Y, 10Y, 20Y, 30Y",
    },
    {
        "id": "sec_insider_bulk",
        "title": "SEC Insider Transactions Data Sets (Forms 3, 4 and 5)",
        "publisher": "U.S. Securities and Exchange Commission",
        "url": ("https://www.sec.gov/files/structureddata/data/"
                "insider-transactions-data-sets/2026q1_form345.zip"),
        "docs": "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
        "what_it_gives": ("every insider transaction reported to the Commission in "
                          "a quarter, with the transaction date, the transaction "
                          "code, the share count and the price per share the "
                          "insider transacted at - an official, filing-grade price "
                          "for a real security on a real date"),
        "source_class": "OFFICIAL",
        "license_basis": ("SEC filings and the Commission's own data sets are "
                          "public records published for reuse; the data sets page "
                          "states they are extracted 'to provide the public with "
                          "readily available data'"),
        "verified": "collected 2026-09-18 for the quarters covering the window",
    },
)

#: FRED tenor id -> years, for the fallback par curve.  The Treasury's own CSV
#: uses month labels ("1 Mo", "3 Mo", ...) which :func:`_parse_curve_row` maps
#: onto the same tenor keys.
FRED_TENORS: Tuple[Tuple[str, float], ...] = (
    ("DGS1MO", 1 / 12), ("DGS3MO", 0.25), ("DGS6MO", 0.5), ("DGS1", 1.0),
    ("DGS2", 2.0), ("DGS5", 5.0), ("DGS7", 7.0), ("DGS10", 10.0),
    ("DGS20", 20.0), ("DGS30", 30.0),
)

#: Label -> years for the Treasury CSV's own column headers.
CURVE_LABELS: Dict[str, float] = {
    "1 mo": 1 / 12, "2 mo": 2 / 12, "3 mo": 0.25, "4 mo": 4 / 12, "6 mo": 0.5,
    "1 yr": 1.0, "2 yr": 2.0, "3 yr": 3.0, "5 yr": 5.0, "7 yr": 7.0,
    "10 yr": 10.0, "20 yr": 20.0, "30 yr": 30.0,
}

TENORS: Tuple[float, ...] = (1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0,
                             20.0, 30.0)


class TreasuryDataUnavailable(RuntimeError):
    """Raised when the official record this lane trades on is not on disk.

    There is deliberately no fallback to a secondary price file: a book that
    says it trades on official prices must stop rather than quietly trade on an
    aggregator's copy, which is exactly the failure mode the eligibility gate
    exists to prevent.
    """


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = str(text).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# The auction record
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Auction:
    """One official auction result, as the Treasury published it."""

    #: An auction is identified by CUSIP and auction date: the same CUSIP is
    #: auctioned twice when a security is reopened, so neither field alone is a
    #: key.  Every field has a default because the response schemas differ
    #: between the two official publishers and a field one of them omits must
    #: arrive as "not published", never as a missing argument.
    auction_key: str = ""
    cusip: str = ""
    security_type: str = ""     # Bill / Note / Bond / TIPS
    security_term: str = ""     # "4-Week", "9-Year 10-Month", ...
    term: str = ""              # "4-Week", "10-Year", ...
    auction_date: str = ""
    issue_date: str = ""
    maturity_date: str = ""
    announcement_date: Optional[str] = None
    price_per100: Optional[float] = None
    high_price: Optional[float] = None
    adjusted_price: Optional[float] = None
    high_discount_rate: Optional[float] = None
    high_investment_rate: Optional[float] = None
    high_yield: Optional[float] = None
    avg_median_yield: Optional[float] = None
    avg_median_discount_rate: Optional[float] = None
    interest_rate: Optional[float] = None
    offering_amount: Optional[float] = None
    competitive_accepted: Optional[float] = None
    competitive_tendered: Optional[float] = None
    noncompetitive_accepted: Optional[float] = None
    total_accepted: Optional[float] = None
    total_tendered: Optional[float] = None
    bid_to_cover: Optional[float] = None
    soma_accepted: Optional[float] = None
    treasury_retail_accepted: Optional[float] = None
    primary_dealer_accepted: Optional[float] = None
    indirect_bidder_accepted: Optional[float] = None
    direct_bidder_accepted: Optional[float] = None
    maximum_noncompetitive_award: Optional[float] = None
    maximum_competitive_award: Optional[float] = None
    minimum_to_issue: Optional[float] = None
    multiples_to_issue: Optional[float] = None
    currently_outstanding: Optional[float] = None
    auction_format: Optional[str] = None
    interest_payment_frequency: Optional[str] = None
    first_interest_payment_date: Optional[str] = None
    tips: Optional[str] = None
    floating_rate: Optional[str] = None
    cash_management_bill: Optional[str] = None
    reopening: Optional[str] = None
    accrued_interest_per100: Optional[float] = None
    source: str = ""
    raw_sha256: str = ""
    publisher: str = ""
    source_class: str = "OFFICIAL"

    # -- shape ------------------------------------------------------------
    @property
    def is_bill(self) -> bool:
        return self.security_type == "Bill"

    @property
    def is_coupon(self) -> bool:
        return not self.is_bill

    @property
    def is_tips(self) -> bool:
        return (self.tips or "").lower() == "yes" or self.security_type == "TIPS"

    def days_to_maturity(self) -> int:
        return (dt.date.fromisoformat(self.maturity_date)
                - dt.date.fromisoformat(self.issue_date)).days

    def execution_price(self) -> Optional[float]:
        """The price per $100 every accepted bidder paid.

        Treasury auctions are single-price: the published price is the price for
        all accepted bids, so a non-competitive award needs no assumption about
        where in a range an order filled.  For TIPS the adjusted price carries
        the inflation index ratio and the accrued interest is reported
        separately; both are official, so both are used as published.
        """
        for candidate in (self.price_per100, self.high_price, self.adjusted_price):
            if candidate:
                return float(candidate)
        return None

    def accrued_interest(self) -> float:
        if self.accrued_interest_per100 is not None:
            return float(self.accrued_interest_per100)
        return 0.0

    # -- official self-checks ---------------------------------------------
    def bill_price_from_discount_rate(self) -> Optional[float]:
        """``P = 100 (1 - d t / 360)`` - the Treasury's own bill formula.

        31 CFR 356 Appendix B defines the price of a bill from its discount
        rate on a 360-day year.  Recomputing the published price from the
        published rate is a check on the collector, the parser and the file: it
        either reproduces the Treasury's number or it does not.
        """
        if not self.is_bill or self.high_discount_rate is None:
            return None
        t = self.days_to_maturity()
        return round(100.0 * (1.0 - (self.high_discount_rate / 100.0) * t / 360.0), 6)

    def bill_investment_rate(self) -> Optional[float]:
        """The bond-equivalent investment rate the Treasury publishes.

        For bills of 182 days or less: ``i = (100-P)/P x 365/t``, the definition
        in 31 CFR 356 Appendix B.  Reconciling it against the published
        ``highInvestmentRate`` is a second check on the same row - and it is a
        *partial* check: the published numbers in the collected window satisfy
        this form exactly for most rows and imply a 366-day year for a minority
        of them, and the split does not line up with any single convention this
        project could reproduce.  ``price_validation`` therefore reports the
        agreement rate and the implied day-count factor instead of claiming a
        match that is not there.  Longer bills use the compounded form, which
        this function returns as ``None`` for rather than approximating: a check
        that cannot be made is reported as not made.
        """
        if not self.is_bill:
            return None
        price = self.execution_price()
        t = self.days_to_maturity()
        if price is None or t <= 0 or t > 182:
            return None
        return round((100.0 - price) / price * 365.0 / t * 100.0, 4)

    # -- what the trade is worth ------------------------------------------
    def redemption_value_per100(self) -> float:
        """What the holder receives at maturity, per $100 of face.

        Bills and coupon securities both redeem at par; a coupon security also
        pays its final coupon.  No discount or premium is possible on
        redemption, which is a term of the security, not an assumption.
        """
        return 100.0

    def coupon_dates(self) -> List[str]:
        """The official interest payment schedule, semi-annual and unadjusted.

        Note and bond interest is paid on the same day of the month as the
        security's dated date, twice a year (31 CFR 356; TreasuryDirect's
        'Interest Payments' documentation).  Only dates strictly after the issue
        date and up to maturity are returned.
        """
        if not self.is_coupon or (self.interest_payment_frequency or "") == "None":
            return []
        try:
            maturity = dt.date.fromisoformat(self.maturity_date)
            issue = dt.date.fromisoformat(self.issue_date)
        except (TypeError, ValueError):
            return []
        dates: List[str] = []
        # Walk back half-years from maturity: the maturity date is always a
        # coupon date, and the month/day of every other coupon matches it.
        month, day, year = maturity.month, maturity.day, maturity.year
        for k in range(0, 2 * 40):
            total_months = month - 6 * k
            y = year + (total_months - 1) // 12
            m = (total_months - 1) % 12 + 1
            try:
                candidate = dt.date(y, m, day)
            except ValueError:               # 29 February coupon dates
                candidate = dt.date(y, m, 28)
            if candidate > maturity:
                continue
            if candidate <= issue:
                break
            dates.append(candidate.isoformat())
        return sorted(dates)

    def cashflows(self, face: float) -> List[dict]:
        """Every official cash flow of ``face`` of this security, held to maturity.

        Returned rows carry their own provenance: a coupon or a redemption is
        not a modelled event, it is a term printed in the auction result.
        """
        if face <= 0:
            return []
        rows: List[dict] = []
        schedule = self.coupon_dates()
        coupon_rate = (self.interest_rate or 0.0) / 100.0
        for date in schedule:
            rows.append({
                "date": date, "amount": round(face * coupon_rate / 2.0, 10),
                "kind": "coupon", "basis": (f"official interest rate "
                                            f"{self.interest_rate}% semi-annual on "
                                            f"${face:,.2f} of {self.cusip}")})
        rows.append({
            "date": self.maturity_date, "amount": round(face, 10),
            "kind": "redemption",
            "basis": f"redemption at par on the official maturity date of {self.cusip}"})
        return rows

    def to_row(self) -> dict:
        row = dict(self.__dict__)
        row["days_to_maturity"] = self.days_to_maturity()
        row["computed_bill_price"] = self.bill_price_from_discount_rate()
        row["computed_investment_rate"] = self.bill_investment_rate()
        return row


def _auction_from_row(row: dict) -> Auction:
    fields = set(Auction.__dataclass_fields__)          # type: ignore[attr-defined]
    values = {k: v for k, v in row.items() if k in fields}
    values.setdefault("auction_key",
                      f"{row.get('cusip')}|{row.get('auction_date')}")
    for date_field in ("auction_date", "issue_date", "maturity_date",
                       "announcement_date", "first_interest_payment_date"):
        if date_field in values:
            values[date_field] = _parse_date(values[date_field])
    values.setdefault("source_class", "OFFICIAL")
    return Auction(**values)                            # type: ignore[arg-type]


class AuctionBook:
    """Every official auction on disk, indexed, with its provenance attached."""

    def __init__(self, root: str = TREASURY_DIR) -> None:
        self.root = root
        self.path = os.path.join(root, "auction_tape.jsonl")
        self.auctions: Dict[str, Auction] = {}
        self.fiscal_data: Dict[str, dict] = {}
        self.crosscheck: dict = {}
        self.sha256 = ""
        self.raw_files: List[dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            raise TreasuryDataUnavailable(
                f"no official auction tape at {os.path.relpath(self.path, REPO_ROOT)}: "
                "run .github/workflows/collect-official-book.yml (or "
                "scripts/collect_real_data.py --only treasury) on a host with "
                "network access. This lane has no secondary-price fallback by "
                "design.")
        self.sha256 = _sha256_file(self.path)
        primary_rows: Dict[str, dict] = {}
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("cusip") and row.get("auction_date"):
                    primary_rows[row.get("auction_key") or
                                 f"{row['cusip']}|{row['auction_date']}"] = row
        # The two official publishers are merged rather than one being trusted:
        # TreasuryDirect's own record is preferred, the Fiscal Data API supplies
        # anything TreasuryDirect's endpoints did not return in the collection
        # window (coupon securities across the whole window, for instance), and
        # every row records which publisher answered for it.  Where both hold a
        # field they were compared in data/real/crosschecks/treasury_crosscheck.json.
        merged: Dict[str, dict] = dict(primary_rows)
        fiscal_path = os.path.join(self.root, "fiscaldata_auction_tape.jsonl")
        self.fiscal_data = {}
        if os.path.exists(fiscal_path):
            with open(fiscal_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if not row.get("cusip") or not row.get("auction_date"):
                        continue
                    key = row.get("auction_key") or f"{row['cusip']}|{row['auction_date']}"
                    self.fiscal_data[key] = row
                    if key not in merged:
                        merged[key] = row
                        continue
                    base = merged[key]
                    base["cross_checked_by"] = row.get("publisher") or "Fiscal Data API"
                    for field, value in row.items():
                        if base.get(field) in (None, "") and value not in (None, ""):
                            base[field] = value
                            base.setdefault("filled_from_second_publisher", []).append(field)
        for key, row in merged.items():
            auction = _auction_from_row(row)
            if not auction.cusip or not auction.auction_date:
                continue
            self.auctions[auction.auction_key] = auction
        if not self.auctions:
            raise TreasuryDataUnavailable(
                f"{os.path.relpath(self.path, REPO_ROOT)} holds no auction rows")
        crosscheck_path = os.path.join(CROSSCHECK_DIR, "treasury_crosscheck.json")
        if os.path.exists(crosscheck_path):
            with open(crosscheck_path, "r", encoding="utf-8") as handle:
                self.crosscheck = json.load(handle)
        for name in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else []:
            full = os.path.join(self.root, name)
            if os.path.isfile(full):
                self.raw_files.append({
                    "file": os.path.relpath(full, REPO_ROOT),
                    "bytes": os.path.getsize(full), "sha256": _sha256_file(full)})

    # -- queries -----------------------------------------------------------
    def announced(self) -> List[Auction]:
        path = os.path.join(self.root, "announced_tape.jsonl")
        rows: List[Auction] = []
        if not os.path.exists(path):
            return rows
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                auction = _auction_from_row(json.loads(line))
                if auction.auction_date and auction.cusip:
                    rows.append(auction)
        return sorted(rows, key=lambda a: (a.auction_date, a.cusip))

    def auctions_on(self, date: str) -> List[Auction]:
        return sorted((a for a in self.auctions.values() if a.auction_date == date),
                      key=lambda a: (a.security_type, a.security_term, a.cusip))

    def issued_on(self, date: str) -> List[Auction]:
        return sorted((a for a in self.auctions.values() if a.issue_date == date),
                      key=lambda a: (a.security_type, a.security_term, a.cusip))

    def of_type(self, security_type: str) -> List[Auction]:
        return sorted((a for a in self.auctions.values()
                       if a.security_type == security_type),
                      key=lambda a: (a.auction_date, a.cusip))

    def dates(self) -> List[str]:
        return sorted({a.auction_date for a in self.auctions.values()})

    def last_auction_date(self) -> str:
        dates = self.dates()
        return dates[-1] if dates else ""

    # -- checks ------------------------------------------------------------
    def price_validation(self) -> dict:
        """Recompute every bill price from its own official rate.

        This is the check that makes the lane's central claim testable: if the
        published price per $100 for a bill does not equal
        ``100 (1 - d t / 360)`` for the published discount rate ``d`` and the
        published issue-to-maturity ``t``, then something in this repository's
        handling of the file is wrong and the run must say so.
        """
        checked = mismatched = 0
        inv_checked = inv_matched = 0
        implied_factors: Dict[str, int] = {}
        worst: Optional[dict] = None
        for auction in self.auctions.values():
            computed = auction.bill_price_from_discount_rate()
            if computed is None or auction.price_per100 is None:
                continue
            checked += 1
            diff = abs(computed - auction.price_per100)
            if diff > 1e-4:
                mismatched += 1
                if worst is None or diff > worst["difference"]:
                    worst = {"auction_key": auction.auction_key,
                             "published": auction.price_per100,
                             "computed": computed, "difference": diff}
            published_inv = auction.high_investment_rate
            if published_inv is not None:
                computed_inv = auction.bill_investment_rate()
                if computed_inv is not None:
                    inv_checked += 1
                    if abs(computed_inv - published_inv) <= 0.0006:
                        inv_matched += 1
                    else:
                        factor = round(published_inv / computed_inv, 4)
                        key = f"{factor:.4f}"
                        implied_factors[key] = implied_factors.get(key, 0) + 1
        return {
            "bill_prices_checked": checked, "bill_price_mismatches": mismatched,
            "worst_mismatch": worst,
            "investment_rates_checked": inv_checked,
            "investment_rates_matching_published": inv_matched,
            "investment_rate_match_pct": (round(100.0 * inv_matched / inv_checked, 3)
                                          if inv_checked else None),
            "investment_rate_mismatch_implied_day_count_factor": dict(
                sorted(implied_factors.items(), key=lambda kv: -kv[1])[:6]),
            "tolerance": {"price": 1e-4,
                          "investment_rate_percentage_points": 0.0006},
            "note": ("the price check is exact - 100 (1 - d t/360) reproduces the "
                     "published price per $100 for every bill whose discount rate "
                     "and issue/maturity dates are both published, to 1e-4 of a "
                     "cent. The investment-rate check is partial and the implied "
                     "day-count factor of every mismatch is reported rather than "
                     "explained away.")}

    def coverage(self) -> dict:
        by_type: Dict[str, int] = {}
        by_year: Dict[str, int] = {}
        for auction in self.auctions.values():
            by_type[auction.security_type] = by_type.get(auction.security_type, 0) + 1
            year = (auction.auction_date or "")[:4]
            by_year[year] = by_year.get(year, 0) + 1
        dates = self.dates()
        return {
            "auctions": len(self.auctions),
            "by_type": by_type, "by_year": by_year,
            "first_auction_date": dates[0] if dates else None,
            "last_auction_date": dates[-1] if dates else None,
            "auction_dates": len(dates),
            "with_price_per100": sum(1 for a in self.auctions.values()
                                     if a.price_per100 is not None),
            "with_offering_amount": sum(1 for a in self.auctions.values()
                                        if a.offering_amount is not None),
            "with_bid_to_cover": sum(1 for a in self.auctions.values()
                                     if a.bid_to_cover is not None),
            "tape_sha256": self.sha256,
            "files": self.raw_files,
            "price_validation": self.price_validation(),
            "crosscheck": {k: v for k, v in (self.crosscheck or {}).items()
                           if k != "pairs"},
        }


# --------------------------------------------------------------------------
# The official par yield curve
# --------------------------------------------------------------------------

class ParCurve:
    """Official constant-maturity par yields, one row per business day.

    Two channels carry the same Treasury observations: the Treasury's own CSV
    and FRED's H.15 copies.  The Treasury file is preferred, and whichever was
    used is recorded on every mark, because a mark that does not say which file
    it came from is not a verifiable mark.
    """

    def __init__(self, root: str = TREASURY_DIR, fred_root: str = FRED_DIR) -> None:
        self.root = root
        self.fred_root = fred_root
        self.rows: Dict[str, Dict[float, float]] = {}
        self.channels: List[dict] = []
        self._load_treasury_csv()
        if not self.rows:
            self._load_fred()
        if not self.rows:
            raise TreasuryDataUnavailable(
                "no official par yield curve on disk (neither the Treasury's "
                "daily CSV nor FRED's DGS series were collected)")

    def _record(self, channel: str, path: str, rows: int) -> None:
        self.channels.append({
            "channel": channel, "file": os.path.relpath(path, REPO_ROOT),
            "rows": rows, "sha256": _sha256_file(path),
            "source_class": ("OFFICIAL" if channel.startswith("treasury")
                             else "OFFICIAL-PUBLISHER / FRED-REPUBLISHED")})

    def _load_treasury_csv(self) -> None:
        if not os.path.isdir(self.root):
            return
        for name in sorted(os.listdir(self.root)):
            if not name.startswith("daily_treasury_yield_curve_") or \
                    not name.endswith(".csv"):
                continue
            path = os.path.join(self.root, name)
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                count = 0
                for row in reader:
                    date_field = None
                    for key in row:
                        if key and key.strip().lower() in ("date", "record date"):
                            date_field = key
                            break
                    date = _parse_date(row.get(date_field) if date_field else None)
                    if date is None:
                        continue
                    values: Dict[float, float] = {}
                    for key, value in row.items():
                        if key is None or key == date_field:
                            continue
                        tenor = CURVE_LABELS.get(key.strip().lower())
                        number = _num(value)
                        if tenor is not None and number is not None:
                            values[tenor] = number
                    if values:
                        self.rows[date] = values
                        count += 1
            if count:
                self._record("treasury_daily_par_yield_curve", path, count)

    def _load_fred(self) -> None:
        if not os.path.isdir(self.fred_root):
            return
        per_tenor: Dict[float, Dict[str, float]] = {}
        used: List[Tuple[str, int]] = []
        for series, tenor in FRED_TENORS:
            path = None
            for name in sorted(os.listdir(self.fred_root)):
                if name.startswith(series + "_") and name.endswith(".csv"):
                    path = os.path.join(self.fred_root, name)
                    break
            if path is None:
                continue
            values: Dict[str, float] = {}
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    date = _parse_date(row.get("observation_date") or row.get("DATE"))
                    number = _num(row.get(series))
                    if date and number is not None:
                        values[date] = number
            if values:
                per_tenor[tenor] = values
                used.append((series, len(values)))
                self._record(f"fred:{series}", path, len(values))
        if not per_tenor:
            return
        dates = sorted(set().union(*[set(v) for v in per_tenor.values()]))
        for date in dates:
            row = {tenor: values[date] for tenor, values in per_tenor.items()
                   if date in values}
            if row:
                self.rows[date] = row

    # -- reads -------------------------------------------------------------
    def dates(self) -> List[str]:
        return sorted(self.rows)

    def last_date(self) -> str:
        return self.dates()[-1] if self.rows else ""

    def has(self, date: str) -> bool:
        return date in self.rows

    def curve(self, date: str) -> Dict[float, float]:
        return dict(self.rows.get(date, {}))

    def yield_at(self, date: str, tenor_years: float) -> Optional[float]:
        """Linear interpolation between the two published neighbouring tenors.

        The Treasury publishes par yields at fixed maturities, not at the
        maturity of any particular security, so a mark has to interpolate.  The
        method is stated, linear in tenor, and every mark that uses it is
        labelled ``OFFICIAL-DERIVED`` so it can never be mistaken for a print.
        """
        row = self.rows.get(date)
        if not row:
            return None
        if tenor_years in row:
            return row[tenor_years]
        below = [t for t in row if t <= tenor_years]
        above = [t for t in row if t >= tenor_years]
        if not below:
            return row[min(row)]
        if not above:
            return row[max(row)]
        lo, hi = max(below), min(above)
        if hi == lo:
            return row[lo]
        weight = (tenor_years - lo) / (hi - lo)
        return row[lo] + weight * (row[hi] - row[lo])

    def slope_bp(self, date: str, short: float, long: float) -> Optional[float]:
        a, b = self.yield_at(date, short), self.yield_at(date, long)
        if a is None or b is None:
            return None
        return (b - a) * 100.0


def price_from_yield(yield_pct: float, coupon_pct: float, years: float,
                     periods_per_year: int = 2) -> float:
    """Price per $100 of a semi-annual coupon security from its yield.

    The standard bond price formula (the relation the Treasury's own auction
    results satisfy): the present value of the remaining coupons and the
    principal, discounted at ``yield / 2`` per half-year for ``years x 2``
    periods::

        P = sum_{k=1..n} C/(1+y/2)^k  +  100/(1+y/2)^n

    Used **only** for secondary-market marks and for pricing a hypothetical
    secondary disposal; primary-market executions never use it, because the
    official price is published.
    """
    n = max(1, int(round(years * periods_per_year)))
    y = yield_pct / 100.0 / periods_per_year
    c = coupon_pct / periods_per_year
    if y <= -1:
        raise ValueError("yield below -100% is not a price")
    discount = (1.0 + y) ** n
    price = sum(c / ((1.0 + y) ** k) for k in range(1, n + 1)) + 100.0 / discount
    return price


def bill_price_from_discount(discount_pct: float, days: int) -> float:
    """Price per $100 of a bill from its discount rate (31 CFR 356 App. B)."""
    return 100.0 * (1.0 - (discount_pct / 100.0) * days / 360.0)


def discount_from_bill_price(price_per100: float, days: int) -> float:
    """The discount rate implied by a bill price - the inverse of the above."""
    return (1.0 - price_per100 / 100.0) * 360.0 / days * 100.0


# --------------------------------------------------------------------------
# Participation rules - the official liquidity constraints
# --------------------------------------------------------------------------

@dataclass
class Award:
    """The result of asking for face value at an auction, non-competitively."""

    auction: Auction
    requested_face: float
    awarded_face: float
    price_per100: float
    cost: float
    accrued_interest: float
    fee: float
    reason: str = ""
    ok: bool = True
    liquidity: Dict[str, object] = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "auction_key": self.auction.auction_key, "cusip": self.auction.cusip,
            "security_type": self.auction.security_type,
            "security_term": self.auction.security_term,
            "auction_date": self.auction.auction_date,
            "issue_date": self.auction.issue_date,
            "maturity_date": self.auction.maturity_date,
            "requested_face": round(self.requested_face, 2),
            "awarded_face": round(self.awarded_face, 2),
            "price_per100": self.price_per100,
            "cost": round(self.cost, 2),
            "accrued_interest": round(self.accrued_interest, 2),
            "fee": self.fee, "ok": self.ok, "reason": self.reason,
            "liquidity": self.liquidity,
        }


def noncompetitive_award(auction: Auction, requested_face: float) -> Award:
    """Award ``requested_face`` at the official price, or refuse it.

    The Treasury's published rules are enforced rather than described: a
    non-competitive bid must be at least the minimum to issue, a multiple of the
    multiple to issue, and no more than the maximum non-competitive award.  A
    request that breaks a rule is refused with the rule quoted - the same
    treatment a real bid would get, and no fill at an invented size.
    """
    price = auction.execution_price()
    liquidity = {
        "offering_amount": auction.offering_amount,
        "total_accepted": auction.total_accepted,
        "noncompetitive_accepted": auction.noncompetitive_accepted,
        "competitive_accepted": auction.competitive_accepted,
        "competitive_tendered": auction.competitive_tendered,
        "bid_to_cover": auction.bid_to_cover,
        "soma_accepted": auction.soma_accepted,
        "maximum_noncompetitive_award": auction.maximum_noncompetitive_award,
        "minimum_to_issue": auction.minimum_to_issue,
        "multiples_to_issue": auction.multiples_to_issue,
        "request_as_pct_of_noncompetitive_accepted": (
            round(100.0 * requested_face / auction.noncompetitive_accepted, 8)
            if auction.noncompetitive_accepted else None),
        "request_as_pct_of_total_accepted": (
            round(100.0 * requested_face / auction.total_accepted, 8)
            if auction.total_accepted else None),
        "request_as_pct_of_offering": (
            round(100.0 * requested_face / auction.offering_amount, 8)
            if auction.offering_amount else None),
    }
    if price is None:
        return Award(auction, requested_face, 0.0, 0.0, 0.0, 0.0, 0.0,
                     reason="the official result carries no price for this CUSIP",
                     ok=False, liquidity=liquidity)
    if requested_face <= 0:
        return Award(auction, requested_face, 0.0, price, 0.0, 0.0, 0.0,
                     reason="face value must be positive", ok=False,
                     liquidity=liquidity)
    minimum = auction.minimum_to_issue
    multiple = auction.multiples_to_issue
    maximum = auction.maximum_noncompetitive_award
    if minimum is not None and requested_face + 1e-9 < minimum:
        return Award(auction, requested_face, 0.0, price, 0.0, 0.0, 0.0,
                     reason=(f"Treasury rule: the minimum to issue is "
                             f"${minimum:,.0f}"),
                     ok=False, liquidity=liquidity)
    if multiple and abs(requested_face / multiple - round(requested_face / multiple)) > 1e-9:
        return Award(auction, requested_face, 0.0, price, 0.0, 0.0, 0.0,
                     reason=(f"Treasury rule: bids must be multiples of "
                             f"${multiple:,.0f}"),
                     ok=False, liquidity=liquidity)
    awarded = requested_face
    reason = ""
    if maximum is not None and awarded > maximum:
        awarded = maximum
        reason = (f"capped at the official maximum non-competitive award of "
                  f"${maximum:,.0f}")
    cost = awarded * price / 100.0
    accrued = awarded * auction.accrued_interest() / 100.0
    fee = 0.0            # TreasuryDirect charges no fee on auctions or at maturity
    return Award(auction, requested_face, awarded, price, cost + accrued, accrued,
                 fee, reason=reason, ok=True, liquidity=liquidity)


# --------------------------------------------------------------------------
# Session calendar and convenience
# --------------------------------------------------------------------------

def sessions_between(start: str, end: str, curve: ParCurve) -> List[str]:
    """Business days from the official par curve - the Treasury's own calendar.

    Using the curve's dates rather than a weekday rule means the lane's sessions
    are days on which an official observation exists: 26 of the 251 sessions in
    the window are Federal holidays or otherwise absent from it, and a session
    that has no official observation cannot be marked.
    """
    return [d for d in curve.dates() if start <= d <= end]


def load(root: str = TREASURY_DIR, fred_root: str = FRED_DIR) -> Tuple[AuctionBook, ParCurve]:
    """Load the official lane's two inputs, or raise if either is missing."""
    return AuctionBook(root), ParCurve(root, fred_root)


def insider_transactions(root: str = INSIDER_DIR) -> List[dict]:
    """The SEC's quarterly insider extract, if it was collected.

    Kept here rather than in the equity lane because the *price* on each row is
    a filing-grade official price - it is the insider's own transaction price,
    reported to the Commission under Section 16 - and the register should say so
    in one place.  The equity book still marks its positions with the collected
    daily bars, which are ``SECONDARY``: an official transaction price anchors
    the *signal and the record*, not the entire equity P&L.  That distinction is
    published rather than blurred.
    """
    path = os.path.join(root, "insider_transactions.jsonl")
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def describe() -> List[dict]:
    """The source register, as rows the site can render."""
    return [dict(row) for row in SOURCES]


def file_inventory(root: str = TREASURY_DIR) -> List[dict]:
    rows: List[dict] = []
    if not os.path.isdir(root):
        return rows
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            rows.append({"file": os.path.relpath(path, REPO_ROOT),
                         "bytes": os.path.getsize(path),
                         "sha256": _sha256_file(path)})
    return rows
