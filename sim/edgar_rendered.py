"""Parse the SEC's *rendered* Form 4 view (degraded-transport lane).

Why this module exists
----------------------
The strict lane fetches the verbatim Form 4 XML from
``https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{primary}.xml`` and
parses it with ``scripts/collect_real_data.py::parse_form4``.  That lane needs
direct HTTPS egress to ``www.sec.gov``, which the shared GitHub-runner IP pool
has been refused (the SEC answers those IPs with the "Request Rate Threshold
Exceeded" page; proven 2026-09-20, run 35477020017 - see IR-76).

The page-fetch route used by the project's research tooling *can* still read
sec.gov, but it converts documents to rendered markdown: XML tags are
stripped, so the verbatim XML cannot be reconstructed byte-for-byte.  What the
SEC's own XSL presentation view of a Form 4 does preserve is the *table
structure* of the filing - the page-fetch route returns it as markdown tables
whose cells carry exactly the values the XML names.  This module parses that
rendered view.

Provenance rules (the reason this lane is separate at all):

* a row produced here carries ``"channel": "agent-rendered-extract"`` and
  ``source_class`` stays ``OFFICIAL`` - the values are the SEC's; only the
  transport is degraded, and the transport is named on every row;
* the staged markdown the parser reads is committed verbatim under
  ``data/real/sec_agent/raw/`` next to its URL and fetch timestamp, so any
  extracted value can be re-checked against the SEC's live page by hand
  (the live URL is also recorded per row);
* parsing is fail-closed: a table row that does not match the declared
  grammar yields no transaction and a flag, never a guess.  An unparseable
  filing contributes zero rows and is counted, so coverage gaps are visible
  instead of silent.

Grammar
-------
The XSL view (``.../{accession}/xslF345X06/{primary}.xml``) renders, among
other blocks:

* the issuer block ``2. Issuer Name and Ticker ... [ AAPL ]``;
* the reporting-person block ``1. Name and Address of Reporting Person`` with
  the person's name linking their CIK, and the relationship checkboxes;
* ``Table I - Non-Derivative Securities ...`` whose data rows, rendered as
  markdown, hold eleven cells::

      title | transaction date | deemed execution | code | V | amount |
      (A)/(D) | price | owned following | D/I | indirect nature

This module reads only Table I - the same restriction as the strict lane's
``parse_form4``, which reads only ``nonDerivativeTable`` transactions.

Standard library only, like the rest of ``sim/``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

#: The relationship checkbox labels as the XSL view prints them.
_ROLE_ROWS = (
    ("Director", "director"),
    ("Officer (give title below)", "officer"),
    ("10% Owner", "10% owner"),
    ("Other (specify below)", "other"),
)

_MD_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_CODE = re.compile(r"^[A-Z](\(\d+\))?$")  # SEC renders footnote markers inline: S(3), G(1)
_NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cells(line: str) -> Optional[List[str]]:
    """The cells of one markdown table row, or None if the line is not one."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")
            and len(stripped) > 1):
        return None
    parts = [c.strip() for c in stripped[1:-1].split("|")]
    return parts


def _is_separator(cells: List[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells)


def _md_to_iso(value: str) -> Optional[str]:
    match = _MD_DATE.match(value.strip())
    if not match:
        return None
    month, day, year = match.groups()
    try:
        return dt.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _to_number(value: str) -> Optional[float]:
    cleaned = value.strip().replace("$", "").replace(",", "")
    # SEC renders footnote markers inline after amounts: 40,747(1), $218.0144(4),
    # 299,428(2)(3). They carry no numeric meaning for the signal lane.
    cleaned = re.sub(r"(\(\d+\))+$", "", cleaned).strip()
    if not cleaned or not _NUM.match(cleaned):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _issuer_ticker(markdown: str) -> Optional[str]:
    """The bracketed ticker from the issuer block (``\\[ AAPL \\]``)."""
    match = re.search(r"\\\[\s*([A-Z][A-Z0-9.\-]{0,9})\s*\\\]", markdown)
    if match:
        return match.group(1)
    match = re.search(r"^\[\s*([A-Z][A-Z0-9.\-]{0,9})\s*\]$", markdown, re.M)
    return match.group(1) if match else None


def _issuer_name(markdown: str) -> Optional[str]:
    # The issuer block renders inside one table cell ("<br>" separated), so
    # the name link can sit on the same physical line as the block's label.
    match = re.search(r"Issuer Name[^\[]*\[([^\]]+)\]", markdown, re.S)
    return match.group(1).strip() if match else None


#: Registered in sim/config.py's verified-sources register; the owner-link
#: pattern is composed from this constant so the source never carries an
#: escaped URL literal the sources-register test could not follow.
_BROWSE_EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar"
_OWNER_LINK = re.compile(
    r"\[([^\]]+)\]\(" + re.escape(_BROWSE_EDGAR + "?action=getcompany&CIK=")
    + r"(\d+)\)")


def _reporting_owner(markdown: str) -> Tuple[Optional[str], Optional[str]]:
    """(name, cik) of the first reporting person link the page renders."""
    match = _OWNER_LINK.search(markdown)
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2)


