#!/usr/bin/env python3
"""Independent audit of Season 2, written to disagree with sim/ if sim/ is wrong.

This script imports **nothing** from the `sim` package: it re-derives the season
from the committed artefacts with its own arithmetic, its own average-cost
matching and its own file hashing. If the engine, the ledger writer and this
script all share the same mistake, that mistake is invisible; the only defence is
to recompute from the raw material in code that was written separately.

Checks, in order:

1. Chain of custody - every file the run lists exists and hashes to its record.
2. Price chain - every bar the engine traded is byte-equal to the collected bar
   for the same date, and the forward-filled sessions are exactly the ones the
   diagnostics declare.
3. Cross-source check - the SPY vs FRED S&P 500 return correlation published by
   the run is recomputed from both files.
4. Signals - signal arrays are recomputed from the raw event files for every
   session, and a signal cannot be marked available and be empty at the same
   time.
5. Ledger - hashes, round trips re-derived from the fill tape, and the summary
   aggregates.
6. Every fill's reference price is the real bar for that symbol and date, taken
   from the collected vendor file rather than from the run's own copy.
7. Accounts - final equity re-derived from the raw fill stream plus reported
   carry, against the published figures.
8. Leaderboard - returns and final equity recomputed from the equity stream.
9. Site - every published participant has a page, and the pages quote the
   numbers this audit just verified.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RUN = "season2-primary-seed20260918"


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: List[str] = []
        self.checks: Dict[str, int] = {}

    def check(self, group: str, ok: bool, message: str) -> None:
        self.checks[group] = self.checks.get(group, 0) + 1
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"[{group}] {message}")

    @property
    def total(self) -> int:
        return self.passed + len(self.failed)


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: str) -> List[dict]:
    if os.path.exists(path + ".gz"):
        with gzip.open(path + ".gz", "rt", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    return []


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else 0.0


# --------------------------------------------------------------------------

def audit_custody(rep: Report, run_dir: str) -> None:
    prov = read_json(os.path.join(run_dir, "data_provenance.json")) or {}
    files = (prov.get("inventory") or {}).get("files") or []
    rep.check("custody", bool(files), "the provenance inventory lists no files")
    for row in files:
        path = os.path.join(REPO_ROOT, row["path"])
        if not os.path.exists(path):
            rep.check("custody", False, f"collected file missing: {row['path']}")
            continue
        rep.check("custody", sha256_file(path) == row["sha256"],
                  f"{row['path']} does not hash to the value the run recorded")


def audit_prices(rep: Report, run_dir: str) -> None:
    md = read_json(os.path.join(run_dir, "market_data.json")) or {}
    bars = md.get("bars") or {}
    provenance = (md.get("provenance") or {}).get("price_series") or {}
    rep.check("prices", bool(bars), "the run recorded no bars")
    for symbol, rows in sorted(bars.items()):
        meta = provenance.get(symbol)
        if not meta:
            rep.check("prices", False, f"{symbol}: no provenance record")
            continue
        path = os.path.join(REPO_ROOT, meta["file"])
        if not os.path.exists(path):
            rep.check("prices", False, f"{symbol}: collected file {meta['file']} missing")
            continue
        rep.check("prices", sha256_file(path) == meta["sha256"],
                  f"{symbol}: collected file hash differs from the run record")
        payload = read_json(path) or {}
        by_date = {b["date"]: b for b in payload.get("bars", [])}
        mismatches, missing = 0, []
        for date, o, h, low, close, volume in rows:
            ref = by_date.get(date)
            if ref is None:
                missing.append(date)
                continue
            if (abs(ref["open"] - o) > 1e-9 or abs(ref["high"] - h) > 1e-9
                    or abs(ref["low"] - low) > 1e-9 or abs(ref["close"] - close) > 1e-9
                    or int(ref.get("volume") or 0) != int(volume)):
                mismatches += 1
        declared = set(meta.get("forward_filled_dates") or [])
        rep.check("prices", mismatches == 0,
                  f"{symbol}: {mismatches} bars differ from the collected file")
        # A forward-filled session is one with no bar in the collected file; the
        # count must match what the run declared, and (for the first 20, which is
        # what the record keeps) the dates must match too.
        rep.check("prices", len(missing) == int(meta.get("sessions_forward_filled", 0)),
                  f"{symbol}: {len(missing)} missing sessions vs "
                  f"{meta.get('sessions_forward_filled')} declared")
        rep.check("prices", set(missing[:20]) == declared,
                  f"{symbol}: forward-filled dates differ from the declared list")


def audit_crosscheck(rep: Report, run_dir: str, data_root: str) -> None:
    prov = read_json(os.path.join(run_dir, "data_provenance.json")) or {}
    recorded = prov.get("crosschecks") or {}
    spy = read_json(os.path.join(data_root, "prices", "yahoo", "SPY.json")) or {}
    fred_path = recorded.get("fred_file") or         ((prov.get("diagnostics") or {}).get("calendar_source") or {}).get("file")
    if not fred_path:
        rep.check("crosscheck", False, "the run did not record which FRED file was used")
        return
    spx: Dict[str, float] = {}
    with open(os.path.join(REPO_ROOT, fred_path), "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip():
                spx[parts[0]] = float(parts[1])
    common = [(b["date"], b["close"]) for b in spy.get("bars", []) if b["date"] in spx
              and b["date"] >= "2024-09-16"]
    if len(common) < 100:
        rep.check("crosscheck", False, f"only {len(common)} common sessions")
        return
    spy_ret = [common[i][1] / common[i - 1][1] - 1.0 for i in range(1, len(common))]
    spx_ret = [spx[common[i][0]] / spx[common[i - 1][0]] - 1.0
               for i in range(1, len(common))]
    corr = correlation(spy_ret, spx_ret)
    worst = max(abs(a - b) for a, b in zip(spy_ret, spx_ret))
    rep.check("crosscheck", abs(corr - float(recorded.get("return_correlation", -1))) < 1e-5,
              f"published correlation {recorded.get('return_correlation')} vs recomputed "
              f"{round(corr, 6)}")
    rep.check("crosscheck", abs(worst - float(recorded.get("max_abs_daily_return_diff", -1)))
              < 1e-5,
              f"published worst daily difference {recorded.get('max_abs_daily_return_diff')} "
              f"vs recomputed {round(worst, 6)}")
    rep.check("crosscheck", corr > 0.99,
              f"SPY and the S&P 500 disagree: correlation {corr:.4f}")


def _count_window(dates: Sequence[str], session: str, days: int) -> int:
    import datetime as dt
    lo = (dt.date.fromisoformat(session) - dt.timedelta(days=days)).isoformat()
    return sum(1 for d in dates if lo <= d < session)


def audit_signals(rep: Report, run_dir: str, data_root: str) -> None:
    book = read_json(os.path.join(run_dir, "signal_book.json")) or {}
    arrays = book.get("arrays") or {}
    dates = book.get("dates") or []
    availability = book.get("availability") or {}
    rep.check("signals", bool(arrays) and bool(dates), "the signal book is empty")
    if not arrays or not dates:
        return

    # -- every available signal must actually carry information --------------
    for name, meta in sorted(availability.items()):
        if meta.get("state") != "AVAILABLE":
            continue
        array = arrays.get(name)
        rep.check("signals", array is not None,
                  f"{name} is marked AVAILABLE but the book has no array for it")
        if array is None:
            continue
        rep.check("signals", any(v != 0.0 for v in array),
                  f"{name} is marked AVAILABLE but every value is zero - the runtime "
                  f"would read it as a real observation of nothing")
    for name, meta in sorted(availability.items()):
        if meta.get("state") == "AVAILABLE":
            continue
        for rel in meta.get("files", []):
            path = os.path.join(REPO_ROOT, rel)
            rep.check("signals", os.path.exists(path) or os.path.isdir(path),
                      f"{name}: record points at {rel}, which does not exist")

    # -- FDA count, recomputed from the raw openFDA file ---------------------
    fda_path = os.path.join(data_root, "fda", "openfda_decisions.jsonl")
    if os.path.exists(fda_path) and "fda_all_30d" in arrays:
        every: List[str] = []
        original: List[str] = []
        with open(fda_path, "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("status") != "AP":
                    continue
                raw = str(row.get("status_date") or "")
                if len(raw) != 8:
                    continue
                iso = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
                every.append(iso)
                if row.get("submission_type") == "ORIG":
                    original.append(iso)
        for name, source in (("fda_all_30d", every), ("fda_orig_30d", original)):
            if name not in arrays:
                continue
            mismatches = sum(
                1 for t, session in enumerate(dates)
                if abs(float(_count_window(source, session, 30)) - arrays[name][t]) > 0.5)
            rep.check("signals", mismatches == 0,
                      f"{name} disagrees with a fresh count of the raw openFDA file on "
                      f"{mismatches} of {len(dates)} sessions")

    # -- MLB count, recomputed from the raw league feed ----------------------
    mlb_path = os.path.join(data_root, "sports", "mlb_games_2026.jsonl")
    if os.path.exists(mlb_path) and "mlb_games_7d" in arrays:
        finals: List[str] = []
        with open(mlb_path, "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("status") == "Final" and row.get("home_score") is not None \
                        and row.get("away_score") is not None:
                    finals.append(row["date"])
        mismatches = 0
        for t, session in enumerate(dates):
            expected = float(_count_window(finals, session, 7))
            if abs(expected - arrays["mlb_games_7d"][t]) > 0.5:
                mismatches += 1
        rep.check("signals", mismatches == 0,
                  f"mlb_games_7d disagrees with a fresh count of the league feed on "
                  f"{mismatches} of {len(dates)} sessions")

    # -- weather anomaly, recomputed from the raw station file ---------------
    wx_path = os.path.join(data_root, "weather", "USW00023272_daily.json")
    if os.path.exists(wx_path) and "weather_cold_anomaly_10d" in arrays:
        rows = read_json(wx_path) or []
        tmin = {str(r["DATE"])[:10]: float(r["TMIN"]) for r in rows
                if r.get("TMIN") not in (None, "")}
        ordered = sorted(tmin)
        mismatches = 0
        for t, session in enumerate(dates):
            import datetime as dt
            lo = (dt.date.fromisoformat(session) - dt.timedelta(days=10)).isoformat()
            recent = [tmin[d] for d in ordered if lo <= d < session]
            expected = 0.0
            if len(recent) >= 3:
                trail = [tmin[d] for d in ordered if d < lo][-60:]
                mean = sum(trail) / len(trail) if trail else sum(recent) / len(recent)
                sd = (math.sqrt(sum((v - mean) ** 2 for v in trail) / (len(trail) - 1))
                      if len(trail) > 2 else 1.0) or 1.0
                expected = float(sum(1 for v in recent if v < mean - sd))
            if abs(expected - arrays["weather_cold_anomaly_10d"][t]) > 0.5:
                mismatches += 1
        rep.check("signals", mismatches == 0,
                  f"weather_cold_anomaly_10d disagrees with a recomputation from the "
                  f"raw station file on {mismatches} sessions")


def _round_trips(fills: Sequence[dict]) -> List[dict]:
    """Average-cost round trips, implemented here rather than imported.

    Ordering is by (date, symbol, interval) which is the order the engine
    executed them in - the same order the ledger writer uses - so a difference in
    the result is a difference in arithmetic, not a difference in ordering.
    """
    ordered = sorted(fills, key=lambda f: (f["date"], f["symbol"], int(f.get("interval") or 0)))
    lots: Dict[str, dict] = {}
    trips: List[dict] = []
    for fill in ordered:
        qty = int(fill["filled_qty"])
        if qty <= 0:
            continue
        symbol = fill["symbol"]
        side = fill["side"]
        signed = qty if side == "buy" else -qty
        price = float(fill["avg_price"])
        fee = (float(fill.get("commission") or 0.0) + float(fill.get("exchange_fee") or 0.0)
               + float(fill.get("regulatory_fee") or 0.0) - float(fill.get("rebate") or 0.0))
        lot = lots.get(symbol)
        if lot and lot["qty"] != 0 and (lot["qty"] > 0) != (signed > 0):
            close = min(abs(lot["qty"]), qty)
            share_closed = close / abs(lot["qty"])
            sign = 1.0 if lot["qty"] > 0 else -1.0
            gross = sign * (price - lot["avg"]) * close
            entry_fee = lot["fee"] * share_closed
            exit_fee = fee * (close / qty)
            trips.append({
                "symbol": symbol,
                "direction": "long" if lot["qty"] > 0 else "short",
                "entry_date": lot["date"], "exit_date": fill["date"],
                "entry_price": lot["avg"], "exit_price": price, "quantity": close,
                "gross": gross, "fees": entry_fee + exit_fee,
                "net": gross - entry_fee - exit_fee})
            lot["qty"] -= close if lot["qty"] > 0 else -close
            lot["fee"] -= entry_fee
            remainder = qty - close
            if lot["qty"] == 0:
                lots.pop(symbol, None)
            if remainder > 0:
                lots[symbol] = {"qty": remainder * (1 if signed > 0 else -1),
                                "avg": price, "date": fill["date"],
                                "fee": fee * (remainder / qty)}
            continue
        if not lot or lot["qty"] == 0:
            lots[symbol] = {"qty": signed, "avg": price, "date": fill["date"], "fee": fee}
            continue
        total = abs(lot["qty"]) + qty
        lot["avg"] = (lot["avg"] * abs(lot["qty"]) + price * qty) / total
        lot["qty"] += signed
        lot["fee"] += fee
    return trips


def audit_ledger(rep: Report, run_dir: str) -> None:
    manifest = read_json(os.path.join(run_dir, "ledger_manifest.json")) or {}
    fills = read_jsonl(os.path.join(run_dir, "ledger_fills.jsonl"))
    published = read_jsonl(os.path.join(run_dir, "ledger_trips.jsonl"))
    summary = read_json(os.path.join(run_dir, "ledger_summary.json")) or {}
    rep.check("ledger", bool(fills), "the ledger has no fills")
    rep.check("ledger", bool(manifest), "the ledger manifest is empty")
    for rel, meta in sorted(manifest.items()):
        path = os.path.join(run_dir, rel)
        if not os.path.exists(path):
            rep.check("ledger", False, f"{rel} is missing")
            continue
        rep.check("ledger", sha256_file(path) == meta["sha256"],
                  f"{rel} does not hash to its manifest entry")

    mine = _round_trips(fills)
    theirs = [t for t in published if t.get("status") == "closed"]
    rep.check("ledger", len(mine) == len(theirs),
              f"re-derived {len(mine)} closed round trips, published {len(theirs)}")
    by_key = {(t["symbol"], t["entry_date"], t["exit_date"], int(t["quantity"])): t
              for t in theirs}
    for trip in mine:
        key = (trip["symbol"], trip["entry_date"], trip["exit_date"], trip["quantity"])
        ref = by_key.get(key)
        if ref is None:
            rep.check("ledger", False, f"round trip {key} is not in the published ledger")
            continue
        rep.check("ledger", abs(float(ref["gross_pnl_usd"]) - round(trip["gross"], 4)) < 0.02,
                  f"{key}: gross {ref['gross_pnl_usd']} vs re-derived {round(trip['gross'], 4)}")
        rep.check("ledger", abs(float(ref["net_pnl_usd"]) - round(trip["net"], 4)) < 0.02,
                  f"{key}: net {ref['net_pnl_usd']} vs re-derived {round(trip['net'], 4)}")
    total_net = sum(t["net"] for t in mine)
    rep.check("ledger", abs(total_net - float(summary.get("net_pnl_usd", 0.0))) < 0.5,
              f"ledger summary net P&L {summary.get('net_pnl_usd')} vs re-derived "
              f"{round(total_net, 2)}")
    rep.check("ledger", int(summary.get("fill_count", -1)) == len(fills),
              f"summary says {summary.get('fill_count')} fills, the tape has {len(fills)}")


def audit_fill_references(rep: Report, run_dir: str, data_root: str) -> None:
    """Every fill's reference price must be the collected bar, not a copy of it."""
    fills = read_jsonl(os.path.join(run_dir, "ledger_fills.jsonl"))
    cache: Dict[str, Dict[str, dict]] = {}
    checked = 0
    for fill in fills:
        symbol = fill["symbol"]
        if symbol not in cache:
            slug = symbol.replace("^", "_")
            payload = read_json(os.path.join(data_root, "prices", "yahoo",
                                             f"{slug}.json")) or {}
            cache[symbol] = {b["date"]: b for b in payload.get("bars", [])}
        bar = cache[symbol].get(fill["date"])
        if bar is None:
            rep.check("references", False,
                      f"{symbol} {fill['date']}: the session is not in the collected file")
            continue
        checked += 1
        rep.check("references",
                  abs(bar["close"] - float(fill["reference_close"])) < 1e-9
                  and abs(bar["open"] - float(fill["reference_open"])) < 1e-9
                  and abs(bar["high"] - float(fill["reference_high"])) < 1e-9
                  and abs(bar["low"] - float(fill["reference_low"])) < 1e-9
                  and int(bar.get("volume") or 0) == int(fill["reference_volume"]),
                  f"{symbol} {fill['date']}: the ledger's reference bar is not the "
                  f"collected bar")
        if bar.get("volume"):
            participation = 100.0 * int(fill["filled_qty"]) / int(bar["volume"])
            recorded = float(fill.get("participation_pct_of_session_volume") or 0.0)
            rep.check("references", abs(participation - recorded) < 5e-4,
                      f"{symbol} {fill['date']}: participation {recorded}% does not match "
                      f"quantity/real volume = {participation:.5f}%")
    rep.check("references", checked > 0, "no fill could be matched to a collected bar")


