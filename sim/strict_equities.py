"""Evidence-gated US equity paper execution, separate from legacy research.

No provider is trusted by default. A future collector must supply approved,
content-addressed receipts and normalize quotes into the receipt's `quotes`
array. This module is NOT a feed adapter or a broker. Even eligible fills are
hypothetical top-of-book executions, never actual exchange executions.

The journal is append-only SQLite with a hash chain; projections are rebuilt
from events, so cash, partial fills and settlements have one source of truth.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")


class EvidenceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise EvidenceError("INVALID_TIMESTAMP") from exc
    if result.tzinfo is None:
        raise EvidenceError("TIMEZONE_REQUIRED")
    return result.astimezone(UTC)


def number(value, positive=False):
    if isinstance(value, bool):
        raise EvidenceError("INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceError("INVALID_NUMBER") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise EvidenceError("INVALID_NUMBER")
    return result


def shares(value):
    result = number(value, positive=True)
    if result != result.to_integral_value():
        raise EvidenceError("WHOLE_SHARES_REQUIRED")
    return int(result)


class EvidenceGate:
    """Trust registry is deployment configuration, never taken from a quote.

    Each receipt is pinned by SHA-256 in that registry. A hash verifies custody,
    NOT truth or permission. Approving a publisher/adapter/rights record is an
    external prerequisite. Production registry is intentionally empty today.
    """

    def __init__(self, root, registry):
        self.root = Path(root).resolve()
        self.registry = registry

    def quote(self, quote, now):
        feed = self.registry.get(quote.get("feed_id"))
        if not feed:
            raise EvidenceError("NO_APPROVED_OFFICIAL_FEED")
        if feed.get("source_class") not in {"EXCHANGE", "SIP"}:
            raise EvidenceError("NON_OFFICIAL_FEED")
        if not feed.get("rights_evidence_url", "").startswith("https://"):
            raise EvidenceError("RIGHTS_NOT_DOCUMENTED")
        if feed.get("redistribution") != "APPROVED":
            raise EvidenceError("REDISTRIBUTION_NOT_APPROVED")
        receipt_id = quote.get("receipt_id")
        receipt = feed.get("receipts", {}).get(receipt_id)
        if not receipt:
            raise EvidenceError("RECEIPT_NOT_APPROVED")
        path = (self.root / receipt["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise EvidenceError("RECEIPT_PATH_ESCAPE")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise EvidenceError("RECEIPT_UNREADABLE") from exc
        if hashlib.sha256(raw).hexdigest() != receipt.get("sha256"):
            raise EvidenceError("RECEIPT_CHECKSUM_MISMATCH")
        url = urlparse(receipt.get("url", ""))
        if url.scheme != "https" or url.hostname not in feed.get("hosts", []):
            raise EvidenceError("RECEIPT_ORIGIN_MISMATCH")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EvidenceError("INVALID_RECEIPT_JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("quotes"), list):
            raise EvidenceError("INVALID_RECEIPT_SCHEMA")
        if quote not in payload["quotes"]:
            raise EvidenceError("QUOTE_NOT_IN_RECEIPT")
        observed = timestamp(quote.get("observed_at"))
        received = timestamp(receipt.get("received_at"))
        at = timestamp(now)
        if not observed <= received <= at:
            raise EvidenceError("FUTURE_OR_REVERSED_TIMESTAMP")
        if (at - observed).total_seconds() > 5:
            raise EvidenceError("STALE_QUOTE")
        if quote.get("currency") != "USD":
            raise EvidenceError("UNSUPPORTED_CURRENCY")
        if quote.get("size_unit") != "shares":
            raise EvidenceError("UNKNOWN_SIZE_UNITS")
        if quote.get("status") != "TRADING" or quote.get("condition") != "REGULAR":
            raise EvidenceError("HALTED_OR_UNSUPPORTED_CONDITION")
        if quote.get("instrument_type") not in {"STOCK", "ETF"}:
            raise EvidenceError("NON_TRADABLE_INDEX_OR_INSTRUMENT")
        if quote.get("listing_exchange") not in {"XNAS", "XNYS", "ARCX", "XASE"}:
            raise EvidenceError("UNVERIFIED_LISTING")
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", quote.get("symbol", "")):
            raise EvidenceError("INVALID_SYMBOL")
        bid, ask = number(quote.get("bid"), True), number(quote.get("ask"), True)
        if bid >= ask:
            raise EvidenceError("LOCKED_OR_CROSSED_QUOTE")
        shares(quote.get("bid_size"))
        shares(quote.get("ask_size"))
        if not quote.get("quote_id"):
            raise EvidenceError("MISSING_QUOTE_ID")
        return {"receipt_sha256": receipt["sha256"], "source_url": receipt["url"],
                "rights_evidence_url": feed["rights_evidence_url"]}


class SessionSchedule:
    """Explicit session records from a reviewed calendar/settlement adapter.

    No weekday fallback: unknown sessions or settlement dates block execution.
    Trading and securities-settlement calendars are separate input fields.
    """

    def __init__(self, sessions):
        self.sessions = sessions

    def at(self, value):
        at = timestamp(value)
        day = at.astimezone(NY).date().isoformat()
        row = self.sessions.get(day)
        if not row or not row.get("source_url", "").startswith("https://"):
            raise EvidenceError("UNVERIFIED_SESSION")
        if not timestamp(row["open"]) <= at < timestamp(row["close"]):
            raise EvidenceError("OUTSIDE_REGULAR_SESSION")
        settlement = row.get("settlement_date")
        if not row.get("settlement_source_url", "").startswith("https://") or not settlement:
            raise EvidenceError("UNVERIFIED_SETTLEMENT_CALENDAR")
        try:
            settle_day = dt.date.fromisoformat(settlement)
        except ValueError as exc:
            raise EvidenceError("INVALID_SETTLEMENT_DATE") from exc
        if settle_day <= dt.date.fromisoformat(day):
            raise EvidenceError("INVALID_SETTLEMENT_DATE")
        return day, row


class PaperLedger:
    """Long-only cash-account, regular-session, market/marketable-limit MVP.

    No synthetic depth, passive queue fills, short locates, leverage or automatic
    liquidation on a missing quote. Per-share fees are an explicit simulation
    assumption, not a claim about any broker's actual fee schedule.
    """

    def __init__(self, path, gate, schedule, season, cash="100000", fee_per_share="0.003"):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL, at TEXT NOT NULL, body TEXT NOT NULL,
            previous_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
        self.db.executescript("""
            CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
        """)
        self.gate, self.schedule, self.season = gate, schedule, season
        self.initial_cash = number(cash, True)
        self.fee = number(fee_per_share)
        start, end = dt.date.fromisoformat(season["start"]), dt.date.fromisoformat(season["end_exclusive"])
        try:
            anniversary = start.replace(year=start.year + 1)
        except ValueError:
            anniversary = start.replace(year=start.year + 1, day=28)
        if end != anniversary:
            raise EvidenceError("SEASON_MUST_BE_ONE_YEAR")
        config = {"season": season, "cash": str(self.initial_cash), "fee": str(self.fee)}
        existing = self.events()
        if existing:
            self.verify()
            if existing[0]["kind"] != "CONFIG" or existing[0]["body"] != config:
                self.db.close()
                raise EvidenceError("IMMUTABLE_CONFIGURATION_MISMATCH")
        else:
            self._append("config", "CONFIG", season["start"] + "T00:00:00Z", config)

    def close(self):
        self.db.close()

    def events(self):
        return [{"seq": r[0], "event_id": r[1], "kind": r[2], "at": r[3],
                 "body": json.loads(r[4]), "previous_hash": r[5], "hash": r[6]}
                for r in self.db.execute("SELECT * FROM events ORDER BY seq")]

    def _append(self, event_id, kind, at, body):
        # Serialize the hash-link read + insert even for submit/cancel/mark.
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                result = self._append(event_id, kind, at, body)
                self.db.execute("COMMIT")
                return result
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        existing = self.db.execute("SELECT kind,at,body FROM events WHERE event_id=?", (event_id,)).fetchone()
        encoded = canonical(body)
        if existing:
            if tuple(existing) != (kind, at, encoded):
                raise EvidenceError("IDEMPOTENCY_CONFLICT")
            return False
        previous = self.db.execute("SELECT hash,at FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if previous and timestamp(at) < timestamp(previous[1]):
            raise EvidenceError("CLOCK_REWIND")
        previous = previous[0] if previous else "0" * 64
        h = digest([previous, event_id, kind, at, body])
        self.db.execute("INSERT INTO events(event_id,kind,at,body,previous_hash,hash) VALUES(?,?,?,?,?,?)",
                        (event_id, kind, at, encoded, previous, h))
        return True

    def verify(self):
        previous = "0" * 64
        for e in self.events():
            if e["previous_hash"] != previous or e["hash"] != digest(
                    [previous, e["event_id"], e["kind"], e["at"], e["body"]]):
                raise EvidenceError("JOURNAL_HASH_MISMATCH")
            previous = e["hash"]
        return previous

    def submit(self, order, now):
        at = timestamp(now)
        if not self.season["start"] <= at.astimezone(NY).date().isoformat() < self.season["end_exclusive"]:
            raise EvidenceError("OUTSIDE_COMPETITION")
        required = {"order_id", "username", "strategy_version", "symbol", "side", "quantity",
                    "order_type", "signal_at", "signal_sha256", "signal_source_url", "expires_at", "reason"}
        if not required <= order.keys() or not all(order[k] is not None for k in required):
            raise EvidenceError("INCOMPLETE_ORDER")
        if not all(isinstance(order[k], str) and order[k] for k in
                   ("order_id", "username", "strategy_version", "reason")):
            raise EvidenceError("INVALID_ORDER_IDENTITY")
        if not re.fullmatch(r"[a-f0-9]{64}", order["signal_sha256"]) or not order["signal_source_url"].startswith("https://"):
            raise EvidenceError("MISSING_SIGNAL_CUSTODY")
        if timestamp(order["signal_at"]) > at or timestamp(order["expires_at"]) <= at:
            raise EvidenceError("INVALID_ORDER_CLOCK")
        if order["side"] not in {"buy", "sell"} or order["order_type"] not in {"market", "limit"}:
            raise EvidenceError("UNSUPPORTED_ORDER")
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", order["symbol"]):
            raise EvidenceError("INVALID_SYMBOL")
        shares(order["quantity"])
        if order["order_type"] == "limit":
            number(order.get("limit_price"), True)
        # copied into the journal; caller mutation cannot change a recorded intent
        return self._append("order:" + order["order_id"], "ORDER", now, order)

    def _state(self, username):
        cash, receivable, realized = self.initial_cash, Decimal(0), Decimal(0)
        positions, lots, fills, settled, marks = {}, {}, [], set(), {}
        for e in self.events():
            b = e["body"]
            if e["kind"] == "SETTLEMENT":
                settled.add(b["fill_id"])
            if b.get("username") != username:
                continue
            if e["kind"] == "FILL":
                fills.append(b)
                symbol, qty = b["symbol"], b["quantity"]
                price, fee = Decimal(b["price"]), Decimal(b["fee"])
                if b["side"] == "buy":
                    cash -= price * qty + fee
                    positions[symbol] = positions.get(symbol, 0) + qty
                    lots.setdefault(symbol, []).append([qty, price + fee / qty])
                else:
                    positions[symbol] -= qty
                    proceeds = price * qty - fee
                    receivable += proceeds
                    left, cost = qty, Decimal(0)
                    while left:
                        lot = lots[symbol][0]
                        used = min(left, lot[0])
                        cost += used * lot[1]
                        left -= used
                        lot[0] -= used
                        if not lot[0]:
                            lots[symbol].pop(0)
                    realized += proceeds - cost
            if e["kind"] == "MARK":
                marks[b["symbol"]] = b
        for f in fills:
            if f["fill_id"] in settled and f["side"] == "sell":
                value = Decimal(f["price"]) * f["quantity"] - Decimal(f["fee"])
                cash += value
                receivable -= value
        return cash, receivable, realized, positions, lots, fills, marks

    def execute(self, order_id, quote, now):
        """Atomic capacity consumption across ALL participants on one quote.

        A repeated order/quote pair is a no-op. An unfilled remainder needs a
        new observed quote. Top-of-book size is a ceiling, not proof of fill.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = self._execute(order_id, quote, now)
            if result["status"] not in {"FILLED", "PARTIAL", "ALREADY_PROCESSED"}:
                attempt = {"order_id": order_id, "quote_sha256": digest(quote), "result": result}
                self._append("evaluation:" + digest([attempt, now]), "EVALUATION", now, attempt)
            self.db.execute("COMMIT")
            return result
        except Exception as exc:
            self.db.execute("ROLLBACK")
            if isinstance(exc, EvidenceError):
                # Invalid timestamps cannot be journal timestamps. With a valid,
                # monotonic processing clock retain each evidence-gate failure.
                try:
                    if timestamp(now) >= timestamp(self.events()[-1]["at"]):
                        rejected = {"order_id": order_id, "reason": str(exc),
                                    "feed_id": quote.get("feed_id"), "quote_id": quote.get("quote_id")}
                        self._append("blocked:" + digest([rejected, now]), "BLOCKED", now, rejected)
                except EvidenceError:
                    pass
            raise

    def _execute(self, order_id, quote, now):
        events = self.events()
        event = next((e for e in events if e["event_id"] == "order:" + order_id), None)
        if not event:
            raise EvidenceError("UNKNOWN_ORDER")
        o = event["body"]
        fills = [e["body"] for e in events if e["kind"] == "FILL"]
        # Feed + quote id identifies capacity, regardless of receipt duplication.
        quote_key = str(quote.get("feed_id")) + ":" + str(quote.get("quote_id"))
        fill_id = digest([order_id, quote_key])
        if any(f["fill_id"] == fill_id for f in fills):
            return {"status": "ALREADY_PROCESSED"}
        if any(e["kind"] in {"CANCEL", "EXPIRE"} and e["body"]["order_id"] == order_id for e in events):
            return {"status": "CLOSED_ORDER"}
        remaining = shares(o["quantity"]) - sum(f["quantity"] for f in fills if f["order_id"] == order_id)
        if not remaining:
            return {"status": "FILLED"}
        if timestamp(now) >= timestamp(o["expires_at"]):
            self._append("expire:" + order_id, "EXPIRE", now, {"order_id": order_id})
            return {"status": "EXPIRED"}
        if timestamp(now) < timestamp(event["at"]):
            raise EvidenceError("CLOCK_REWIND")
        if not self.season["start"] <= timestamp(now).astimezone(NY).date().isoformat() < self.season["end_exclusive"]:
            raise EvidenceError("OUTSIDE_COMPETITION")
        evidence = self.gate.quote(quote, now)
        if timestamp(quote["observed_at"]) <= timestamp(event["at"]):
            raise EvidenceError("QUOTE_PRECEDES_ORDER")
        day, session = self.schedule.at(quote["observed_at"])
        self.schedule.at(now)
        if quote["symbol"] != o["symbol"]:
            raise EvidenceError("SYMBOL_MISMATCH")
        same_quote = [f for f in fills if f["quote_key"] == quote_key]
        if any(f["quote_sha256"] != digest(quote) for f in same_quote):
            raise EvidenceError("QUOTE_ID_REUSED_WITH_DIFFERENT_CONTENT")
        is_buy = o["side"] == "buy"
        price = Decimal(str(quote["ask"] if is_buy else quote["bid"]))
        if o["order_type"] == "limit":
            limit = Decimal(str(o["limit_price"]))
            if (is_buy and price > limit) or (not is_buy and price < limit):
                return {"status": "RESTING_UNVERIFIED_QUEUE", "reason": "No passive fills without queue evidence"}
        capacity = shares(quote["ask_size"] if is_buy else quote["bid_size"])
        # A new quote identifier alone does not prove replenishment. Until an
        # order-lifecycle feed is integrated, consume capacity conservatively
        # across this feed/symbol/price/side/session, even on later snapshots.
        used = sum(f["quantity"] for f in fills if f["side"] == o["side"]
                   and f["symbol"] == o["symbol"] and f["trade_date"] == day
                   and f["quote"]["feed_id"] == quote["feed_id"] and Decimal(f["price"]) == price)
        qty = min(remaining, max(0, capacity - used))
        cash, _, _, positions, _, _, _ = self._state(o["username"])
        qty = min(qty, int(cash / (price + self.fee)) if is_buy else positions.get(o["symbol"], 0))
        if qty <= 0:
            return {"status": "BLOCKED_CAPACITY_OR_CASH_OR_HOLDINGS"}
        mid = (Decimal(str(quote["ask"])) + Decimal(str(quote["bid"]))) / 2
        body = {"fill_id": fill_id, "order_id": order_id, "username": o["username"],
                "strategy_version": o["strategy_version"], "symbol": o["symbol"], "side": o["side"],
                "quantity": qty, "price": str(price), "fee": str(qty * self.fee),
                "fee_class": "DECLARED_SIMULATION_ASSUMPTION", "trade_date": day,
                "settlement_date": session["settlement_date"], "session_source_url": session["source_url"],
                "settlement_source_url": session["settlement_source_url"],
                "quote_key": quote_key, "quote_sha256": digest(quote), "quote": quote,
                "slippage_vs_mid_bps": str((price - mid) / mid * 10000 * (1 if is_buy else -1)),
                "liquidity_model": "SHARED_DISPLAYED_TOP_OF_BOOK_CAP_NO_QUEUE_GUARANTEE",
                "execution_class": "SIMULATED_NOT_EXCHANGE_EXECUTION", **evidence}
        self._append("fill:" + fill_id, "FILL", now, body)
        return {"status": "FILLED" if qty == remaining else "PARTIAL", **body}

    def cancel(self, order_id, now):
        order = next((e for e in self.events() if e["event_id"] == "order:" + order_id), None)
        if not order or timestamp(now) < timestamp(order["at"]):
            raise EvidenceError("UNKNOWN_ORDER_OR_CLOCK_REWIND")
        return self._append("cancel:" + order_id, "CANCEL", now, {"order_id": order_id})

    def refuse(self, order_id, now, reason):
        """Journal a deliberate refusal for an order that was never offered a quote.

        Execution BLOCKED events only appear when a real quote was presented and
        the evidence gate failed.  A scheduler that holds no approved feed at
        all never offers a quote, so without this record the journal would show
        an order stuck PENDING forever and a reviewer could not tell "waiting"
        from "refused by policy".  Refusals carry the same BLOCKED kind so the
        orders() projection surfaces the reason.
        """
        order = next((e for e in self.events() if e["event_id"] == "order:" + order_id), None)
        if not order or timestamp(now) < timestamp(order["at"]):
            raise EvidenceError("UNKNOWN_ORDER_OR_CLOCK_REWIND")
        if not reason or not isinstance(reason, str):
            raise EvidenceError("REFUSAL_REASON_REQUIRED")
        rejected = {"order_id": order_id, "reason": reason, "feed_id": None, "quote_id": None}
        return self._append("blocked:" + digest([rejected, now]), "BLOCKED", now, rejected)

    def settle(self, now):
        """Simulated completion only AFTER the scheduled settlement day has ended.

        This conservative end-of-day rule is not an assertion of actual DTC
        completion. Unknown calendars never reach this method as filled trades.
        """
        today = timestamp(now).astimezone(NY).date().isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            events = self.events()
            settled = {e["body"]["fill_id"] for e in events if e["kind"] == "SETTLEMENT"}
            count = 0
            for e in events:
                f = e["body"]
                if e["kind"] == "FILL" and f["fill_id"] not in settled and f["settlement_date"] < today:
                    self._append("settlement:" + f["fill_id"], "SETTLEMENT", now,
                                 {"fill_id": f["fill_id"], "scheduled_date": f["settlement_date"],
                                  "classification": "SIMULATED_SETTLEMENT_NOT_DTC_CONFIRMATION"})
                    count += 1
            self.db.execute("COMMIT")
            return count
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def mark(self, username, quote, now):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = self._mark(username, quote, now)
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _mark(self, username, quote, now):
        evidence = self.gate.quote(quote, now)
        self.schedule.at(quote["observed_at"])
        previous = self._state(username)[-1].get(quote["symbol"])
        if previous and timestamp(previous["observed_at"]) > timestamp(quote["observed_at"]):
            raise EvidenceError("OUT_OF_ORDER_MARK")
        return self._append("mark:" + digest([username, quote]), "MARK", now,
                            {"username": username, "symbol": quote["symbol"], "bid": str(quote["bid"]),
                             "observed_at": quote["observed_at"], **evidence})

    def account(self, username, now):
        last = self.events()[-1]
        if timestamp(now) < timestamp(last["at"]):
            raise EvidenceError("CLOCK_REWIND")
        cash, receivable, realized, positions, lots, fills, marks = self._state(username)
        value, cost, missing = Decimal(0), Decimal(0), []
        for symbol, qty in positions.items():
            if not qty:
                continue
            mark = marks.get(symbol)
            age = (timestamp(now) - timestamp(mark["observed_at"])).total_seconds() if mark else -1
            if not mark or not 0 <= age <= 5:
                missing.append(symbol)
                continue
            value += qty * Decimal(mark["bid"])
            cost += sum(Decimal(q) * p for q, p in lots[symbol])
        equity = None if missing else cash + receivable + value
        return {"username": username, "available_cash": str(cash), "unsettled_sale_proceeds": str(receivable),
                "realized_pnl": str(realized), "unrealized_pnl": None if missing else str(value - cost),
                "equity": str(equity) if equity is not None else None, "positions": positions,
                "missing_fresh_marks": missing, "fills": len(fills),
                "return_pct": str((equity / self.initial_cash - 1) * 100) if equity is not None and fills else None}

    def orders(self):
        """Reconstruct every placed intent, partial fill and terminal status."""
        rows = {}
        for e in self.events():
            b = e["body"]
            if e["kind"] == "ORDER":
                rows[b["order_id"]] = {**b, "recorded_at": e["at"], "filled_quantity": 0,
                                       "remaining_quantity": shares(b["quantity"]), "status": "PENDING"}
            elif e["kind"] == "FILL":
                o = rows[b["order_id"]]
                o["filled_quantity"] += b["quantity"]
                o["remaining_quantity"] -= b["quantity"]
                o["status"] = "PARTIAL" if o["remaining_quantity"] else "FILLED"
            elif e["kind"] in {"CANCEL", "EXPIRE"}:
                o = rows[b["order_id"]]
                if o["remaining_quantity"]:
                    o["status"] = "CANCELLED" if e["kind"] == "CANCEL" else "EXPIRED"
            elif e["kind"] == "BLOCKED" and b["order_id"] in rows:
                rows[b["order_id"]]["last_blocker"] = b["reason"]
        return list(rows.values())

    def leaderboard(self, usernames, now):
        accounts = [self.account(u, now) for u in sorted(set(usernames))]
        measured = sorted((a for a in accounts if a["return_pct"] is not None),
                          key=lambda a: (-Decimal(a["return_pct"]), a["username"]))
        for a in accounts:
            a["rank"] = None
        prior, rank = None, 0
        for index, a in enumerate(measured, 1):
            value = Decimal(a["return_pct"])
            if value != prior:
                rank = index
            a["rank"], prior = rank, value
        return measured + [a for a in accounts if a["return_pct"] is None]

    def export(self, path):
        self.verify()
        with Path(path).open("w", encoding="utf-8") as stream:
            for event in self.events():
                stream.write(canonical(event) + "\n")
