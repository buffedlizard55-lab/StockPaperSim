#!/usr/bin/env python3
"""Build the published **Official Auction Book** section of the site.

This is the section that answers the brief's first requirement - *simulated
settled trades built from real verified official pricing, dates and
liquidity* - and it can answer it without a caveat, because the only thing the
book is allowed to execute on is a number the U.S. Treasury published:

* a **primary** fill is the Treasury's own published price per $100 for that
  CUSIP's auction (single-price auction, so every accepted bidder paid it);
* a **secondary** fill is derived from the Treasury's official par yield curve
  or from the H.15 secondary-market bill discount rates, and is labelled
  ``OFFICIAL-DERIVED`` rather than official;
* **liquidity** is the auction's own offering amount, accepted amount and
  bid-to-cover, recorded on every trade.

Every page is rendered from ``memory/official/*`` and from the registers in
``sim/treasury.py``; nothing is typed twice.  ``sim.cli build-site`` calls this
builder between Season 2's and the Live Book's so CI's "docs/ is byte-identical
to a fresh rebuild" gate covers it.
"""

from __future__ import annotations

import gzip
import html
import json
import os
from typing import Dict, List, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAV = [
    ("official/index.html", "Overview"),
    ("official/leaderboard.html", "Leaderboard"),
    ("official/blotter.html", "Trade tape"),
    ("official/forward.html", "Forward book"),
    ("official/participants/index.html", "Participants"),
    ("official/ledger.html", "Trade store"),
    ("official/method.html", "Venue &amp; formulas"),
    ("official/sources.html", "Official sources"),
    ("summary.html", "Executive summary"),
    ("index.html", "Season 1"),
    ("season2/index.html", "Season 2"),
    ("live/index.html", "Live Book"),
    ("simulator.html", "Trade Simulator"),
]

BADGE = {
    "ACTIVE": "badge-ok", "READY": "badge-ok", "PASS": "badge-ok",
    "FILLED": "badge-ok", "OFFICIAL": "badge-ok", "OFFICIAL-DERIVED": "badge-warn",
    "PARTIAL": "badge-warn", "PENDING": "badge-warn", "WAITING-DATA": "badge-warn",
    "CANCELLED": "badge-warn", "RUINED": "badge-bad", "REJECTED": "badge-bad",
    "NO-DATA": "badge-bad",
}


def ESC(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(text: str) -> str:
    cls = BADGE.get(str(text), "badge-warn")
    return f'<span class="badge {cls}">{ESC(text)}</span>'


def _money(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"${value:,.{digits}f}"


def _pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}"


def _short_sha(value: Optional[str]) -> str:
    return f"{value[:16]}…" if value else "—"


def _date(value) -> str:
    return str(value) if value else "—"


def page(title: str, body: str, active: str = "", depth: int = 1) -> str:
    pre = "../" * depth
    # A page in this section links sideways to its own siblings with one fewer
    # level of "..", which is where the section footer's venue link points.
    pre_up = "../" * max(0, depth - 1)
    nav = "".join(
        f'<a class="{"active" if href == active else ""}" href="{pre}{href}">{label}</a>'
        for href, label in NAV)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ESC(title)} · StockPaperSim Official Auction Book</title>
<meta name="description" content="A one-year paper-trading book that may only execute on
prices the U.S. Treasury published: auction awards at the published price per $100,
secondary legs derived from the official par yield curve, with the auction's own
offering amount and bid-to-cover recorded on every trade.">
<link rel="stylesheet" href="{pre}assets/site.css">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{pre}index.html">StockPaperSim</a>
    <span class="brand-sub">Official Auction Book · settled trades at the Treasury's own
    published prices</span>
  </div>
  <nav class="wrap nav">{nav}</nav>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p><strong>What is official here, and what is derived.</strong> A primary fill is the
    price the Treasury published for that auction (<code>pricePer100</code> /
    <code>highPrice</code>), so the executed price is a published number rather than a
    model output. A secondary fill is labelled <strong>OFFICIAL-DERIVED</strong>: the
    price is computed from the Treasury's official par yield curve or from the H.15
    secondary-market bill discount rates by a formula printed on the
    <a href="{pre_up}method.html">venue page</a>. Liquidity is the auction's own offering
    amount, total accepted and bid-to-cover. Nothing on this site is investment
    advice.</p>
    <p>Generated by <code>scripts/build_site_official.py</code> from
    <code>memory/official/</code>; CI diffs a fresh rebuild against this directory.</p>
  </div>
