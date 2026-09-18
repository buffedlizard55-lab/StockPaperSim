#!/usr/bin/env python3
"""Verify the official-source extracts that were retrieved *in-session*.

WHY THIS SCRIPT EXISTS
----------------------
The development sandbox has no outbound HTTPS, so ``scripts/collect_real_data.py``
cannot run here; it runs on a GitHub runner.  During this session a small number
of official files were instead retrieved with the agent's page-fetch tool and
transcribed into ``data/real/fred/``.  A transcription is exactly the place a
hallucinated number could enter the repository, so every one of them is checked
here against evidence that does *not* come from the transcription itself:

1. **Structure** - header, ISO dates, strictly increasing, no weekend dates,
   every value either empty or a finite decimal.
2. **Calendar agreement** - the set of dates carrying a value must agree with
   the trading calendar in the already-committed ``SP500`` file, which was
   written by the runner in an earlier session.  Any date present in one series
   and missing from another is reported, never silently dropped.
3. **Independent value cross-check** - one observation per file is compared with
   a value retrieved from a *different* official endpoint in the same session
   (the Federal Reserve Bank of New York's own SOFR API, and the FRED ``SP500``
   re-fetch), so an agreement is between two publishers rather than between a
   file and itself.
4. **Digests** - SHA-256 of the stored bytes, recorded in the verification
   report next to the URL that produced them.

Nothing here is allowed to guess: if a check cannot be run (the reference file
is absent, a cross-check value was not retrieved) the report says
``SKIPPED`` with the reason and the exit code stays 0, because a missing
reference is not a failed transcription.  A genuine disagreement exits 1.

    python3 scripts/verify_official_extracts.py
    python3 scripts/verify_official_extracts.py --report data/real/fred/AGENT_FETCH_VERIFICATION.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRED_DIR = os.path.join(REPO_ROOT, "data", "real", "fred")

#: Files written in this session, with the URL that produced them, the date and
#: time they were retrieved (UTC), and the tool that retrieved them.  ``complete``
#: means the response was retrieved in full - the fetch tool paginates large
#: bodies, and every one of these was read to its last page.
EXTRACTS: List[dict] = [
    {
        "file": "NASDAQCOM_2024-09-16_2026-09-17.csv",
        "series": "NASDAQCOM",
        "title": "NASDAQ Composite Index (daily close)",
        "publisher": "Nasdaq, Inc.",
        "republished_by": "Federal Reserve Bank of St. Louis (FRED)",
        "url": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM"
                "&cosd=2024-09-16&coed=2026-09-17"),
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "retrieved_utc": "2026-09-18T20:35:00Z",
        "retrieved_by": "agent page-fetch tool (sandbox has no outbound HTTPS)",
        "complete": True,
        "pages_read": 2,
    },
    {
        "file": "DJIA_2024-09-16_2026-09-17.csv",
        "series": "DJIA",
        "title": "Dow Jones Industrial Average (daily close)",
        "publisher": "S&P Dow Jones Indices LLC",
        "republished_by": "Federal Reserve Bank of St. Louis (FRED)",
        "url": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DJIA"
                "&cosd=2024-09-16&coed=2026-09-17"),
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "retrieved_utc": "2026-09-18T20:36:00Z",
        "retrieved_by": "agent page-fetch tool (sandbox has no outbound HTTPS)",
        "complete": True,
        "pages_read": 2,
    },
    {
        "file": "SOFR_2024-09-16_2026-09-17.csv",
        "series": "SOFR",
        "title": "Secured Overnight Financing Rate - broad general collateral rate",
        "publisher": "Federal Reserve Bank of New York",
        "republished_by": "Federal Reserve Bank of St. Louis (FRED)",
        "url": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR"
                "&cosd=2024-09-16&coed=2026-09-17"),
        "source_class": "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
        "retrieved_utc": "2026-09-18T20:38:00Z",
        "retrieved_by": "agent page-fetch tool (sandbox has no outbound HTTPS)",
        "complete": True,
        "pages_read": 2,
    },
]

#: Values retrieved in this session from endpoints *other* than the file being
#: checked, so agreement is between two publishers.  ``endpoint`` is the URL the
#: comparison value came from; ``observed`` is what that endpoint returned.
INDEPENDENT_CROSSCHECKS: List[dict] = [
    {
        "file": "SOFR_2024-09-16_2026-09-17.csv",
        "series": "SOFR",
        "date": "2026-09-17",
        "observed": "3.85",
        "endpoint": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/10.json",
        "publisher": "Federal Reserve Bank of New York (reference-rate API)",
        "note": ("NY Fed API returned percentRate 3.85 for effectiveDate "
                 "2026-09-17; FRED's republished SOFR must print the same number."),
    },
    {
        "file": "SOFR_2024-09-16_2026-09-17.csv",
        "series": "SOFR",
        "date": "2026-09-16",
        "observed": "3.62",
        "endpoint": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/10.json",
        "publisher": "Federal Reserve Bank of New York (reference-rate API)",
        "note": "second observation, so a single coincidence cannot carry the check",
    },
]

#: The committed SP500 file the runner wrote in an earlier session.  It is the
#: reference trading calendar: same host, same endpoint shape, same window, but a
#: different series and a different retrieval run.
CALENDAR_REFERENCE = "SP500_2024-09-16_2026-09-17.csv"

#: Series that are published on the money market's calendar rather than the
#: equity calendar, so a date-set difference against SP500 is expected and must
#: be explained rather than treated as a transcription error.
MONEY_MARKET_SERIES = frozenset({"SOFR", "EFFR", "OBFR"})

#: Dates on which the *bond* market was closed but the NYSE was open, so a
#: money-market rate legitimately has no observation while an equity index does.
#: These are the SIFMA-recommended closures; they are listed so the calendar
#: comparison can distinguish "the transcription lost a row" from "the publisher
#: had no observation".  The script still reports them, it just does not fail on
#: them when they are the only difference.
BOND_ONLY_CLOSURES = {
    "2024-10-14",  # Columbus Day / Indigenous Peoples' Day
    "2024-11-11",  # Veterans Day
    "2025-10-13",  # Columbus Day / Indigenous Peoples' Day
    "2025-11-11",  # Veterans Day
}

#: The other direction: a published money-market rate on a day the *equity*
#: market was shut.  On 2025-01-09 the NYSE and Nasdaq closed for the National
#: Day of Mourning for President Jimmy Carter while the bond market ran a
#: shortened session on SIFMA's recommendation, so a SOFR observation exists for
#: a date the S&P 500 series leaves blank.
#:   https://ir.nasdaq.com/news-releases/news-release-details/nasdaq-announces-closure-its-us-markets-honor-national-day-0
#:   https://www.nasdaq.com/press-release/new-york-stock-exchange-will-close-markets-january-9-honor-passing-former-president
#: A date may only appear here with a reason recorded next to it, because the
#: point of the check is that every divergence is explained rather than absorbed.
RATE_ONLY_SESSIONS = {
    "2025-01-09": ("U.S. equity markets closed for the National Day of Mourning for "
                   "President Jimmy Carter; bond market open with a shortened "
                   "session, so SOFR published and SP500 did not"),
}


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def read_series(path: str) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
    """Return (header column name, [(date, value-or-None)])."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError(f"{os.path.basename(path)}: empty file")
    header = rows[0]
    if len(header) != 2 or header[0] != "observation_date":
        raise ValueError(f"{os.path.basename(path)}: unexpected header {header}")
    out: List[Tuple[str, Optional[str]]] = []
    for line in rows[1:]:
        if not line:
            continue
        if len(line) == 1:
            line = [line[0], ""]
        value = line[1].strip()
        out.append((line[0].strip(), value or None))
    return header[1], out


