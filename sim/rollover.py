"""The forward rollover ladder: settle every session that has a published print.

The live book used to advance one session per manual run, and the only way a
session could settle at all was for someone to hand it a date.  That is a
to-do list, not a forward test.  This module is the missing loop:

* read the collected history and overlay the *print ledger* (real published bars
  for the sessions after it, :mod:`sim.prints`);
* replay the book forward from its plan date, settling **every** session in turn
  for which each traded symbol has a published print, and planning the next
  session from the previous close;
* publish a ladder row per session saying what happened and, when a session could
  not settle, exactly which evidence is missing;
* do it deterministically, so the same inputs always produce the same book and a
  re-run after new prints arrive only ever *adds* sessions.

Nothing in this path invents a price.  A symbol with no print for a session is
reported as waiting, and a session the official calendar has not caught up with
is labelled provisional until the official series publish their own observation.

Run it with ``python3 -m sim.cli rollover``; the scheduled workflow runs that
command every week-day and opens a pull request with whatever it settled.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from typing import Dict, List, Optional, Sequence

from . import live, live_season, memory, prints as print_mod, realdata

LADDER_FILE = "forward-ladder.json"
HISTORY_FILE = "forward-ladder-history.jsonl"
LADDER_SUBDIR = "live"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def last_fred_session(series: str = "SP500", root: str = realdata.REAL_ROOT) -> str:
    """The newest observation the official calendar authority has published."""
    values, _path, _sha = realdata.load_fred(series, root)
    return max(values) if values else ""


def _existing_plan_date(root: str = memory.DEFAULT_ROOT) -> str:
    """The plan date of the newest forward run on disk, so the book continues."""
    base = os.path.join(root, live_season.LIVE_MEMORY_SUBDIR)
    if not os.path.isdir(base):
        return ""
    candidates = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "manifest.json")
        if not name.startswith("live-forward-") or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        candidates.append((manifest.get("created_utc", name), manifest.get("plan_date", "")))
    return candidates[-1][1] if candidates else ""


def _targeted(book: live.LiveBook, session: str) -> List[live.Intent]:
    return [i for i in book.intents if i.intended_session == session]


def official_evidence(session: str, root: str = realdata.REAL_ROOT) -> dict:
    """Which official series have published an observation for ``session``.

    A native session never needs this to settle, but the ladder shows it anyway:
    a reader comparing two sessions has to be able to see that 2026-09-17 has the
    S&P 500 observation and 2026-09-18 does not, which is the difference between
    "the official calendar says this was a session" and "the exchange printed a
    bar for every symbol and two official index series agree it traded".
    """
    present, missing = [], []
    for row in live.OFFICIAL_SERIES_REGISTER:
        sid = row.get("sid")
        if not sid:
            continue
        try:
            values = realdata.load_fred(sid, root)[0]
        except Exception:                                   # pragma: no cover
            missing.append(sid)
            continue
        if session in values:
            present.append({"sid": sid, "value": values[session]})
        else:
            missing.append(sid)
    return {"official_series_present": present, "official_series_missing": missing}


def ladder_row(book: live.LiveBook, md, session: str, result: dict) -> dict:
    """One row per session: what settled, what waited, and on what evidence."""
    targeted = _targeted(book, session)
    evidence = (getattr(md, "session_evidence", {}) or {}).get(session, {})
    if not evidence:
        evidence = official_evidence(session)
    print_provenance = (getattr(md, "bar_provenance", {}) or {})
    classes = sorted({print_provenance[(i.symbol, session)]["source_class"]
                      for i in targeted if (i.symbol, session) in print_provenance})
    missing_symbols = sorted({i.symbol for i in targeted
                              if (i.symbol, session) not in print_provenance
                              and (getattr(md, "gaps", {}) or {}).get(i.symbol)
                              and session in (md.gaps or {}).get(i.symbol, [])})
    if session in (getattr(md, "provisional_sessions", set()) or set()):
        verification = evidence.get("verification", print_mod.PROVISIONAL)
    elif session in md.dates:
        verification = "COLLECTED-OFFICIAL-CALENDAR"
    else:
        verification = "NO-SESSION"
    fills = result.get("fills", []) if isinstance(result, dict) else []
    return {
        "session": session,
        "verification": verification,
        "official_series_missing": evidence.get("official_series_missing", []),
        "official_series_present": evidence.get("official_series_present", []),
        "price_sources": classes,
        "intents_targeted": len(targeted),
        "filled": result.get("filled", 0),
        "partial": result.get("partial", 0),
        "rejected": result.get("rejected", 0),
        "expired": result.get("expired", 0),
        "waiting_for_prints": result.get("waiting", 0),
        "notional_usd": round(float(result.get("notional") or 0.0), 2),
        "symbols_waiting_for_a_print": missing_symbols,
        "status": ("SETTLED" if (result.get("filled") or result.get("partial"))
                   else "WAITING-FOR-PRINTS" if result.get("waiting")
                   else "NOTHING-SCHEDULED"),
        "fills": [{"intent_id": f.get("intent_id"), "participant": f.get("participant"),
                   "symbol": f.get("symbol"), "side": f.get("side"),
                   "quantity": f.get("filled_qty"), "price": f.get("avg_price"),
                   "reference_source_class": f.get("reference_source_class"),
                   "reference_redistribution_status": f.get(
                       "reference_redistribution_status"),
                   "slippage_bps": f.get("slippage_bps"),
                   "participation_pct_of_session_volume": f.get(
                       "participation_pct_of_session_volume")}
                  for f in fills],
    }


# --------------------------------------------------------------------------
# the step
# --------------------------------------------------------------------------

def step(root: str = memory.DEFAULT_ROOT,
         real_root: str = realdata.REAL_ROOT,
         plan_date: Optional[str] = None,
         through: Optional[str] = None,
         horizon: int = 3,
         verbose: bool = False,
         write: bool = True) -> dict:
    """Replay the forward book over every session that has published prints."""
    end = last_fred_session("SP500", real_root) or realdata.SEASON2_END
    md = live_season.live_market(real_root, verbose=verbose, end=end)
    overlay = realdata.apply_forward_prints(md, root=real_root, verbose=verbose)

    plan_date = plan_date or _existing_plan_date(root) or md.dates[-1]
    if plan_date not in md.dates:
        raise ValueError(f"plan date {plan_date} is not a session in the market data")

    feed = live.OfficialFeed(root=real_root)
    roster = live_season.build_live_roster()
    book = live.LiveBook(md, feed, roster, cfg=live_season.live_config("forward"),
                         mode="forward")

    sessions = [d for d in md.dates if d > plan_date and (through is None or d <= through)]
    book.plan(plan_date, horizon=horizon)
    marks: List[dict] = []
    settlements: List[dict] = []
    ladder: List[dict] = []
    previous = plan_date
    for session in sessions:
        result = book.settle(session)
        settlements.append(result)
        book.accrue_carry(session, previous)
        marks.extend(book.mark(session))
        ladder.append(ladder_row(book, md, session, result))
        book.plan(session, horizon=horizon)
        previous = session

    benchmark = live.benchmark_from_official(feed, plan_date, md.dates[-1])
    board = live.live_leaderboard(book, benchmark)
    pending = [i for i in book.intents
               if i.status in (live.INTENT_PENDING, live.INTENT_WAITING_DATA)]
    verification = live.verify_live(book, marks)

    settled_sessions = [row["session"] for row in ladder if row["status"] == "SETTLED"]
    waiting_sessions = [row["session"] for row in ladder if row["status"] == "WAITING-FOR-PRINTS"]
    fills = [f for account in book.accounts.values() for f in account.fills]
    official_notional = sum(float(f.get("notional") or 0.0) for f in fills
                            if f.get("reference_source_class") in
                            ("OFFICIAL", "EXCHANGE-PUBLISHED"))
    total_notional = sum(float(f.get("notional") or 0.0) for f in fills)
    status = (f"ROLLOVER: {len(settled_sessions)} session(s) settled "
              f"({', '.join(settled_sessions) or 'none'}), "
              f"{len(waiting_sessions)} waiting for prints, "
              f"{len(pending)} intents outstanding.")

    run_id = f"live-forward-{plan_date}"
    if write:
        run_dir = os.path.join(root, live_season.LIVE_MEMORY_SUBDIR, run_id)
    else:
        # A dry run is only honest if it writes nothing at all: an earlier
        # revision accepted --no-write and then rewrote the committed book
        # anyway, which is worse than having no dry run. The artifacts are
        # built in a scratch directory and thrown away.
        run_dir = tempfile.mkdtemp(prefix="rollover-dryrun-")
    extra = {
        "kind": "forward",
        "plan_date": plan_date,
        "settle_sessions": settled_sessions,
        "plan_history": list(book.sessions_planned),
        "last_verified_equity_session": md.dates[-1],
        "last_official_observation": max(
            (s.last_date() for s in feed.series.values()), default=""),
        "horizon_sessions": horizon,
        "status": status,
        "rollover": {
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calendar_authority": "FRED SP500 observation dates",
            "price_print_source": overlay.get("source", ""),
            "price_print_class": (overlay.get("ledger") or {}).get("source_class", ""),
            "price_print_redistribution_status": (
                overlay.get("ledger") or {}).get("redistribution_status", ""),
            "print_ledger_file": (overlay.get("ledger") or {}).get("file", ""),
            "print_ledger_sha256": (overlay.get("ledger") or {}).get("sha256", ""),
            "sessions_replayed": sessions,
            "extended_sessions": [row["session"] for row in overlay.get("extended", [])],
            "blocked_sessions": overlay.get("blocked", []),
            "replaced_forward_fill_bars": len(overlay.get("replaced", [])),
            "official_notional_share_pct": (round(100.0 * official_notional / total_notional, 4)
                                            if total_notional else None),
            "official_notional_basis": ("EXCHANGE-PUBLISHED and OFFICIAL reference "
                                        "prices over all settled notional; the strict "
                                        "official-price gate is separate and stays "
                                        "closed until redistribution permission exists"),
        },
        "ladder": ladder,
        "pending_intents": [i.to_row() for i in pending],
        "projected_sessions": sorted({i.intended_session for i in pending}),
        "benchmark": benchmark,
        "leaderboard": board,
        "verification_summary": {"verdict": verification["verdict"],
                                 "checks": verification["checks"],
                                 "failures": verification["failure_count"]},
        "coverage": live_season._coverage(book, board),
    }
    manifest = live.write_live_run(book, run_dir, marks, settlements, extra=extra)

    reports = {r["username"]: live.participant_report(book, r["username"], benchmark)
               for r in board}
    _write_json(os.path.join(run_dir, "leaderboard.json"),
                {"leaderboard": board, "benchmark": benchmark,
                 "rank_metric": "total_return_pct", "status": status})
    _write_json(os.path.join(run_dir, "verification.json"), verification)
    _write_json(os.path.join(run_dir, "reports.json"), reports)
    _write_json(os.path.join(run_dir, "official_sources.json"),
                {"series": feed.provenance(),
                 "auxiliary": live.AUXILIARY_OFFICIAL_ENDPOINTS,
                 "missing": feed.missing,
                 "projection": {"source": live.PROJECTION_SOURCE,
                                "closures": sorted(live.PROJECTED_CLOSURES),
                                "early_closes": sorted(live.PROJECTED_EARLY_CLOSES),
                                "limit": live.PROJECTION_LIMIT}})
    if write:
        ladder_payload = _write_ladder(root, manifest, ladder, overlay, pending)
        _append_history(root, manifest, ladder)
        live_season.write_site_payload(root)
    else:
        run_dir = ""
        ladder_payload = ""
    return {"run_id": run_id, "run_dir": os.path.abspath(run_dir) if run_dir else "",
            "dry_run": not write,
            "run_dir_rel": (os.path.relpath(run_dir, live_season.REPO_ROOT)
                            if run_dir else ""),
            "manifest": manifest, "ladder": ladder, "ladder_path": ladder_payload,
            "pending": [i.to_row() for i in pending], "benchmark": benchmark,
            "leaderboard": board, "verification": verification,
            "overlay": {k: v for k, v in overlay.items() if k != "session_evidence"}}


def _append_history(root: str, manifest: dict, ladder: Sequence[dict]) -> str:
    """One line per rollover run, append-only.

    The book directory is rewritten on every run, deliberately: it is the
    current state, and a reader diffing it should see only what actually
    changed. The history file is the opposite - it is the record that a session
    was settled on a particular day from a particular ledger hash, which is what
    makes "it settled itself while nobody was looking" checkable afterwards.
    """
    path = os.path.join(root, live_season.LIVE_MEMORY_SUBDIR, HISTORY_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    roll = manifest.get("rollover", {})
    row = {
        "generated_utc": roll.get("generated_utc"),
        "run_id": manifest.get("run_id"),
        "plan_date": manifest.get("plan_date"),
        "status": manifest.get("status"),
        "sessions_settled": [r["session"] for r in ladder if r["status"] == "SETTLED"],
        "sessions_waiting": [r["session"] for r in ladder
                             if r["status"] == "WAITING-FOR-PRINTS"],
        "price_print_source": roll.get("price_print_source"),
        "price_print_class": roll.get("price_print_class"),
        "print_ledger_sha256": roll.get("print_ledger_sha256"),
        "fills": sum(int(r.get("filled") or 0) for r in ladder),
        "notional_usd": round(sum(float(r.get("notional_usd") or 0.0) for r in ladder), 2),
        "independence": ("settled without a human in the loop by "
                         ".github/workflows/live-rollover.yml" if os.environ.get("GITHUB_ACTIONS")
                         else "settled by a local run of sim.cli rollover"),
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return os.path.relpath(path, live_season.REPO_ROOT)


def _write_ladder(root: str, manifest: dict, ladder: Sequence[dict], overlay: dict,
                  pending: Sequence[live.Intent]) -> str:
    path = os.path.join(root, live_season.LIVE_MEMORY_SUBDIR, LADDER_FILE)
    payload = {
        "generated_utc": manifest.get("rollover", {}).get("generated_utc"),
        "run_id": manifest.get("run_id"),
        "plan_date": manifest.get("plan_date"),
        "status": manifest.get("status"),
        "calendar_authority": manifest.get("rollover", {}).get("calendar_authority"),
        "price_print_source": manifest.get("rollover", {}).get("price_print_source"),
        "price_print_class": manifest.get("rollover", {}).get("price_print_class"),
        "ladder": list(ladder),
        "sessions_settled": [row["session"] for row in ladder
                             if row["status"] == "SETTLED"],
        "sessions_waiting": [row["session"] for row in ladder
                             if row["status"] == "WAITING-FOR-PRINTS"],
        "blocked_sessions": overlay.get("blocked", []),
        "extended_sessions": [row["session"] for row in overlay.get("extended", [])],
        "pending_intents": [
            {"intent_id": i.intent_id, "participant": i.participant,
             "symbol": i.symbol, "side": i.side, "quantity": i.quantity,
             "intended_session": i.intended_session, "status": i.status,
             "reason": i.rationale}
            for i in pending],
        "note": ("One row per session the book has stepped through. A session settles "
                 "only when every trade it contains has a published print; a session "
                 "with no print says so instead of showing a modelled fill."),
    }
    _write_json(path, payload)
    return os.path.relpath(path, live_season.REPO_ROOT)


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")


def load_ladder(root: str = memory.DEFAULT_ROOT) -> dict:
    path = os.path.join(root, live_season.LIVE_MEMORY_SUBDIR, LADDER_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


__all__ = ["step", "load_ladder", "ladder_row", "last_fred_session",
           "LADDER_FILE", "HISTORY_FILE"]