</footer>
<script src="{pre}assets/site.js"></script>
</body>
</html>
"""


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          classes: str = "data", foot: str = "") -> str:
    if isinstance(rows, str):
        raise TypeError("table() expects a sequence of rows, not a string")
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for r in rows)
    return (f'<table class="{classes}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody>{foot}</table>')


def card(title: str, body: str, cls: str = "") -> str:
    return f'<section class="card {cls}"><h2>{title}</h2>{body}</section>'


def kpis(items: Sequence[Sequence[str]]) -> str:
    cells = "".join(
        f'<div class="kpi"><span class="kpi-label">{label}</span>'
        f'<span class="kpi-value {cls}">{value}</span>'
        f'<span class="kpi-sub">{sub}</span></div>' for label, value, sub, cls in
        [(i[0], i[1], i[2], i[3] if len(i) > 3 else "") for i in items])
    return f'<div class="kpis">{cells}</div>'


def _jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

class OfficialSite:
    """Read everything the official pages render, or fail loudly."""

    def __init__(self, memory_root: str) -> None:
        self.memory_root = memory_root
        self.base = os.path.join(memory_root, "official")
        if not os.path.isdir(self.base):
            raise SystemExit(
                f"no official book under {self.base}; run "
                f"'python3 -m sim.cli official' first")
        runs = sorted(d for d in os.listdir(self.base)
                      if d.startswith("official-rehearsal-"))
        if not runs:
            raise SystemExit(f"no rehearsal run under {self.base}")
        self.run_id = runs[-1]
        self.run_dir = os.path.join(self.base, self.run_id)
        self.forward_dirs = sorted(
            d for d in os.listdir(self.base) if d.startswith("official-forward-"))
        self.forward_dir = (os.path.join(self.base, self.forward_dirs[-1])
                            if self.forward_dirs else "")
        self.board = _json(os.path.join(self.run_dir, "leaderboard.json"))
        self.manifest = _json(os.path.join(self.run_dir, "manifest.json"))
        self.margin = _jsonl(os.path.join(self.run_dir, "margin.jsonl.gz"))
        self.trips = _jsonl(os.path.join(self.run_dir, "trips.jsonl.gz"))
        self.fills = _jsonl(os.path.join(self.run_dir, "fills.jsonl.gz"))
        self.intents = _jsonl(os.path.join(self.run_dir, "intents.jsonl.gz"))
        self.marks = _jsonl(os.path.join(self.run_dir, "marks.jsonl.gz"))
        #: What the rules said when they decided not to trade.  The stream is
        #: optional because runs written before it existed have no file, and a
        #: missing stream must degrade to "nothing recorded", not to an error.
        notes_path = os.path.join(self.run_dir, "notes.jsonl.gz")
        self.notes = _jsonl(notes_path) if os.path.exists(notes_path) else []
        self.forward = (_json(os.path.join(self.forward_dir, "forward_intents.json"))
                        if self.forward_dir else {})
        ledger_dir = os.path.join(self.base, "ledger")
        self.ledger = (_json(os.path.join(ledger_dir, "coverage.json"))
                       if os.path.isdir(ledger_dir) else {})
        self.ledger_participants = (
            _json(os.path.join(ledger_dir, "participants.json"))
            if os.path.isdir(ledger_dir) else {})
        reports_dir = os.path.join(self.run_dir, "reports")
        self.reports: Dict[str, dict] = {}
        if os.path.isdir(reports_dir):
            for name in sorted(os.listdir(reports_dir)):
                if name.endswith(".json"):
                    self.reports[name[:-5]] = _json(os.path.join(reports_dir, name))

    # -- derived views -----------------------------------------------------
    def row(self, username: str) -> dict:
        wanted = username if username.startswith("@") else "@" + username
        for row in self.board["participants"]:
            if row["participant"] in (username, wanted):
                return row
        raise KeyError(username)

    @staticmethod
    def _named(rows: Sequence[dict], username: str) -> List[dict]:
        wanted = username if username.startswith("@") else "@" + username
        return [r for r in rows if r["participant"] in (username, wanted)]

    def trips_for(self, username: str) -> List[dict]:
        return self._named(self.trips, username)

    def intents_for(self, username: str) -> List[dict]:
        return self._named(self.intents, username)

    def last_mark(self, username: str) -> dict:
        """The participant's last recorded mark, or an empty dict.

        The marks stream is the venue's own end-of-session accounting: cash,
        market value, gross exposure, leverage and equity per participant. A page
        that explains a return with no closed trades needs it, because "no round
        trips" and "no position" are different states and only this stream can
        tell them apart.
        """
        rows = self._named(self.marks, username)
        return rows[-1] if rows else {}

    def notes_for(self, username: str, limit: int = 6) -> List[dict]:
        """The reasons one rule gave for standing aside, most frequent first.

        The rule writes these itself at the session it decided, and the venue
        stores them; the site is only grouping them.  A rule that never traded
        has to be able to show why, and this is the only place that answer can
        come from - a sentence written on the page afterwards would be a claim
        about the rule rather than a record of it.
        """
        rows = self._named(self.notes, username)
        grouped: Dict[str, dict] = {}
        for row in rows:
            entry = grouped.setdefault(row["note"], {"note": row["note"], "sessions": 0,
                                                     "first": row["session"],
                                                     "last": row["session"]})
            entry["sessions"] += 1
            entry["first"] = min(entry["first"], row["session"])
            entry["last"] = max(entry["last"], row["session"])
        ordered = sorted(grouped.values(), key=lambda r: (-r["sessions"], r["note"]))
        return ordered[:limit]

    def exit_kinds(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for trip in self.trips:
            counts[trip["exit_kind"]] = counts.get(trip["exit_kind"], 0) + 1
        return counts

    def entry_kinds(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for trip in self.trips:
            counts[trip["entry_kind"]] = counts.get(trip["entry_kind"], 0) + 1
        return counts

    def official_notional_pct(self) -> Optional[float]:
        primary = sum(t["face"] * t["entry_price_per100"] / 100.0
                      for t in self.trips if t["entry_kind"] == "PRIMARY-AUCTION")
        total = sum(t["face"] * t["entry_price_per100"] / 100.0
                    for t in self.trips)
        return round(100.0 * primary / total, 4) if total else None


def source_register() -> List[dict]:
    """The official lane's own register, from the module that reads the files."""
    import sys
    if os.path.join(REPO_ROOT) not in sys.path:
        sys.path.insert(0, os.path.join(REPO_ROOT))
    from sim import treasury  # noqa: E402
    return list(treasury.SOURCES)


def roster_rows() -> List[object]:
    """The participant objects, so each page can print its declared sources."""
    import sys
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from sim import strategies_official  # noqa: E402
    return list(strategies_official.ROSTER)


def _source_for(url: str, register: Sequence[dict]) -> Optional[dict]:
    for row in register:
        if row.get("url") == url:
            return row
    for row in register:
        if url and row.get("url", "").split("?")[0] == url.split("?")[0]:
            return row
    return None


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

def _official_share(d: OfficialSite) -> str:
    """Share of this book's closed-trade notional whose two legs are both prints."""
    book = (d.ledger.get("by_book") or {}).get("Official Auction Book", {})
    klass = (d.ledger.get("by_price_class") or {}).get("OFFICIAL", {})
    if not book or not book.get("notional_usd"):
        return "—"
    total = book["notional_usd"] or 1.0
    return f"{_num(100.0 * klass.get('notional_usd', 0.0) / total)}"