def audit_accounts(rep: Report, run_dir: str, starting_cash: float) -> None:
    fills = read_jsonl(os.path.join(run_dir, "events", "fills.jsonl"))
    board = (read_json(os.path.join(run_dir, "leaderboard.json")) or {}).get(
        "leaderboard", [])
    by_user: Dict[str, List[dict]] = {}
    for row in fills:
        by_user.setdefault(row["participant"], []).append(row)
    for entry in board:
        user = entry["username"]
        report = read_json(os.path.join(run_dir, "reports",
                                        f"{user.lstrip('@')}.json")) or {}
        cash = starting_cash
        positions: Dict[str, List[float]] = {}
        realized = 0.0
        for fill in sorted(by_user.get(user, []),
                           key=lambda f: (f["date"], f["symbol"], int(f.get("interval") or 0))):
            qty = int(fill["filled_qty"])
            if qty <= 0:
                continue
            price = float(fill["avg_price"])
            signed = qty if fill["side"] == "buy" else -qty
            held = positions.setdefault(fill["symbol"], [0.0, 0.0])  # qty, average cost
            if held[0] != 0 and (held[0] > 0) != (signed > 0):
                closing = min(abs(held[0]), qty)
                realized += (price - held[1]) * closing if held[0] > 0 else \
                    (held[1] - price) * closing
                held[0] -= closing if held[0] > 0 else -closing
                remainder = qty - closing
                if held[0] == 0:
                    held[1] = 0.0
                if remainder > 0:
                    held[0] += remainder * (1 if signed > 0 else -1)
                    held[1] = price
            else:
                total = abs(held[0]) + qty
                held[1] = (held[1] * abs(held[0]) + price * qty) / total
                held[0] += signed
            cash += (-1 if signed > 0 else 1) * qty * price
            cash -= (float(fill.get("commission") or 0.0)
                     + float(fill.get("exchange_fee") or 0.0)
                     + float(fill.get("regulatory_fee") or 0.0))
            cash += float(fill.get("rebate") or 0.0)
        carry = float((report.get("carry") or {}).get("net_carry_usd", 0.0) or 0.0)
        open_value = sum(held[0] * held[1] for held in positions.values() if held[0])
        derived = cash + open_value + carry
        published = float(report.get("final_equity", 0.0))
        quantities = sum(abs(float(f["avg_price"])) * int(f["filled_qty"])
                         for f in by_user.get(user, []))
        bound = 0.05 + 0.5e-6 * quantities
        rep.check("accounts", abs(open_value) < 1e-6,
                  f"{user}: {open_value:.2f} of inventory is still open after the forced "
                  f"liquidation on the last session")
        rep.check("accounts", abs(derived - published) <= bound,
                  f"{user}: published final equity {published:,.2f} vs re-derived "
                  f"{derived:,.2f} (bound {bound:,.2f})")
        published_realized = float((report.get("pnl_decomposition") or {})
                                   .get("realized_trading_pnl_usd", 0.0))
        # The published realised figure is net of explicit fees; the re-derived
        # gross figure above plus the fee total must match it.
        fees = sum(float(f.get("commission") or 0.0) + float(f.get("exchange_fee") or 0.0)
                   + float(f.get("regulatory_fee") or 0.0) - float(f.get("rebate") or 0.0)
                   for f in by_user.get(user, []))
        rep.check("accounts", abs((realized - fees) - published_realized) <= bound,
                  f"{user}: published realised P&L {published_realized:,.2f} vs re-derived "
                  f"gross {realized:,.2f} minus explicit fees {fees:,.2f} = "
                  f"{realized - fees:,.2f} (bound {bound:,.2f})")
        rep.check("accounts", abs(float(entry["final_equity"]) - published) <= 0.01,
                  f"{user}: the leaderboard equity {entry['final_equity']:,.2f} differs from "
                  f"the report's {published:,.2f}")


