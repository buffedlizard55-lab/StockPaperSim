"""The verified trade ledger.

The brief asks for every placed trade to be tracked with verified prices, dates,
entries, exits and P&L, with slippage, size relative to the book and liquidity
taken into account - and for all of it to be stored in a data-efficient form for
later analysis.

This module produces exactly that, and it is deliberately built to be *checked*:

* every fill is written with the real session's open/high/low/close/volume, the
  **file and SHA-256** those came from, the participation rate against the real
  volume, and the model's whole cost decomposition;
* every round trip carries entry and exit on real dates, net P&L, the cost
  stack, sessions held and the strategy's own stated reason;
* :func:`verify_ledger` re-derives realised P&L from the raw fill stream with
  average-cost accounting written independently of the engine, and returns any
  discrepancy instead of hiding it.

Data efficiency: fills are written as JSON Lines (one object per line, gzip
optional) and the per-run summary is a single small JSON.  Nothing is
denormalised twice.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LEDGER_FILLS = "ledger_fills.jsonl"
LEDGER_TRIPS = "ledger_trips.jsonl"
LEDGER_SUMMARY = "ledger_summary.json"
LEDGER_CSV = "ledger_fills.csv"


def _open_write(path: str, compress: bool = False):
    if compress:
        return gzip.open(path + ".gz", "wt", encoding="utf-8")
    return open(path, "w", encoding="utf-8")


def _reference(md, symbol: str, date: str) -> dict:
    """Real bar for ``symbol`` on ``date`` plus the file it came from.

    ``md`` may be None: the ledger is written to be readable on its own, and a
    caller who only wants the round-trip arithmetic (a test, or an audit of an
    exported CSV) should not have to rebuild the whole market.
    """
    if md is None:
        return {}
    try:
        t = md.dates.index(date)
        bar = md.bar(symbol, t)
    except (ValueError, KeyError, IndexError):
        return {}
    meta = md.series_meta.get(symbol)
    return {
        "reference_open": bar.open, "reference_high": bar.high,
        "reference_low": bar.low, "reference_close": bar.close,
        "reference_volume": bar.volume,
        "reference_file": meta.path if meta else "",
        "reference_sha256": meta.sha256 if meta else "",
    }


def _bps(a: float, b: float) -> float:
    if not b:
        return 0.0
    return round((a - b) / b * 10000.0, 3)


def enrich_fill(fill: dict, md) -> dict:
    """Attach verified reference prices, participation and provenance to a fill."""
    symbol = fill["symbol"]
    ref = _reference(md, symbol, fill["date"])
    row = dict(fill)
    row.update(ref)
    row["slippage_vs_open_bps"] = _bps(fill["avg_price"], ref.get("reference_open", 0.0))
    row["slippage_vs_close_bps"] = _bps(fill["avg_price"], ref.get("reference_close", 0.0))
    row["slippage_vs_decision_bps"] = fill.get("slippage_bps")
    volume = ref.get("reference_volume") or 0
    row["participation_pct_of_session_volume"] = (
        round(100.0 * fill["filled_qty"] / volume, 5) if volume else None)
    row["fill_rate_pct"] = (round(100.0 * fill["filled_qty"] / fill["requested_qty"], 3)
                            if fill.get("requested_qty") else None)
    row["cash_impact_usd"] = round(
        (-1 if fill["side"] == "buy" else 1) * fill["filled_qty"] * fill["avg_price"]
        - fill.get("commission", 0.0) - fill.get("exchange_fee", 0.0)
        - fill.get("regulatory_fee", 0.0) + fill.get("rebate", 0.0), 4)
    return row


def build_round_trips(fills: Sequence[dict]) -> List[dict]:
    """Average-cost round trips, re-derived here rather than imported.

    The rule (documented in ``sim/portfolio.py``): a fill reduces an opposite
    lot at the lot's average cost; a same-side fill blends into the lot's
    average cost; a fill that crosses zero closes the lot and opens a new one in
    the opposite direction.
    """
    open_lots: Dict[str, dict] = {}
    trips: List[dict] = []
    for fill in fills:
        if fill.get("filled_qty", 0) <= 0:
            continue
        symbol = fill["symbol"]
        signed = fill["filled_qty"] if fill["side"] == "buy" else -fill["filled_qty"]
        direction = "long" if signed > 0 else "short"
        price = fill["avg_price"]
        qty = abs(signed)
        fees = (fill.get("commission", 0.0) + fill.get("exchange_fee", 0.0)
                + fill.get("regulatory_fee", 0.0) - fill.get("rebate", 0.0))
        lot = open_lots.get(symbol)
        if lot and lot["remaining"] > 0 and lot["direction"] != direction:
            close_qty = min(lot["remaining"], qty)
            share_in = close_qty / lot["remaining"] if lot["remaining"] else 0.0
            share_out = close_qty / qty if qty else 0.0
            entry_fees = lot["fees"] * share_in
            exit_fees = fees * share_out
            sign = 1.0 if lot["direction"] == "long" else -1.0
            gross = sign * (price - lot["avg_cost"]) * close_qty
            trips.append({
                "symbol": symbol, "direction": lot["direction"], "quantity": close_qty,
                "entry_date": lot["entry_date"], "entry_price": round(lot["avg_cost"], 6),
                "exit_date": fill["date"], "exit_price": round(price, 6),
                "gross_pnl_usd": round(gross, 4),
                "fees_usd": round(entry_fees + exit_fees, 4),
                "net_pnl_usd": round(gross - entry_fees - exit_fees, 4),
                "entry_reason": lot.get("reason", ""),
                "exit_reason": fill.get("reason", ""),
                "entry_fill_count": lot.get("fills", 1),
                "entry_reference_close": lot.get("reference_close"),
                "exit_reference_close": fill.get("reference_close"),
                "entry_participation_pct": lot.get("participation"),
                "exit_participation_pct": fill.get("participation_pct_of_session_volume"),
                "status": "closed",
            })
            lot["remaining"] -= close_qty
            lot["fees"] -= entry_fees
            residual = qty - close_qty
            if lot["remaining"] <= 0:
                open_lots.pop(symbol, None)
            if residual > 0:
                open_lots[symbol] = {
                    "direction": direction, "remaining": residual, "avg_cost": price,
                    "entry_date": fill["date"], "fees": fees - exit_fees,
                    "reason": fill.get("reason", ""), "fills": 1,
                    "reference_close": fill.get("reference_close"),
                    "participation": fill.get("participation_pct_of_session_volume"),
                }
            continue
        if lot is None or lot["remaining"] <= 0:
            open_lots[symbol] = {
                "direction": direction, "remaining": qty, "avg_cost": price,
                "entry_date": fill["date"], "fees": fees,
                "reason": fill.get("reason", ""), "fills": 1,
                "reference_close": fill.get("reference_close"),
                "participation": fill.get("participation_pct_of_session_volume"),
            }
            continue
        prev = lot["remaining"]
        lot["avg_cost"] = (lot["avg_cost"] * prev + price * qty) / (prev + qty)
        lot["remaining"] = prev + qty
        lot["fees"] += fees
        lot["fills"] = lot.get("fills", 1) + 1
    for symbol, lot in sorted(open_lots.items()):
        if lot["remaining"]:
            trips.append({
                "symbol": symbol, "direction": lot["direction"], "quantity": lot["remaining"],
                "entry_date": lot["entry_date"], "entry_price": round(lot["avg_cost"], 6),
                "exit_date": "OPEN", "exit_price": None,
                "gross_pnl_usd": 0.0, "fees_usd": round(lot["fees"], 4),
                "net_pnl_usd": round(-lot["fees"], 4),
                "entry_reason": lot.get("reason", ""), "exit_reason": "",
                "entry_fill_count": lot.get("fills", 1),
                "entry_reference_close": lot.get("reference_close"),
                "exit_reference_close": None,
                "entry_participation_pct": lot.get("participation"),
                "exit_participation_pct": None, "status": "open",
            })
    return trips


def build_ledger(fills: Sequence[dict], md, username: str = "") -> dict:
    rows = [enrich_fill(f, md) for f in fills
            if not username or f.get("participant") == username]
    rows.sort(key=lambda r: (r["date"], r["symbol"], r["interval"] or 0))
    trips = build_round_trips(rows)
    closed = [t for t in trips if t["status"] == "closed"]
    open_trips = [t for t in trips if t["status"] == "open"]
    dates = sorted({r["date"] for r in rows})
    sessions = len(dates)
    summary = {
        "participant": username or "ALL",
        "fill_count": len(rows),
        "order_days": sessions,
        "round_trips_closed": len(closed),
        "round_trips_open": len(open_trips),
        "gross_pnl_usd": round(sum(t["gross_pnl_usd"] for t in closed), 4),
        "fees_usd": round(sum(t["fees_usd"] for t in closed), 4),
        "net_pnl_usd": round(sum(t["net_pnl_usd"] for t in closed), 4),
        "win_rate_pct": (round(100.0 * sum(1 for t in closed if t["net_pnl_usd"] > 0)
                               / len(closed), 2) if closed else None),
        # Holding period is in *sessions*, which needs the market calendar. A
        # caller who passed ``md=None`` (an audit of an exported tape, a test)
        # gets None rather than a wrong number: a calendar-day difference would
        # quietly disagree with every other holding figure in the ledger.
        "average_holding_sessions": _mean(
            [_sessions_between(t["entry_date"], t["exit_date"], md) for t in closed]
            if md is not None else []),
        # ``notional`` is the engine's own field. A tape that arrived from
        # elsewhere (an exported CSV, a hand-built fixture) may not carry it, and
        # traded notional is exactly quantity times price, so it is re-derived
        # rather than raising - the ledger is the tool that checks other people's
        # arithmetic and should not depend on their bookkeeping.
        "total_notional_usd": round(sum(
            r.get("notional") or r["filled_qty"] * r["avg_price"] for r in rows), 2),
        "total_explicit_cost_usd": round(sum(
            r.get("commission", 0.0) + r.get("exchange_fee", 0.0)
            + r.get("regulatory_fee", 0.0) - r.get("rebate", 0.0) for r in rows), 4),
        "total_modelled_execution_cost_usd": round(sum(
            r.get("spread_cost", 0.0) + r.get("depth_cost", 0.0) + r.get("impact_cost", 0.0)
            for r in rows), 4),
        "median_participation_pct": _median(
            [r["participation_pct_of_session_volume"] for r in rows
             if r.get("participation_pct_of_session_volume") is not None]),
        "max_participation_pct": max(
            [r["participation_pct_of_session_volume"] for r in rows
             if r.get("participation_pct_of_session_volume") is not None] or [None]),
        "slippage_vs_open_bps_mean": _mean(
            [r["slippage_vs_open_bps"] for r in rows]),
        "slippage_vs_close_bps_mean": _mean(
            [r["slippage_vs_close_bps"] for r in rows]),
        "symbols": sorted({r["symbol"] for r in rows}),
    }
    return {"fills": rows, "round_trips": trips, "summary": summary}


def _sessions_between(entry: str, exit_date: str, md) -> Optional[int]:
    """Sessions between two dates on the market calendar, or None if unknown."""
    if md is None:
        return None
    try:
        return md.dates.index(exit_date) - md.dates.index(entry)
    except ValueError:
        return 0


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _median(values: Sequence[float]) -> Optional[float]:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return round(values[mid] if len(values) % 2
                 else (values[mid - 1] + values[mid]) / 2.0, 5)


def write_ledger(run_dir: str, ledger: dict, compress: bool = False) -> List[str]:
    """Write the ledger next to the run memory. Returns the relative paths."""
    written: List[str] = []
    fills_path = os.path.join(run_dir, LEDGER_FILLS)
    with _open_write(fills_path, compress) as handle:
        for row in ledger["fills"]:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    written.append(os.path.relpath(fills_path + (".gz" if compress else ""), run_dir))
    trips_path = os.path.join(run_dir, LEDGER_TRIPS)
    with _open_write(trips_path, compress) as handle:
        for row in ledger["round_trips"]:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    written.append(os.path.relpath(trips_path + (".gz" if compress else ""), run_dir))
    with open(os.path.join(run_dir, LEDGER_SUMMARY), "w", encoding="utf-8") as handle:
        json.dump(ledger["summary"], handle, indent=1, sort_keys=False)
        handle.write("\n")
    written.append(LEDGER_SUMMARY)
    if ledger["fills"]:
        fields = sorted({k for row in ledger["fills"] for k in row})
        with open(os.path.join(run_dir, LEDGER_CSV), "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in ledger["fills"]:
                writer.writerow({k: row.get(k, "") for k in fields})
        written.append(LEDGER_CSV)
    return written


def read_ledger(run_dir: str) -> Dict[str, List[dict]]:
    def _read(name: str) -> List[dict]:
        plain = os.path.join(run_dir, name)
        gz = plain + ".gz"
        if os.path.exists(gz):
            with gzip.open(gz, "rt", encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        if os.path.exists(plain):
            with open(plain, "r", encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        return []

    return {"fills": _read(LEDGER_FILLS), "round_trips": _read(LEDGER_TRIPS)}


def verify_ledger(fills: Sequence[dict], md=None,
                  engine_final_equity: Optional[float] = None,
                  engine_realized_pnl: Optional[float] = None,
                  carry: Optional[Sequence[dict]] = None,
                  carry_net_usd: float = 0.0,
                  starting_cash: float = 100_000.0) -> dict:
    """Re-derive the account from the raw fills, independently of the engine.

    Cash rule (as documented in ``sim/portfolio.py``):
      cash -= qty*price on a buy; cash += qty*price on a sell; then subtract
      commission + exchange fee + regulatory fee and add any maker rebate.
    Realised P&L uses average cost.  Every discrepancy is returned, not raised,
    so the caller can decide whether it is material.
    """
    cash = starting_cash
    realized = 0.0
    explicit_fees = 0.0
    positions: Dict[str, dict] = {}
    for fill in sorted(fills, key=lambda f: (f["date"], f["symbol"], f.get("interval") or 0)):
        qty = fill.get("filled_qty", 0)
        if qty <= 0:
            continue
        symbol = fill["symbol"]
        signed = qty if fill["side"] == "buy" else -qty
        price = fill["avg_price"]
        pos = positions.setdefault(symbol, {"qty": 0, "avg": 0.0})
        if pos["qty"] != 0 and (pos["qty"] > 0) != (signed > 0):
            closing = min(abs(pos["qty"]), qty)
            realized += ((price - pos["avg"]) if pos["qty"] > 0 else (pos["avg"] - price)) * closing
            remainder = qty - closing
            pos["qty"] += -closing if pos["qty"] > 0 else closing
            if remainder > 0:
                pos["avg"] = price
                pos["qty"] += remainder if fill["side"] == "buy" else -remainder
            if pos["qty"] == 0:
                pos["avg"] = 0.0
        else:
            new_qty = pos["qty"] + signed
            if pos["qty"] == 0:
                pos["avg"] = price
            else:
                pos["avg"] = (pos["avg"] * abs(pos["qty"]) + price * qty) / abs(new_qty)
            pos["qty"] = new_qty
        cash += (-1 if fill["side"] == "buy" else 1) * qty * price
        cash -= (fill.get("commission", 0.0) + fill.get("exchange_fee", 0.0)
                 + fill.get("regulatory_fee", 0.0))
        cash += fill.get("rebate", 0.0)
        explicit_fees += (fill.get("commission", 0.0) + fill.get("exchange_fee", 0.0)
                          + fill.get("regulatory_fee", 0.0)
                          - fill.get("rebate", 0.0))
    carry_total = float(carry_net_usd)
    dividends = 0.0
    for row in carry or []:
        carry_total += float(row.get("amount_usd") or row.get("total") or 0.0)
        if str(row.get("kind", "")).startswith("dividend"):
            dividends += float(row.get("amount_usd") or row.get("total") or 0.0)
    marks = {}
    for symbol, pos in positions.items():
        if pos["qty"] and md is not None:
            marks[symbol] = md.bar(symbol, len(md.dates) - 1).close
    open_value = sum(pos["qty"] * marks.get(symbol, pos["avg"])
                     for symbol, pos in positions.items())
    derived_equity = cash + open_value + carry_total
    residual = None
    if engine_final_equity is not None:
        residual = round(derived_equity - engine_final_equity, 4)
    # Rounding bound.  The fill stream stores prices to 6 decimals and fees to
    # 4, so a re-derivation from the *stored* stream can differ from the engine's
    # internal float arithmetic.  The bound below is the worst case for this
    # run's actual quantities, and it is reported next to the residual so a
    # reader can see that "0.0072" is rounding, not an accounting error.
    rounding_bound = 0.0
    for fill in fills:
        rounding_bound += 0.5e-6 * fill.get("filled_qty", 0) * abs(fill.get("avg_price", 0.0))
        rounding_bound += 4 * 0.5e-4
    return {
        "derived_cash_usd": round(cash, 4),
        "derived_realized_pnl_usd": round(realized, 4),
        "derived_open_value_usd": round(open_value, 4),
        "derived_dividends_and_carry_usd": round(carry_total, 4),
        "derived_final_equity_usd": round(derived_equity, 4),
        "engine_final_equity_usd": engine_final_equity,
        "equity_residual_usd": residual,
        "engine_realized_pnl_usd": engine_realized_pnl,
        # Like-for-like comparison.  The engine's realised figure
        # (analytics.DECOMPOSITION_REALIZED) is *net* of the explicit cash costs
        # it charges to the account, so the re-derived gross figure has to have
        # the same fees taken off before the two are compared.  Comparing gross
        # against net produced a spurious 118.75 USD "residual" in the first
        # Season 2 run, which is exactly the kind of number that gets mistaken
        # for a bug in the engine.
        "derived_realized_pnl_net_usd": round(realized - explicit_fees, 4),
        "explicit_fees_usd": round(explicit_fees, 4),
        "realized_residual_usd": (round(realized - explicit_fees - engine_realized_pnl, 4)
                                  if engine_realized_pnl is not None else None),
        "open_positions": {s: p["qty"] for s, p in positions.items() if p["qty"]},
        "rounding_bound_usd": round(rounding_bound, 4),
        "within_rounding_bound": (residual is None or
                                  abs(residual) <= rounding_bound + 1e-6),
    }


def ledger_digest(ledger: dict) -> str:
    blob = json.dumps(ledger["fills"], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


__all__ = ["build_ledger", "build_round_trips", "enrich_fill", "ledger_digest",
           "read_ledger", "verify_ledger", "write_ledger", "LEDGER_CSV",
           "LEDGER_FILLS", "LEDGER_SUMMARY", "LEDGER_TRIPS"]