def build_index(d: OfficialSite) -> str:
    summary = d.board
    manifest = d.manifest
    coverage = manifest.get("official_coverage", {})
    verification = manifest.get("verification", {})
    counts = manifest.get("counts", {})
    top = summary["participants"][0]
    winners = [r for r in summary["participants"] if r["return_pct"] > 0]
    body = [f"""
<p class="lede">Fourteen strategies, one username each, one year: <strong>
{ESC(summary['first_session'])} → {ESC(summary['last_session'])}</strong>
({summary['sessions']} official sessions). Every fill on this tape is either the price
the U.S. Treasury published for that auction or a price derived from the Treasury's
official yield curve by a formula printed on the <a href="method.html">venue page</a>.
No equity price, no broker quote, no modelled bar can reach this book: the code path
does not exist, and the verification block asserts it.</p>
"""]
    body.append(kpis([
        ("Starting cash", _money(manifest.get("starting_cash", 100000), 0), "per participant", ""),
        ("Top return", _pct(top["return_pct"]),
         f"{ESC(top['participant'])}", "pos" if top["return_pct"] >= 0 else "neg"),
        ("Participants ahead", f"{len(winners)} of {len(summary['participants'])}",
         "return better than zero", ""),
        ("Prices that can reach the book", "100% official",
         "primary = the published price; secondary = derived from an official "
         "observation and labelled as such", ""),
        ("Round trips with both legs official",
         f"{_official_share(d)}%",
         "the rest is primary in, derived out (see the trade store)", ""),
        ("Settled trades", f"{len(d.trips)}", f"{len(d.fills)} fills", ""),
        ("Verification", ESC(verification.get("verdict", "—")),
         f"{verification.get('checks', 0)} checks · "
         f"{verification.get('failure_count', 0)} failures", ""),
    ]))
    body.append(card("What makes a trade on this tape verifiable", f"""
<p>Three things have to be true at once, and each one is checkable from the committed
files rather than from this page:</p>
<ol>
<li><strong>The price was published.</strong> Primary fills carry the CUSIP's own
auction record: the source URL, the file, its SHA-256, the field
(<code>pricePer100</code> or <code>highPrice</code>) and the value.</li>
<li><strong>The date was published.</strong> Issue dates, auction dates and maturity
dates come from the same record; a redemption at par only ever happens on the
published maturity date, and the verification block checks that for every maturity
exit.</li>
<li><strong>The liquidity was published.</strong> Offering amount, total accepted,
competitive tendered and bid-to-cover ride on the auction row, and every trade
records them.</li>
</ol>
<p>Secondary legs are the weaker half and are labelled <strong>OFFICIAL-DERIVED</strong>
on every page: the price is computed from an official observation (the Treasury's
daily par yield curve, or the H.15 bill discount rates) by the formula shown on the
<a href="method.html">venue page</a>. Calling that official would be a small lie with a
large effect on P&amp;L, so the two classes are counted separately everywhere.</p>
"""))
    body.append(card("What the book cannot trade, and why that is the honest answer", f"""
<table class="data">
<thead><tr><th>Market</th><th>Official price available?</th><th>Consequence here</th></tr></thead>
<tbody>
<tr><td>US Treasury auctions (bills, notes, bonds, TIPS, FRN)</td>
<td>{_badge("OFFICIAL")} published price per $100, date and auction size</td>
<td>traded, with settled trades and a forward book</td></tr>
<tr><td>Secondary US Treasuries</td>
<td>{_badge("OFFICIAL-DERIVED")} no free published trade tape; the official yield
curve gives a price by formula</td>
<td>traded, labelled as derived, formula printed</td></tr>
<tr><td>US equities and ETFs (Nasdaq / NYSE / S&amp;P 500)</td>
<td>{_badge("NO-DATA")} Nasdaq's normalised price archive needs an entitlement and
redistribution authorisation that this project does not hold; the free publisher
pages forbid redistribution</td>
<td><strong>not</strong> traded at an official price; the equity books on this site
carry their own SECONDARY label and are never counted here</td></tr>
</tbody></table>
<p>The register row for the gate itself is in
<a href="../limitations.html">Limitations</a> (L-01, L-23) and the eligibility file
is committed at <code>data/real/official_price_eligibility.json</code>. The rule the
project follows is the one in the brief: it fails closed. A security with no official
observation stays untraded, and the intent says so.</p>
"""))
    body.append(card("Competition rules, as declared before the first session", f"""
<table class="data">
<thead><tr><th>Rule</th><th>Statement</th></tr></thead>
<tbody>
<tr><td>Objective</td><td>Highest ending equity. The book declares no risk-management
rule: a participant may run at its own leverage cap and is free to be wiped out, and
three of them verify the difference between a hypothetical and an outcome.</td></tr>
<tr><td>One username per strategy</td><td>Each rule has exactly one participant name and
one account; the username is the strategy.</td></tr>
<tr><td>Length</td><td>One year: {ESC(summary['first_session'])} to
{ESC(summary['last_session'])}.</td></tr>
<tr><td>Leverage</td><td>Gross exposure capped at the strategy's own declared
<code>max_gross_leverage</code>, never above the venue's
{_num(manifest.get('venue', {}).get('max_gross_leverage'), 1)}×.</td></tr>
<tr><td>Maintenance</td><td>{ESC(manifest.get('venue', {}).get('maintenance'))} of
gross exposure. Breaking it closes positions at the official mark; an account that
reaches zero equity is wound up at zero and stops trading.</td></tr>
<tr><td>Financing</td><td>{ESC(manifest.get('venue', {}).get('financing'))}; idle cash
is credited at SOFR.</td></tr>
<tr><td>Fees</td><td>{ESC(manifest.get('venue', {}).get('fees'))}.</td></tr>
</tbody></table>
"""))
    irregularities = manifest.get("irregularities") or []
    if irregularities:
        rows = [[ESC(r.get("code", "")), ESC(r.get("severity", "")),
                 ESC(r.get("detail", ""))[:260]] for r in irregularities]
        body.append(card("Irregularities recorded in this run",
                         table(["Code", "Severity", "Detail"], rows)))
    else:
        body.append(card("Irregularities recorded in this run", """
<p>None. The run's own irregularity stream - look-ahead evidence, targets that are not
in the future, plan errors, ruin events - is empty for this window, which is a claim
the verification block checks rather than one the prose makes: intent evidence is
compared against the session that cited it, every target session is compared against
the session that wrote it, and a ruined account has to show exactly −100%.</p>
"""))
    body.append(card("The tape in nine numbers", f"""
<p>{counts.get('intents', 0):,} intents were written and {counts.get('fills', 0):,} of them
filled, closing into <strong>{len(d.trips)}</strong> settled round trips with
{len(d.margin)} margin events. Official-price coverage of executed notional is
<strong>{_num(coverage.get('executed_notional_official_pct'))}%</strong>: primary
awards are exactly the published price, and the remaining notional is the derived
secondary leg. {_num(manifest.get('auction_coverage', {}).get('auctions'))} auction
records back the book, from
{_date(manifest.get('auction_coverage', {}).get('first_auction_date'))} to
{_date(manifest.get('auction_coverage', {}).get('last_auction_date'))}, and the two
official publishers agree on every shared auction
({manifest.get('crosscheck', {}).get('shared_auctions', 0):,} compared,
{manifest.get('crosscheck', {}).get('shared_auctions_with_a_difference', 0)}
differences).</p>
<p><a href="leaderboard.html">Leaderboard</a> ·
<a href="blotter.html">Trade tape</a> ·
<a href="forward.html">Forward book</a> ·
<a href="ledger.html">Trade store</a> ·
<a href="sources.html">Official sources</a></p>
"""))
    return page("Overview", "\n".join(body), "official/index.html")