def _roles_and_title(markdown: str) -> Tuple[List[str], Optional[str]]:
    """Checkbox roles plus the officer title, from the relationship block.

    The rendered relationship block prints, per form layout, two checkbox rows
    - ``[ ] Director  [ ] 10% Owner`` and ``[X] Officer  [ ] Other`` - and then
    the officer title on its own row.  Rendered as markdown, each checkbox sits
    in the cell immediately *before* the label it belongs to, so that is the
    only attribution this parser makes: a label sharing a row with another
    label never inherits the other's mark.
    """
    roles: List[str] = []
    title: Optional[str] = None
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        cells = _cells(line)
        if not cells or _is_separator(cells):
            continue
        for label, role in _ROLE_ROWS:
            label_at = next((i for i, c in enumerate(cells) if label in c), None)
            if label_at is None:
                continue
            box = cells[label_at - 1] if label_at > 0 else ""
            if box in ("X", "x"):
                roles.append(role)
            if role == "officer":
                # The officer title prints on the row after the checkbox row.
                for follow in lines[index + 1:index + 4]:
                    follow_cells = _cells(follow)
                    if not follow_cells or _is_separator(follow_cells):
                        continue
                    candidate = follow_cells[1] if len(follow_cells) > 1 else ""
                    if candidate and not any(l in candidate for l, _ in _ROLE_ROWS):
                        title = candidate
                    break
    return sorted(set(roles)), title


def _tenb5_checked(markdown: str) -> bool:
    """Whether the 10b5-1 checkbox itself is marked.

    The instruction text around the box is printed on every Form 4, so the
    phrase alone proves nothing; only an ``X`` cell on the same table row as
    the 10b5-1 wording counts.
    """
    for line in markdown.splitlines():
        if "10b5-1" not in line:
            continue
        cells = _cells(line)
        if cells and any(c in ("X", "x") for c in cells):
            return True
    return False


def _earliest_transaction(markdown: str) -> Optional[str]:
    match = re.search(r"Date of Earliest Transaction[^\n]*\n[^\n]*\n"
                      r"(\d{2}/\d{2}/\d{4})", markdown)
    if not match:
        return None
    return _md_to_iso(match.group(1))


def _table_one_rows(markdown: str) -> Tuple[List[List[str]], int]:
    """The eleven-cell data rows of Table I, plus skipped-in-region count."""
    lines = markdown.splitlines()
    rows: List[List[str]] = []
    malformed = 0
    in_region = False
    for line in lines:
        if "Table I - Non-Derivative" in line:
            in_region = True
            continue
        if in_region and "Table II - Derivative" in line:
            break
        if not in_region:
            continue
        cells = _cells(line)
        if not cells or _is_separator(cells):
            continue
        if len(cells) != 11:
            # Header and sub-header rows have other widths; a data row with
            # the wrong width is a finding, not a guess.
            if _MD_DATE.match(cells[1].strip()) if len(cells) > 1 else False:
                malformed += 1
            continue
        if not _MD_DATE.match(cells[1].strip()):
            continue  # header row ("1. Title of Security ...") etc.
        if not _CODE.match(cells[3].strip()):
            malformed += 1
            continue
        rows.append(cells)
    return rows, malformed


