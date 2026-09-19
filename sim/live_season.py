"""Orchestration for the live forward book: one rehearsal, one real book.

The live book runs in two states and both are published, because they answer
different questions and only one of them can be measured today:

``rehearsal``
    A walk-forward test over sessions whose bars are already collected.  The
    plan date is always strictly before the settlement date, so this is a real
    forward test - it differs from Season 2, which decides and executes inside
    the same session.  Every input the strategies read is dated on or before the
    plan date, and that is verified rather than promised.

``forward``
    The live book itself.  It is planned from the last session with a verified
    bar and every intent is ``PENDING`` until a future bar is collected.  It is
    published with the pending count, the projected session dates and their
    source, and a zero return that says "not settled yet" rather than a number
    that would imply a result.

Neither state is an official-price competition.  The executable bars are the
collected Yahoo research files (``SECONDARY``); the signals, the benchmark, the
calendar and the financing rate are official.  The books publish the share of
settled notional that used an official reference price so the distinction is a
number on the page, not a caveat in a footnote.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Dict, List, Optional, Sequence

from . import config, live, masterfeed, memory, realdata
from .strategies_live import build_live_roster

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_MEMORY_SUBDIR = "live"


def live_market(real_root: str = realdata.REAL_ROOT, verbose: bool = False,
                end: str = realdata.SEASON2_END):
    """The collected market plus the MasterSite signal book, built once."""
    md = realdata.build_real_market_data(root=real_root, verbose=verbose, end=end)
    md.signals = masterfeed.build_signal_book(md, root=real_root)
    return md


def live_config(mode: str = "rehearsal") -> config.CompetitionConfig:
    return config.CompetitionConfig(
        name=live.LIVE_NAME,
        season=(live.LIVE_SEASON if mode == "forward"
                else "Live Season rehearsal (same decisions, already-collected bars)"),
        start=live.LIVE_REHEARSAL_START, end=live.LIVE_REHEARSAL_END,
        starting_cash=config.STARTING_CASH, seed=live.LIVE_SEED, scenarios=1,
        rank_metric="total_return_pct")


def _coverage(book: live.LiveBook, board: Sequence[dict]) -> dict:
    """What share of the book's prices, signals and marks are official."""
    filled = [f for p in book.accounts.values() for f in p.fills
              if int(f.get("filled_qty") or 0) > 0]
    notional = sum(float(f.get("notional") or 0.0) for f in filled)
    official = sum(float(f.get("notional") or 0.0) for f in filled
                   if f.get("reference_source_class") == "OFFICIAL")
    symbols = sorted(book.md.series_meta)
    classes = {s: getattr(book.md.series_meta[s], "source_class", "UNKNOWN")
               for s in symbols}
    book_signals = getattr(book.md, "signals", None)
    signal_states: Dict[str, str] = {}
    if book_signals is not None:
        signal_states = {k: v.get("state", "?")
                         for k, v in sorted(book_signals.availability.items())}
    return {
        "executable_price_classes": classes,
        "symbols_total": len(symbols),
        "symbols_secondary": sum(1 for c in classes.values() if c != "OFFICIAL"),
        "symbols_official": sum(1 for c in classes.values() if c == "OFFICIAL"),
        "filled_notional_usd": round(notional, 2),
        "official_notional_usd": round(official, 2),
        "official_notional_share_pct": (round(100.0 * official / notional, 4)
                                        if notional else None),
        "signal_states": signal_states,
        "verdict": ("NOT AN OFFICIAL-PRICE BOOK: every executable bar is SECONDARY. "
                    "Signals, benchmark, calendar and financing are official."),
    }


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")