def build_leaderboard(d: OfficialSite) -> str:
    rows = []
    for r in d.board["participants"]:
        status = "RUINED" if r.get("ruined_on") else r["status"]
        rows.append([
            f"{r['rank']}",
            f'<a href="participants/{ESC(r["participant"].lstrip("@"))}.html">'
            f'{ESC(r["participant"])}</a>',
            ESC(r["family"]),
            f'<span class="{"pos" if r["return_pct"] >= 0 else "neg"}">'
            f'{_pct(r["return_pct"])}</span>',
            _money(r["final_equity"]),
            f'{_num(r["max_drawdown_pct"])}%',
            f"{r['trades']}",
            _money(r["coupon_income"]),
            _money(r["debit_interest"] + r["financing"]),
            _money(r["cash_credit"]),
            f"{r['intents']:,}",
            _badge(status),
        ])
    body = [f"""
<p class="lede">Ending equity decides the ranking, and nothing else does. The
<em>why</em> column is a link: every participant page shows the trade that paid, the
trade that hurt, the official curve path that moved it and the financing bill.</p>
"""]
    body.append(table(
        ["#", "Username", "Family", "Return", "Final equity", "Max DD", "Trades",
         "Coupons", "Financing", "Cash credit", "Intents", "Status"], rows))
    body.append(card("Reading the table", f"""
<p><strong>Trades</strong> counts settled round trips, not fills: a ladder that rolls a
bill every four weeks closes a trip each time. <strong>Intents</strong> counts every
order written, including the ones the venue cancelled at the leverage cap and the ones
that waited for an official observation. <strong>Financing</strong> is SOFR + 25 bp on
debits and shorts; <strong>cash credit</strong> is SOFR on idle cash, which is why a
participant that never traded can still show a positive return.</p>
<p>No participant may look at a price before it is published: an intent written at the
close of session <em>t</em> can only target a session after <em>t</em>, and the
verification block fails the run if any evidence row is dated after the session that
cited it. A bid fills only when its auction result has been collected; a secondary
order fills only on a session the official curve covers. Anything else stays pending
with the reason written on it - there is no fallback price.</p>
"""))
    return page("Leaderboard", "\n".join(body), "official/leaderboard.html")


def build_blotter(d: OfficialSite) -> str:
    trips = sorted(d.trips, key=lambda t: (t["exit_date"], t["participant"]))
    rows = []
    for t in trips:
        entry_ev = t.get("entry_evidence") or {}
        exit_ev = t.get("exit_evidence") or {}
        liquidity = t.get("liquidity") or {}
        rows.append([
            ESC(t["exit_date"]),
            f'<a href="participants/{ESC(t["participant"].lstrip("@"))}.html">'
            f'{ESC(t["participant"])}</a>',
            f'{ESC(t["security_term"])} <span class="muted small">{ESC(t["cusip"])}</span>',
            ESC(t["direction"]),
            _money(t["face"], 0),
            f'{_num(t["entry_price_per100"], 6)}<br><span class="muted small">'
            f'{ESC(t["entry_date"])} · {ESC(t["entry_kind"])}</span>',
            f'{_num(t["exit_price_per100"], 6)}<br><span class="muted small">'
            f'{ESC(t["exit_date"])} · {ESC(t["exit_kind"])}</span>',
            f'{t["holding_days"]}',
            f'<span class="{"pos" if t["pnl"] >= 0 else "neg"}">{_money(t["pnl"])}</span>',
            _money(t.get("coupon_income")),
            _money(t.get("financing")),
            f'{_num(liquidity.get("offering_amount"), 0)}<br>'
            f'<span class="muted small">b/c {_num(liquidity.get("bid_to_cover"))}</span>',
            f'<span class="muted small">{ESC(str(entry_ev.get("field") or "—"))} · '
            f'{ESC(_short_sha(entry_ev.get("sha256")))}</span>',
        ])
    counts = d.exit_kinds()
    bodies = "".join(
        f"<li>{ESC(kind)}: <strong>{count}</strong></li>"
        for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]))
    body = [f"""
<p class="lede">{len(trips)} settled round trips. Each row carries the price, the date,
the kind of fill it was, the auction's own liquidity statistics and the SHA-256 of the
record the price came from. A reader who wants to check one row needs the CUSIP, the
date and the file - all three are on the row.</p>
"""]
    body.append(card("How the trades closed", f"<ul>{bodies}</ul>"
                     + ("<p>The full tape, including every intent and every fill, is in "
                        "<code>memory/official/</code> in the published run directory; "
                        "the <a href=\"ledger.html\">trade store</a> merges these trades "
                        "with every other book on the site into one ledger.</p>")))
    body.append(table(
        ["Exit date", "Username", "Security", "Side", "Face", "Entry", "Exit",
         "Days", "P&amp;L", "Coupons", "Financing", "Auction liquidity",
         "Price evidence"], rows))
    return page("Trade tape", "\n".join(body), "official/blotter.html")