def parse_rendered_form4(markdown: str, expected_ticker: str, accession: str,
                         cik: Optional[int] = None,
                         filed_date: Optional[str] = None,
                         source_url: str = "",
                         filing_index: str = "") -> Dict[str, object]:
    """Extract non-derivative transactions from one rendered Form 4 view.

    Returns ``{"rows": [...], "flags": [...]}`` where every row follows the
    strict lane's ``parse_form4`` schema (plus ``channel``) and every flag
    names something the parser refused to guess about.
    """
    flags: List[str] = []
    ticker = _issuer_ticker(markdown)
    if ticker is None:
        return {"rows": [], "flags": [f"{accession}: issuer ticker not found "
                                      "in rendered view; filing rejected"]}
    if expected_ticker and ticker != expected_ticker.upper():
        return {"rows": [], "flags": [f"{accession}: rendered issuer ticker "
                                      f"{ticker} != expected {expected_ticker}; "
                                      "filing rejected"]}
    owner, owner_cik = _reporting_owner(markdown)
    if owner is None:
        flags.append(f"{accession}: reporting owner not found in rendered view")
    owners = len(re.findall(r"Name and Address of Reporting Person", markdown))
    if owners > 1:
        flags.append(f"{accession}: {owners} reporting persons in one filing; "
                     "rows are attributed to the first rendered person only")
    roles, title = _roles_and_title(markdown)
    earliest = _earliest_transaction(markdown)
    data_rows, malformed = _table_one_rows(markdown)
    if malformed:
        flags.append(f"{accession}: {malformed} Table I row(s) matched no "
                     "declared grammar and were discarded")
    digest = sha256_text(markdown)
    rows: List[dict] = []
    for cells in data_rows:
        security, tx_date_raw = cells[0], cells[1]
        code, ad, price_raw = cells[3].strip(), cells[6].strip(), cells[7]
        code = re.sub(r"\(\d+\)$", "", code)  # drop the rendered footnote marker
        owned_raw, ownership = cells[8], cells[9].strip()
        tx_date = _md_to_iso(tx_date_raw)
        if tx_date is None:
            flags.append(f"{accession}: undecipherable transaction date "
                         f"{tx_date_raw!r}; row discarded")
            continue
        if ad not in ("A", "D"):
            flags.append(f"{accession}: acquired/disposed cell {ad!r} not "
                         "A or D; row discarded")
            continue
        if ownership not in ("D", "I", ""):
            flags.append(f"{accession}: ownership cell {ownership!r} not "
                         "D or I; row discarded")
            continue
        rows.append({
            "ticker": ticker, "cik": cik, "issuer": _issuer_name(markdown),
            "insider": owner, "owner_cik": owner_cik, "roles": roles,
            "title": title,
            "transaction_date": tx_date,
            "filed_date": filed_date,
            "code": code,
            "security": re.sub(r"\(\d+\)$", "", security).strip(),
            "acquired_or_disposed": ad,
            "shares": _to_number(cells[5]),
            "price": _to_number(price_raw),
            "shares_after": _to_number(owned_raw),
            "ownership": ownership or None,
            "tenb5_1_plan": _tenb5_checked(markdown) or None,
            "accession": accession,
            "filing_index": filing_index,
            "source_url": source_url,
            "source_sha256": digest,
            "source_class": "OFFICIAL",
            "channel": "agent-rendered-extract",
        })
    if not rows and not data_rows:
        flags.append(f"{accession}: no Table I data rows found (derivative-only "
                     "or exempt filing, or rendering mismatch)")
    if earliest and rows:
        # The earliest-transaction date the form itself declares is a cheap
        # structural check on the extraction: at least one parsed row must sit
        # on or after it, otherwise the table alignment was misread.
        if not any(r["transaction_date"] >= earliest for r in rows):  # type: ignore[operator]
            flags.append(f"{accession}: parsed rows all precede the form's own "
                         f"earliest-transaction date {earliest}; rows kept but "
                         "flagged for review")
    return {"rows": rows, "flags": flags}


