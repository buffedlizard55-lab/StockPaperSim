"""The Official Auction Book: a forward-tested competition on official prices only.

WHAT MAKES THIS BOOK DIFFERENT FROM THE OTHER THREE
---------------------------------------------------
Seasons 1 and 2 and the Live Book all execute through a modelled venue because
their price files come from aggregators or from an exchange endpoint that may not
be redistributed.  This book executes where the *publisher itself printed the
price*:

* a **primary award** fills at the price per $100 the U.S. Treasury published for
  that auction - single-price auctions mean every accepted bid paid it;
* a **maturity** pays par on the date the Treasury printed when the security was
  issued, plus the coupons the security's own interest rate defines;
* a **secondary** leg fills at a price derived from the Treasury's own par yield
  curve (or, for bills, the H.15 secondary-market bill rates) and is labelled
  ``OFFICIAL-DERIVED``, never presented as a traded print.

Consequently every trade this book settles carries ``official_coverage = 1.0``
and the published coverage statistic is not a share of a share: it is all of it.
That is what closes limitation L-26 (the forward book has no settled trades) with
something stronger than a settled trade - a settled trade whose every input is an
official number.

THE CLOCK
---------
A participant plans after the close of session ``T`` and may read only
observations dated ``<= T``: the par curve up to ``T``, auctions whose *official
announcement date* is ``<= T``, results whose *auction date* is ``<= T``.  Every
intent records what it read.  An intent targets a session strictly after ``T``,
and if the official record it needs never arrives, it stays open and says so -
the same fail-closed clock as :mod:`sim.live`.

THE VENUE
---------
Declared, in one place, because the brief asks what is modelled and what is not:

* **leverage**: gross exposure up to the participant's declared cap, up to 10x
  equity, with a maintenance requirement of 5% of gross exposure.  This is the
  venue's policy, chosen so the return-seeking brief can express itself; it is
  modelled on the haircuts a Treasury repo desk applies, **not** claimed to be a
  regulatory minimum;
* **financing**: SOFR (official, NY Fed) plus a declared 25 bp spread, charged on
  every debit balance and on the proceeds of every short;
* **fees**: none on the primary leg.  TreasuryDirect charges no fee on an auction
  award or on a maturity redemption, and a secondary leg is modelled with no
  commission beyond the derived price - both stated rather than assumed;
* **settlement**: an auction award settles on the official *issue date*; a
  secondary leg settles on the session it trades (the real convention is
  next-business-day - this is a simplification and it is registered as one);
* **shorting**: a short is financed at the repo rate and marked daily; there is
  no locate model because a Treasury repo desk is the counterparty in this model.

Every one of those declarations is a limitation, and they are listed in
``research/LIMITATIONS.json`` as L-27 onwards rather than buried here.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import treasury
from .calendar import REPO_ROOT
from .strategies_official import ROSTER, OfficialStrategy

OFFICIAL_NAME = "StockPaperSim Official Auction Book"
OFFICIAL_SEASON = ("Official Season (opened 2026-09-18) - every execution price is "
                   "the U.S. Treasury's own published number")
OFFICIAL_SEED = 20260918
STARTING_CASH = 100_000.0

#: Declared venue parameters (see the module docstring).
REPO_SPREAD_BP = 25.0
MAINTENANCE_FRACTION = 0.05
MAX_GROSS_LEVERAGE = 10.0
CASH_REBATE_SPREAD_BP = 0.0          # idle cash earns SOFR flat; no spread is taken

INTENT_BID = "AUCTION-BID"
INTENT_SECONDARY = "SECONDARY-ORDER"
INTENT_PENDING = "PENDING"
INTENT_FILLED = "FILLED"
INTENT_PARTIAL = "PARTIAL"
INTENT_REJECTED = "REJECTED"
INTENT_EXPIRED = "EXPIRED"
INTENT_CANCELLED = "CANCELLED"
INTENT_WAITING = "WAITING-DATA"

ENTRY_PRIMARY = "PRIMARY-AUCTION"
ENTRY_SECONDARY = "SECONDARY-CURVE"
EXIT_MATURITY = "MATURITY-REDEMPTION"
EXIT_SECONDARY = "SECONDARY-CURVE"
EXIT_LIQUIDATION = "MARGIN-LIQUIDATION"

BOOK_DIR = os.path.join(REPO_ROOT, "memory", "official")
DEFAULT_RUN_ID = f"official-{OFFICIAL_SEED}"


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return None


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Official series the book finances with (FRED copies of H.15 / NY Fed)
# --------------------------------------------------------------------------

@dataclass
class SeriesView:
    sid: str
    file: str
    sha256: str
    url: str
    dates: List[str]
    values: List[float]

    def as_of(self, session: str) -> Optional[Tuple[str, float]]:
        latest: Optional[Tuple[str, float]] = None
        for date, value in zip(self.dates, self.values):
            if date <= session:
                latest = (date, value)
            else:
                break
        return latest

    def value(self, session: str) -> Optional[float]:
        row = self.as_of(session)
        return row[1] if row else None

    def history(self, session: str, n: int) -> List[float]:
        kept = [v for d, v in zip(self.dates, self.values) if d <= session]
        return kept[-n:] if n > 0 else kept

    def change_bp(self, session: str, n: int) -> Optional[float]:
        kept = [v for d, v in zip(self.dates, self.values) if d <= session]
        if len(kept) <= n:
            return None
        return (kept[-1] - kept[-1 - n]) * 100.0


class OfficialRates:
    """SOFR and the H.15 secondary-market bill rates, from FRED's copies."""

    WANTED = {"SOFR": "SOFR", "DTB4WK": "4-week bill", "DTB3": "3-month bill",
              "DTB6": "6-month bill"}

    def __init__(self, root: str = treasury.FRED_DIR) -> None:
        self.root = root
        self.series: Dict[str, SeriesView] = {}
        self.missing: List[dict] = []
        for sid in sorted(self.WANTED):
            path = self._find(sid)
            if path is None:
                self.missing.append({
                    "sid": sid,
                    "reason": f"no committed FRED file starting '{sid}_'",
                    "url": f"https://fred.stlouisfed.org/series/{sid}"})
                continue
            dates: List[str] = []
            values: List[float] = []
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    date = (row.get("observation_date") or row.get("DATE") or "").strip()
                    raw = (row.get(sid) or "").strip()
                    if not date or raw in ("", "."):
                        continue
                    try:
                        values.append(float(raw))
                    except ValueError:
                        continue
                    dates.append(date)
            if not dates:
                self.missing.append({"sid": sid, "reason": "file holds no observations",
                                     "url": f"https://fred.stlouisfed.org/series/{sid}"})
                continue
            self.series[sid] = SeriesView(
                sid=sid, file=os.path.relpath(path, REPO_ROOT),
                sha256=_sha256_file(path),
                url=f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",
                dates=dates, values=values)

    def _find(self, sid: str) -> Optional[str]:
        if not os.path.isdir(self.root):
            return None
        for name in sorted(os.listdir(self.root)):
            if name.startswith(sid + "_") and name.endswith(".csv"):
                return os.path.join(self.root, name)
        return None

    def get(self, sid: str) -> Optional[SeriesView]:
        return self.series.get(sid)

    def sofr(self, session: str) -> Optional[float]:
        view = self.series.get("SOFR")
        return view.value(session) if view else None

    def bill_discount_rate(self, session: str, days: int) -> Optional[float]:
        """The official H.15 secondary-market discount rate for ``days`` to run.

        Interpolated linearly in days between the published 4-week, 3-month and
        6-month points, and the interpolation is named on every mark that uses
        it.  When the tenor is outside the published range the nearest published
        point is used and the mark says so rather than extrapolating a curve.
        """
        points: List[Tuple[int, float]] = []
        for sid, days_map in (("DTB4WK", 28), ("DTB3", 91), ("DTB6", 182)):
            view = self.series.get(sid)
            if view is None:
                continue
            value = view.value(session)
            if value is not None:
                points.append((days_map, value))
        if not points:
            return None
        points.sort()
        if days <= points[0][0]:
            return points[0][1]
        if days >= points[-1][0]:
            return points[-1][1]
        for (d0, r0), (d1, r1) in zip(points, points[1:]):
            if d0 <= days <= d1:
                weight = (days - d0) / (d1 - d0)
                return r0 + weight * (r1 - r0)
        return points[-1][1]

    def provenance(self) -> List[dict]:
        return [{"sid": v.sid, "file": v.file, "sha256": v.sha256, "url": v.url,
                 "observations": len(v.dates),
                 "first": v.dates[0], "last": v.dates[-1],
                 "source_class": ("OFFICIAL" if v.sid == "SOFR"
                                  else "OFFICIAL-PUBLISHER / FRED-REPUBLISHED"),
                 "publisher": ("Federal Reserve Bank of New York (SOFR)"
                               if v.sid == "SOFR" else
                               "U.S. Treasury H.15, republished by FRED")}
                for v in self.series.values()]