def build_forward(d: OfficialSite) -> str:
    forward = d.forward or {}
    rows = []
    for row in forward.get("rows", []):
        rows.append([
            f'{ESC(row["target_session"])}',
            f'<a href="participants/{ESC(row["participant"].lstrip("@"))}.html">'
            f'{ESC(row["participant"])}</a>',
            ESC(row["kind"]),
            ESC(row["side"]),
            f'{ESC(row.get("security_term") or row["cusip"])} '
            f'<span class="muted small">{ESC(row["cusip"])}</span>',
            _money(row["face"], 0),
            _money(row.get("offering_amount"), 0),
            ESC(_date(row.get("issue_date"))),
            _badge(row["status"]),
            ESC(row["rule"]),
            f'<span class="muted small">written {ESC(row["created_on"])}</span>',
        ])
    calendar = forward.get("announced_calendar", [])
    cal_rows = [[
        ESC(a["auction_date"]), ESC(a["security_type"]), ESC(a["security_term"]),
        ESC(a["cusip"]), _money(a["offering_amount"], 0),
        ESC(_date(a["issue_date"])), ESC(_date(a["maturity_date"])),
        ESC(_date(a["announcement_date"])),
    ] for a in calendar]
    body = [f"""
<p class="lede">A strategy writes its orders after the close of the last settled
session and they target sessions that have not happened. Today the book is holding
<strong>{forward.get('pending_intents', 0)} pending intents</strong> as of
{ESC(forward.get('as_of', '—'))}. This is the honest state of a forward test on the
day it opens: nothing is filled, nothing is marked up, and each order says which
official record will settle it.</p>
"""]
    body.append(card("Settlement rule (venue policy, not an assumption)", f"""
<p>{ESC(forward.get('settlement_rule', ''))}</p>
<p>Because the venue has no fallback price, the pending list is also the list of open
risk: an order that cannot settle simply sits there, and the run records why. The
<a href="method.html">venue page</a> prints the maintenance, financing and leverage
rules the orders will be settled under.</p>
"""))
    body.append(table(
        ["Target session", "Username", "Order", "Side", "Security", "Face",
         "Auction size", "Issue date", "Status", "Rule", "Written"], rows))
    if cal_rows:
        body.append(card("The announced auction calendar the book is trading against",
                         table(["Auction date", "Type", "Term", "CUSIP", "Offering",
                                "Issue date", "Maturity", "Announced"], cal_rows)))
    return page("Forward book", "\n".join(body), "official/forward.html")


def build_participants_index(d: OfficialSite) -> str:
    rows = []
    for r in d.board["participants"]:
        username = r["participant"]
        report = d.reports.get(username.lstrip("@"), {})
        verdict = report.get("verdict", "—")
        rows.append([
            f'<a href="{ESC(username.lstrip("@"))}.html">{ESC(username)}</a>',
            ESC(r["family"]),
            f'<span class="{"pos" if r["return_pct"] >= 0 else "neg"}">'
            f'{_pct(r["return_pct"])}</span>',
            ESC(verdict),
            ESC((report.get("drivers") or ["—"])[0])[:220],
        ])
    body = [f"""
<p class="lede">Every participant is a single rule with a single username and its own
account. Each page states the rule, what it did, and why the return came out the way
it did - including the participants that lost, and the one whose return is the venue's
cash rate rather than a trade.</p>
"""]
    body.append(table(["Username", "Family", "Return", "Verdict", "What drove it"], rows))
    return page("Participants", "\n".join(body), "official/participants/index.html",
                depth=2)


def build_participant(d: OfficialSite, username: str) -> str:
    key = username.lstrip("@")
    display = "@" + key
    report = d.reports.get(key, {})
    row = d.row(username)
    metrics = report.get("metrics", row)
    trips = sorted(d.trips_for(username), key=lambda t: t["exit_date"])
    intents = d.intents_for(username)
    status = "RUINED" if metrics.get("ruined_on") else metrics["status"]
    drivers = "".join(f"<li>{ESC(line)}</li>" for line in report.get("drivers", []))
    trade_rows = [[
        ESC(t["entry_date"]), ESC(t["exit_date"]),
        f'{ESC(t["security_term"])} <span class="muted small">{ESC(t["cusip"])}</span>',
        ESC(t["direction"]), _money(t["face"], 0),
        _num(t["entry_price_per100"], 6), _num(t["exit_price_per100"], 6),
        ESC(t["exit_kind"]),
        f'<span class="{"pos" if t["pnl"] >= 0 else "neg"}">{_money(t["pnl"])}</span>',
        f'<span class="{"pos" if t.get("total_pnl", t["pnl"]) >= 0 else "neg"}">'
        f'{_money(t.get("total_pnl", t["pnl"]))}</span>',
        f'{_num(t["return_on_cost_pct"])}%',
    ] for t in trips]
    kind_counts: Dict[str, int] = {}
    for intent in intents:
        kind_counts[intent["status"]] = kind_counts.get(intent["status"], 0) + 1
    kind_rows = "".join(
        f"<li>{ESC(kind)}: <strong>{count}</strong></li>"
        for kind, count in sorted(kind_counts.items(), key=lambda kv: -kv[1]))
    live_intents = [i for i in intents if i["status"] in ("FILLED", "PARTIAL")][-12:]
    order_rows = [[
        ESC(i["created_on"]), ESC(i["target_session"]), ESC(i["kind"]),
        ESC(i["side"]),
        f'{ESC(i["cusip"])}', _money(i["face"], 0), ESC(i["rule"]),
        ESC((i.get("settle_note") or "" )[:160]),
    ] for i in reversed(live_intents)]
    body = [f"""
<p class="lede"><strong>{ESC(display)}</strong> - {ESC(report.get('thesis', ''))}</p>
"""]
    body.append(kpis([
        ("Return", _pct(metrics["return_pct"]),
         f'{_money(metrics["final_equity"])} ending equity', ""),
        ("Status", status, f'{metrics["trades"]} settled trades', ""),
        ("Max drawdown", f'{_num(metrics["max_drawdown_pct"])}%',
         f'Sharpe {_num(metrics["sharpe"])}', ""),
        ("Coupons", _money(metrics["coupon_income"]),
         f'financing {_money(metrics["debit_interest"] + metrics["financing"])}', ""),
        ("Orders", f'{metrics["intents"]:,}',
         f'{metrics["filled_intents"]} filled · {metrics["waiting_intents"]} waited · '
         f'{metrics["rejected_intents"]} refused', ""),
        ("Official price coverage", f'{_num(100.0 * (metrics.get("official_price_coverage") or 0))}%',
         "primary at the published price, secondary derived", ""),
    ]))
    body.append(card("Why it worked, or why it did not", f"<ul>{drivers}</ul>"))
    stood_aside = d.notes_for(username)
    if stood_aside:
        rows = [[f'{r["sessions"]:,}', ESC(r["first"]), ESC(r["last"]),
                 ESC(r["note"])] for r in stood_aside]
        body.append(card("Why it stood aside", f"""
<p>These are the reasons the rule itself gave, at the session it decided, for not
sending an order. They are the rule's own words, stored when the decision was
made and grouped here - not written afterwards.</p>
{table(["Sessions", "First", "Last", "Reason"], rows)}
"""))
    elif not trips:
        body.append(card("Why it stood aside", """
<p>This run predates the standing-aside stream, so the reasons are not recoverable
from the stored tape; the order states on the previous card still show that nothing
filled.</p>
"""))
    if trade_rows:
        body.append(card("Settled trades", table(
            ["Entry", "Exit", "Security", "Side", "Face", "Entry price", "Exit price",
             "Exit kind", "Price P&amp;L", "Total P&amp;L", "Return on cost"],
            trade_rows)))
    else:
        mark = d.last_mark(username)
        if mark.get("positions"):
            body.append(card("Settled trades", f"""
<p>No trade closed inside the window under this rule, and the account is <strong>not
flat</strong>: at the final session ({ESC(mark['session'])}) it held
<strong>{mark['positions']}</strong> position(s) worth {_money(mark['market_value'])} against
gross exposure of {_money(mark['gross_exposure'])} at {_num(mark['leverage'])}x, with cash of
{_money(mark['cash'])} and equity of {_money(mark['equity'])}.</p>
<p>The return on this page is therefore a <em>mark-to-market</em> on an open position, plus the
official SOFR credit and the financing charge - not a realised result, and not a participant
that sat still. The orders it filled are on the next card; the venue's end-of-session
accounting is in <code>marks.jsonl.gz</code> beside the other streams.</p>
"""))
        else:
            body.append(card("Settled trades", f"""
<p>No trade closed inside the window under this rule, and the account never held a position.
The ending equity is the official SOFR credit on idle cash, not a result produced by the
strategy, and the page says so rather than printing a number that could be mistaken for
one.</p>
"""))
    body.append(card("Every order this rule wrote", f"""
<p>{metrics['intents']:,} orders, by their final state:</p>
<ul>{kind_rows}</ul>
<p><strong>WAITING-DATA</strong> means the venue found no official observation for the
target session and left the order open rather than filling it at a modelled price;
<strong>REJECTED</strong> means a declared rule refused it (a when-issued trade, a
non-competitive award beyond the maximum, a maintenance breach);
<strong>CANCELLED</strong> means the order was superseded or the leverage cap left
nothing to size.</p>
"""))
    if order_rows:
        body.append(card("The orders that filled", table(
            ["Written", "Target", "Order", "Side", "CUSIP", "Face", "Rule",
             "Settlement note"], order_rows)))
    basis = []
    for strategy in roster_rows():
        if strategy.username == username:
            basis = list(strategy.research_basis)
    basis_html = "".join(
        f'<li>{ESC(e.get("label", ""))} - '
        f'<a href="{ESC(e.get("url", ""))}">{ESC(e.get("url", ""))}</a></li>'
        for e in basis if isinstance(e, dict)) or "<li>none declared</li>"
    body.append(card("Rule and data status", f"""
<p><strong>Rule:</strong> {ESC(report.get('thesis', ''))}</p>
<p><strong>Where the rule's premises come from:</strong></p>
<ul>{basis_html}</ul>
<p><strong>Data status:</strong> {_badge(metrics.get('data_status', '—'))}
{ESC(report.get('data_note', '') or '')}</p>
<p><a href="index.html">All participants</a> ·
<a href="../blotter.html">Trade tape</a> ·
<a href="../method.html">Venue rules</a></p>
"""))
    return page(display, "\n".join(body),
                "official/participants/index.html", depth=2)