_ACCESSION_RE = re.compile(r"(\d{10})-(\d{2})-(\d{6})")
_INDEX_FILED = re.compile(r"Filing Date\s+(\d{4}-\d{2}-\d{2})")
_INDEX_ACCEPTED = re.compile(r"Accepted\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_INDEX_PERIOD = re.compile(r"Period of Report\s+(\d{4}-\d{2}-\d{2})")


def _index_facts(markdown: str) -> Dict[str, Optional[str]]:
    filed = _INDEX_FILED.search(markdown)
    accepted = _INDEX_ACCEPTED.search(markdown)
    period = _INDEX_PERIOD.search(markdown)
    return {
        "filing_date": filed.group(1) if filed else None,
        "accepted": accepted.group(1) if accepted else None,
        "period_of_report": period.group(1) if period else None,
    }


def integrate_staged_lane(root: str) -> dict:
    """Parse everything staged under ``root`` into the lane's output files.

    ``root`` holds ``staged_manifest.json`` and the ``raw/`` captures.  Writes
    ``form4_transactions.jsonl``, ``form4_index_agent.json`` and
    ``parse_report.json`` next to them and returns the report.  Digests are
    re-computed from the staged bytes; a file whose digest drifted from its
    recorded one is rejected outright.
    """
    manifest_path = os.path.join(root, "staged_manifest.json")
    if not os.path.exists(manifest_path):
        return {"filings_parsed": 0, "digests_verified": 0, "transactions": 0,
                "flags": [f"no staged manifest at {manifest_path}"]}
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    entries = manifest.get("entries", [])

    index_facts: Dict[str, Dict[str, Optional[str]]] = {}
    for entry in entries:
        if entry.get("kind") != "index":
            continue
        path = os.path.join(root, entry["file"])
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            index_facts[entry.get("accession", "")] = _index_facts(handle.read())

    rows_all: List[dict] = []
    flags: List[str] = []
    per_filing: List[dict] = []
    digests_checked = 0
    for entry in entries:
        if entry.get("kind") != "form":
            continue
        rel = entry["file"]
        path = os.path.join(root, rel)
        ticker = entry.get("ticker", "")
        accession = entry.get("accession", "")
        if not os.path.exists(path):
            flags.append(f"{rel}: staged file missing; skipped")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            markdown = handle.read()
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        if digest != entry.get("sha256"):
            flags.append(f"{rel}: sha256 drift ({digest[:12]}... vs recorded "
                         f"{str(entry.get('sha256'))[:12]}...); filing rejected")
            continue
        digests_checked += 1
        # The issuer CIK comes from the manifest (where the staging step
        # resolved it from the company's own EDGAR feed), never from the
        # accession number: the accession's first field is the *filing
        # agent's* CIK (Workiva, for one, is 0001140361), not the issuer's.
        cik = entry.get("cik")
        facts = index_facts.get(accession, {})
        filing_index = (f"https://www.sec.gov/Archives/edgar/data/"
                        f"{cik}/{accession.replace('-', '')}/"
                        f"{accession}-index.htm") if cik else ""
        result = parse_rendered_form4(
            markdown, expected_ticker=ticker, accession=accession, cik=cik,
            filed_date=facts.get("filing_date"), source_url=entry.get("url", ""),
            filing_index=filing_index)
        flags.extend(result["flags"])
        parsed = result["rows"]
        if facts.get("filing_date") and parsed:
            if not all(r.get("filed_date") == facts["filing_date"] for r in parsed):
                flags.append(f"{accession}: filed-date mismatch between index "
                             f"({facts['filing_date']}) and parsed rows; rows kept "
                             "but flagged")
        rows_all.extend(parsed)
        per_filing.append({
            "ticker": ticker, "accession": accession, "file": rel,
            "url": entry.get("url", ""),
            "fetched_at_utc": entry.get("fetched_at_utc", ""),
            "rows": len(parsed), "index_facts": facts,
        })

    rows_all.sort(key=lambda r: (r.get("filed_date") or "",
                                 r.get("transaction_date") or "",
                                 r.get("accession") or ""), reverse=True)
    with open(os.path.join(root, "form4_transactions.jsonl"), "w",
              encoding="utf-8") as handle:
        for row in rows_all:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(root, "form4_index_agent.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"lane": "agent-rendered-extract", "generated_at_utc": now,
                   "filings": per_filing}, handle, indent=1, sort_keys=True)
        handle.write("\n")
    report = {"lane": "agent-rendered-extract", "generated_at_utc": now,
              "filings_parsed": len(per_filing),
              "digests_verified": digests_checked,
              "transactions": len(rows_all), "flags": flags}
    with open(os.path.join(root, "parse_report.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
        handle.write("\n")
    return report


__all__ = ["parse_rendered_form4", "sha256_text", "integrate_staged_lane"]