def audit_leaderboard(rep: Report, run_dir: str, starting_cash: float) -> None:
    board = (read_json(os.path.join(run_dir, "leaderboard.json")) or {}).get(
        "leaderboard", [])
    curves: Dict[str, List[Tuple[str, float]]] = {}
    for row in read_jsonl(os.path.join(run_dir, "events", "equity.jsonl")):
        curves.setdefault(row["participant"], []).append((row["date"], row["equity"]))
    rep.check("leaderboard", bool(board), "the leaderboard is empty")
    for entry in board:
        user = entry["username"]
        curve = sorted(curves.get(user, []))
        if not curve:
            rep.check("leaderboard", False, f"{user}: no equity curve in the event stream")
            continue
        rep.check("leaderboard", abs(float(entry["final_equity"]) - curve[-1][1]) <= 0.01,
                  f"{user}: final equity {entry['final_equity']:,.2f} vs last equity mark "
                  f"{curve[-1][1]:,.2f}")
        total = 100.0 * (curve[-1][1] / starting_cash - 1.0)
        rep.check("leaderboard", abs(total - float(entry["total_return_pct"])) <= 0.01,
                  f"{user}: published return {entry['total_return_pct']:.4f}% vs re-derived "
                  f"{total:.4f}%")
        peak, worst = -1e18, 0.0
        for _, equity in curve:
            peak = max(peak, equity)
            worst = min(worst, 100.0 * (equity / peak - 1.0))
        rep.check("leaderboard", abs(worst - float(entry["max_drawdown_pct"])) <= 0.01,
                  f"{user}: published max drawdown {entry['max_drawdown_pct']:.4f}% vs "
                  f"re-derived {worst:.4f}%")
    order = [e["total_return_pct"] for e in board]
    rep.check("leaderboard", order == sorted(order, reverse=True),
              "the leaderboard is not sorted by total return")
    rep.check("leaderboard", [e["rank"] for e in board] == list(range(1, len(board) + 1)),
              "ranks are not 1..n in order")