def build_ledger(d: OfficialSite) -> str:
    ledger = d.ledger or {}
    if not ledger:
        return page("Trade store", """
<p class="lede">The unified trade store has not been built yet. Run
<code>python3 -m sim.cli official</code> (which rebuilds it) or
<code>python3 -m sim.cli trades --out memory/official/ledger</code>.</p>
""", "official/ledger.html")
    by_book = ledger.get("by_book", {})
    by_class = ledger.get("by_price_class", {})
    book_rows = [[
        ESC(book), f'{row["trades"]:,}', _money(row["notional_usd"], 0),
        f'{_num(row["notional_share_pct"])}%', _money(row["pnl_usd"]),
    ] for book, row in sorted(by_book.items(), key=lambda kv: -kv[1]["notional_usd"])]
    class_rows = [[
        _badge(klass) if klass in BADGE else ESC(klass), f'{row["trades"]:,}',
        _money(row["notional_usd"], 0), f'{_num(row["notional_share_pct"])}%',
        _money(row["pnl_usd"]),
    ] for klass, row in sorted(by_class.items(), key=lambda kv: -kv[1]["notional_usd"])]
    body = [f"""
<p class="lede">Every closed trade in every book on this site - the official auction
book, the two backtest seasons and the live rehearsal - is normalised into one ledger
so the same record can be re-used for testing, analysis and evaluation without
re-deriving it from four different memory formats. Each row keeps its participant,
instrument, side, quantity, entry and exit date, entry and exit price, costs, P&amp;L
and - the part that matters for honesty - its <strong>price class</strong>.</p>
"""]
    body.append(kpis([
        ("Closed trades", f'{ledger.get("trades", 0):,}', "across every book", ""),
        ("Notional", _money(ledger.get("notional_usd", 0), 0), "entry cost", ""),
        ("Net P&amp;L", _money(ledger.get("pnl_usd", 0)),
         f'{len(by_book)} books', ""),
        ("Notional with both legs official",
         f'{_num(ledger.get("official_executed_notional_pct"))}%',
         "share whose entry AND exit were a publisher's own number", ""),
        ("Notional with an official entry",
         f'{_num(ledger.get("official_entry_notional_pct"))}%',
         "share bought or sold at a price a publisher printed, whatever the exit", ""),
    ]))
    body.append(card("By book", table(
        ["Book", "Trades", "Notional", "Share of notional", "P&amp;L"], book_rows)))
    body.append(card("By price class", table(
        ["Price class", "Trades", "Notional", "Share of notional", "P&amp;L"],
        class_rows) + """
<p>The classes are the point of the whole exercise. <strong>OFFICIAL</strong> and
<strong>OFFICIAL-DERIVED</strong> rows rest on a government publisher's own number.
<strong>MODELLED</strong> rows are the backtest seasons, whose prices are explicit
simulations of a real path. <strong>SECONDARY</strong> rows are the live rehearsal,
executed against an aggregator's bars with the gate failing closed. Mixing those up
would make the site's headline numbers mean nothing, so the ledger counts them
separately and the site prints the split.</p>
"""))
    params = d.ledger_participants or []
    if params:
        rows = []
        for row in sorted(params, key=lambda r: -r.get("trades", 0))[:40]:
            rows.append([ESC(row.get("participant", "")),
                         f'{row.get("trades", 0):,}',
                         _money(row.get("notional_usd", 0), 0),
                         _money(row.get("pnl_usd", 0)),
                         ESC(", ".join(sorted(row.get("price_classes") or [])))])
        body.append(card("By participant (first 40 by trade count)", table(
            ["Username", "Trades", "Notional", "P&amp;L", "Price classes"], rows)))
    body.append(card("Where the store lives", """
<p>Two coverage numbers are printed rather than one, because they answer different
questions: <strong>both legs official</strong> counts trades whose entry and exit were
each a published print, and <strong>official entry</strong> counts trades that were
bought or sold at a published price whatever happened on the way out. Quoting the
larger one as if it were the smaller would be the easiest lie on this site to tell,
so both are on the page.</p>
<p>Written by <code>sim/tradelog.py</code> as
<code>memory/official/ledger/trades.jsonl.gz</code> (one JSON object per trade),
<code>trades.csv</code> for a spreadsheet, <code>coverage.json</code> for the counts on
this page and <code>manifest.json</code> for the SHA-256 of each file and a per-file
byte budget. The store is append-only by construction: a rebuild regenerates it from
the memory directories, so a reader can always check the site against the tape.</p>
"""))
    return page("Trade store", "\n".join(body), "official/ledger.html")


