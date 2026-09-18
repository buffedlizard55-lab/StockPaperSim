"""One trade ledger for every book in this repository.

The brief asks for a single place where *every* strategy's placed trades can be
read: entry, exit, dates, prices, PnL, and the evidence behind each price.  The
project has four books - Season 1, Season 2, the Live Book and the Official
Auction Book - and each was built at a different time with its own event schema.
Rather than rewrite them, this module normalises their closed trades into one
record shape and publishes the result, together with the two things a reader
needs in order to weigh it:

* **where each executed price came from** - the publisher, the file, the SHA-256,
  the field, and whether the price is the publisher's own number (``OFFICIAL``),
  a number derived from an official observation (``OFFICIAL-DERIVED``), or an
  aggregator's copy (``SECONDARY``);
* **what share of traded notional each class carries** - published as a number,
  so no page in this repository can imply an official-price result it cannot
  show.

Nothing here recomputes a P&L: the trades are the books' own records, read back
from the memory store.  The ledger's job is to make them comparable and to keep
the provenance attached, not to second-guess the engines - that is what
``scripts/independent_audit.py``, ``scripts/independent_audit_season2.py`` and
``scripts/independent_audit_forward.py`` are for.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .calendar import REPO_ROOT

MEMORY_ROOT = os.path.join(REPO_ROOT, "memory")
LIVE_ROOT = os.path.join(MEMORY_ROOT, "live")
OFFICIAL_ROOT = os.path.join(MEMORY_ROOT, "official")

#: The class of an executed price, most official first.  ``OFFICIAL`` means the
#: publisher of record printed the number that filled the order; nothing else
#: qualifies, and no derived number is ever presented as a print.
PRICE_CLASSES = ("OFFICIAL", "OFFICIAL-DERIVED", "OFFICIAL-PUBLISHER / FRED-REPUBLISHED",
                 "SECONDARY", "MODELLED", "UNKNOWN")

_SC_RANK = {"OFFICIAL": 0, "OFFICIAL-DERIVED": 1,
            "OFFICIAL-PUBLISHER / FRED-REPUBLISHED": 1,
            "SECONDARY": 2, "MODELLED": 3, "UNKNOWN": 4}


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    rows: List[dict] = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(str(end)[:10])
                - dt.date.fromisoformat(str(start)[:10])).days
    except (TypeError, ValueError):
        return None


def _trade(book: str, participant: str, instrument: str, side: str,
           quantity: float, entry: dict, exit_: dict, pnl: float,
           extra: Optional[dict] = None) -> dict:
    row = {
        "trade_id": f"{book}:{participant}:{entry.get('date')}:{instrument}:"
                    f"{exit_.get('date')}",
        "book": book,
        "participant": participant,
        "instrument": instrument,
        "side": side,
        "quantity": quantity,
        "notional_usd": None,
        "entry_date": entry.get("date"), "entry_price": entry.get("price"),
        "entry_kind": entry.get("kind"), "entry_source_class": entry.get("source_class"),
        "entry_evidence": entry.get("evidence"),
        "exit_date": exit_.get("date"), "exit_price": exit_.get("price"),
        "exit_kind": exit_.get("kind"), "exit_source_class": exit_.get("source_class"),
        "exit_evidence": exit_.get("evidence"),
        "holding_days": _days_between(entry.get("date", ""), exit_.get("date", "")),
        "pnl_usd": None if pnl is None else round(float(pnl), 2),
        "fees_usd": None, "financing_usd": None, "coupon_usd": None,
        "slippage_bps": None, "participation_pct": None,
        "liquidity": None,
        "price_class": _worst_class(entry.get("source_class"),
                                    exit_.get("source_class")),
        "official_execution_price": (entry.get("source_class") == "OFFICIAL"
                                     and exit_.get("source_class") == "OFFICIAL"),
        "verification": [],
    }
    notional = None
    try:
        if quantity and entry.get("price"):
            notional = abs(float(quantity)) * float(entry["price"])
            if instrument and len(str(instrument)) == 9 and str(instrument).isdigit():
                notional = abs(float(quantity)) * float(entry["price"]) / 100.0
    except (TypeError, ValueError):
        notional = None
    row["notional_usd"] = None if notional is None else round(notional, 2)
    if row["pnl_usd"] is not None and notional:
        row["return_on_notional_pct"] = round(100.0 * row["pnl_usd"] / notional, 6)
    else:
        row["return_on_notional_pct"] = None
    if extra:
        row.update(extra)
    row["price_class"] = _worst_class(row.get("entry_source_class"),
                                      row.get("exit_source_class"))
    row["official_execution_price"] = (row.get("entry_source_class") == "OFFICIAL"
                                       and row.get("exit_source_class") == "OFFICIAL")
    return row


def _worst_class(*classes: Optional[str]) -> str:
    ranked = sorted((c or "UNKNOWN" for c in classes),
                    key=lambda c: _SC_RANK.get(c, 4))
    return ranked[-1] if ranked else "UNKNOWN"


# --------------------------------------------------------------------------
# The Official Auction Book
# --------------------------------------------------------------------------

def from_official_run(run_dir: str) -> List[dict]:
    """Closed trades of the official auction book, with their evidence intact."""
    trips = _read_jsonl(os.path.join(run_dir, "trips.jsonl.gz"))
    out: List[dict] = []
    for trip in trips:
        entry_evidence = trip.get("entry_evidence") or {}
        exit_evidence = trip.get("exit_evidence") or {}
        entry = {"date": trip.get("entry_date"), "price": trip.get("entry_price_per100"),
                 "kind": trip.get("entry_kind"), "source_class": "OFFICIAL",
                 "evidence": entry_evidence}
        exit_ = {"date": trip.get("exit_date"), "price": trip.get("exit_price_per100"),
                 "kind": trip.get("exit_kind"),
                 "source_class": ("OFFICIAL" if trip.get("exit_kind") ==
                                  "MATURITY-REDEMPTION" else "OFFICIAL-DERIVED"),
                 "evidence": exit_evidence}
        row = _trade("Official Auction Book", trip.get("participant", ""),
                     trip.get("cusip", ""), trip.get("direction", "long"),
                     trip.get("face"), entry, exit_, trip.get("pnl"),
                     extra={"instrument_name": trip.get("security_term"),
                            "instrument_type": trip.get("security_type"),
                            "strategy_trip_id": trip.get("trip_id")})
        row["fees_usd"] = trip.get("fees")
        row["financing_usd"] = trip.get("financing")
        row["coupon_usd"] = trip.get("coupon_income")
        row["liquidity"] = trip.get("liquidity")
        # The executed price on the primary leg is the Treasury's own published
        # price per $100; the evidence row carries the URL, the SHA-256 of the
        # response and the field name, so a reader can re-fetch and re-check.
        for leg, evidence in (("entry", entry_evidence), ("exit", exit_evidence)):
            if not evidence:
                continue
            row["verification"].append({
                "leg": leg,
                "publisher": evidence.get("publisher"),
                "field": evidence.get("field"),
                "value": evidence.get("value"),
                "source": evidence.get("source"),
                "sha256": evidence.get("sha256"),
                "derivation": evidence.get("derivation"),
                "cross_checked_by": evidence.get("cross_checked_by")})
        row["official_execution_price"] = (
            trip.get("exit_kind") == "MATURITY-REDEMPTION"
            and trip.get("entry_kind") in ("PRIMARY-AUCTION",))
        row["price_class"] = ("OFFICIAL" if row["official_execution_price"]
                              else "OFFICIAL-DERIVED")
        out.append(row)
    return out


# --------------------------------------------------------------------------
# The Live Book (and the Season books, which use the same trip schema)
# --------------------------------------------------------------------------

def from_live_run(run_dir: str, book: str = "Live Book") -> List[dict]:
    """Closed trades of a live-style book, joined to the fills that carried them."""
    trips = _read_jsonl(os.path.join(run_dir, "trips.jsonl.gz"))
    fills = _read_jsonl(os.path.join(run_dir, "fills.jsonl.gz"))
    by_key: Dict[Tuple[str, str, str, str], List[dict]] = {}
    for fill in fills:
        key = (fill.get("participant", ""), fill.get("symbol", ""),
               fill.get("date", ""), "buy" if fill.get("side") == "buy" else "sell")
        by_key.setdefault(key, []).append(fill)
    out: List[dict] = []
    for trip in trips:
        if trip.get("status") != "closed":
            continue
        participant = trip.get("participant", "")
        symbol = trip.get("symbol", "")
        entry_fills = by_key.get((participant, symbol, trip.get("entry_date"), "buy")
                                 if trip.get("direction") == "long" else
                                 (participant, symbol, trip.get("entry_date"), "sell"), [])
        exit_fills = by_key.get((participant, symbol, trip.get("exit_date"), "sell")
                                if trip.get("direction") == "long" else
                                (participant, symbol, trip.get("exit_date"), "buy"), [])
        entry_fill = entry_fills[0] if entry_fills else {}
        exit_fill = exit_fills[0] if exit_fills else {}
        entry = {"date": trip.get("entry_date"), "price": trip.get("entry_price"),
                 "kind": "VENUE-SIMULATED-FILL",
                 "source_class": entry_fill.get("reference_source_class", "SECONDARY"),
                 "evidence": {
                     "reference_close": entry_fill.get("reference_close"),
                     "reference_date": entry_fill.get("date"),
                     "reference_file": entry_fill.get("reference_file"),
                     "reference_sha256": entry_fill.get("reference_sha256"),
                     "reference_provider": entry_fill.get("reference_provider"),
                     "reference_url": entry_fill.get("reference_url"),
                     "decision_price": entry_fill.get("decision_price"),
                     "bid": entry_fill.get("bid"), "ask": entry_fill.get("ask"),
                     "mid": entry_fill.get("mid"),
                     "spread_cost": entry_fill.get("spread_cost"),
                     "impact_cost": entry_fill.get("impact_cost"),
                     "drift_cost": entry_fill.get("drift_cost"),
                     "implementation_shortfall": entry_fill.get(
                         "implementation_shortfall"),
                     "execution_cost_bps": entry_fill.get("execution_cost_bps")}}
        exit_ = {"date": trip.get("exit_date"), "price": trip.get("exit_price"),
                 "kind": "VENUE-SIMULATED-FILL",
                 "source_class": exit_fill.get("reference_source_class", "SECONDARY"),
                 "evidence": {
                     "reference_close": exit_fill.get("reference_close"),
                     "reference_date": exit_fill.get("date"),
                     "reference_file": exit_fill.get("reference_file"),
                     "reference_sha256": exit_fill.get("reference_sha256"),
                     "reference_provider": exit_fill.get("reference_provider"),
                     "reference_url": exit_fill.get("reference_url"),
                     "bid": exit_fill.get("bid"), "ask": exit_fill.get("ask"),
                     "mid": exit_fill.get("mid"),
                     "spread_cost": exit_fill.get("spread_cost"),
                     "impact_cost": exit_fill.get("impact_cost"),
                     "execution_cost_bps": exit_fill.get("execution_cost_bps")}}
        row = _trade(book, participant, symbol, trip.get("direction", "long"),
                     trip.get("quantity"), entry, exit_,
                     trip.get("net_pnl_usd"),
                     extra={"strategy_reason_entry": trip.get("entry_reason"),
                            "strategy_reason_exit": trip.get("exit_reason"),
                            "gross_pnl_usd": trip.get("gross_pnl_usd")})
        row["fees_usd"] = trip.get("fees_usd")
        row["slippage_bps"] = (exit_fill.get("slippage_bps")
                               if exit_fill else entry_fill.get("slippage_bps"))
        row["participation_pct"] = trip.get("entry_participation_pct")
        row["liquidity"] = {
            "participation_pct_of_session_volume": trip.get("entry_participation_pct"),
            "exit_participation_pct_of_session_volume": trip.get("exit_participation_pct"),
            "reference_volume": entry_fill.get("reference_volume"),
            "reference_high": entry_fill.get("reference_high"),
            "reference_low": entry_fill.get("reference_low"),
            "bid_size_available": entry_fill.get("bid"),
            "spread_cost_usd": entry_fill.get("spread_cost"),
            "impact_cost_usd": entry_fill.get("impact_cost"),
            "implementation_shortfall_usd": entry_fill.get("implementation_shortfall"),
        }
        for leg, evidence in (("entry", entry["evidence"]), ("exit", exit_["evidence"])):
            row["verification"].append({
                "leg": leg, "publisher": evidence.get("reference_provider"),
                "field": "daily bar close", "value": evidence.get("reference_close"),
                "file": evidence.get("reference_file"),
                "sha256": evidence.get("reference_sha256"),
                "source": evidence.get("reference_url"),
                "note": ("the bar is the collected vendor file; the fill price is "
                         "this project's venue model, not the vendor's number")})
        out.append(row)
    return out


def from_season_run(run_dir: str, book: str) -> List[dict]:
    """Season 1 and Season 2 trips, re-derived from their own fill streams.

    Those books have no ``trips`` stream: their round trips are re-derived from
    the fill tape by the same average-cost rule the ledger uses, which is what
    their published reports do.  Their venue is a *model* - the fills are the
    engine's own simulated executions and their price files are aggregated daily
    bars - so every trade is labelled ``MODELLED`` and carries the model note
    rather than a publisher's link it does not have.
    """
    from . import ledger

    events = os.path.join(run_dir, "events")
    fills = _read_jsonl(os.path.join(events, "fills.jsonl.gz"))
    if not fills:
        return []
    by_participant: Dict[str, List[dict]] = {}
    for fill in fills:
        by_participant.setdefault(fill.get("participant", ""), []).append(fill)
    out: List[dict] = []
    for participant, rows in by_participant.items():
        rows.sort(key=lambda r: r.get("date", ""))
        for trip in ledger.build_round_trips(rows):
            if trip.get("status") != "closed":
                continue
            evidence = {
                "note": ("the fill is this project's venue model (depth, spread, "
                         "impact, dated fees) executed inside a real daily bar; the "
                         "bar itself is a collected file and the season's pages "
                         "carry its SHA-256. No publisher priced this fill.")}
            entry = {"date": trip.get("entry_date"), "price": trip.get("entry_price"),
                     "kind": "VENUE-SIMULATED-FILL", "source_class": "MODELLED",
                     "evidence": dict(evidence,
                                      reference_close=trip.get("entry_reference_close"))}
            exit_ = {"date": trip.get("exit_date"), "price": trip.get("exit_price"),
                     "kind": "VENUE-SIMULATED-FILL", "source_class": "MODELLED",
                     "evidence": dict(evidence,
                                      reference_close=trip.get("exit_reference_close"))}
            row = _trade(book, participant, trip.get("symbol", ""),
                         trip.get("direction", "long"), trip.get("quantity"),
                         entry, exit_, trip.get("net_pnl_usd"),
                         extra={"strategy_reason_entry": trip.get("entry_reason"),
                                "strategy_reason_exit": trip.get("exit_reason"),
                                "gross_pnl_usd": trip.get("gross_pnl_usd")})
            row["fees_usd"] = trip.get("fees_usd")
            row["price_class"] = "MODELLED"
            row["official_execution_price"] = False
            out.append(row)
    return out


# --------------------------------------------------------------------------
# Coverage: what share of the tape is an official price
# --------------------------------------------------------------------------

def coverage(trades: Sequence[dict]) -> dict:
    """Share of closed-trade notional by price class, per book and overall."""
    def blank() -> dict:
        return {"trades": 0, "notional_usd": 0.0, "pnl_usd": 0.0,
                "by_price_class": {}, "by_book": {}}

    totals = blank()
    for trade in trades:
        notional = float(trade.get("notional_usd") or 0.0)
        pnl = float(trade.get("pnl_usd") or 0.0)
        klass = trade.get("price_class") or "UNKNOWN"
        book = trade.get("book") or "unknown"
        totals["trades"] += 1
        totals["notional_usd"] += notional
        totals["pnl_usd"] += pnl
        for bucket, key in ((totals["by_price_class"], klass),
                            (totals["by_book"], book)):
            row = bucket.setdefault(key, {"trades": 0, "notional_usd": 0.0,
                                          "pnl_usd": 0.0})
            row["trades"] += 1
            row["notional_usd"] += notional
            row["pnl_usd"] += pnl
    for bucket in (totals["by_price_class"], totals["by_book"]):
        for row in bucket.values():
            row["notional_usd"] = round(row["notional_usd"], 2)
            row["pnl_usd"] = round(row["pnl_usd"], 2)
            row["notional_share_pct"] = (round(100.0 * row["notional_usd"]
                                               / totals["notional_usd"], 4)
                                         if totals["notional_usd"] else None)
    totals["notional_usd"] = round(totals["notional_usd"], 2)
    totals["pnl_usd"] = round(totals["pnl_usd"], 2)
    official = totals["by_price_class"].get("OFFICIAL", {})
    totals["official_executed_notional_pct"] = official.get("notional_share_pct")
    totals["official_trades"] = official.get("trades", 0)
    return totals


def per_participant(trades: Sequence[dict]) -> List[dict]:
    rows: Dict[str, dict] = {}
    for trade in trades:
        row = rows.setdefault(trade["participant"], {
            "participant": trade["participant"], "books": set(), "trades": 0,
            "pnl_usd": 0.0, "notional_usd": 0.0, "wins": 0, "losses": 0,
            "official_trades": 0, "best_pnl": None, "worst_pnl": None,
            "price_classes": set()})
        row["books"].add(trade["book"])
        row["price_classes"].add(trade.get("price_class") or "UNKNOWN")
        row["trades"] += 1
        pnl = float(trade.get("pnl_usd") or 0.0)
        row["pnl_usd"] += pnl
        row["notional_usd"] += float(trade.get("notional_usd") or 0.0)
        row["wins" if pnl > 0 else "losses"] += 1
        if trade.get("official_execution_price"):
            row["official_trades"] += 1
        row["best_pnl"] = pnl if row["best_pnl"] is None else max(row["best_pnl"], pnl)
        row["worst_pnl"] = pnl if row["worst_pnl"] is None else min(row["worst_pnl"], pnl)
    out: List[dict] = []
    for row in rows.values():
        row["books"] = sorted(row["books"])
        row["price_classes"] = sorted(row["price_classes"])
        row["pnl_usd"] = round(row["pnl_usd"], 2)
        row["notional_usd"] = round(row["notional_usd"], 2)
        row["win_rate_pct"] = (round(100.0 * row["wins"] / row["trades"], 2)
                               if row["trades"] else None)
        out.append(row)
    out.sort(key=lambda r: -r["pnl_usd"])
    return out


# --------------------------------------------------------------------------
# Writing the ledger
# --------------------------------------------------------------------------

def _write_gzip_jsonl(path: str, rows: Sequence[dict]) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = 0
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        for row in rows:
            line = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
            raw += len(line.encode("utf-8")) + 1
            handle.write(line + "\n")
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return {"file": os.path.relpath(path, REPO_ROOT), "rows": len(rows),
            "bytes_on_disk": size, "bytes_uncompressed": raw,
            "bytes_per_row_on_disk": round(size / len(rows), 2) if rows else 0.0,
            "sha256": digest}


def write_ledger(trades: Sequence[dict], out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    trades = sorted(trades, key=lambda t: (t.get("exit_date") or "",
                                           t["participant"], t["trade_id"]))
    streams = {"trades": _write_gzip_jsonl(
        os.path.join(out_dir, "trades.jsonl.gz"), trades)}
    csv_path = os.path.join(out_dir, "trades.csv")
    fields = ["trade_id", "book", "participant", "instrument", "instrument_name",
              "side", "quantity", "notional_usd", "entry_date", "entry_price",
              "entry_kind", "entry_source_class", "exit_date", "exit_price",
              "exit_kind", "exit_source_class", "holding_days", "pnl_usd",
              "return_on_notional_pct", "fees_usd", "financing_usd", "coupon_usd",
              "slippage_bps", "participation_pct", "price_class",
              "official_execution_price"]
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)
    coverage_row = coverage(trades)
    with open(os.path.join(out_dir, "coverage.json"), "w", encoding="utf-8") as handle:
        json.dump(coverage_row, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    people = per_participant(trades)
    with open(os.path.join(out_dir, "participants.json"), "w", encoding="utf-8") as handle:
        json.dump(people, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    manifest = {
        "generated_utc": _utcnow(),
        "books": sorted({t["book"] for t in trades}),
        "trades": len(trades),
        "participants": len(people),
        "coverage": coverage_row,
        "storage": dict(list(streams.items()) + [("csv", {"file": os.path.relpath(
            csv_path, REPO_ROOT), "rows": len(trades)})]),
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    return manifest


def collect_trades(memory_root: str = MEMORY_ROOT,
                   include_seasons: bool = False) -> List[dict]:
    """Every closed trade this repository holds, newest books first.

    ``include_seasons`` also walks the Season 1 and Season 2 run directories:
    their event schema is the same trip/fill shape as the Live Book's, so the
    same reader works on them, and the brief's "every strategy" means those too.
    """
    trades: List[dict] = []
    official_root = os.path.join(memory_root, "official")
    if os.path.isdir(official_root):
        for name in sorted(os.listdir(official_root)):
            run_dir = os.path.join(official_root, name)
            if os.path.isfile(os.path.join(run_dir, "trips.jsonl.gz")):
                trades.extend(from_official_run(run_dir))
    live_root = os.path.join(memory_root, "live")
    if os.path.isdir(live_root):
        for name in sorted(os.listdir(live_root)):
            run_dir = os.path.join(live_root, name)
            if os.path.isfile(os.path.join(run_dir, "trips.jsonl.gz")):
                book = ("Live Book (rehearsal)" if "rehearsal" in name
                        else "Live Book (forward)")
                trades.extend(from_live_run(run_dir, book))
    if include_seasons:
        runs_root = os.path.join(memory_root, "runs")
        if os.path.isdir(runs_root):
            for name in sorted(os.listdir(runs_root)):
                run_dir = os.path.join(runs_root, name)
                if name.startswith("season1"):
                    trades.extend(from_season_run(run_dir, "Season 1"))
                elif name.startswith("season2"):
                    trades.extend(from_season_run(run_dir, "Season 2"))
                elif os.path.isfile(os.path.join(run_dir, "trips.jsonl.gz")):
                    trades.extend(from_live_run(run_dir, "Live Book"))
    return trades