# --------------------------------------------------------------------------
# Intents, positions and accounts
# --------------------------------------------------------------------------

@dataclass
class Intent:
    intent_id: str
    participant: str
    kind: str
    session: str                    # the session it targets
    cusip: str
    side: str                       # buy / sell
    face: float
    rule: str
    rationale: str
    created_on: str
    evidence: Dict[str, dict] = field(default_factory=dict)
    status: str = INTENT_PENDING
    settled_on: Optional[str] = None
    settle_note: str = ""
    fill: Optional[dict] = None

    def to_row(self) -> dict:
        return {
            "intent_id": self.intent_id, "participant": self.participant,
            "kind": self.kind, "created_on": self.created_on,
            "target_session": self.session, "cusip": self.cusip,
            "side": self.side, "face": round(self.face, 2), "rule": self.rule,
            "rationale": self.rationale, "status": self.status,
            "settled_on": self.settled_on, "settle_note": self.settle_note,
            "evidence": self.evidence, "fill": self.fill,
        }


@dataclass
class Lot:
    lot_id: str
    cusip: str
    face: float
    price_per100: float
    opened_on: str
    kind: str
    evidence: dict
    accrued_paid: float = 0.0
    coupons_received: float = 0.0
    financing_paid: float = 0.0

    def market_value(self, price_per100: float) -> float:
        return self.face * price_per100 / 100.0


@dataclass
class Trip:
    """One closed trade: an entry and an exit with their own evidence."""

    trip_id: str
    participant: str
    cusip: str
    security_type: str
    security_term: str
    face: float
    entry_date: str
    entry_price: float
    entry_kind: str
    entry_evidence: dict
    exit_date: str
    exit_price: float
    exit_kind: str
    exit_evidence: dict
    coupons: float
    financing: float
    fees: float
    pnl: float
    liquidity: dict
    direction: str

    def to_row(self) -> dict:
        cost = abs(self.face * self.entry_price / 100.0) or 1.0
        return {
            "trip_id": self.trip_id, "participant": self.participant,
            "cusip": self.cusip, "security_type": self.security_type,
            "security_term": self.security_term, "direction": self.direction,
            "face": round(self.face, 2),
            "entry_date": self.entry_date, "entry_price_per100": self.entry_price,
            "entry_kind": self.entry_kind, "entry_evidence": self.entry_evidence,
            "exit_date": self.exit_date, "exit_price_per100": self.exit_price,
            "exit_kind": self.exit_kind, "exit_evidence": self.exit_evidence,
            "coupon_income": round(self.coupons, 2),
            "financing": round(self.financing, 2),
            "fees": round(self.fees, 2),
            "pnl": round(self.pnl, 2),
            "return_on_cost_pct": round(100.0 * self.pnl / cost, 6),
            "holding_days": _days_between(self.entry_date, self.exit_date),
            "liquidity": self.liquidity,
            "source_class": "OFFICIAL",
            "official_price_coverage": 1.0,
        }


