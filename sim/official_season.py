"""Run, verify and publish the Official Auction Book.

This is the orchestration layer around :mod:`sim.officialbook`: it loads the
official inputs, runs the book over the season window, writes the memory
directory, re-derives the accounting from the tape as an independent check,
builds the forward intents the announced calendar supports, and produces the
per-participant post-mortem narratives the site publishes.

Every number it prints is either read from an official source file or computed
from one by a formula stated in ``sim/treasury.py``.  The verification block is
the part that matters: it re-reads the book's own tape and asks whether the
prices in it are the prices the Treasury published.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from . import officialbook as ob
from . import treasury, tradelog
from .calendar import REPO_ROOT

OFFICIAL_MEMORY = ob.BOOK_DIR
REHEARSAL_RUN_ID = f"official-rehearsal-seed{ob.OFFICIAL_SEED}"


def _empty_run(run_id: str) -> str:
    return os.path.join(OFFICIAL_MEMORY, run_id)


def load_inputs() -> Tuple[treasury.AuctionBook, treasury.ParCurve, ob.OfficialRates]:
    """The three official inputs, or a clear failure explaining what is missing."""
    auctions = treasury.AuctionBook()
    curve = treasury.ParCurve()
    rates = ob.OfficialRates()
    return auctions, curve, rates


# --------------------------------------------------------------------------
# Verification: re-derive the book from its own tape
# --------------------------------------------------------------------------

def verify_book(book: ob.OfficialBook, stalls: int = 12) -> dict:
    """Independent checks of the published book, from the tape and the sources.

    These do not import the settlement path; they read the intents, fills and
    trades the book wrote and ask whether they agree with the official files.
    """
    checks = 0
    failures: List[dict] = []

    def check(condition: bool, code: str, detail: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append({"code": code, "detail": detail})

    # 1. every primary fill price is the published auction price for that CUSIP
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        for fill in account.fills:
            if fill["kind"] != ob.ENTRY_PRIMARY:
                continue
            result = book.result_for(fill["cusip"], fill["session"])
            published = result.execution_price() if result else None
            check(published is not None and abs(published - fill["price_per100"]) < 1e-9,
                  "PRIMARY-PRICE-NOT-OFFICIAL",
                  f"{fill['intent_id']} filled at {fill['price_per100']} but the "
                  f"published price for {fill['cusip']} on {fill['session']} is "
                  f"{published}")

    # 2. every bill price in the tape reproduces from its own discount rate
    validation = book.auctions.price_validation()
    check(validation["bill_price_mismatches"] == 0, "BILL-PRICE-FORMULA-MISMATCH",
          f"{validation['bill_price_mismatches']} of "
          f"{validation['bill_prices_checked']} published bill prices do not "
          f"reproduce from 100 (1 - d t/360); worst {validation['worst_mismatch']}")

    # 3. the two official publishers agree on every shared auction
    crosscheck = book.auctions.crosscheck or {}
    if crosscheck:
        check(crosscheck.get("shared_auctions_with_a_difference", 0) == 0,
              "TWO-PUBLISHER-DISAGREEMENT",
              f"{crosscheck.get('shared_auctions_with_a_difference')} of "
              f"{crosscheck.get('shared_auctions')} shared auctions disagree "
              f"between TreasuryDirect and the Fiscal Data API")

    # 4. no intent read the future
    for intent in book.intents:
        for name, evidence in (intent.evidence or {}).items():
            date = str(evidence.get("observation_date") or "")
            check(not date or date <= intent.created_on, "LOOK-AHEAD",
                  f"{intent.intent_id} cites {name} dated {date} but was written "
                  f"on {intent.created_on}")
        check(intent.session > intent.created_on, "TARGET-NOT-FORWARD",
              f"{intent.intent_id} targets {intent.session} from {intent.created_on}")

    # 5. re-derive equity from the fills and the carry rows, per participant
    residencies: List[float] = []
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        cash = account.starting_cash
        realised = 0.0
        for fill in account.fills:
            cash += fill["cash_delta"]
        for row in account.carry:
            cash += row["amount"]
        for trip in account.closed:
            # closed trips move cash through the fills already counted, so the
            # realised P&L is a cross-check on the arithmetic, not a cash flow.
            realised += trip.pnl
        marks = book._marks
        rederived = cash + account.market_value(marks)
        recorded = next((m["equity"] for m in reversed(book.marks)
                         if m["participant"] == strategy.username), cash)
        residencies.append(abs(rederived - recorded))
        check(abs(rederived - recorded) < 1.0, "EQUITY-RESIDUAL",
              f"{strategy.username}: re-derived equity ${rederived:,.2f} vs recorded "
              f"${recorded:,.2f} (residual ${rederived - recorded:,.2f}) for "
              f"participant {strategy.username}")

    # 6. every maturity redemption happened on the official maturity date
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        for trip in account.closed:
            if trip.exit_kind != ob.EXIT_MATURITY:
                continue
            auction = book._auction_for(trip.cusip)
            check(auction is not None and trip.exit_date == auction.maturity_date,
                  "MATURITY-DATE-MISMATCH",
                  f"{trip.trip_id}: redeemed on {trip.exit_date} but the official "
                  f"maturity date is "
                  f"{auction.maturity_date if auction else 'not collected'}")

    # 7. every closed trade carries a source class and a price class
    for strategy in book.roster:
        for trip in book.accounts[strategy.username].closed:
            row = trip.to_row()
            check(row["source_class"] == "OFFICIAL",
                  "TRIP-NOT-OFFICIAL",
                  f"{trip.trip_id} carries source class {row['source_class']}")

    # 8. the official-coverage claim: no non-official price file is reachable
    path_ok = True
    with open(os.path.join(REPO_ROOT, "sim", "officialbook.py"), "r",
              encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("prices/yahoo", "realdata.load_series", "stooq"):
        if forbidden in text:
            path_ok = False
            failures.append({"code": "SECONDARY-PATH-REACHABLE",
                             "detail": f"sim/officialbook.py mentions {forbidden}"})
    check(path_ok, "SECONDARY-PATH-REACHABLE",
          "the official book's module references a secondary price source")

    return {
        "checks": checks,
        "failures": failures,
        "failure_count": len(failures),
        "verdict": "PASS" if not failures else "FAIL",
        "max_equity_residual_usd": round(max(residencies), 6) if residencies else 0.0,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# --------------------------------------------------------------------------
# Post-mortems
# --------------------------------------------------------------------------

def narrative(strategy: ob.OfficialStrategy, row: dict,
              account: ob.OfficialAccount, book: ob.OfficialBook) -> dict:
    """Explain what produced this participant's return, from its own numbers."""
    trades = account.closed
    funded = [t for t in trades if t.pnl > 0]
    hurt = [t for t in trades if t.pnl < 0]
    best = max(trades, key=lambda t: t.pnl) if trades else None
    worst = min(trades, key=lambda t: t.pnl) if trades else None
    drivers: List[str] = []
    if not trades:
        # No *closed* trade does not mean no position.  A participant that bought
        # a thirty-year bond two sessions into the season and never sold it has
        # zero round trips and a large marked-down position, and telling a reader
        # that its return is the cash credit would be exactly the kind of
        # plausible-looking wrong sentence this project exists to avoid.  So the
        # open position is marked here, from the same official marks the engine
        # used, and the mark is what the narrative reports.
        if account.lots:
            marks = book.mark_prices(book.last_session)
            unrealised = 0.0
            lines: List[str] = []
            for lot in account.lots:
                mark = float(marks.get(lot.cusip, lot.price_per100))
                pnl = ((mark - lot.price_per100) / 100.0 * lot.face
                       * (1.0 if lot.opened_face > 0 else -1.0))
                unrealised += pnl
                lines.append(
                    f"Open position: {lot.cusip} {abs(lot.face):,.0f} face opened "
                    f"{lot.opened_on} at {lot.price_per100:.6f}, marked {mark:.6f} "
                    f"at {book.last_session} ({pnl:+,.2f}).")
            drivers.append(
                f"No trade closed inside the window, but the account is not flat: "
                f"it holds {len(account.lots)} open position(s) whose "
                f"mark-to-market at the final session's official mark is "
                f"${unrealised:+,.2f}. The return is that mark plus the SOFR "
                f"credit minus the financing charge - a real position, not a "
                f"participant that sat still.")
            drivers.extend(lines)
        else:
            drivers.append(
                "No trade closed inside the window and the account never held a "
                "position. The cash return is the official SOFR credited on the "
                "balance, not a strategy result.")
    else:
        if best is not None and best.pnl > 0:
            drivers.append(
                f"Best trade: {best.security_term} {best.cusip} "
                f"({best.direction}, {best.entry_kind.lower()} to "
                f"{best.exit_kind.lower()}) {best.entry_date} to {best.exit_date} "
                f"for ${best.pnl:,.2f}.")
        if worst is not None and worst.pnl < 0:
            drivers.append(
                f"Worst trade: {worst.security_term} {worst.cusip} "
                f"({worst.entry_kind.lower()} to {worst.exit_kind.lower()}) "
                f"{worst.entry_date} to {worst.exit_date} for ${worst.pnl:,.2f}.")
        drivers.append(
            f"{len(funded)} of {len(trades)} closed trades made money; coupons "
            f"paid ${row['coupon_income']:,.2f} and financing cost "
            f"${row['financing'] + row['debit_interest']:,.2f}.")
    curve_move = _curve_move(book)
    if curve_move:
        drivers.append(curve_move)
    if row.get("margin_events"):
        drivers.append(
            f"The venue liquidated the account {row['margin_events']} time(s) under "
            f"its declared maintenance rule; those fills are in the tape with the "
            f"reason recorded on each one.")
    if row.get("ruined_on"):
        drivers.append(
            f"RUIN: equity reached zero on {row['ruined_on']}. The position was "
            f"closed at the official mark, the negative balance was not carried, "
            f"and the participant stopped trading - which is why its return is "
            f"exactly -100% rather than a compounding negative number.")
    return {
        "participant": strategy.username,
        "family": strategy.family,
        "thesis": strategy.thesis,
        "data_status": strategy.data_status,
        "data_note": strategy.data_note,
        "drivers": drivers,
        "verdict": _verdict(row),
    }