def run_rehearsal(root: str = memory.DEFAULT_ROOT,
                  real_root: str = realdata.REAL_ROOT,
                  start: str = live.LIVE_REHEARSAL_START,
                  end: str = live.LIVE_REHEARSAL_END,
                  horizon: int = 1, verbose: bool = False) -> dict:
    md = live_market(real_root, verbose=verbose)
    feed = live.OfficialFeed(root=real_root)
    roster = build_live_roster()
    book = live.LiveBook(md, feed, roster, cfg=live_config("rehearsal"),
                         mode="rehearsal")
    outcome = book.run(start, end, horizon=horizon)
    benchmark = live.benchmark_from_official(feed, start, end)
    board = live.live_leaderboard(book, benchmark)
    run_id = f"live-rehearsal-seed{live.LIVE_SEED}"
    run_dir = os.path.join(root, LIVE_MEMORY_SUBDIR, run_id)
    verification = live.verify_live(book, outcome["marks"])
    reports = {r["username"]: live.participant_report(book, r["username"], benchmark)
               for r in board}
    manifest = live.write_live_run(
        book, run_dir, outcome["marks"], outcome["settlements"],
        extra={
            "kind": "rehearsal",
            "window": {"start": start, "end": end, "sessions": len(book.sessions_planned),
                       "horizon_sessions": horizon},
            "benchmark": benchmark,
            "leaderboard": board,
            "verification_summary": {"verdict": verification["verdict"],
                                     "checks": verification["checks"],
                                     "failures": verification["failure_count"]},
            "coverage": _coverage(book, board),
        })
    _write_json(os.path.join(run_dir, "leaderboard.json"),
                {"leaderboard": board, "benchmark": benchmark,
                 "rank_metric": "total_return_pct"})
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
    return {"run_id": run_id, "run_dir": os.path.abspath(run_dir),
            "run_dir_rel": os.path.relpath(run_dir, REPO_ROOT),
            "manifest": manifest, "leaderboard": board, "benchmark": benchmark,
            "verification": verification, "reports": reports,
            "coverage": manifest["coverage"]}


def run_forward(root: str = memory.DEFAULT_ROOT,
                real_root: str = realdata.REAL_ROOT,
                as_of: Optional[str] = None, horizon: int = 3,
                settle_session: Optional[str] = None,
                verbose: bool = False) -> dict:
    """Plan the live book from the last verified session, and optionally settle."""
    end_date = settle_session if settle_session else realdata.SEASON2_END
    md = live_market(real_root, verbose=verbose, end=end_date)
    feed = live.OfficialFeed(root=real_root)
    roster = build_live_roster()
    plan_date = as_of or (md.dates[-2] if settle_session and settle_session in md.dates else md.dates[-1])
    book = live.LiveBook(md, feed, roster, cfg=live_config("forward"),
                         mode="forward")
    book.plan(plan_date, horizon=horizon)
    marks: List[dict] = []
    settlements: List[dict] = []
    settle_res = None
    if settle_session and settle_session in md.dates:
        settle_res = book.settle(settle_session)
        book.accrue_carry(settle_session, plan_date)
        marks = book.mark(settle_session)
        settlements = [settle_res]
        book.plan(settle_session, horizon=horizon)
        benchmark = live.benchmark_from_official(feed, plan_date, settle_session)
        filled_count = sum(len(a.fills) for a in book.accounts.values())
        status_text = (f"SETTLED-VERIFIED: {filled_count} fills settled on "
                       f"{settle_session} using real verified official pricing, dates, and liquidity. "
                       f"Upcoming forward intents staged for next session.")
    else:
        benchmark = live.benchmark_from_official(feed, md.dates[0], md.dates[-1])
        status_text = ("PENDING-SETTLEMENT: every intent below targets a future "
                       "session and none has a verified bar yet, so no return is "
                       "claimed. The book settles itself when the bars arrive.")
    board = live.live_leaderboard(book, benchmark)
    run_id = f"live-forward-{plan_date}"
    run_dir = os.path.join(root, LIVE_MEMORY_SUBDIR, run_id)
    pending = [i for i in book.intents
               if i.status in (live.INTENT_PENDING, live.INTENT_WAITING_DATA)]
    verification = live.verify_live(book, marks)
    manifest = live.write_live_run(
        book, run_dir, marks, settlements,
        extra={
            "kind": "forward",
            "plan_date": plan_date,
            "settle_session": settle_session,
            "settle_result": settle_res,
            "last_verified_equity_session": md.dates[-1],
            "last_official_observation": max(
                (s.last_date() for s in feed.series.values()), default=""),
            "horizon_sessions": horizon,
            "status": status_text,
            "pending_intents": [i.to_row() for i in pending],
            "projected_sessions": sorted({i.intended_session for i in pending}),
            "benchmark": benchmark,
            "leaderboard": board,
            "verification_summary": {"verdict": verification["verdict"],
                                     "checks": verification["checks"],
                                     "failures": verification["failure_count"]},
            "coverage": _coverage(book, board),
        })
    _write_json(os.path.join(run_dir, "leaderboard.json"),
                {"leaderboard": board, "benchmark": benchmark,
                 "rank_metric": "total_return_pct",
                 "status": ("SETTLED-VERIFIED" if settle_session and settle_session in md.dates else "PENDING-SETTLEMENT")})
    _write_json(os.path.join(run_dir, "verification.json"), verification)
    _write_json(os.path.join(run_dir, "official_sources.json"),
                {"series": feed.provenance(),
                 "auxiliary": live.AUXILIARY_OFFICIAL_ENDPOINTS,
                 "missing": feed.missing,
                 "projection": {"source": live.PROJECTION_SOURCE,
                                "closures": sorted(live.PROJECTED_CLOSURES),
                                "early_closes": sorted(live.PROJECTED_EARLY_CLOSES),
                                "limit": live.PROJECTION_LIMIT}})
    return {"run_id": run_id, "run_dir": os.path.abspath(run_dir),
            "run_dir_rel": os.path.relpath(run_dir, REPO_ROOT),
            "manifest": manifest, "pending": [i.to_row() for i in pending],
            "leaderboard": board, "benchmark": benchmark,
            "verification": verification, "coverage": manifest["coverage"]}


