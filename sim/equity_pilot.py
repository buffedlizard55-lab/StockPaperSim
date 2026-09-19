"""Forward pilot for the strict equities desk.

Purpose, stated plainly: the brief asks for a *durable* forward-test loop with
timestamped order submission, reconciled before any competition returns are
published.  This module is that loop's runner.  It runs on a GitHub Actions
schedule (see .github/workflows/equity-pilot.yml), every run is an append-only
event, and it is structurally incapable of quietly falling back to a price
somebody typed in:

* **Intent generation** reads only committed, checksummed files (currently the
  Yahoo-daily files whose ``source_class`` is ``SECONDARY``).  Every order it
  journals carries ``input_class`` and ``signal_source_url`` so a reviewer can
  see, on the line itself, which class of data produced it.
* **Execution** goes through :class:`sim.strict_equities.EvidenceGate`, whose
  production registry deliberately contains no approved feed.  Until an
  exchange/SIP feed with documented redistribution rights is recorded in
  ``research/strict/registry.json`` (``approved_feeds``), every order is
  rejected ``NO_APPROVED_OFFICIAL_FEED`` in the same hash-chained journal, so
  "the pilot placed zero fills" is a *re-derived count*, not a sentence.
* **Market making** is behind :func:`market_making_gate`, which stays closed
  until quote-size and queue-priority receipts exist.  Enabling it without
  queue evidence is a design error, not a reporting detail (IR-24 in
  research/IRREGULARITIES.json says why).
* **Reconciliation** re-checks the journal hash chain and re-counts events by
  kind per run; a pilot whose counts disagree with the committed report fails
  closed and the site shows the failure rather than a stale "all good".

The pilot's universe is one broad instrument - SPY, the S&P 500 ETF - because
the brief names the S&P 500 explicitly and a single documented seed universe
keeps every run reviewable line by line.  Widening the universe is next-session
work registered in research/REMAINING_WORK.json.

Nothing here changes the honest headline: **strict equity fills remain zero
until an official licensed feed lands.**  This pilot exists so that the moment
such a feed exists, the order pipeline, the calendars, the halts checks, the
fees and the reconciliation are already running rather than starting then.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .research_rules import evaluate as evaluate_rule
from .strict_equities import (
    EvidenceError,
    EvidenceGate,
    PaperLedger,
    SessionSchedule,
    timestamp as strict_timestamp,
    digest,
)
from .venue_admin import (
    MARKET_CALENDAR_SOURCE,
    CorporateActionTable,
    is_market_open,
    next_market_day,
    session_record,
)

UTC = dt.timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = "research/strict/registry.json"
PRICE_DIR = "data/real/prices/yahoo"
PILOT_MEMO = "memory/pilot"

#: Seed universe.  One broad instrument, explicitly declared, widening is
#: logged future work rather than silently present.
PILOT_UNIVERSE = ("SPY",)

IMPLEMENTABLE = {"sma", "ema", "momentum", "donchian", "rsi"}


def load_registry(path: str = REGISTRY_PATH) -> dict:
    payload = json.loads((REPO_ROOT / path).read_text())
    for key in ("competition", "approved_feeds", "strategies"):
        if key not in payload:
            raise ValueError(f"REGISTRY_MISSING:{key}")
    return payload


def implementable_specs(registry: dict) -> List[dict]:
    specs = []
    for spec in registry["strategies"]:
        if spec.get("implementation") in IMPLEMENTABLE:
            specs.append(spec)
    return sorted(specs, key=lambda s: s["id"])


def load_bars(symbol: str) -> List[dict]:
    """Committed daily bars, converted to the point-in-time bar shape.

    The research-rules evaluator requires ``ended_at``, ``available_at`` and
    ``received_at`` per bar so no price can pretend to have been knowable
    earlier than it was.  For a committed daily file the honest values are:
    the bar ends at that date's 16:00 NY close, was available when the vendor
    published it that evening (collected ``last_bar`` is the collection
    ceiling, not per-bar provenance) - we therefore use the *next* documented
    session's open as ``available_at``/``received_at``, guaranteeing no
    same-day peeking, and the file itself records ``source_class`` for review.
    """
    path = REPO_ROOT / PRICE_DIR / f"{symbol}.json"
    payload = json.loads(path.read_text())
    if payload.get("source_class") != "SECONDARY":
        raise ValueError(f"PILOT_INPUT_CLASS:{symbol}:{payload.get('source_class')}")
    raw = payload["bars"]
    out = []
    for idx, bar in enumerate(raw):
        day = dt.date.fromisoformat(bar["date"])
        # SECONDARY research-label convention: the vendor's evening publication
        # is modelled as the next WEEKDAY at 09:30 NY.  This is not a claim
        # about official exchange sessions (venue_admin is the only authority
        # for those); it is the availability envelope of a research intent,
        # deliberately conservative and stated here in one place.
        available = day
        for _ in range(6):
            available += dt.timedelta(days=1)
            if available.weekday() < 5:
                break
        close_local = dt.datetime(
            available.year, available.month, available.day, 9, 30,
            tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"),
        ).astimezone(UTC)
        out.append(
            {
                "date": bar["date"],
                "close": bar["close"],
                "high": bar["high"],
                "low": bar["low"],
                "ended_at": f"{bar['date']}T20:00:00Z",
                "available_at": close_local.isoformat().replace("+00:00", "Z"),
                "received_at": close_local.isoformat().replace("+00:00", "Z"),
            }
        )
    out.sort(key=lambda b: b["ended_at"])
    return out, payload


def holding_usernames(ledger: PaperLedger) -> Dict[str, int]:
    """Which usernames currently hold a long position (public events only)."""
    holdings: Dict[str, Dict[str, int]] = {}
    for event in ledger.events():
        body = event["body"]
        if event["kind"] != "FILL":
            continue
        pos = holdings.setdefault(body["username"], {})
        qty = body["quantity"] if body["side"] == "buy" else -body["quantity"]
        pos[body["symbol"]] = pos.get(body["symbol"], 0) + qty
    return {u: sum(p.values()) for u, p in holdings.items()}


def market_making_gate(registry: dict) -> dict:
    """Queue-evidence gate for market making; closed until evidence exists.

    Enabling two-sided quoting without observed quote sizes and queue
    priority would print imaginary fill rates.  The gate opens only when an
    approved feed entry carries both ``quote_size_evidence`` and
    ``queue_priority_evidence`` keys; today none does, and the gate says so.
    """
    reasons = []
    for feed_id, feed in registry.get("approved_feeds", {}).items():
        if not feed.get("quote_size_evidence"):
            reasons.append(f"{feed_id}:NO_QUOTE_SIZE_RECEIPTS")
        if not feed.get("queue_priority_evidence"):
            reasons.append(f"{feed_id}:NO_QUEUE_PRIORITY_EVIDENCE")
        if feed.get("redistribution") != "APPROVED":
            reasons.append(f"{feed_id}:REDISTRIBUTION_NOT_APPROVED")
    if not registry.get("approved_feeds"):
        reasons.append("NO_APPROVED_FEEDS_AT_ALL")
    return {"allowed": not reasons, "reasons": reasons}


def _order_body(spec, symbol, side, qty, limit, signal_digest, source_url, signal_at, expires_at, input_class, note):
    return {
        "order_id": f"{spec['id']}:{symbol}:{signal_digest[:12]}",
        "username": spec["username"],
        "strategy_version": f"{spec['id']}@{spec['version']}",
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "order_type": "market" if limit is None else "limit",
        "limit_price": limit,
        "signal_at": signal_at,
        "signal_sha256": signal_digest,
        "signal_source_url": source_url,
        "expires_at": expires_at,
        "reason": note,
        "input_class": input_class,
        "strategy_family": spec["family"],
    }


def run_pilot_day(
    run_date: dt.date,
    now: dt.datetime,
    memo_root: str = PILOT_MEMO,
    registry_path: str = REGISTRY_PATH,
    seeded_rehearsal: bool = False,
) -> dict:
    """One scheduled pilot run: intents in, gate decisions out, reconcile at the end.

    ``run_date`` must be a documented market session (venue_admin is the only
    authority); ``now`` is the caller's wall-clock submission timestamp.
    ``seeded_rehearsal`` marks a run whose clock was supplied by hand (for
    example a developer sandbox bootstrap) so reviewers can distinguish
    scheduler wall-clock runs from seeded ones.
    """
    """One scheduled pilot run: intents in, gate decisions out, reconcile at the end.

    ``run_date`` must be a documented market session (venue_admin is the only
    authority); ``now`` is the caller's wall-clock submission timestamp.
    """
    registry = load_registry(registry_path)
    if not is_market_open(run_date):
        raise ValueError(f"MARKET_CLOSED:{run_date.isoformat()}")
    record = session_record(run_date)
    # The scheduler runs after the close; an order submitted Monday evening
    # must be alive for the NEXT documented session or the ledger's clock rule
    # (expires_at <= submission time) rejects every one of them.  Orders
    # therefore carry the next session's close as expires_at.
    exec_session = next_market_day(run_date)
    exec_record = session_record(exec_session)
    schedule = SessionSchedule({record["date"]: record, exec_record["date"]: exec_record})
    root = REPO_ROOT
    memo = root / memo_root
    memo.mkdir(parents=True, exist_ok=True)
    gate = EvidenceGate(root, registry.get("approved_feeds", {}))
    # Sourced corporate actions stand orders down within +/-1 session of the
    # ex-date.  The table is committed at data/real/corporate-actions/ and is
    # empty by design until the EDGAR collection workflow lands - an empty,
    # coverage-noted table is a statement, not a bug.
    corp_actions = load_corporate_actions(root)
    window = dt.timedelta(days=2)
    ledger = PaperLedger(
        memo / "journal.sqlite",
        gate,
        schedule,
        registry["competition"],
        cash=registry["competition"].get("starting_cash", "100000"),
        fee_per_share="0.003",
    )
    head_before = ledger.verify()
    specs = implementable_specs(registry)
    mm_gate = market_making_gate(registry)

    submitted, rejected, skipped = [], [], []
    holdings = holding_usernames(ledger)
    bars_by_symbol: Dict[str, List[dict]] = {}
    sources: Dict[str, dict] = {}
    for symbol in PILOT_UNIVERSE:
        bars, payload = load_bars(symbol)
        if bars:
            bars_by_symbol[symbol] = bars
            sources[symbol] = payload

    as_of = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    for spec in specs:
        for symbol in PILOT_UNIVERSE:
            bars = bars_by_symbol.get(symbol)
            if not bars:
                skipped.append({"username": spec["username"], "symbol": symbol, "code": "NO_BARS"})
                continue
            usable = [b for b in bars if strict_timestamp(b["received_at"]) < strict_timestamp(as_of)]
            if len(usable) < 2:
                skipped.append({"username": spec["username"], "symbol": symbol, "code": "INSUFFICIENT_USABLE_BARS"})
                continue
            try:
                verdict = evaluate_rule(spec, usable, as_of, holding=holdings.get(spec["username"], 0) > 0)
            except EvidenceError as exc:
                skipped.append({"username": spec["username"], "symbol": symbol, "code": f"SIGNAL_REJECTED:{exc}"})
                continue
            if verdict.get("status") != "RESEARCH_SIGNAL_NOT_ORDER" or verdict.get("target_long") is None:
                skipped.append(
                    {
                        "username": spec["username"],
                        "symbol": symbol,
                        "code": f"NO_SIGNAL:{verdict.get('status')}",
                    }
                )
                continue
            want_long = bool(verdict["target_long"])
            have_long = holdings.get(spec["username"], 0) > 0
            if want_long == have_long:
                skipped.append({"username": spec["username"], "symbol": symbol, "code": "ALREADY_AT_TARGET"})
                continue
            near = corp_actions.actions_for(
                symbol, run_date - window, run_date + window
            ) if corp_actions else []
            if near:
                skipped.append({
                    "username": spec["username"],
                    "symbol": symbol,
                    "code": "CORP_ACTION_STANDDOWN",
                    "actions": [a.action_type + "@" + a.ex_date for a in near],
                })
                continue
            last_close = usable[-1]["close"]
            side = "buy" if want_long else "sell"
            if side == "buy":
                cash = ledger_cash(ledger, spec["username"])
                qty = int(cash // last_close)
                if qty < 1:
                    skipped.append({"username": spec["username"], "symbol": symbol, "code": "NO_CASH_FOR_ONE_SHARE"})
                    continue
            else:
                qty = holdings.get(spec["username"], 0)
            signal = {
                "spec": spec["id"],
                "version": spec["version"],
                "symbol": symbol,
                "side": side,
                "evaluated_bars": len(usable),
                "last_bar": usable[-1]["date"],
                "input_class": sources[symbol].get("source_class"),
                "run_date": run_date.isoformat(),
            }
            signal_digest = digest(signal)
            expires = strict_timestamp(exec_record["close"]).astimezone(UTC).isoformat().replace("+00:00", "Z")
            try:
                signal_at = strict_timestamp(usable[-1]["received_at"]).astimezone(UTC).isoformat().replace("+00:00", "Z")
            except EvidenceError:
                signal_at = as_of
            order = _order_body(
                spec, symbol, side, str(qty), None, signal_digest,
                source_url="https://github.com/buffedlizard55-lab/StockPaperSim/blob/main/" + PRICE_DIR + f"/{symbol}.json",
                signal_at=signal_at, expires_at=expires,
                input_class=sources[symbol].get("source_class", "UNKNOWN"),
                note=(f"pilot {side} {qty} {symbol} target_long={want_long} mm_gate={mm_gate['allowed']}"
                      + (f" SEEDED_REHEARSAL" if seeded_rehearsal else "")),
            )
            try:
                ledger.submit(order, as_of)
                submitted.append(order)
            except EvidenceError as exc:
                rejected.append({"order": order["order_id"], "code": f"SUBMIT:{exc}"})
                continue
            # Execution is attempted only through the evidence gate, which has
            # no approved official feed today; the refusal is journaled so the
            # zero-fill headline is a count, not prose.
            if registry.get("approved_feeds"):
                # A future licensed feed hands quotes here; this branch cannot
                # run today and fills stay zero until it can.
                pass
            else:
                ledger.refuse(order["order_id"], as_of,
                              "NO_APPROVED_OFFICIAL_FEED: production approved-feed "
                              "registry is empty; see research/strict/registry.json")
                rejected.append({"order": order["order_id"], "code": "EXEC:NO_APPROVED_OFFICIAL_FEED"})

    upcoming = compute_upcoming(next_market_day(run_date), registry)
    report = {
        "run_date": run_date.isoformat(),
        "execution_session": exec_session.isoformat(),
        "submitted_at": as_of,
        "seeded_rehearsal": bool(seeded_rehearsal),
        "journal_head_before": head_before,
        "journal_head_after": ledger.verify(),
        "feeds_approved": len(registry.get("approved_feeds", {})),
        "market_making_gate": mm_gate,
        "orders_submitted": len(submitted),
        "orders_rejected": rejected,
        "signals_skipped": skipped,
        "fills": event_count(ledger, "FILL"),
        "settlements": event_count(ledger, "SETTLEMENT"),
        "universe": list(PILOT_UNIVERSE),
        "calendar_source": MARKET_CALENDAR_SOURCE,
        "broadcast": (
            "strict equity pilot: intents are journaled with timestamps and "
            "input classes; fills remain zero while approved_feeds is empty"
        ),
    }
    day_dir = memo / f"run-{run_date.isoformat()}"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "run_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    (day_dir / "orders.json").write_text(json.dumps(submitted, indent=1, sort_keys=True) + "\n")
    ledger.export(day_dir / "journal.jsonl")
    recon = reconcile(ledger, registry)
    (memo / "reconcile.json").write_text(json.dumps(recon, indent=1, sort_keys=True) + "\n")
    (memo / "upcoming_orders.json").write_text(json.dumps(upcoming, indent=1, sort_keys=True) + "\n")
    ledger.close()
    return report


CORP_ACTIONS_PATH = "data/real/corporate-actions/actions.json"


def load_corporate_actions(root: Path) -> Optional[CorporateActionTable]:
    """Committed, sourced corporate-action rows; None when none collected yet."""
    path = Path(root) / CORP_ACTIONS_PATH
    if not path.exists():
        return None
    return CorporateActionTable.from_json(json.loads(path.read_text()))


def ledger_cash(ledger: PaperLedger, username: str) -> float:
    cash, receivable, _realized, _p, _l, _f, _m = ledger._state(username)
    return float(cash)


def event_count(ledger: PaperLedger, kind: str) -> int:
    return sum(1 for e in ledger.events() if e["kind"] == kind)


def compute_upcoming(session: dt.date, registry: dict) -> dict:
    """The scheduled intents the pilot will evaluate for the next session.

    Values are *planned* quantities flagged PLAN_ONLY: actual orders get their
    timestamp at submission, never from this preview file.
    """
    rows = []
    for spec in implementable_specs(registry):
        for symbol in PILOT_UNIVERSE:
            rows.append(
                {
                    "username": spec["username"],
                    "strategy": spec["id"],
                    "version": spec["version"],
                    "symbol": symbol,
                    "session": session.isoformat(),
                    "entry_rule": spec.get("entry_rule", ""),
                    "state": "SCHEDULED_FOR_EVALUATION",
                    "input_class_required": "SECONDARY intent / OFFICIAL fill only",
                }
            )
    return {
        "generated_for_session": session.isoformat(),
        "calendar_source": MARKET_CALENDAR_SOURCE,
        "note": "intents become timestamped orders only at the scheduled run; "
        "no row here is an order, a price, or a fill",
        "rows": rows,
    }


def reconcile(ledger: PaperLedger, registry: dict) -> dict:
    """Re-derived journal audit.  FAILs loudly on any impossible state."""
    head = ledger.verify()
    events = ledger.events()
    by_kind: Dict[str, int] = {}
    for e in events:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    fills = by_kind.get("FILL", 0)
    verdict = "PASS"
    failures = []
    if fills and not registry.get("approved_feeds"):
        verdict = "FAIL"
        failures.append("FILLS_WITHOUT_APPROVED_FEED: the journal claims fills "
                        "while the production registry approves no feed - treat "
                        "as tamper evidence, not performance")
    usernames = set()
    for e in events:
        if e["kind"] in {"ORDER", "FILL"}:
            usernames.add(e["body"].get("username", ""))
    return {
        "verdict": verdict,
        "failures": failures,
        "journal_head": head,
        "events": len(events),
        "by_kind": by_kind,
        "participants_seen": sorted(usernames),
        "feeds_approved": len(registry.get("approved_feeds", {})),
        "checked_against": "research/strict/registry.json::approved_feeds",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="strict equities forward pilot (scheduled)")
    parser.add_argument("command", choices=["run", "reconcile", "upcoming"])
    parser.add_argument("--date", default=None, help="ISO market session to run for (default: today, America/New_York)")
    parser.add_argument("--now", default=None, help="ISO wall-clock submission timestamp (default: system UTC now)")
    parser.add_argument("--seeded-rehearsal", action="store_true",
                        help="stamp this run as a sandbox-seeded rehearsal (clock supplied by hand)")
    args = parser.parse_args(argv)

    if args.date:
        run_date = dt.date.fromisoformat(args.date)
    else:
        from zoneinfo import ZoneInfo

        run_date = dt.datetime.now(UTC).astimezone(ZoneInfo("America/New_York")).date()
    now = strict_timestamp(args.now) if args.now else dt.datetime.now(UTC)

    registry = load_registry()
    if args.command == "run":
        report = run_pilot_day(run_date, now, seeded_rehearsal=args.seeded_rehearsal)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0
    if args.command == "upcoming":
        session = run_date if is_market_open(run_date) else next_market_day(run_date)
        print(json.dumps(compute_upcoming(session, registry), indent=1, sort_keys=True))
        return 0
    memo = REPO_ROOT / PILOT_MEMO
    journal = memo / "journal.sqlite"
    if not journal.exists():
        print(json.dumps({"verdict": "NO_JOURNAL_YET", "detail": "pilot has not been scheduled yet"}, indent=1))
        return 0
    record = session_record(run_date)
    gate = EvidenceGate(REPO_ROOT, registry.get("approved_feeds", {}))
    ledger = PaperLedger(journal, gate, SessionSchedule({record["date"]: record}), registry["competition"])
    recon = reconcile(ledger, registry)
    (memo / "reconcile.json").write_text(json.dumps(recon, indent=1, sort_keys=True) + "\n")
    print(json.dumps(recon, indent=1, sort_keys=True))
    ledger.close()
    return 0 if recon["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