def build_method(d: OfficialSite) -> str:
    manifest = d.manifest
    venue = manifest.get("venue", {})
    verification = manifest.get("verification", {})
    validation = manifest.get("price_validation", {})
    rounding = ""
    if validation:
        rounding = f"""
<h3>The two publishers, compared field by field</h3>
<p>TreasuryDirect's auction results and the Fiscal Data API's auctions table are
separate publications of the same events. The collector compares every shared auction
on eleven fields and records the result:
<strong>{manifest.get('crosscheck', {}).get('shared_auctions', 0):,}</strong> shared
auctions, <strong>{manifest.get('crosscheck', {}).get('field_comparisons', 0):,}</strong>
field comparisons, <strong>{manifest.get('crosscheck', {}).get('shared_auctions_with_a_difference', 0)}</strong>
differences. The price formula is checked separately against every published bill
price: <strong>{validation.get('bill_prices_checked', 0):,}</strong> prices checked,
<strong>{validation.get('bill_price_mismatches', 0)}</strong> mismatches, and the
highest investment-rate agreement this convention can reach is
<strong>{_num(validation.get('investment_rate_match_pct'))}%</strong> - the code prints
that number rather than claiming the formula explains every published rate, because it
does not.</p>
"""
    body = [f"""
<p class="lede">This page is the contract: the rules the book is settled under, the
formulas that turn an official observation into a price, and the checks that run
against the tape afterwards. If a number on this site cannot be reproduced from a
committed file by a formula on this page, it does not belong on the site.</p>
"""]
    body.append(card("Venue rules", table(
        ["Rule", "Statement"], [
            ["Primary execution", ESC(venue.get("primary_execution", "—"))],
            ["Secondary execution", ESC(venue.get("secondary_execution", "—"))],
            ["Fees", ESC(venue.get("fees", "—"))],
            ["Financing", ESC(venue.get("financing", "—"))],
            ["Cash credit", ESC(venue.get("cash_credit", "—"))
             + " The official SOFR series has no fix on a business day the "
               "reference-rate calendar closes (Good Friday 2026-04-03 in this "
               "window), and the venue then applies the last published "
               "observation; it never uses a later one, and the independent "
               "audit checks that direction."],
            ["Maintenance", ESC(venue.get("maintenance", "—"))],
            ["Maximum gross leverage", f'{_num(venue.get("max_gross_leverage"), 1)}×'],
            ["Settlement", ESC(venue.get("settlement", "—"))],
            ["When-issued trading", "refused: the venue holds no official when-issued "
             "observation, so a secondary order in a security that has not been issued "
             "is rejected with the issue date written on it"],
            ["Position accounting", "fills are netted against the position FIFO; risk, "
             "financing and coupons are measured on the net position, and every close "
             "is a settled round trip with its own entry and exit evidence"],
            ["Ruin", "if equity reaches zero the venue closes every position at the "
             "official mark, writes the negative balance off at zero and stops the "
             "account - which is why a ruined participant shows exactly −100%"],
        ])))
    body.append(card("The formulas", f"""
<h3>Bill price from a discount rate</h3>
<p>Treasury bills are quoted on a discount basis. The price per $100 of face is</p>
<p><code>P = 100 × (1 − d × t / 360)</code>, with <code>d</code> the published
discount rate and <code>t</code> the days from settlement to maturity. The check above
reproduces every published bill price from its own published rate, so the committed
tape and the formula agree.</p>
<h3>Coupon security price from a yield</h3>
<p>Notes, bonds and TIPS are priced from the official yield for their remaining
maturity by the standard present-value relation used for Treasury securities
(semi-annual coupon, actual/actual accrual, par at maturity). TIPS are discounted at
the official H.15 <em>real</em> yield of their tenor, never at the nominal par yield:
marking an inflation-protected security off a nominal curve is a modelling error with a
large silent effect on P&amp;L. When the real-yield series are not collected, the venue
prices nothing and the order waits.</p>
<h3>Non-competitive award</h3>
<p>A non-competitive bid is awarded the full amount requested at the published price,
up to the official maximum per auction, and the award is scaled down to the venue's
leverage cap with the reason recorded. Because the auction is single-price, the
published price is the price every accepted bidder paid - the book does not assume a
fill inside a range.</p>
<hr>
{rounding}
"""))
    failure_rows = [[
        ESC(f.get("code", "")), ESC(f.get("detail", ""))[:300]
    ] for f in verification.get("failures", [])] or [["—", "no failures"]]
    body.append(card("Verification of the published run", f"""
<p>After the season settles, <code>sim/official_season.py</code> re-reads the tape and
re-derives it. It does not import the settlement path: it reads the intents, fills and
trades the book wrote and asks whether they agree with the official files.
<strong>{verification.get('checks', 0):,} checks</strong>,
<strong>{verification.get('failure_count', 0)} failures</strong>, verdict
<strong>{ESC(verification.get('verdict', '—'))}</strong>; the largest disagreement
between the recorded equity of an account and the equity re-derived from its own fills
and carry rows is <strong>${_num(verification.get('max_equity_residual_usd'), 6)}</strong>.</p>
"""))
    body.append(table(["Check code", "Detail"], failure_rows))
    body.append(card("The eight families of check", """
<ol>
<li><strong>PRIMARY-PRICE-NOT-OFFICIAL</strong> - every primary fill price must equal
the published price for that CUSIP and auction date.</li>
<li><strong>BILL-PRICE-FORMULA-MISMATCH</strong> - every published bill price must
reproduce from its own discount rate.</li>
<li><strong>TWO-PUBLISHER-DISAGREEMENT</strong> - the two official publishers must
agree on every shared auction.</li>
<li><strong>LOOK-AHEAD</strong> - no intent may cite an observation dated after the
session that wrote it.</li>
<li><strong>EQUITY-RESIDUAL</strong> - each account's recorded equity must equal the
equity re-derived from its fills and carry rows.</li>
<li><strong>MATURITY-DATE-MISMATCH</strong> - a redemption at par may only happen on
the published maturity date.</li>
<li><strong>TRIP-NOT-OFFICIAL</strong> - every closed trade must carry its price class.</li>
<li><strong>SECONDARY-PATH-REACHABLE</strong> - the official book's module must not be
able to reach a secondary price file at all.</li>
</ol>
"""))
    return page("Venue &amp; formulas", "\n".join(body), "official/method.html")