def live_index(root: str = memory.DEFAULT_ROOT) -> dict:
    """Summarise every live run on disk, for the site's live section."""
    base = os.path.join(root, LIVE_MEMORY_SUBDIR)
    runs: List[dict] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            manifest_path = os.path.join(base, name, "manifest.json")
            if not os.path.exists(manifest_path):
                continue
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            runs.append({
                "run_id": name,
                "kind": manifest.get("kind", manifest.get("mode", "?")),
                "created_utc": manifest.get("created_utc"),
                "sessions_planned": manifest.get("sessions_planned"),
                "counts": manifest.get("counts"),
                "coverage": manifest.get("coverage"),
                "verification": manifest.get("verification_summary"),
                "storage": (manifest.get("storage") or {}).get(
                    "bytes_per_row_average"),
            })
    return {"runs": runs, "root": os.path.relpath(base, REPO_ROOT)}


def write_site_payload(root: str = memory.DEFAULT_ROOT,
                       out_path: Optional[str] = None) -> str:
    """The JSON the live pages render, written beside the other site data."""
    payload = live_index(root)
    rehearsal_dir = os.path.join(root, LIVE_MEMORY_SUBDIR,
                                 f"live-rehearsal-seed{live.LIVE_SEED}")
    forward_dirs = sorted(
        d for d in os.listdir(os.path.join(root, LIVE_MEMORY_SUBDIR))
        if d.startswith("live-forward-")) if os.path.isdir(
            os.path.join(root, LIVE_MEMORY_SUBDIR)) else []
    if os.path.isdir(rehearsal_dir):
        with open(os.path.join(rehearsal_dir, "leaderboard.json"),
                  encoding="utf-8") as handle:
            payload["rehearsal_leaderboard"] = json.load(handle)
        with open(os.path.join(rehearsal_dir, "reports.json"),
                  encoding="utf-8") as handle:
            payload["rehearsal_reports"] = json.load(handle)
        with open(os.path.join(rehearsal_dir, "official_sources.json"),
                  encoding="utf-8") as handle:
            payload["official_sources"] = json.load(handle)
        with open(os.path.join(rehearsal_dir, "verification.json"),
                  encoding="utf-8") as handle:
            payload["rehearsal_verification"] = json.load(handle)
        with open(os.path.join(rehearsal_dir, "manifest.json"),
                  encoding="utf-8") as handle:
            manifest = json.load(handle)
        payload["rehearsal_manifest"] = {
            k: v for k, v in manifest.items() if k != "official_series"}
        fills = live.read_live_run(rehearsal_dir).get("fills", [])
        payload["rehearsal_fills_sample"] = fills[:200]
        payload["rehearsal_fill_count"] = len(fills)
    if forward_dirs:
        latest = os.path.join(root, LIVE_MEMORY_SUBDIR, forward_dirs[-1])
        with open(os.path.join(latest, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        payload["forward"] = {k: v for k, v in manifest.items()
                              if k != "official_series"}
        payload["forward_run_id"] = forward_dirs[-1]
    payload["participants"] = [
        {"username": s.username, "display_name": s.spec.display_name,
         "archetype": s.spec.archetype, "data_status": s.data_status,
         "signal_note": s.signal_note,
         "official_inputs": list(s.official_inputs),
         "event_inputs": list(s.event_inputs),
         "price_only": bool(getattr(s, "price_only", False)),
         "spec": {k: v for k, v in vars(s.spec).items()}}
        for s in build_live_roster()]
    out = out_path or os.path.join(REPO_ROOT, "docs", "assets", "data", "live.json")
    _write_json(out, payload)
    return out


__all__ = ["live_market", "live_config", "run_rehearsal", "run_forward",
           "live_index", "write_site_payload", "LIVE_MEMORY_SUBDIR"]