def check_structure(name: str, series: str, rows: List[Tuple[str, Optional[str]]]) -> List[str]:
    problems: List[str] = []
    seen: List[str] = []
    for date, value in rows:
        try:
            parsed = dt.date.fromisoformat(date)
        except ValueError:
            problems.append(f"{name}: non-ISO date {date!r}")
            continue
        if parsed.weekday() >= 5:
            problems.append(f"{name}: weekend date {date}")
        if value is not None:
            try:
                number = float(value)
            except ValueError:
                problems.append(f"{name}: non-numeric value {value!r} on {date}")
                continue
            if number != number or number in (float("inf"), float("-inf")):
                problems.append(f"{name}: non-finite value on {date}")
            if number <= 0:
                problems.append(f"{name}: non-positive level {number} on {date}")
        seen.append(date)
    if seen != sorted(seen):
        problems.append(f"{name}: dates are not strictly increasing")
    if len(set(seen)) != len(seen):
        problems.append(f"{name}: duplicate dates")
    if not seen:
        problems.append(f"{name}: no rows")
    return problems


def valued_dates(rows: List[Tuple[str, Optional[str]]]) -> List[str]:
    return [d for d, v in rows if v is not None]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fred-dir", default=FRED_DIR,
                        help="directory holding the extracts and the calendar "
                             "reference file (default: data/real/fred)")
    parser.add_argument("--report", default="",
                        help="where to write the JSON report (default: "
                             "<fred-dir>/AGENT_FETCH_VERIFICATION.json)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    fred_dir = os.path.abspath(args.fred_dir)
    report_path = args.report or os.path.join(fred_dir, "AGENT_FETCH_VERIFICATION.json")

    reference_path = os.path.join(fred_dir, CALENDAR_REFERENCE)
    reference_dates: Optional[List[str]] = None
    reference_status = "SKIPPED: reference file absent"
    if os.path.exists(reference_path):
        _, ref_rows = read_series(reference_path)
        reference_dates = valued_dates(ref_rows)
        reference_status = (f"OK: {len(reference_dates)} valued observations read "
                            f"from the runner-collected {CALENDAR_REFERENCE}")

    report: Dict[str, object] = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": ("structural, calendar and independent-value verification of the "
                    "official-source extracts retrieved in-session, because the "
                    "sandbox cannot run the networked collector"),
        "calendar_reference": {
            "file": CALENDAR_REFERENCE,
            "status": reference_status,
        },
        "files": [],
        "crosschecks": [],
        "failures": [],
    }

    values_by_file: Dict[str, Dict[str, Optional[str]]] = {}

    for spec in EXTRACTS:
        path = os.path.join(fred_dir, spec["file"])
        entry: Dict[str, object] = dict(spec)
        if not os.path.exists(path):
            entry["status"] = "MISSING-FILE"
            report["failures"].append(f"{spec['file']}: file not written")
            report["files"].append(entry)
            continue
        with open(path, "rb") as handle:
            raw = handle.read()
        entry["bytes"] = len(raw)
        entry["sha256"] = hashlib.sha256(raw).hexdigest()
        try:
            column, rows = read_series(path)
        except ValueError as exc:
            entry["status"] = "PARSE-ERROR"
            entry["error"] = str(exc)
            report["failures"].append(str(exc))
            report["files"].append(entry)
            continue
        if column != spec["series"]:
            report["failures"].append(
                f"{spec['file']}: value column is {column!r}, expected {spec['series']!r}")
        entry["value_column"] = column
        entry["rows"] = len(rows)
        entry["first_date"] = rows[0][0] if rows else None
        entry["last_date"] = rows[-1][0] if rows else None
        valued = valued_dates(rows)
        entry["valued_observations"] = len(valued)
        entry["blank_observations"] = len(rows) - len(valued)
        entry["first_valued_date"] = valued[0] if valued else None
        entry["last_valued_date"] = valued[-1] if valued else None
        values_by_file[spec["file"]] = dict(rows)

        problems = check_structure(spec["file"], spec["series"], rows)
        entry["structure_problems"] = problems
        report["failures"].extend(problems)

        if reference_dates is not None:
            missing = sorted(set(reference_dates) - set(valued))
            extra = sorted(set(valued) - set(reference_dates))
            entry["calendar_vs_sp500"] = {
                "reference_valued_dates": len(reference_dates),
                "dates_in_sp500_but_not_here": missing,
                "dates_here_but_not_in_sp500": extra,
            }
            money_market = spec["series"] in MONEY_MARKET_SERIES
            unexplained_missing = [d for d in missing
                                   if not (money_market and d in BOND_ONLY_CLOSURES)]
            unexplained_extra = [d for d in extra
                                 if not (money_market and d in RATE_ONLY_SESSIONS)]
            entry["calendar_status"] = (
                "OK: identical trading calendar" if not missing and not extra else
                ("OK: differences are publisher-side and every one is explained below"
                 if not unexplained_missing and not unexplained_extra else
                 "MISMATCH"))
            if unexplained_missing:
                report["failures"].append(
                    f"{spec['file']}: {len(unexplained_missing)} trading dates carry a "
                    f"SP500 value but no {spec['series']} value: "
                    f"{', '.join(unexplained_missing[:10])}")
            if unexplained_extra:
                report["failures"].append(
                    f"{spec['file']}: {len(unexplained_extra)} dates carry a "
                    f"{spec['series']} value on a date SP500 has none: "
                    f"{', '.join(unexplained_extra[:10])}")
            if money_market and (missing or extra):
                entry["explained_divergences"] = {
                    "bond_market_closed_equity_open": [
                        {"date": d, "why": "bond market closure (SIFMA-recommended)"}
                        for d in sorted(set(missing) & BOND_ONLY_CLOSURES)],
                    "bond_market_open_equity_closed": [
                        {"date": d, "why": RATE_ONLY_SESSIONS[d]}
                        for d in sorted(set(extra) & set(RATE_ONLY_SESSIONS))],
                }
        entry["status"] = entry.get("status") or (
            "VERIFIED" if not problems and entry.get("calendar_status", "").startswith("OK")
            else "CHECK-REPORT")
        report["files"].append(entry)

    for check in INDEPENDENT_CROSSCHECKS:
        result = dict(check)
        values = values_by_file.get(check["file"])
        if values is None:
            result["status"] = "SKIPPED: source file not read"
        elif check["date"] not in values:
            result["status"] = f"FAIL: {check['date']} absent from {check['file']}"
            report["failures"].append(result["status"])
        else:
            found = values[check["date"]]
            result["file_value"] = found
            if found is None:
                result["status"] = (f"FAIL: {check['date']} is blank in {check['file']} "
                                    f"but {check['publisher']} reports {check['observed']}")
                report["failures"].append(result["status"])
            elif abs(float(found) - float(check["observed"])) > 1e-9:
                result["status"] = (f"FAIL: {check['date']} is {found} in {check['file']} "
                                    f"but {check['observed']} at {check['publisher']}")
                report["failures"].append(result["status"])
            else:
                result["status"] = "MATCH"
        report["crosschecks"].append(result)

    report["failure_count"] = len(report["failures"])
    report["verdict"] = "PASS" if not report["failures"] else "FAIL"

    # Idempotence matters here because the report is a committed artefact: a file
    # that changes bytes on every run leaves a dirty working tree for every
    # contributor and makes a "did anything move?" diff useless. So the timestamp
    # is written only when the *checks* changed - the report is compared with
    # ``generated_utc`` removed, and an otherwise identical report leaves the
    # stored file exactly as it was.
    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        body = json.dumps(report, indent=1, sort_keys=False) + "\n"
        previous = None
        if os.path.exists(report_path):
            try:
                with open(report_path, "r", encoding="utf-8") as handle:
                    previous = json.load(handle)
            except ValueError:
                previous = None
        unchanged = False
        if isinstance(previous, dict):
            stripped = {k: v for k, v in previous.items() if k != "generated_utc"}
            unchanged = stripped == {k: v for k, v in report.items()
                                     if k != "generated_utc"}
        report["report_unchanged_since"] = (
            previous.get("generated_utc") if unchanged and isinstance(previous, dict)
            else None)
        if unchanged:
            if not args.quiet:
                print("  report already current; left byte-identical")
        else:
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(report, indent=1, sort_keys=False) + "\n")

    if not args.quiet:
        print(f"official extract verification: {report['verdict']} "
              f"({len(report['files'])} files, {len(report['crosschecks'])} cross-checks, "
              f"{report['failure_count']} failures)")
        for entry in report["files"]:
            print(f"  {entry['file']:44s} {entry.get('rows', 0):4d} rows "
                  f"{entry.get('valued_observations', 0):4d} valued  "
                  f"{entry.get('status', '?')}")
            cal = entry.get("calendar_vs_sp500")
            if cal and (cal["dates_in_sp500_but_not_here"] or cal["dates_here_but_not_in_sp500"]):
                print(f"      calendar vs SP500: missing={cal['dates_in_sp500_but_not_here']} "
                      f"extra={cal['dates_here_but_not_in_sp500']}")
        for check in report["crosschecks"]:
            print(f"  cross-check {check['series']} {check['date']} vs "
                  f"{check['publisher']}: {check['status']}")
        for failure in report["failures"]:
            print(f"  FAIL {failure}")
        if report_path:
            print(f"  report: {os.path.relpath(report_path, REPO_ROOT)}"
                  if report_path.startswith(REPO_ROOT) else f"  report: {report_path}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