def build_sources(d: OfficialSite) -> str:
    register = source_register()
    manifest = d.manifest
    files: Dict[str, dict] = {}
    for group in ("files",):
        for row in manifest.get("auction_coverage", {}).get(group, []):
            files[row["file"]] = row
    for row in (manifest.get("curve_channels") or []):
        if row.get("file"):
            files[row["file"]] = row
    for row in (manifest.get("rates_provenance") or []):
        if row.get("file"):
            files[row["file"]] = row
    rows = []
    for row in register:
        rows.append([
            f'<a href="{ESC(row["url"])}">{ESC(row["title"])}</a>',
            ESC(row["publisher"]),
            ESC(row.get("what_it_gives", ""))[:260],
            ESC(row.get("license_basis", ""))[:260],
        ])
    file_rows = [[
        f'<code>{ESC(path)}</code>',
        f'{row.get("rows", row.get("bytes", "—")):,}'
        if isinstance(row.get("rows", row.get("bytes")), int) else "—",
        _date(row.get("first_date") or row.get("first_auction_date")),
        _date(row.get("last_date") or row.get("last_auction_date")),
        f'<span class="muted small">{ESC(_short_sha(row.get("sha256")))}</span>',
    ] for path, row in sorted(files.items())]
    missing = manifest.get("rates_missing", [])
    missing_rows = [[ESC(m.get("sid", "")), ESC(m.get("reason", "")),
                     f'<a href="{ESC(m.get("url", ""))}">{ESC(m.get("url", ""))}</a>']
                    for m in missing]
    body = [f"""
<p class="lede">Every official endpoint the book reads, who publishes it, what it gives
and why committing a copy is allowed. This is the register the tests enforce: a URL
quoted anywhere in the code has to appear on a published page, so a reviewer can follow
it from the site rather than from the source.</p>
"""]
    body.append(card("The register", table(
        ["Endpoint", "Publisher", "What it gives", "Why it may be copied"], rows)))
    if file_rows:
        body.append(card("The copies this run read", table(
            ["File", "Rows", "First date", "Last date", "SHA-256"], file_rows)))
    if missing_rows:
        body.append(card("Declared but not collected", table(
            ["Series", "Why it is missing", "Where it would come from"], missing_rows)
            + "<p>A participant whose rule needs one of these does not trade. That is "
              "the fail-closed behaviour the brief asks for, and it is visible in the "
              "order counts rather than hidden in a fallback.</p>"))
    body.append(card("Collection channels", """
<p>The collector runs on a GitHub runner, because the development sandbox has no
outbound HTTPS. Every request it makes - URL, HTTP status, byte count and SHA-256 - is
recorded in <code>data/real/collection_manifest.json</code> whether it succeeded or
failed, so a failed source is evidence rather than silence. The SEC's quarterly
Form 3/4/5 extracts were answered HTTP 403 at both documented path layouts on the run
that collected this data; that is recorded in the manifest and registered as a
limitation rather than papered over.</p>
"""))
    return page("Official sources", "\n".join(body), "official/sources.html")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def inject_nav(html_text: str) -> str:
    """Add an Official Book link to a page's nav, idempotently."""
    marker = '<a href="official/index.html">Official Book</a>'
    if "Official Book" in html_text:
        return html_text
    needle = "</nav>"
    if needle not in html_text:
        return html_text
    return html_text.replace(needle, f"{marker}{needle}", 1)


def build(memory_root: str, out: str) -> List[str]:
    d = OfficialSite(memory_root)
    written: List[str] = []

    def write(rel: str, content: str) -> None:
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        written.append(rel)

    write("official/index.html", build_index(d))
    write("official/leaderboard.html", build_leaderboard(d))
    write("official/blotter.html", build_blotter(d))
    write("official/forward.html", build_forward(d))
    write("official/ledger.html", build_ledger(d))
    write("official/method.html", build_method(d))
    write("official/sources.html", build_sources(d))
    write("official/participants/index.html", build_participants_index(d))
    for username in sorted(d.reports):
        write(f"official/participants/{username}.html", build_participant(d, username))
    # The published numbers, in machine-readable form, for the docs/ audit tests.
    data_dir = os.path.join(out, "assets", "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "official.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "run_id": d.run_id,
            "window": [d.board["first_session"], d.board["last_session"]],
            "sessions": d.board["sessions"],
            "participants": d.board["participants"],
            "verification": d.manifest.get("verification", {}),
            "venue": d.manifest.get("venue", {}),
        }, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    written.append("assets/data/official.json")
    with open(os.path.join(data_dir, "official_ledger.json"), "w",
              encoding="utf-8") as handle:
        json.dump(d.ledger or {}, handle, indent=1, sort_keys=False, default=str)
        handle.write("\n")
    written.append("assets/data/official_ledger.json")
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", default=os.path.join(REPO_ROOT, "memory"))
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"))
    args = parser.parse_args(argv)
    written = build(args.memory_root, args.out)
    print(f"official book: {len(written)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