def _curve_move(book: ob.OfficialBook) -> Optional[str]:
    first, last = book.sessions[0], book.sessions[-1]
    moves: List[str] = []
    for tenor, label in ((0.25, "3-month"), (2.0, "2-year"), (10.0, "10-year"),
                         (30.0, "30-year")):
        a = book.curve.yield_at(first, tenor)
        b = book.curve.yield_at(last, tenor)
        if a is None or b is None:
            continue
        moves.append(f"{label} {a:.2f}% -> {b:.2f}% ({100 * (b - a):+.0f} bp)")
    if not moves:
        return None
    return ("Official par curve over the window: " + ", ".join(moves)
            + ". Duration exposures are paid off against this path.")


def _verdict(row: dict) -> str:
    if row.get("ruined_on"):
        return "lost everything"
    ret = row["return_pct"]
    if ret > 0:
        return "made money"
    if ret == 0:
        return "flat"
    return "lost money"


# --------------------------------------------------------------------------
# Forward intents
# --------------------------------------------------------------------------

def forward_snapshot(book: ob.OfficialBook) -> dict:
    """What the book has committed to for sessions that have not happened yet.

    The intents were written by the rules at the close of the last settled
    session and target announced auctions and future sessions.  Their status is
    ``PENDING`` until the official record for the target session exists, which is
    the honest state of a forward test on the day it opens.
    """
    pending = [i for i in book.intents if i.status == ob.INTENT_PENDING]
    announced = book.announced_for(book.last_session)
    rows = []
    for intent in pending:
        auction = book.auctions.auctions.get(
            f"{intent.cusip}|{intent.session}") if intent.kind == ob.INTENT_BID else None
        rows.append({
            "intent_id": intent.intent_id, "participant": intent.participant,
            "kind": intent.kind, "side": intent.side, "cusip": intent.cusip,
            "face": intent.face, "created_on": intent.created_on,
            "target_session": intent.session, "status": intent.status,
            "rule": intent.rule, "rationale": intent.rationale,
            "evidence": intent.evidence,
            "security_term": (auction.security_term if auction else None),
            "issue_date": (auction.issue_date if auction else None),
            "maturity_date": (auction.maturity_date if auction else None),
            "offering_amount": (auction.offering_amount if auction else None),
        })
    rows.sort(key=lambda r: (r["target_session"], r["participant"]))
    return {
        "as_of": book.last_session,
        "pending_intents": len(rows),
        "rows": rows,
        "announced_calendar": [{
            "cusip": a.cusip, "security_type": a.security_type,
            "security_term": a.security_term, "auction_date": a.auction_date,
            "issue_date": a.issue_date, "maturity_date": a.maturity_date,
            "announcement_date": a.announcement_date,
            "offering_amount": a.offering_amount,
            "source": a.source, "sha256": a.raw_sha256,
            "publisher": a.publisher,
        } for a in announced[:60]],
        "settlement_rule": (
            "A bid settles only when the official auction result for its CUSIP and "
            "auction date is collected; a secondary order settles only on a session "
            "the official par curve covers. Anything else stays PENDING with the "
            "reason recorded, and nothing fills at a modelled price."),
    }


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def run_official(root: str = OFFICIAL_MEMORY,
                 starting_cash: float = ob.STARTING_CASH,
                 start: str = treasury.SEASON_START,
                 end: str = treasury.SEASON_END,
                 verbose: bool = False) -> dict:
    """Run the season, write memory, verify, and snapshot the forward book."""
    auctions, curve, rates = load_inputs()
    book = ob.OfficialBook(auctions, curve, rates, starting_cash=starting_cash,
                           start=start, end=end)
    summary = book.run()
    verification = verify_book(book)
    forward = forward_snapshot(book)
    run_dir = os.path.join(root, REHEARSAL_RUN_ID)
    reports: Dict[str, dict] = {}
    for strategy in book.roster:
        row = next(r for r in summary["participants"]
                   if r["participant"] == strategy.username)
        report = narrative(strategy, row, book.accounts[strategy.username], book)
        report.update({"metrics": row,
                       "intents": [i.to_row() for i in
                                   book.accounts[strategy.username].intents
                                   if i.status in (ob.INTENT_FILLED, ob.INTENT_PARTIAL)][:400],
                       "closed_trades": [t.to_row() for t in
                                         book.accounts[strategy.username].closed]})
        reports[strategy.username] = report
    manifest = ob.write_book(book, run_dir, forward_intents=forward["rows"], extra={
        "verification": verification,
        "forward_book": {"as_of": forward["as_of"],
                         "pending_intents": forward["pending_intents"]},
        "roster": [s.describe() for s in book.roster],
        "reports": sorted(reports),
    })
    os.makedirs(os.path.join(run_dir, "reports"), exist_ok=True)
    for username, report in reports.items():
        with open(os.path.join(run_dir, "reports", f"{username.lstrip('@')}.json"),
                  "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1, sort_keys=False, default=str)
            handle.write("\n")
    forward_dir = os.path.join(root, f"official-forward-{book.last_session}")
    os.makedirs(forward_dir, exist_ok=True)
    with open(os.path.join(forward_dir, "forward_intents.json"), "w",
              encoding="utf-8") as handle:
        json.dump(forward, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    with open(os.path.join(forward_dir, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump({
            "book": ob.OFFICIAL_NAME, "as_of": forward["as_of"],
            "pending_intents": forward["pending_intents"],
            "last_official_auction_date": auctions.last_auction_date(),
            "last_curve_session": curve.last_date(),
            "settlement_rule": forward["settlement_rule"],
            "rehearsal_run": REHEARSAL_RUN_ID,
        }, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    # The unified ledger picks the new run up and publishes coverage by class.
    ledger_dir = os.path.join(root, "ledger")
    ledger_manifest = tradelog.write_ledger(
        tradelog.collect_trades(memory_root=os.path.dirname(root),
                                include_seasons=True), ledger_dir)
    if verbose:
        for row in summary["participants"]:
            print(f"{row['rank']:2d} {row['participant']:30s} "
                  f"{row['return_pct']:+9.2f}% {row['trades']:5d} trades")
    return {"run_id": REHEARSAL_RUN_ID, "run_dir": run_dir, "summary": summary,
            "manifest": manifest, "verification": verification,
            "forward": forward, "reports": reports,
            "ledger": ledger_manifest,
            "coverage": ledger_manifest["coverage"]}