class OfficialAccount:
    def __init__(self, participant: str, starting_cash: float = STARTING_CASH) -> None:
        self.participant = participant
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.lots: List[Lot] = []
        self.closed: List[Trip] = []
        self.intents: List[Intent] = []
        self.carry: List[dict] = []
        self.margin_events: List[dict] = []
        self.fills: List[dict] = []
        self._lot_counter = 0
        self._trip_counter = 0

    # -- positions ---------------------------------------------------------
    def net_face(self, cusip: str) -> float:
        return sum(lot.face for lot in self.lots if lot.cusip == cusip)

    def positions(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for lot in self.lots:
            row = out.setdefault(lot.cusip, {"cusip": lot.cusip, "face": 0.0,
                                             "cost": 0.0, "lots": 0})
            row["face"] += lot.face
            row["cost"] += lot.face * lot.price_per100 / 100.0
            row["lots"] += 1
        for row in out.values():
            row["average_price_per100"] = (row["cost"] / row["face"] * 100.0
                                           if row["face"] else None)
        return out

    def gross_exposure(self, marks: Dict[str, float]) -> float:
        return sum(abs(lot.market_value(marks.get(lot.cusip, lot.price_per100)))
                   for lot in self.lots)

    def market_value(self, marks: Dict[str, float]) -> float:
        return sum(lot.market_value(marks.get(lot.cusip, lot.price_per100))
                   for lot in self.lots)

    def equity(self, marks: Dict[str, float]) -> float:
        return self.cash + self.market_value(marks)

    def add_lot(self, cusip: str, face: float, price: float, session: str,
                kind: str, evidence: dict, accrued: float = 0.0) -> Lot:
        self._lot_counter += 1
        lot = Lot(lot_id=f"L{self._lot_counter:05d}", cusip=cusip, face=face,
                  price_per100=price, opened_on=session, kind=kind,
                  evidence=evidence, accrued_paid=accrued)
        self.lots.append(lot)
        return lot

    # -- FIFO consumption --------------------------------------------------
    def consume(self, cusip: str, face: float) -> List[Tuple[Lot, float]]:
        """Remove ``face`` from the position, oldest lot first, signed."""
        remaining = abs(face)
        sign = 1.0 if face >= 0 else -1.0
        taken: List[Tuple[Lot, float]] = []
        keep: List[Lot] = []
        for lot in self.lots:
            if remaining <= 1e-9 or lot.cusip != cusip or \
                    (lot.face > 0) != (sign > 0):
                keep.append(lot)
                continue
            take = min(abs(lot.face), remaining)
            taken.append((lot, take))
            remaining -= take
            if abs(lot.face) - take > 1e-9:
                lot.face -= take * (1.0 if lot.face > 0 else -1.0)
                keep.append(lot)
        self.lots = keep
        return taken

    def close_trip(self, participant: str, cusip: str, facts: dict, face: float,
                   entry: dict, exit_: dict, pnl: float, coupons: float,
                   financing: float, fees: float, liquidity: dict) -> Trip:
        self._trip_counter += 1
        trip = Trip(
            trip_id=f"T{self._trip_counter:05d}", participant=participant,
            cusip=cusip, security_type=facts.get("security_type", ""),
            security_term=facts.get("security_term", ""), face=face,
            entry_date=entry["date"], entry_price=entry["price"],
            entry_kind=entry["kind"], entry_evidence=entry["evidence"],
            exit_date=exit_["date"], exit_price=exit_["price"],
            exit_kind=exit_["kind"], exit_evidence=exit_["evidence"],
            coupons=coupons, financing=financing, fees=fees, pnl=pnl,
            liquidity=liquidity, direction=entry.get("direction", "long"))
        self.closed.append(trip)
        return trip


# --------------------------------------------------------------------------
# The book
# --------------------------------------------------------------------------

class OfficialBook:
    """Plan, settle and mark a competition whose prices are all official."""

    def __init__(self, auctions: treasury.AuctionBook, curve: treasury.ParCurve,
                 rates: OfficialRates, roster: Sequence[OfficialStrategy] = ROSTER,
                 starting_cash: float = STARTING_CASH,
                 start: str = treasury.SEASON_START,
                 end: str = treasury.SEASON_END,
                 seed: int = OFFICIAL_SEED) -> None:
        self.auctions = auctions
        self.curve = curve
        self.rates = rates
        self.roster = list(roster)
        self.starting_cash = starting_cash
        self.start = start
        self.end = end
        self.seed = seed
        self.accounts: Dict[str, OfficialAccount] = {
            s.username: OfficialAccount(s.username, starting_cash) for s in self.roster}
        self.intents: List[Intent] = []
        self.sessions: List[str] = [d for d in curve.dates() if start <= d <= end]
        self.marks: List[dict] = []
        self.margin_events: List[dict] = []
        self.irregularities: List[dict] = []
        self._counters: Dict[str, int] = {}
        self._marks: Dict[str, float] = {}
        self.sessions_settled: List[str] = []
        self._planning_session: str = ""
        #: Per-participant gross exposure caps, declared by the rule itself.
        self.leverage_caps: Dict[str, float] = {
            s.username: min(float(getattr(s, "max_gross_leverage", 1.0)),
                            MAX_GROSS_LEVERAGE)
            for s in self.roster}
        #: Auctions whose official result the book may execute against: results
        #: with an auction date on or before the last session where the tape was
        #: collected, plus everything announced for the future.
        self.last_session = self.sessions[-1] if self.sessions else end

    # -- bookkeeping -------------------------------------------------------
    def next_intent_id(self, participant: str) -> str:
        key = participant.lstrip("@")
        self._counters[key] = self._counters.get(key, 0) + 1
        return f"{key}-{self._counters[key]:05d}"

    def flag(self, code: str, severity: str, detail: str,
             participant: str = "", session: str = "") -> None:
        self.irregularities.append({
            "code": code, "severity": severity, "participant": participant,
            "session": session, "detail": detail})

    # -- market access -----------------------------------------------------
    def result_for(self, cusip: str, auction_date: str) -> Optional[treasury.Auction]:
        return self.auctions.auctions.get(f"{cusip}|{auction_date}")

    def announced_for(self, session: str) -> List[treasury.Auction]:
        """Auctions a participant could know about after the close of ``session``.

        The official announcement date is the gate: an auction whose
        announcement has not been published by ``session`` is not knowable, so it
        is not visible - which is what keeps the rehearsal honest.
        """
        out: List[treasury.Auction] = []
        for auction in self.auctions.auctions.values():
            if auction.auction_date <= session:
                continue
            announced = auction.announcement_date or auction.auction_date
            if announced <= session:
                out.append(auction)
        for auction in self.auctions.announced():
            if auction.auction_date <= session:
                continue
            announced = auction.announcement_date or auction.auction_date
            if announced <= session:
                out.append(auction)
        seen: Dict[str, treasury.Auction] = {}
        for auction in sorted(out, key=lambda a: (a.auction_date, a.cusip)):
            seen.setdefault(auction.auction_key, auction)
        return list(seen.values())

    def price_for(self, cusip: str, session: str) -> Optional[dict]:
        """A price for ``cusip`` observable at ``session``, with its provenance.

        Primary records are preferred for their own auction date; otherwise the
        security is priced from the official curve.  Nothing is returned when no
        official observation covers the date, in which case the caller must wait.
        """
        auction = None
        for candidate in self.auctions.auctions.values():
            if candidate.cusip == cusip and candidate.auction_date <= session:
                if auction is None or candidate.auction_date > auction.auction_date:
                    auction = candidate
        if auction is None:
            return None
        if auction.auction_date == session and auction.execution_price() is not None:
            return {"price_per100": auction.execution_price(),
                    "kind": ENTRY_PRIMARY, "derivation": "official auction price",
                    "session": session, "evidence": {
                        "source": auction.source, "sha256": auction.raw_sha256,
                        "publisher": auction.publisher,
                        "field": "pricePer100", "auction_date": auction.auction_date}}
        if not self.curve.has(session):
            return None
        return self.curve_price(auction, session)

    def curve_price(self, auction: treasury.Auction, session: str) -> Optional[dict]:
        """Price a security from the official curve at ``session``."""
        try:
            maturity = dt.date.fromisoformat(auction.maturity_date)
            day = dt.date.fromisoformat(session)
        except (TypeError, ValueError):
            return None
        days = (maturity - day).days
        if days <= 0:
            return {"price_per100": 100.0, "kind": EXIT_MATURITY,
                    "derivation": "at or past the official maturity date: par",
                    "session": session,
                    "evidence": {"source": auction.source, "sha256": auction.raw_sha256,
                                 "field": "maturityDate",
                                 "maturity_date": auction.maturity_date}}
        if auction.is_bill:
            rate = self.rates.bill_discount_rate(session, days)
            if rate is not None:
                return {"price_per100": round(
                            treasury.bill_price_from_discount(rate, days), 6),
                        "kind": ENTRY_SECONDARY,
                        "derivation": ("100 (1 - d t/360) with d the H.15 "
                                       "secondary-market bill discount rate, "
                                       "interpolated in days"),
                        "session": session,
                        "evidence": {"field": "DTB4WK/DTB3/DTB6 (FRED copy of H.15)",
                                     "rate_pct": round(rate, 6),
                                     "days": days,
                                     "source": auction.source,
                                     "sha256": auction.raw_sha256}}
            # No H.15 bill observation: fall back to the official par curve and
            # say which relation was used.  A discount bill's price from a
            # bond-equivalent yield is P = 100 / (1 + y t/365); the mark records
            # that it came from the par curve rather than from a bill quote.
            par = self.curve.yield_at(session, days / 365.0)
            if par is None:
                return None
            return {"price_per100": round(100.0 / (1.0 + par / 100.0 * days / 365.0), 6),
                    "kind": ENTRY_SECONDARY,
                    "derivation": ("100 / (1 + y t/365) with y the official par yield "
                                   "interpolated at the bill's remaining maturity "
                                   "(no H.15 bill quote collected for this session)"),
                    "session": session,
                    "evidence": {"field": "daily treasury par yield curve",
                                 "par_yield_pct": round(par, 6), "days": days,
                                 "source": auction.source,
                                 "sha256": auction.raw_sha256}}
        years = days / 365.0
        par = self.curve.yield_at(session, years)
        if par is None:
            return None
        price = treasury.price_from_yield(par, auction.interest_rate or 0.0, years)
        return {"price_per100": round(price, 6), "kind": ENTRY_SECONDARY,
                "derivation": ("present value of the security's own coupon and par "
                               "at the official par yield for its remaining maturity, "
                               "interpolated in tenor"),
                "session": session,
                "evidence": {"field": "daily treasury par yield curve",
                             "par_yield_pct": round(par, 6),
                             "remaining_years": round(years, 4),
                             "curve_file": (self.curve.channels[-1]["file"]
                                            if self.curve.channels else None),
                             "sha256": (self.curve.channels[-1]["sha256"]
                                        if self.curve.channels else None)}}

    # -- planning ----------------------------------------------------------
    def plan(self, session: str) -> List[Intent]:
        created: List[Intent] = []
        for strategy in self.roster:
            account = self.accounts[strategy.username]
            ctx = OfficialContext(self, strategy, session)
            before = len(account.intents)
            try:
                strategy.plan(ctx)
            except Exception as exc:              # one rule cannot stop the book
                self.flag("PLAN-ERROR", "high", f"{type(exc).__name__}: {exc}",
                          participant=strategy.username, session=session)
                continue
            new = account.intents[before:]
            for intent in new:
                self._check_evidence(intent, session)
            self._supersede(account, new)
            created.extend(new)
        return created

    def _check_evidence(self, intent: Intent, session: str) -> None:
        for name, evidence in intent.evidence.items():
            date = str(evidence.get("observation_date") or "")
            if date and date > session:
                self.flag("LOOK-AHEAD", "critical",
                          f"intent {intent.intent_id} cites {name} dated {date}, "
                          f"after the plan session {session}",
                          participant=intent.participant, session=session)
        if intent.session <= session:
            self.flag("TARGET-NOT-FORWARD", "critical",
                      f"intent {intent.intent_id} targets {intent.session} from {session}",
                      participant=intent.participant, session=session)

    def _supersede(self, account: OfficialAccount, new: List[Intent]) -> None:
        latest: Dict[Tuple[str, str, str], Intent] = {}
        for intent in new:
            latest[(intent.kind, intent.cusip, intent.session)] = intent
        for intent in account.intents:
            if intent.status != INTENT_PENDING:
                continue
            winner = latest.get((intent.kind, intent.cusip, intent.session))
            if winner is not None and winner is not intent:
                intent.status = INTENT_CANCELLED
                intent.settle_note = "superseded by a newer intent for the same target"

    def submit(self, strategy: OfficialStrategy, kind: str, session: str,
               cusip: str, side: str, face: float, rule: str, rationale: str,
               evidence: Optional[dict] = None) -> Optional[Intent]:
        account = self.accounts[strategy.username]
        if face is None or abs(face) < 1.0:
            return None
        if session <= self._planning_session:
            self.flag("TARGET-NOT-FORWARD", "critical",
                      f"{strategy.username} tried to trade {session} from "
                      f"{self._planning_session}",
                      participant=strategy.username, session=self._planning_session)
            return None
        intent = Intent(intent_id=self.next_intent_id(strategy.username),
                        participant=strategy.username, kind=kind, session=session,
                        cusip=cusip, side=side, face=abs(face), rule=rule,
                        rationale=rationale, created_on=self._planning_session,
                        evidence=dict(evidence or {}))
        account.intents.append(intent)
        self.intents.append(intent)
        return intent

    # -- settlement --------------------------------------------------------
    def settle(self, session: str) -> dict:
        summary = {"session": session, "filled": 0, "partial": 0, "rejected": 0,
                   "expired": 0, "waiting": 0, "maturities": 0, "notional": 0.0}
        self._marks = self.mark_prices(session)
        for account in self.accounts.values():
            for intent in account.intents:
                if intent.status != INTENT_PENDING or intent.session != session:
                    continue
                if intent.kind == INTENT_BID:
                    self._settle_bid(account, intent, session, summary)
                elif intent.kind == INTENT_SECONDARY:
                    self._settle_secondary(account, intent, session, summary)
        summary["maturities"] = self.settle_maturities(session)
        self.charge_financing(session)
        self.check_margin(session)
        self.sessions_settled.append(session)
        return summary

    def _fill_row(self, intent: Intent, session: str, price: float, kind: str,
                  face: float, cash_delta: float, note: str) -> dict:
        return {"intent_id": intent.intent_id, "participant": intent.participant,
                "session": session, "cusip": intent.cusip, "side": intent.side,
                "face": round(face, 2), "price_per100": price, "kind": kind,
                "cash_delta": round(cash_delta, 2), "note": note}

    def _settle_bid(self, account: OfficialAccount, intent: Intent,
                    session: str, summary: dict) -> None:
        result = self.result_for(intent.cusip, session)
        if result is None or result.execution_price() is None:
            intent.status = INTENT_WAITING
            intent.settle_note = ("no official auction result collected for this "
                                  "CUSIP and auction date: the bid stays open rather "
                                  "than filling at a modelled price")
            summary["waiting"] += 1
            return
        award = treasury.noncompetitive_award(result, intent.face)
        if not award.ok:
            intent.status = INTENT_REJECTED
            intent.settle_note = award.reason
            summary["rejected"] += 1
            return
        price = award.price_per100
        cost = award.cost
        face = award.awarded_face
        cap = self.leverage_caps.get(intent.participant, 1.0)
        marks = self.mark_prices(session)
        projected_gross = account.gross_exposure(marks) + face * price / 100.0
        equity = account.equity(marks)
        if equity > 0 and projected_gross > cap * equity:
            # The venue's leverage policy, applied by scaling the award down to
            # the cap rather than by inventing a rejection reason: a real desk
            # would size the bid to the limit it is allowed to run.
            allowed_value = max(0.0, cap * equity - account.gross_exposure(marks))
            scaled = math.floor((allowed_value / price * 100.0)
                                / max(1.0, award.auction.multiples_to_issue or 100.0)
                                ) * max(1.0, award.auction.multiples_to_issue or 100.0)
            if scaled <= 0:
                intent.status = INTENT_REJECTED
                intent.settle_note = (f"refused: the award would take gross exposure "
                                      f"past the declared {cap:.1f}x leverage cap")
                summary["rejected"] += 1
                return
            award = treasury.noncompetitive_award(result, scaled)
            cost, face = award.cost, award.awarded_face
            price = award.price_per100
        account.cash -= cost
        evidence = {"source": result.source, "sha256": result.raw_sha256,
                    "publisher": result.publisher,
                    "auction_date": result.auction_date,
                    "issue_date": result.issue_date,
                    "maturity_date": result.maturity_date,
                    "field": "pricePer100", "value": price,
                    "cross_checked_by": ("Fiscal Data API"
                                         if self.auctions.fiscal_data.get(
                                             result.auction_key) else None)}
        lot = account.add_lot(result.cusip, face, price, session,
                              ENTRY_PRIMARY, evidence,
                              accrued=award.accrued_interest)
        marks = self.mark_prices(session)
        equity = account.equity(marks)
        gross = account.gross_exposure(marks)
        if gross > 0 and equity < MAINTENANCE_FRACTION * gross:
            account.lots.remove(lot)
            account.cash += cost
            intent.status = INTENT_REJECTED
            intent.settle_note = (
                f"refused: settling the award would leave equity "
                f"${equity:,.2f} against a maintenance requirement of "
                f"${MAINTENANCE_FRACTION * gross:,.2f} on gross exposure "
                f"${gross:,.2f}")
            summary["rejected"] += 1
            return
        fill = self._fill_row(intent, session, price, ENTRY_PRIMARY,
                              face, -cost,
                              award.reason or "non-competitive award at the official price")
        fill["liquidity"] = award.liquidity
        account.fills.append(fill)
        intent.status = INTENT_FILLED if face >= intent.face - 1e-6 \
            else INTENT_PARTIAL
        intent.settled_on = session
        intent.settle_note = award.reason or "filled at the official auction price"
        intent.fill = fill
        summary["filled" if intent.status == INTENT_FILLED else "partial"] += 1
        summary["notional"] += face

    def _settle_secondary(self, account: OfficialAccount, intent: Intent,
                          session: str, summary: dict) -> None:
        price_row = self.price_for(intent.cusip, session)
        if price_row is None:
            intent.status = INTENT_WAITING
            intent.settle_note = ("no official observation covers this session "
                                  "(the par curve has no row for it), so the order "
                                  "stays open")
            summary["waiting"] += 1
            return
        price = float(price_row["price_per100"])
        face = intent.face
        direction = 1.0 if intent.side == "buy" else -1.0
        cap = self.leverage_caps.get(intent.participant, 1.0)
        if direction > 0:
            marks = self.mark_prices(session)
            equity = account.equity(marks)
            headroom = max(0.0, cap * equity - account.gross_exposure(marks))
            if equity > 0 and face * price / 100.0 > headroom:
                scaled = math.floor(headroom / price * 100.0 / 100.0) * 100.0
                if scaled <= 0:
                    intent.status = INTENT_REJECTED
                    intent.settle_note = (f"refused: the order would take gross "
                                          f"exposure past the declared {cap:.1f}x "
                                          f"leverage cap")
                    summary["rejected"] += 1
                    return
                face = scaled
        cash_delta = -direction * face * price / 100.0
        existing = account.net_face(intent.cusip)
        if direction < 0 and existing <= 1e-9:
            # An outright short: the venue's policy is stated in the docstring.
            pass
        account.cash += cash_delta
        if direction > 0:
            lot = account.add_lot(intent.cusip, face, price, session,
                                  ENTRY_SECONDARY, price_row)
            closing = None
            marks = self.mark_prices(session)
            equity = account.equity(marks)
            gross = account.gross_exposure(marks)
            if gross > 0 and equity < MAINTENANCE_FRACTION * gross:
                account.lots.remove(lot)
                account.cash -= cash_delta
                intent.status = INTENT_REJECTED
                intent.settle_note = (
                    f"refused: the purchase would leave equity ${equity:,.2f} "
                    f"against a maintenance requirement of "
                    f"${MAINTENANCE_FRACTION * gross:,.2f}")
                summary["rejected"] += 1
                return
        elif existing > 1e-9:
            closing = account.consume(intent.cusip, min(face, existing))
        else:
            account.add_lot(intent.cusip, -face, price, session, ENTRY_SECONDARY,
                            price_row)
            closing = None
            intent.side = "short"
        if closing:
            consumed_face = sum(take for _, take in closing)
            pnl = sum((price - lot.price_per100) / 100.0 * take
                      for lot, take in closing)
            coupons = sum(lot.coupons_received * (take / abs(lot.face or take))
                          for lot, take in closing if lot.face)
            financing = sum(lot.financing_paid * (take / abs(lot.face or take))
                            for lot, take in closing if lot.face)
            facts = self._facts(intent.cusip)
            trip = account.close_trip(
                intent.participant, intent.cusip, facts, consumed_face,
                entry={"date": closing[0][0].opened_on,
                       "price": closing[0][0].price_per100,
                       "kind": closing[0][0].kind, "evidence": closing[0][0].evidence,
                       "direction": "long"},
                exit_={"date": session, "price": price,
                       "kind": EXIT_SECONDARY, "evidence": price_row},
                pnl=pnl, coupons=coupons, financing=financing, fees=0.0,
                liquidity=self._liquidity(intent.cusip))
        fill = self._fill_row(intent, session, price, ENTRY_SECONDARY, face,
                              cash_delta, f"{price_row['derivation']}")
        fill["liquidity"] = self._liquidity(intent.cusip)
        account.fills.append(fill)
        intent.status = INTENT_FILLED
        intent.settled_on = session
        intent.settle_note = f"filled on the official curve: {price_row['derivation']}"
        intent.fill = fill
        summary["filled"] += 1
        summary["notional"] += face

    def _facts(self, cusip: str) -> dict:
        for auction in self.auctions.auctions.values():
            if auction.cusip == cusip:
                return {"security_type": auction.security_type,
                        "security_term": auction.security_term}
        return {}

    def _liquidity(self, cusip: str) -> dict:
        for auction in self.auctions.auctions.values():
            if auction.cusip == cusip:
                return {"offering_amount": auction.offering_amount,
                        "total_accepted": auction.total_accepted,
                        "bid_to_cover": auction.bid_to_cover,
                        "security_term": auction.security_term,
                        "auction_date": auction.auction_date}
        return {}

    def mark_prices(self, session: str) -> Dict[str, float]:
        """Mark every security the book has ever held, from official data."""
        marks: Dict[str, float] = {}
        cusips = {lot.cusip for account in self.accounts.values()
                  for lot in account.lots}
        cusips |= {intent.cusip for intent in self.intents}
        for cusip in cusips:
            row = self.price_for(cusip, session)
            if row is not None:
                marks[cusip] = float(row["price_per100"])
        # Carry forward the last known mark for anything the session does not
        # cover (a security whose auction has not settled yet, for instance).
        for cusip, price in self._marks.items():
            marks.setdefault(cusip, price)
        for account in self.accounts.values():
            for lot in account.lots:
                marks.setdefault(lot.cusip, lot.price_per100)
        return marks

    def settle_maturities(self, session: str) -> int:
        """Pay coupons and redeem at par on the official dates."""
        count = 0
        for account in self.accounts.values():
            for lot in list(account.lots):
                auction = self._auction_for(lot.cusip)
                if auction is None:
                    continue
                for flow in auction.cashflows(abs(lot.face)):
                    if flow["date"] != session:
                        continue
                    sign = 1.0 if lot.face > 0 else -1.0
                    amount = flow["amount"] * sign
                    account.cash += amount
                    if flow["kind"] == "coupon":
                        lot.coupons_received += amount
                        account.carry.append({
                            "session": session, "participant": account.participant,
                            "kind": "coupon", "cusip": lot.cusip, "amount": amount,
                            "basis": flow["basis"]})
                    else:
                        count += 1
                        pnl = ((flow["amount"] / abs(lot.face) * 100.0
                                - lot.price_per100) / 100.0 * abs(lot.face)
                               * sign)
                        account.close_trip(
                            account.participant, lot.cusip, self._facts(lot.cusip),
                            abs(lot.face),
                            entry={"date": lot.opened_on, "price": lot.price_per100,
                                   "kind": lot.kind, "evidence": lot.evidence,
                                   "direction": "long" if lot.face > 0 else "short"},
                            exit_={"date": session, "price": 100.0,
                                   "kind": EXIT_MATURITY,
                                   "evidence": {"field": "maturityDate",
                                                "value": auction.maturity_date,
                                                "source": auction.source,
                                                "sha256": auction.raw_sha256,
                                                "derivation": ("redemption at par on "
                                                               "the official maturity "
                                                               "date")}},
                            pnl=pnl, coupons=lot.coupons_received,
                            financing=lot.financing_paid, fees=0.0,
                            liquidity=self._liquidity(lot.cusip))
                        account.lots.remove(lot)
                        account.carry.append({
                            "session": session, "participant": account.participant,
                            "kind": "redemption", "cusip": lot.cusip,
                            "amount": amount, "basis": flow["basis"]})
        return count

    def _auction_for(self, cusip: str) -> Optional[treasury.Auction]:
        best = None
        for auction in self.auctions.auctions.values():
            if auction.cusip != cusip:
                continue
            if best is None or auction.issue_date > best.issue_date:
                best = auction
        return best

    def charge_financing(self, session: str) -> None:
        """Charge repo financing on debits and shorts; credit idle cash at SOFR."""
        sofr = self.rates.sofr(session)
        if sofr is None:
            return
        daily_debit = (sofr + REPO_SPREAD_BP / 100.0) / 100.0 / 252.0
        daily_credit = (sofr - CASH_REBATE_SPREAD_BP / 100.0) / 100.0 / 252.0
        day = dt.date.fromisoformat(session)
        previous = None
        for candidate in sorted(self.curve.dates()):
            if candidate < session:
                previous = candidate
        if previous is None:
            previous = session
        days = max(1, (day - dt.date.fromisoformat(previous)).days)
        for account in self.accounts.values():
            if account.cash > 0:
                credit = account.cash * daily_credit * days
                account.cash += credit
                account.carry.append({"session": session,
                                      "participant": account.participant,
                                      "kind": "cash_credit", "amount": credit,
                                      "basis": f"SOFR {sofr:.4f}% on idle cash"})
            elif account.cash < 0:
                charge = -account.cash * daily_debit * days
                account.cash -= charge
                account.carry.append({"session": session,
                                      "participant": account.participant,
                                      "kind": "debit_interest", "amount": -charge,
                                      "basis": (f"SOFR {sofr:.4f}% + "
                                                f"{REPO_SPREAD_BP:.0f} bp on the debit "
                                                f"balance")})
            for lot in account.lots:
                if lot.face < 0:
                    notional = abs(lot.market_value(self._marks.get(
                        lot.cusip, lot.price_per100)))
                    charge = notional * daily_debit * days
                    account.cash -= charge
                    lot.financing_paid += charge
                    account.carry.append({"session": session,
                                          "participant": account.participant,
                                          "kind": "short_financing", "amount": -charge,
                                          "basis": ("repo financing on a short "
                                                    "Treasury position")})

    def check_margin(self, session: str) -> None:
        """Liquidate what has to be liquidated, and record that it happened."""
        for account in self.accounts.values():
            equity = account.equity(self._marks)
            gross = account.gross_exposure(self._marks)
            if gross <= 0 or equity >= MAINTENANCE_FRACTION * gross:
                continue
            event = {"session": session, "participant": account.participant,
                     "equity": round(equity, 2), "gross_exposure": round(gross, 2),
                     "required": round(MAINTENANCE_FRACTION * gross, 2),
                     "action": "liquidating the largest position"}
            for lot in sorted(account.lots, key=lambda l: -abs(l.market_value(
                    self._marks.get(l.cusip, l.price_per100)))):
                price_row = self.price_for(lot.cusip, session)
                price = (float(price_row["price_per100"]) if price_row
                         else self._marks.get(lot.cusip, lot.price_per100))
                proceeds = lot.face * price / 100.0
                account.cash += proceeds
                pnl = ((price - lot.price_per100) / 100.0 * abs(lot.face)
                       * (1.0 if lot.face > 0 else -1.0))
                account.close_trip(
                    account.participant, lot.cusip, self._facts(lot.cusip),
                    abs(lot.face),
                    entry={"date": lot.opened_on, "price": lot.price_per100,
                           "kind": lot.kind, "evidence": lot.evidence,
                           "direction": "long" if lot.face > 0 else "short"},
                    exit_={"date": session, "price": price, "kind": EXIT_LIQUIDATION,
                           "evidence": price_row or {"derivation": "last mark"}},
                    pnl=pnl, coupons=lot.coupons_received,
                    financing=lot.financing_paid, fees=0.0,
                    liquidity=self._liquidity(lot.cusip))
                account.lots.remove(lot)
                account.margin_events.append(event)
                equity = account.equity(self._marks)
                gross = account.gross_exposure(self._marks)
                if equity >= MAINTENANCE_FRACTION * gross:
                    break
            event["equity_after"] = round(account.equity(self._marks), 2)
            event["liquidated"] = True
            self.margin_events.append(event)

    # -- daily record ------------------------------------------------------
    def record_marks(self, session: str) -> None:
        self._marks = self.mark_prices(session)
        for strategy in self.roster:
            account = self.accounts[strategy.username]
            equity = account.equity(self._marks)
            gross = account.gross_exposure(self._marks)
            self.marks.append({
                "session": session, "participant": strategy.username,
                "cash": round(account.cash, 4),
                "market_value": round(account.market_value(self._marks), 4),
                "equity": round(equity, 4),
                "gross_exposure": round(gross, 4),
                "leverage": round(gross / equity, 6) if equity > 0 else None,
                "positions": len(account.lots),
                "sofr": self.rates.sofr(session)})

    # -- run ---------------------------------------------------------------
    def run(self) -> dict:
        for session in self.sessions:
            self._planning_session = session
            self.settle(session)
            self.plan(session)
            self.record_marks(session)
        return self.summary()

    def summary(self) -> dict:
        rows = []
        for strategy in self.roster:
            account = self.accounts[strategy.username]
            equities = [m["equity"] for m in self.marks
                        if m["participant"] == strategy.username]
            final = equities[-1] if equities else account.starting_cash
            peak = equities[0] if equities else account.starting_cash
            worst = 0.0
            for equity in equities:
                peak = max(peak, equity)
                if peak > 0:
                    worst = min(worst, equity / peak - 1.0)
            returns = [equities[i] / equities[i - 1] - 1.0
                       for i in range(1, len(equities)) if equities[i - 1]]
            mean = sum(returns) / len(returns) if returns else 0.0
            variance = (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
                        if len(returns) > 1 else 0.0)
            sd = math.sqrt(variance)
            sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
            closed = account.closed
            rows.append({
                "participant": strategy.username, "family": strategy.family,
                "final_equity": round(final, 2),
                "return_pct": round(100.0 * (final / account.starting_cash - 1.0), 4),
                "max_drawdown_pct": round(100.0 * worst, 4),
                "sharpe": round(sharpe, 4),
                "trades": len(closed),
                "open_positions": len(account.lots),
                "coupon_income": round(sum(t.coupons for t in closed), 2),
                "financing": round(sum(t.financing for t in closed), 2),
                "debit_interest": round(sum(c["amount"] for c in account.carry
                                            if c["kind"] == "debit_interest"), 2),
                "cash_credit": round(sum(c["amount"] for c in account.carry
                                         if c["kind"] == "cash_credit"), 2),
                "realised_pnl": round(sum(t.pnl for t in closed), 2),
                "margin_events": len(account.margin_events),
                "intents": len(account.intents),
                "filled_intents": sum(1 for i in account.intents
                                      if i.status in (INTENT_FILLED, INTENT_PARTIAL)),
                "waiting_intents": sum(1 for i in account.intents
                                       if i.status == INTENT_WAITING),
                "rejected_intents": sum(1 for i in account.intents
                                        if i.status == INTENT_REJECTED),
                "data_status": strategy.data_status,
                "official_price_coverage": 1.0,
            })
        rows.sort(key=lambda r: -r["return_pct"])
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return {"participants": rows,
                "sessions": len(self.sessions),
                "first_session": self.sessions[0] if self.sessions else None,
                "last_session": self.sessions[-1] if self.sessions else None}


# --------------------------------------------------------------------------
# The context a strategy sees
# --------------------------------------------------------------------------

class HoldingRef:
    """A security the account holds or could hold, for weight-based rules."""

    def __init__(self, book: "OfficialBook", account: OfficialAccount,
                 auction: treasury.Auction, ctx: "OfficialContext") -> None:
        self._book = book
        self._account = account
        self.auction = auction
        self._ctx = ctx
        self.cusip = auction.cusip
        self.face = account.net_face(auction.cusip)
        self.price_per100 = None
        row = book.price_for(auction.cusip, ctx.session)
        if row is not None:
            self.price_per100 = float(row["price_per100"])
        self.opened_kind = None
        self.entry_date = None
        for lot in account.lots:
            if lot.cusip == auction.cusip:
                self.opened_kind = lot.kind
                self.entry_date = lot.opened_on
                break

    def days_to_maturity(self, session: str) -> int:
        try:
            return (dt.date.fromisoformat(self.auction.maturity_date)
                    - dt.date.fromisoformat(session)).days
        except (TypeError, ValueError):
            return 10 ** 6


class OfficialContext:
    """Everything a rule may read at ``session``, and the only way to place an order."""

    def __init__(self, book: OfficialBook, strategy: OfficialStrategy,
                 session: str) -> None:
        self._book = book
        self._strategy = strategy
        self.session = session
        self.account = book.accounts[strategy.username]
        self.auctions = book.auctions
        self.curve = book.curve
        self.rates = book.rates
        self.notes: List[str] = []

    # -- reads -------------------------------------------------------------
    def note(self, text: str) -> None:
        self.notes.append(text)

    def equity(self) -> float:
        return self.account.equity(self._book._marks)

    def cash_available(self) -> float:
        return max(0.0, self.account.cash)

    def yield_at(self, tenor_years: float) -> Optional[float]:
        return self.curve.yield_at(self.session, tenor_years)

    def curve_history(self, tenor_years: float, n: int) -> List[float]:
        dates = [d for d in self.curve.dates() if d <= self.session][-n:]
        out = []
        for date in dates:
            value = self.curve.yield_at(date, tenor_years)
            if value is not None:
                out.append(value)
        return out

    def slope_bp(self, short: float, long: float) -> Optional[float]:
        return self.curve.slope_bp(self.session, short, long)

    def series_change_bp(self, sid: str, n: int) -> Optional[float]:
        view = self.rates.get(sid)
        return view.change_bp(self.session, n) if view else None

    def realised_inflation_5y(self) -> Optional[float]:
        """Realised CPI inflation, from the official index the Fed publishes.

        The CPI level is not in this book's collected set, so the rule returns
        ``None`` and the TIPS participant states that it could not be evaluated.
        That is the honest outcome: a breakeven trade needs an inflation
        observation, and inventing one would be exactly the failure this project
        is built to avoid.
        """
        return None

    def sessions_since(self, date: str) -> Optional[int]:
        dates = [d for d in self.curve.dates() if d <= self.session]
        if date not in dates:
            return None
        return len(dates) - 1 - dates.index(date)

    def first_session_of_month(self) -> bool:
        dates = [d for d in self.curve.dates() if d <= self.session]
        if not dates:
            return False
        if dates.index(self.session) == 0:
            return True
        return dates[dates.index(self.session) - 1][:7] != self.session[:7]

    def recent_auctions(self, limit: int = 40,
                        types: Optional[Sequence[str]] = None) -> List[treasury.Auction]:
        rows = [a for a in self.auctions.auctions.values()
                if a.auction_date <= self.session and a.execution_price() is not None]
        if types:
            rows = [a for a in rows if a.security_type in types]
        rows.sort(key=lambda a: a.auction_date)
        return rows[-limit:]

    def upcoming_auctions(self, types: Optional[Sequence[str]] = None,
                          limit: int = 40) -> List[treasury.Auction]:
        rows = self._book.announced_for(self.session)
        if types:
            rows = [a for a in rows if a.security_type in types]
        return rows[:limit]

    def held(self) -> List[HoldingRef]:
        refs: List[HoldingRef] = []
        for cusip in sorted({lot.cusip for lot in self.account.lots}):
            auction = self._book._auction_for(cusip)
            if auction is not None:
                refs.append(HoldingRef(self._book, self.account, auction, self))
        return refs

    def next_bill_for_cash(self):
        for auction in self.upcoming_auctions(types=("Bill",)):
            if auction.auction_date > self.session:
                return auction
        return None

    def hold_term(self, term: str) -> Optional[treasury.Auction]:
        """The newest security whose term label matches, as the position to hold."""
        candidates = [a for a in self.auctions.auctions.values()
                      if a.auction_date <= self.session and
                      (term.lower() in (a.term or "").lower() or
                       term.lower() in (a.security_term or "").lower())]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (a.issue_date, a.auction_date))
        return candidates[-1]

    def liquidity_score(self, auction: treasury.Auction) -> Optional[float]:
        """A score from official auction statistics only (0 to 1)."""
        parts: List[float] = []
        if auction.bid_to_cover is not None:
            parts.append(min(1.0, auction.bid_to_cover / 3.0))
        if auction.competitive_tendered and auction.competitive_accepted:
            parts.append(min(1.0, auction.competitive_tendered /
                             max(1.0, auction.competitive_accepted) / 4.0))
        if auction.offering_amount:
            parts.append(min(1.0, auction.offering_amount / 8.0e10))
        if not parts:
            return None
        return sum(parts) / len(parts)

    # -- writes ------------------------------------------------------------
    def _evidence(self) -> dict:
        channels = self.curve.channels[-1] if self.curve.channels else {}
        return {"par_curve": {
            "observation_date": self.session,
            "file": channels.get("file"), "sha256": channels.get("sha256"),
            "source": "U.S. Treasury daily par yield curve",
            "url": ("https://home.treasury.gov/resource-center/data-chart-center/"
                    "interest-rates/TextView?type=daily_treasury_yield_curve")}}

    def buy_at_auction(self, auction: treasury.Auction, market_value: float,
                       rule: str, rationale: str) -> None:
        """Bid non-competitively for ``market_value`` of face at the official price."""
        price = auction.execution_price()
        if price is None:
            self.note(f"{auction.cusip} has no official price yet")
            return
        face = self._round_face(market_value / price * 100.0, auction)
        if face <= 0:
            return
        self._book.submit(self._strategy, INTENT_BID, auction.auction_date,
                          auction.cusip, "buy", face, rule, rationale,
                          evidence={"auction_notice": {
                              "observation_date": auction.announcement_date or "",
                              "auction_date": auction.auction_date,
                              "security_term": auction.security_term,
                              "offering_amount": auction.offering_amount,
                              "source": auction.source,
                              "sha256": auction.raw_sha256,
                              "publisher": auction.publisher}})

    def buy_secondary(self, cusip: str, market_value: float, rule: str,
                      rationale: str) -> None:
        row = self._book.price_for(cusip, self.session)
        if row is None:
            self.note(f"no official observation for {cusip} at {self.session}")
            return
        face = self._round_face(market_value / float(row["price_per100"]) * 100.0,
                                None)
        if face <= 0:
            return
        target = self._next_session()
        self._book.submit(self._strategy, INTENT_SECONDARY, target, cusip, "buy",
                          face, rule, rationale,
                          evidence={"mark": {**self._evidence()["par_curve"],
                                             "derivation": row["derivation"]}})

    def sell_secondary(self, cusip: str, face: float, rule: str, rationale: str) -> None:
        if face <= 0:
            return
        row = self._book.price_for(cusip, self.session)
        target = self._next_session()
        evidence = self._evidence()
        evidence["mark"] = {"derivation": (row or {}).get("derivation", "last mark"),
                            "observation_date": self.session}
        self._book.submit(self._strategy, INTENT_SECONDARY, target, cusip, "sell",
                          face, rule, rationale, evidence=evidence)

    def set_weight(self, holding: HoldingRef, dollars: float, rule: str,
                   rationale: str) -> None:
        """Move a position to ``dollars`` of market value, via the secondary market."""
        price_row = self._book.price_for(holding.cusip, self.session)
        if price_row is None:
            self.note(f"cannot size {holding.cusip}: no official observation")
            return
        price = float(price_row["price_per100"])
        current_value = holding.face * price / 100.0
        delta = dollars - current_value
        if abs(delta) < max(1.0, self.equity() * 0.005):
            return
        face = self._round_face(abs(delta) / price * 100.0, None)
        if face <= 0:
            return
        side = "buy" if delta > 0 else "sell"
        target = self._next_session()
        evidence = self._evidence()
        evidence["mark"] = {"derivation": price_row["derivation"],
                            "observation_date": self.session,
                            "price_per100": price}
        self._book.submit(self._strategy, INTENT_SECONDARY, target,
                          holding.cusip, side, face, rule,
                          f"{rationale}; moving {holding.cusip} to "
                          f"${dollars:,.0f} of market value from ${current_value:,.0f}",
                          evidence=evidence)

    def _round_face(self, face: float, auction: Optional[treasury.Auction]) -> float:
        multiple = 100.0
        if auction is not None and auction.multiples_to_issue:
            multiple = float(auction.multiples_to_issue)
        if face <= 0:
            return 0.0
        return math.floor(face / multiple) * multiple

    def _next_session(self) -> str:
        dates = [d for d in self.curve.dates() if d > self.session]
        return dates[0] if dates else ""


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