def audit_site(rep: Report, run_dir: str, docs: str) -> None:
    board = (read_json(os.path.join(run_dir, "leaderboard.json")) or {}).get(
        "leaderboard", [])
    season2 = os.path.join(docs, "season2")
    rep.check("site", os.path.isdir(season2), f"{season2} does not exist")
    for name in ("index.html", "leaderboard.html", "ledger.html", "masterfeed.html",
                 "data.html", "stress.html", "participants/index.html"):
        rep.check("site", os.path.exists(os.path.join(season2, name)),
                  f"season 2 page {name} is missing")
    for entry in board:
        slug = entry["username"].lstrip("@").replace("/", "_")
        path = os.path.join(season2, "participants", f"{slug}.html")
        if not os.path.exists(path):
            rep.check("site", False, f"no page for {entry['username']}")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            page = handle.read()
        rep.check("site", entry["username"] in page,
                  f"{slug}.html does not name its participant")
        rep.check("site", f'{entry["total_return_pct"]:+.2f}' in page
                  or f'{entry["total_return_pct"]:+,.2f}' in page,
                  f"{slug}.html does not quote the published return "
                  f"{entry['total_return_pct']:+.2f}%")
    # The Season 1 pages must not have absorbed Season 2's participants.
    people = os.path.join(docs, "participants")
    if os.path.isdir(people):
        season2_names = {e["username"].lstrip("@").replace("/", "_") + ".html"
                         for e in board}
        stray = sorted(n for n in os.listdir(people) if n in season2_names)
        rep.check("site", not stray,
                  f"Season 2 participant pages are published inside Season 1's "
                  f"directory: {stray}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", default=os.path.join(REPO_ROOT, "memory"))
    parser.add_argument("--run", default=DEFAULT_RUN)
    parser.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data", "real"))
    parser.add_argument("--docs", default=os.path.join(REPO_ROOT, "docs"))
    args = parser.parse_args(argv)

    run_dir = os.path.join(args.memory_root, "runs", args.run)
    if not os.path.isdir(run_dir):
        raise SystemExit(f"no Season 2 run at {run_dir}")
    manifest = read_json(os.path.join(run_dir, "manifest.json")) or {}
    starting_cash = float((manifest.get("competition") or {}).get("starting_cash", 100_000.0))

    rep = Report()
    audit_custody(rep, run_dir)
    audit_prices(rep, run_dir)
    audit_crosscheck(rep, run_dir, args.data_root)
    audit_signals(rep, run_dir, args.data_root)
    audit_ledger(rep, run_dir)
    audit_fill_references(rep, run_dir, args.data_root)
    audit_accounts(rep, run_dir, starting_cash)
    audit_leaderboard(rep, run_dir, starting_cash)
    audit_site(rep, run_dir, args.docs)

    print(f"Season 2 independent audit - run {args.run}")
    for group, count in sorted(rep.checks.items()):
        print(f"  {group:12s} {count:6d} checks")
    print(f"  {'TOTAL':12s} {rep.total:6d} checks · {rep.passed} passed · "
          f"{len(rep.failed)} failed")
    if rep.failed:
        print("\nFAILURES (first 40):")
        for line in rep.failed[:40]:
            print(f"  {line}")
        return 1
    print("OK: every published Season 2 number re-derives from the collected files, "
          "and every published price is the collected price")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
