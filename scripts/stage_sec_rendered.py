#!/usr/bin/env python3
"""Stage-and-parse the SEC Form 4 rendered-extraction lane.

The agent page-fetch route can read ``www.sec.gov`` from environments whose
direct HTTPS egress the SEC refuses (the shared GitHub-runner IP pool - see
IR-76), but the route renders documents to markdown, so verbatim XML cannot
cross it.  This script is the durable half of that lane: it consumes markdown
staged verbatim under ``data/real/sec_agent/raw/`` - each file recorded with
its URL, fetch timestamp and SHA-256 in ``staged_manifest.json`` - parses the
rendered Form 4 views with ``sim.edgar_rendered`` (fail-closed, no guessing),
cross-checks every filing against its rendered filing-index page, and writes
the transaction rows in the exact JSONL shape the strict collector's
``parse_form4`` produces, so ``sim.masterfeed._load_insider_rows`` can merge
the two lanes without seeing the difference except the ``channel`` field.

Layout (all under ``data/real/sec_agent/``)::

    raw/atom/{TICKER}.atom.md              browse-edgar filing list, rendered
    raw/index/{TICKER}_{ACCESSION}.index.md  filing index page, rendered
    raw/form/{TICKER}_{ACCESSION}.form.md    XSL-rendered Form 4 view
    staged_manifest.json                   url + fetched_at + sha256 per file
    form4_transactions.jsonl               <- written here
    form4_index_agent.json                 <- written here
    parse_report.json                      <- written here (counts + flags)

Every row keeps the URL of the SEC page it was read from, so a reviewer can
open the live page and re-check any single value by hand.  Standard library
only, like the rest of the project.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from sim.edgar_rendered import integrate_staged_lane  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(REPO_ROOT, "data",
                                                        "real", "sec_agent"))
    args = parser.parse_args(argv)
    report = integrate_staged_lane(args.root)
    if report.get("filings_parsed") == 0 and report.get("transactions") == 0:
        for flag in report.get("flags", []):
            print(f"  flag: {flag}")
        print("agent lane: nothing parsed (see flags above)")
        return 1
    print(f"agent lane: {report['filings_parsed']} filings staged, "
          f"{report['digests_verified']} digests verified, "
          f"{report['transactions']} transactions, "
          f"{len(report.get('flags', []))} flag(s)")
    for flag in report.get("flags", []):
        print(f"  flag: {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
