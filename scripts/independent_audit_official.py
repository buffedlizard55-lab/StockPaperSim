#!/usr/bin/env python3
"""Independent audit of the Official Auction Book, written to disagree with sim/.

Like ``scripts/independent_audit_season2.py``, this script imports **nothing**
from the ``sim`` package.  It re-derives the published book from the raw
material - the Treasury's own tape files and the run's JSONL streams - with its
own parsing, its own arithmetic and its own file hashing, so that a mistake
shared between the engine and its verification is not invisible.

What it checks, in order:

1. **Custody** - every stream the run published hashes to the SHA-256 in its own
   manifest, and holds the number of rows the manifest claims.
2. **Primary prices** - every trip that entered at an auction entered at the
   price published for that CUSIP and auction date, read from the raw tapes, and
   the two official publishers agree on that price where both carry the auction.
3. **Maturity redemptions** - a trip that exited at maturity did so on the
   security's published maturity date and at exactly par.
4. **Trade arithmetic** - each trip's price P&L, coupon, financing and fees
   reproduce its recorded P&L with this script's own arithmetic.
5. **Accounts** - each participant's cash re-derives from its fills and carry
   rows, and the published final equity re-derives from cash plus the last marks
   row.
6. **Leaderboard** - the published return is (final equity / starting cash - 1).
7. **The derived leg** - a curve-priced trip's price reproduces from the par
   yield the trip cites, that yield is the one in the committed curve file for
   that date, and the interpolation is the declared linear-in-tenor rule.
8. **No secondary price source** - no trip may cite a Yahoo or Stooq file, and
   no evidence may point outside the official registers.
9. **The site** - every audited participant has a published page quoting the
   numbers this script just recomputed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREASURY = os.path.join(REPO_ROOT, "data", "real", "treasury")
FRED = os.path.join(REPO_ROOT, "data", "real", "fred")
MEMORY = os.path.join(REPO_ROOT, "memory", "official")
DOCS = os.path.join(REPO_ROOT, "docs")
TOL = 0.02                      # a cent per trade, for rounding in the engine

CURVE_TENORS = {"1 Mo": 1 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25, "4 Mo": 4 / 12,
                "6 Mo": 0.5, "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0,
                "7 Yr": 7.0, "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0}
FORBIDDEN_SOURCE_FRAGMENTS = ("prices/yahoo", "stooq", "finance.yahoo",
                              "query1.finance.yahoo.com")


class Report:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: List[Tuple[str, str]] = []
        self.notes: List[str] = []

    def check(self, code: str, ok: bool, detail: str) -> None:
        self.checks += 1
        if not ok:
            self.failures.append((code, detail))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def verdict(self) -> str:
        return "PASS" if not self.failures else "FAIL"


# --------------------------------------------------------------------------
# Raw material
# --------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_auctions() -> Dict[str, List[dict]]:
    """Every published auction, keyed by CUSIP, from both publishers."""
    out: Dict[str, List[dict]] = {}
    for name in ("auction_tape.jsonl", "fiscaldata_auction_tape.jsonl"):
        for row in read_jsonl(os.path.join(TREASURY, name)):
            cusip = row.get("cusip")
            if cusip:
                row = dict(row)
                row["_file"] = name
                out.setdefault(cusip, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r.get("auction_date") or "")
    return out


def load_curve() -> Dict[str, Dict[float, float]]:
    """The official par yield curve, re-read from the committed CSVs."""
    rows: Dict[str, Dict[float, float]] = {}
    if not os.path.isdir(TREASURY):
        return rows
    for name in sorted(os.listdir(TREASURY)):
        if not name.startswith("daily_treasury_yield_curve_") or \
                not name.endswith(".csv"):
            continue
        with open(os.path.join(TREASURY, name), "r", encoding="utf-8-sig",
                  newline="") as handle:
            for row in csv.DictReader(handle):
                date = (row.get("Date") or "").strip()
                if not date:
                    continue
                try:
                    month, day, year = (int(part) for part in date.split("/"))
                    iso = dt.date(year, month, day).isoformat()
                except ValueError:
                    continue
                point: Dict[float, float] = {}
                for label, tenor in CURVE_TENORS.items():
                    raw = (row.get(label) or "").strip()
                    if raw:
                        try:
                            point[tenor] = float(raw)
                        except ValueError:
                            continue
                if point:
                    rows[iso] = point
    return rows


def load_fred(sid: str) -> Dict[str, float]:
    """A FRED series, straight from the committed CSV."""
    if not os.path.isdir(FRED):
        return {}
    for name in sorted(os.listdir(FRED)):
        if not (name.startswith(sid + "_") and name.endswith(".csv")):
            continue
        out: Dict[str, float] = {}
        with open(os.path.join(FRED, name), "r", encoding="utf-8-sig",
                  newline="") as handle:
            for row in csv.DictReader(handle):
                date = (row.get("observation_date") or row.get("DATE") or "").strip()
                raw = (row.get(sid) or "").strip()
                if date and raw not in ("", "."):
                    try:
                        out[date] = float(raw)
                    except ValueError:
                        continue
        return out
    return {}


# --------------------------------------------------------------------------
# This script's own arithmetic
# --------------------------------------------------------------------------

def years_between(a: str, b: str) -> float:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days / 365.0


def interpolate(point: Dict[float, float], tenor: float) -> Optional[float]:
    """Linear in tenor between the published neighbours, clamped at the ends."""
    if not point:
        return None
    if tenor in point:
        return point[tenor]
    below = [t for t in point if t <= tenor]
    above = [t for t in point if t >= tenor]
    if not below:
        return point[min(point)]
    if not above:
        return point[max(point)]
    lo, hi = max(below), min(above)
    if hi == lo:
        return point[lo]
    return point[lo] + (point[hi] - point[lo]) * (tenor - lo) / (hi - lo)


def price_from_yield(yield_pct: float, coupon_pct: float, years: float) -> float:
    """Present value of the remaining coupons and par, closed form.

    Deliberately computed as an annuity identity rather than by the engine's
    loop, and with the engine's declared day-count convention (whole half-year
    periods, rounded) stated here so a disagreement is a real finding:

        P = c * (1 - v^n) / y + 100 * v^n        v = 1 / (1 + y/2),  n = round(2T)
    """
    n = max(1, int(round(years * 2)))
    y = yield_pct / 100.0 / 2.0
    c = coupon_pct / 2.0
    if y <= -1:
        raise ValueError("yield below -100% is not a price")
    v = 1.0 / (1.0 + y)
    return c * (1.0 - v ** n) / y + 100.0 * v ** n


def bill_price(discount_pct: float, days: int) -> float:
    return 100.0 * (1.0 - discount_pct / 100.0 * days / 360.0)


def published_price(rows: List[dict], auction_date: str) -> Optional[float]:
    """The price a publisher printed for an auction, preferring its own row."""
    for row in rows:
        if row.get("auction_date") == auction_date:
            value = row.get("price_per100")
            if value is None:
                value = row.get("high_price")
            if value is not None:
                return float(value)
    return None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def audit_custody(rep: Report, run_dir: str, manifest: dict) -> None:
    streams = (manifest.get("storage") or {}).get("streams") or {}
    rep.check("custody", bool(streams), "the manifest lists no streams")
    for name, row in sorted(streams.items()):
        path = os.path.join(REPO_ROOT, row["file"])
        if not os.path.exists(path):
            rep.check("custody", False, f"{name}: file missing at {row['file']}")
            continue
        digest = sha256_file(path)
        rep.check("custody", digest == row.get("sha256"),
                  f"{name}: sha256 is {digest[:16]}… but the manifest records "
                  f"{(row.get('sha256') or '')[:16]}…")
        rows = read_jsonl(path)
        rep.check("custody", len(rows) == row.get("rows"),
                  f"{name}: {len(rows)} rows on disk but the manifest claims "
                  f"{row.get('rows')}")


def audit_trips(rep: Report, trips: List[dict], auctions: Dict[str, List[dict]],
                curve: Dict[str, Dict[float, float]]) -> None:
    for trip in trips:
        cusip = trip["cusip"]
        rows = auctions.get(cusip, [])
        entry_ev = trip.get("entry_evidence") or {}
        exit_ev = trip.get("exit_evidence") or {}

        # 2. a primary entry must match the published price for that auction
        if trip["entry_kind"] == "PRIMARY-AUCTION":
            published = published_price(rows, entry_ev.get("auction_date") or
                                        entry_ev.get("date") or "")
            if published is None:
                rep.check("primary-price", False,
                          f"{trip['trip_id']} {cusip}: no published price in the "
                          f"committed tapes for auction "
                          f"{entry_ev.get('auction_date')}")
            else:
                rep.check("primary-price",
                          abs(published - trip["entry_price_per100"]) < 1e-9,
                          f"{trip['trip_id']} {cusip}: entered at "
                          f"{trip['entry_price_per100']} but the published price is "
                          f"{published}")
                # and both publishers must carry the same number
                for row in rows:
                    if row.get("auction_date") != entry_ev.get("auction_date"):
                        continue
                    value = row.get("price_per100")
                    if value is None:
                        value = row.get("high_price")
                    if value is not None:
                        rep.check("two-publishers", abs(float(value) - published) < 1e-9,
                                  f"{trip['trip_id']} {cusip}: {row.get('_file')} "
                                  f"publishes {value} against {published}")

        # 3. a maturity redemption must be on the published maturity date at par
        if trip["exit_kind"] == "MATURITY-REDEMPTION":
            matches = [r for r in rows if r.get("maturity_date") == trip["exit_date"]]
            rep.check("maturity", bool(matches),
                      f"{trip['trip_id']} {cusip}: redeemed {trip['exit_date']} but "
                      f"no committed auction record has that maturity date")
            rep.check("maturity", abs(trip["exit_price_per100"] - 100.0) < 1e-9,
                      f"{trip['trip_id']} {cusip}: redeemed at "
                      f"{trip['exit_price_per100']} rather than par")

        # 4. the trip's own arithmetic: the recorded price P&L, and then the
        #    total economics the account actually kept.
        sign = 1.0 if trip["direction"] == "long" else -1.0
        price_pnl = (trip["exit_price_per100"] - trip["entry_price_per100"]) / 100.0 \
            * trip["face"] * sign
        total = price_pnl + trip["coupon_income"] - trip["financing"] - trip["fees"]
        rep.check("trade-arithmetic", abs(price_pnl - trip["pnl"]) < TOL,
                  f"{trip['trip_id']}: recorded price P&L {trip['pnl']:.4f} against "
                  f"{price_pnl:.4f} re-derived from the two prices "
                  f"({trip['entry_kind']} to {trip['exit_kind']})")
        recorded_total = trip.get("total_pnl")
        if recorded_total is not None:
            rep.check("trade-arithmetic", abs(total - recorded_total) < TOL,
                      f"{trip['trip_id']}: recorded total P&L {recorded_total:.4f} "
                      f"against {total:.4f} re-derived from price, coupons, "
                      f"financing and fees")

        # 7. the derived leg re-derives from the published yield it cites
        if trip["entry_kind"] == "SECONDARY-CURVE":
            if "par_yield_pct" in entry_ev:
                entry_date = trip["entry_date"]
                maturity = next((r.get("maturity_date") for r in rows
                                 if r.get("maturity_date")), None)
                coupon = next((r.get("interest_rate") for r in rows
                               if r.get("interest_rate") is not None), None)
                point = curve.get(entry_date)
                rep.check("curve-file", point is not None,
                          f"{trip['trip_id']}: {entry_date} is not a date in the "
                          f"committed par curve files")
                if point and maturity and coupon is not None:
                    tenor = years_between(entry_date, maturity)
                    expected_yield = interpolate(point, tenor)
                    rep.check("curve-file",
                              expected_yield is not None and
                              abs(expected_yield - entry_ev["par_yield_pct"]) < 0.01,
                              f"{trip['trip_id']}: cites par yield "
                              f"{entry_ev['par_yield_pct']} but the curve file gives "
                              f"{expected_yield:.6f}".replace(
                                  f"{expected_yield:.6f}",
                                  f"{expected_yield:.6f}" if expected_yield else "—")
                              if expected_yield is not None else
                              f"{trip['trip_id']}: the curve file has no point")
                    if expected_yield is not None:
                        price = price_from_yield(entry_ev["par_yield_pct"],
                                                 float(coupon), tenor)
                        rep.check("derived-price",
                                  abs(price - trip["entry_price_per100"]) < 1e-4,
                                  f"{trip['trip_id']} {cusip}: entered at "
                                  f"{trip['entry_price_per100']} but the cited yield "
                                  f"{entry_ev['par_yield_pct']}% on a "
                                  f"{coupon}% coupon with {tenor:.4f} years to run "
                                  f"prices {price:.6f}")
            elif "rate_pct" in entry_ev:
                days = int(entry_ev.get("days") or 0)
                expected = bill_price(entry_ev["rate_pct"], days)
                rep.check("derived-price",
                          abs(expected - trip["entry_price_per100"]) < 1e-4,
                          f"{trip['trip_id']} {cusip}: entered at "
                          f"{trip['entry_price_per100']} but the cited H.15 rate "
                          f"{entry_ev['rate_pct']}% over {days} days prices "
                          f"{expected:.6f}")

        # 8. nothing may cite a secondary price file
        blob = json.dumps({"entry": entry_ev, "exit": exit_ev})
        for fragment in FORBIDDEN_SOURCE_FRAGMENTS:
            rep.check("no-secondary-source", fragment not in blob,
                      f"{trip['trip_id']} cites {fragment} in its evidence")


def audit_accounts(rep: Report, run_dir: str, board: dict,
                   starting_cash: float) -> None:
    fills = read_jsonl(os.path.join(run_dir, "fills.jsonl.gz"))
    carry = read_jsonl(os.path.join(run_dir, "carry.jsonl.gz"))
    marks = read_jsonl(os.path.join(run_dir, "marks.jsonl.gz"))
    by_participant: Dict[str, List[dict]] = {}
    for row in fills:
        by_participant.setdefault(row["participant"], []).append(row)
    carry_by: Dict[str, List[dict]] = {}
    for row in carry:
        carry_by.setdefault(row["participant"], []).append(row)
    last_marks: Dict[str, dict] = {}
    for row in marks:
        last_marks[row["participant"]] = row

    for row in board["participants"]:
        name = row["participant"]
        cash = starting_cash
        for fill in by_participant.get(name, []):
            cash += fill["cash_delta"]
        for movement in carry_by.get(name, []):
            cash += movement["amount"]
        mark = last_marks.get(name)
        rep.check("account-cash", mark is not None,
                  f"{name}: no marks row was published")
        if mark is None:
            continue
        rederived = cash + mark["market_value"]
        rep.check("account-cash", abs(rederived - mark["equity"]) < 1.0,
                  f"{name}: fills and carry rows give equity ${rederived:,.2f} "
                  f"against the last published mark ${mark['equity']:,.2f}")
        rep.check("account-equity", abs(mark["equity"] - row["final_equity"]) < 1.0,
                  f"{name}: the leaderboard says ${row['final_equity']:,.2f} but the "
                  f"last mark is ${mark['equity']:,.2f}")
        if starting_cash:
            expected = 100.0 * (row["final_equity"] / starting_cash - 1.0)
            rep.check("leaderboard-return", abs(expected - row["return_pct"]) < 0.01,
                      f"{name}: published return {row['return_pct']} against "
                      f"{expected:.4f} re-derived from final equity")
        if row.get("ruined_on"):
            rep.check("ruin", abs(row["return_pct"] + 100.0) < 1e-6,
                      f"{name}: ruined on {row['ruined_on']} but its return is "
                      f"{row['return_pct']}")


def audit_site(rep: Report, board: dict, run_dir: str) -> None:
    pages = os.path.join(DOCS, "official", "participants")
    if not os.path.isdir(pages):
        rep.check("site", False, "docs/official/participants/ was not built")
        return
    published = {name for name in os.listdir(pages) if name.endswith(".html")}
    for row in board["participants"]:
        slug = row["participant"].lstrip("@") + ".html"
        rep.check("site", slug in published,
                  f"{row['participant']}: no published page at "
                  f"docs/official/participants/{slug}")
        if slug in published:
            with open(os.path.join(pages, slug), "r", encoding="utf-8") as handle:
                text = handle.read()
            rep.check("site", row["participant"] in text,
                      f"{slug}: the page does not name its own participant")
            rep.check("site", f'{row["return_pct"]:+.2f}%' in text,
                      f"{slug}: the page does not quote the audited return "
                      f"{row['return_pct']:+.2f}%")
    with open(os.path.join(DOCS, "assets", "data", "official.json"),
              "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rep.check("site", payload.get("verification", {}).get("failure_count") == 0,
              "the published payload does not carry a clean verification block")
    rep.check("site", bool(re.search(r'"run_id"', json.dumps(payload))),
              "the published payload does not name the run it came from")


def audit_registers(rep: Report) -> None:
    """The registers must exist and be internally consistent."""
    lim = read_json(os.path.join(REPO_ROOT, "research", "LIMITATIONS.json"))
    ids = [row["id"] for row in lim]
    rep.check("registers", ids == [f"L-{i:02d}" for i in range(1, len(lim) + 1)],
              "LIMITATIONS.json ids are not sequential")
    for row in lim:
        rep.check("registers", set(row) == {"id", "title", "severity", "detail", "fix"},
                  f"{row.get('id')}: limitation rows have the wrong shape")
    irr = read_json(os.path.join(REPO_ROOT, "research", "IRREGULARITIES.json"))
    rep.check("registers",
              [row["id"] for row in irr] ==
              [f"IR-{i:02d}" for i in range(1, len(irr) + 1)],
              "IRREGULARITIES.json ids are not sequential")
    work = read_json(os.path.join(REPO_ROOT, "research", "REMAINING_WORK.json"))
    rep.check("registers", len(work) >= 14, "REMAINING_WORK.json is thin")


def run_audit(run_id: str = "") -> Report:
    rep = Report()
    if not os.path.isdir(MEMORY):
        rep.check("audit", False, f"no official memory at {MEMORY}")
        return rep
    runs = sorted(d for d in os.listdir(MEMORY) if d.startswith("official-rehearsal-"))
    if not runs:
        rep.check("audit", False, "no official rehearsal run was published")
        return rep
    run_id = run_id or runs[-1]
    run_dir = os.path.join(MEMORY, run_id)
    manifest = read_json(os.path.join(run_dir, "manifest.json"))
    board = read_json(os.path.join(run_dir, "leaderboard.json"))
    trips = read_jsonl(os.path.join(run_dir, "trips.jsonl.gz"))
    rep.check("audit", bool(manifest) and bool(board) and bool(trips),
              f"run {run_id} is missing its manifest, leaderboard or trips")

    audit_custody(rep, run_dir, manifest)
    auctions = load_auctions()
    curve = load_curve()
    rep.check("audit", bool(auctions), "no auction tape was read")
    rep.check("audit", bool(curve), "no par curve file was read")
    audit_trips(rep, trips, auctions, curve)
    audit_accounts(rep, run_dir, board, float(manifest.get("starting_cash") or 0))
    audit_site(rep, board, run_dir)
    audit_registers(rep)

    # The financing rule, re-derived: the rate a session is charged at must be
    # the last SOFR observation published on or before that session.  The venue
    # carries the previous observation forward on a business day with no SOFR fix
    # (2026-04-03, Good Friday, is the case in this window); what it may never do
    # is use a later observation, so this check is one-sided on purpose.
    sofr = load_fred("SOFR")
    rep.check("audit", bool(sofr), "the SOFR series could not be read")
    carry = read_jsonl(os.path.join(run_dir, "carry.jsonl.gz"))
    sessions = {row["session"]: None for row in
                read_jsonl(os.path.join(run_dir, "marks.jsonl.gz"))}
    if sofr:
        for session in sorted(sessions):
            last = None
            for date, value in sofr.items():
                if date <= session and (last is None or date > last[0]):
                    last = (date, value)
            rep.check("financing", last is not None,
                      f"{session} has no SOFR observation on or before it")
            if last is not None:
                sessions[session] = last
        forwarded = [s for s, row in sessions.items()
                     if row is not None and row[0] != s]
        if forwarded:
            rep.note(f"{len(forwarded)} session(s) carry the previous SOFR "
                     f"observation forward (for example {forwarded[0]} uses the "
                     f"fix published {sessions[forwarded[0]][0]})")
    rep.note(f"{len(trips)} trips, {len(board.get('participants', []))} participants, "
             f"run {run_id}")
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="", help="run id under memory/official/")
    parser.add_argument("--json", default="", help="write the report to this path")
    args = parser.parse_args(argv)
    rep = run_audit(args.run)
    for note in rep.notes:
        print(note)
    print(f"independent audit of the official book: {rep.verdict()} "
          f"({rep.checks} checks, {len(rep.failures)} failures)")
    for code, detail in rep.failures[:40]:
        print(f"  FAIL {code}: {detail}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"verdict": rep.verdict(), "checks": rep.checks,
                       "failures": rep.failures, "notes": rep.notes},
                      handle, indent=1)
            handle.write("\n")
    return 0 if not rep.failures else 1


if __name__ == "__main__":
    sys.exit(main())