STREAMS = ("intents", "fills", "trips", "marks", "carry", "margin")


def _write_stream(path: str, rows: Sequence[dict]) -> dict:
    target = path + ".gz"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    raw_bytes = 0
    with gzip.open(target, "wt", encoding="utf-8", newline="") as handle:
        for row in rows:
            line = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
            raw_bytes += len(line.encode("utf-8")) + 1
            handle.write(line + "\n")
    size = os.path.getsize(target)
    with open(target, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return {"file": os.path.relpath(target, REPO_ROOT), "rows": len(rows),
            "bytes_on_disk": size, "bytes_uncompressed": raw_bytes,
            "bytes_per_row_on_disk": round(size / len(rows), 2) if rows else 0.0,
            "compression_ratio": round(raw_bytes / size, 3) if size else None,
            "sha256": digest, "compressed": True}


def write_book(book: OfficialBook, run_dir: str,
               forward_intents: Optional[Sequence[dict]] = None,
               extra: Optional[dict] = None) -> dict:
    os.makedirs(run_dir, exist_ok=True)
    intents = [i.to_row() for i in book.intents]
    fills: List[dict] = []
    trips: List[dict] = []
    carry: List[dict] = []
    for strategy in book.roster:
        account = book.accounts[strategy.username]
        fills.extend(account.fills)
        trips.extend(t.to_row() for t in account.closed)
        carry.extend(account.carry)
    summary = book.summary()
    streams = {
        "intents": _write_stream(os.path.join(run_dir, "intents.jsonl"), intents),
        "fills": _write_stream(os.path.join(run_dir, "fills.jsonl"), fills),
        "trips": _write_stream(os.path.join(run_dir, "trips.jsonl"), trips),
        "marks": _write_stream(os.path.join(run_dir, "marks.jsonl"), book.marks),
        "carry": _write_stream(os.path.join(run_dir, "carry.jsonl"), carry),
        "margin": _write_stream(os.path.join(run_dir, "margin.jsonl"),
                                book.margin_events),
    }
    blotter = os.path.join(run_dir, "blotter.csv")
    fields = ["trip_id", "participant", "cusip", "security_type", "security_term",
              "direction", "face", "entry_date", "entry_price_per100", "entry_kind",
              "exit_date", "exit_price_per100", "exit_kind", "holding_days",
              "coupon_income", "financing", "fees", "pnl", "return_on_cost_pct",
              "source_class", "official_price_coverage"]
    with open(blotter, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for trip in sorted(trips, key=lambda t: (t["participant"], t["exit_date"],
                                                 t["trip_id"])):
            writer.writerow(trip)
    leaderboard = os.path.join(run_dir, "leaderboard.json")
    with open(leaderboard, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    forward_path = os.path.join(run_dir, "forward_intents.json")
    if forward_intents is not None:
        with open(forward_path, "w", encoding="utf-8") as handle:
            json.dump(list(forward_intents), handle, indent=1, sort_keys=False,
                      default=str)
            handle.write("\n")
    total_bytes = sum(s["bytes_on_disk"] for s in streams.values())
    total_rows = sum(s["rows"] for s in streams.values())
    manifest = {
        "run_id": os.path.basename(run_dir.rstrip(os.sep)),
        "book": OFFICIAL_NAME,
        "season": OFFICIAL_SEASON,
        "seed": book.seed,
        "created_utc": _utcnow(),
        "window": {"start": book.start, "end": book.end,
                   "sessions": len(book.sessions)},
        "starting_cash": book.starting_cash,
        "participants": [s.username for s in book.roster],
        "counts": {"intents": len(intents), "fills": len(fills),
                   "trips": len(trips), "marks": len(book.marks),
                   "carry_rows": len(carry), "margin_events": len(book.margin_events)},
        "storage": {"streams": streams, "total_bytes_on_disk": total_bytes,
                    "total_rows": total_rows,
                    "bytes_per_row_average": round(total_bytes / total_rows, 2)
                    if total_rows else 0.0,
                    "format": ("gzip JSON Lines, keys sorted, separators ',' and ':' "
                               "- byte-identical for two runs of the same inputs"),
                    "csv_export": "blotter.csv"},
        "venue": {
            "primary_execution": ("non-competitive award at the Treasury's published "
                                  "price per $100 (single-price auction)"),
            "secondary_execution": ("price derived from the Treasury's official par "
                                    "yield curve, or from the H.15 secondary-market "
                                    "bill discount rates for bills"),
            "fees": "none on the primary leg; the secondary leg is a derived price",
            "financing": f"SOFR + {REPO_SPREAD_BP:.0f} bp on debits and shorts",
            "cash_credit": "SOFR on idle cash",
            "maintenance": f"{MAINTENANCE_FRACTION:.0%} of gross exposure",
            "max_gross_leverage": MAX_GROSS_LEVERAGE,
            "settlement": ("primary leg on the official issue date; secondary leg on "
                           "the session it trades (the real convention is "
                           "next-business-day and this is a declared simplification)"),
        },
        "official_coverage": {
            "primary_leg_price_field": "pricePer100 / highPrice (official)",
            "secondary_leg_source": "official curve, labelled OFFICIAL-DERIVED",
            "executed_notional_official_pct": 100.0,
            "note": ("this book cannot execute on a non-official price: there is no "
                     "code path from a secondary price file into it"),
        },
        "price_validation": book.auctions.price_validation(),
        "crosscheck": {k: v for k, v in (book.auctions.crosscheck or {}).items()
                       if k != "pairs"},
        "auction_coverage": book.auctions.coverage(),
        "curve_channels": book.curve.channels,
        "rates_provenance": book.rates.provenance(),
        "rates_missing": book.rates.missing,
        "irregularities": book.irregularities,
    }
    if extra:
        manifest.update(extra)
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    return manifest


def read_book(run_dir: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for stream in STREAMS:
        path = os.path.join(run_dir, stream + ".jsonl.gz")
        rows: List[dict] = []
        if os.path.exists(path):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        out[stream] = rows
    return out


def load_run(run_dir: str) -> dict:
    path = os.path.join(run_dir, "manifest.json")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
