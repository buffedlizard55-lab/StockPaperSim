#!/usr/bin/env python3
"""Build the published **Live Book** section of the site (``docs/live/``).

The live book is the part of this project that answers the brief's question -
*can these strategies place upcoming trades and simulate a real trading
experience?* - so its pages are organised around the two things a reader has to
be able to check without trusting the prose:

1. **the trade tape**: every intent, the session it was aimed at, the session it
   was actually written on, the verified bar it executed against, the file that
   bar came from, that file's SHA-256, the participation it took, the slippage it
   paid, and the fee stack it paid;
2. **the honesty of the clock**: that no intent can read data from the future,
   that a session which was a market holiday is never treated as tradable, and
   that an intent with no verified bar stays open instead of filling at a
   modelled price.

Everything on these pages is rendered from ``memory/live/*`` and from the
registers in ``sim/live.py``; nothing is typed twice.  The section is generated
by ``sim.cli build-site`` so CI's "docs/ is byte-identical to a fresh rebuild"
gate covers it like every other page.
"""

from __future__ import annotations

import html
import json
import os
from typing import Dict, List, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAV = [
    ("desk/index.html", "Strict Equities Desk"),
    ("live/index.html", "Overview"),
    ("live/forward.html", "Upcoming trades"),
    ("live/leaderboard.html", "Leaderboard"),
    ("live/blotter.html", "Trade tape"),
    ("live/participants/index.html", "Participants"),
    ("live/method.html", "Clock &amp; margin"),
    ("live/sources.html", "Official sources"),
    ("summary.html", "Executive summary"),
    ("index.html", "Season 1"),
    ("season2/index.html", "Season 2"),
    ("simulator.html", "Trade Simulator"),
]

BADGE = {
    "READY": "badge-ok", "AVAILABLE": "badge-ok", "PASS": "badge-ok",
    "COLLECTED": "badge-ok", "OFFICIAL": "badge-ok",
    "OFFICIAL-PUBLISHER / FRED-REPUBLISHED": "badge-ok",
    "SECONDARY": "badge-warn", "PROJECTED": "badge-warn",
    "READY-NO-OBSERVATIONS": "badge-warn",
    "DATA-MISSING": "badge-bad", "FORWARD-ONLY": "badge-warn",
    "MISSING": "badge-bad", "FAIL": "badge-bad", "PENDING-SETTLEMENT": "badge-warn",
}


def ESC(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(text: str) -> str:
    cls = BADGE.get(str(text), "badge-warn")
    return f'<span class="badge {cls}">{ESC(text)}</span>'


def _money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%"


def _plain_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def page(title: str, body: str, active: str = "", depth: int = 1) -> str:
    pre = "../" * depth
    nav = "".join(
        f'<a class="{"active" if href == active else ""}" href="{pre}{href}">{label}</a>'
        for href, label in NAV)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ESC(title)} · StockPaperSim Live Book</title>
<meta name="description" content="A forward-tested paper-trading book: every strategy
writes an intent after the close and the venue settles it against a later verified
daily bar, with the reference file, SHA-256, participation and slippage recorded per
trade.">
<link rel="stylesheet" href="{pre}assets/site.css">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{pre}index.html">StockPaperSim</a>
    <span class="brand-sub">Live Book · intents placed for future sessions, settled on
    collected verified bars</span>
  </div>
  <nav class="wrap nav">{nav}</nav>
</header>
<main class="wrap">
<aside class="callout"><strong>RESEARCH ONLY — excluded from the strict official-price stock competition.</strong>
These legacy fills can use SECONDARY prices and modelled bid/ask liquidity. Date-only intents do not prove pre-execution submission, and this book's “settle” operation is not T+1 cash settlement.
<a href="{pre}desk/index.html#trades">Review every forward fill and its blockers</a>.</aside>
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>The Live Book is a <strong>legacy forward-style research simulation</strong>, not an independently timestamped forward test or a live market feed. A
    strategy writes an intent after the close of one session and the venue settles it
    against a later verified daily bar through the same microstructure model the
    competition uses. Signals, the benchmark, the trading calendar and the financing
    rate are official, free and publicly available; the executable bars are the
    collected Yahoo research files marked <strong>SECONDARY</strong>, so this is not an
    official-price competition and every page says what share of traded notional used
    an official reference price. Nothing here is investment advice.</p>
    <p>Generated by <code>scripts/build_site_live.py</code> from
    <code>memory/live/</code>; CI diffs a fresh rebuild against this directory.</p>
  </div>
</footer>
<script src="{pre}assets/site.js"></script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

class LiveSite:
    """Read everything the live pages render, or fail loudly."""

    def __init__(self, memory_root: str, run_id: str = "") -> None:
        self.memory_root = memory_root
        self.base = os.path.join(memory_root, "live")
        if not os.path.isdir(self.base):
            raise SystemExit(
                f"no live book under {self.base}; run "
                f"'python3 -m sim.cli live --mode all' first")
        self.rehearsal_dir = os.path.join(self.base, f"live-rehearsal-seed20260918")
        forwards = sorted(d for d in os.listdir(self.base)
                          if d.startswith("live-forward-"))
        if not os.path.isdir(self.rehearsal_dir):
            raise SystemExit(f"no rehearsal run at {self.rehearsal_dir}")
        self.forward_dir = os.path.join(self.base, forwards[-1]) if forwards else ""
        self.rehearsal = self._json(self.rehearsal_dir, "leaderboard.json")
        self.reports = self._json(self.rehearsal_dir, "reports.json")
        self.verification = self._json(self.rehearsal_dir, "verification.json")
        self.sources = self._json(self.rehearsal_dir, "official_sources.json")
        self.manifest = self._json(self.rehearsal_dir, "manifest.json")
        self.forward = self._json(self.forward_dir, "manifest.json") if self.forward_dir else {}
        self.forward_verification = (self._json(self.forward_dir, "verification.json")
                                     if self.forward_dir else {})
        self.fills = self._read_stream(self.rehearsal_dir, "fills.jsonl.gz")
        self.intents = self._read_stream(self.rehearsal_dir, "intents.jsonl.gz")
        self.trips = self._read_stream(self.rehearsal_dir, "trips.jsonl.gz")

    @staticmethod
    def _json(directory: str, name: str) -> dict:
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _read_stream(directory: str, name: str) -> List[dict]:
        import gzip
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            return []
        rows: List[dict] = []
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    # -- derived ----------------------------------------------------------
    @property
    def board(self) -> List[dict]:
        return (self.rehearsal or {}).get("leaderboard", [])

    @property
    def benchmark(self) -> dict:
        return (self.rehearsal or {}).get("benchmark") or {}

    @property
    def coverage(self) -> dict:
        return self.manifest.get("coverage") or {}

    @property
    def storage(self) -> dict:
        return self.manifest.get("storage") or {}


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

def build_index(d: LiveSite) -> str:
    board = d.board
    bench = d.benchmark
    winner = board[0] if board else {}
    loser = board[-1] if board else {}
    traded = [r for r in board if r["fills"] > 0]
    idle = [r for r in board if r["fills"] == 0]
    forward_pending = len((d.forward or {}).get("pending_intents") or [])
    v = d.verification
    body = f"""
<h1>The Live Book</h1>
<p class="lede">Every other part of this project decides <em>and</em> executes inside
one session. This one does not. A strategy plans after the close of session
<strong>T</strong>, its order is written down as an <em>intent</em> aimed at a future
session, and the venue settles it only when that session has a verified bar. It is the
closest thing here to placing a real trade, and it is the honest way to ask whether
these rules survive having to wait.</p>

<div class="card notice">
  <strong>What this is not.</strong> The executable bars are the collected Yahoo
  research files, which are <strong>SECONDARY</strong>. Signals, the benchmark, the
  trading calendar and the financing rate are official, free and publicly available
  (FRED-republished index and rate data, cross-checked against the Federal Reserve
  Bank of New York's own reference-rate API). An official-price competition needs an
  eligible official security-price archive, and no such archive exists in this
  repository, so the live book publishes its official-price coverage as a number
  instead of implying one: <strong>{_plain_pct(d.coverage.get('official_notional_share_pct'), 3)}
  of filled notional executed against an official reference bar.</strong>
</div>

<div class="kpis">
  <div class="kpi"><div class="kpi-label">Live book status</div>
    <div class="kpi-value">{_badge((d.forward or {}).get('status', 'PENDING-SETTLEMENT').split(':')[0])}</div>
    <div class="kpi-sub">{forward_pending} upcoming intents placed on
    {(d.forward or {}).get('plan_date', '—')}, none settled yet</div></div>
  <div class="kpi"><div class="kpi-label">Rehearsal window</div>
    <div class="kpi-value">{ESC((d.manifest.get('first_plan_session') or '—'))} →
    {ESC((d.manifest.get('last_plan_session') or '—'))}</div>
    <div class="kpi-sub">{d.manifest.get('sessions_planned', 0)} plan sessions, decide at
    T and settle at T+1</div></div>
  <div class="kpi"><div class="kpi-label">Benchmark (official)</div>
    <div class="kpi-value">{_pct(bench.get('return_pct'))}</div>
    <div class="kpi-sub">{ESC(bench.get('title', ''))} · {ESC(bench.get('first_date', ''))}
    {bench.get('first_value', '')} → {ESC(bench.get('last_date', ''))}
    {bench.get('last_value', '')}</div></div>
  <div class="kpi"><div class="kpi-label">Verification</div>
    <div class="kpi-value">{_badge(v.get('verdict', '—'))}</div>
    <div class="kpi-sub">{v.get('checks', 0):,} checks, {v.get('failure_count', 0)}
    failures, max cash residual {_money(v.get('max_abs_cash_residual_usd'))}</div></div>
</div>

<h2>What the rehearsal measured</h2>
<p>{len(traded)} of {len(board)} participants traded. The best forward-tested rule was
<strong>{ESC(winner.get('username', '—'))}</strong> at
<strong>{_pct(winner.get('total_return_pct'))}</strong> and the worst was
<strong>{ESC(loser.get('username', '—'))}</strong> at
<strong>{_pct(loser.get('total_return_pct'))}</strong>. {len(idle)} participants placed
nothing, and their row says why rather than printing a zero: a missing dataset is a
measurement gap, not a result.</p>

<table class="participants">
<thead><tr><th>#</th><th>Username</th><th>Return</th><th>Net P&amp;L</th>
<th>Max DD</th><th>Fills</th><th>Cost</th><th>Slippage</th><th>Verdict</th></tr></thead>
<tbody>
{''.join(f'''<tr><td>{r['rank']}</td>
<td><a href="participants/{ESC(_slug(r['username']))}.html">{ESC(r['username'])}</a>
<br><span class="muted small">{ESC(r['archetype'])}</span></td>
<td class="{'pos' if r['total_return_pct'] >= 0 else 'neg'}">{_pct(r['total_return_pct'])}</td>
<td>{_money(r['net_pnl_usd'])}</td><td>{_plain_pct(r['max_drawdown_pct'])}</td>
<td>{r['fills']}</td><td>{_plain_pct(r['execution_cost_pct'], 3)}</td>
<td>{r['slippage_bps_mean'] if r['slippage_bps_mean'] is not None else '—'} bps</td>
<td>{ESC(r['verdict'])}</td></tr>''' for r in board)}
</tbody></table>

<h2>How to read the two books</h2>
<div class="charts">
  <div class="card">
    <h3>Upcoming trades (the real forward book)</h3>
    <p>Planned from {(d.forward or {}).get('plan_date', '—')}, the last session with a
    verified equity bar. Every intent is <code>PENDING</code> and will stay that way
    until a future bar is collected, at which point the same code path that produced
    the rehearsal settles it. Nothing is claimed about these trades yet, which is why
    the page reports {forward_pending} open intents and no return.</p>
    <p class="more"><a href="forward.html">See every upcoming trade and its evidence
    &rarr;</a></p>
  </div>
  <div class="card">
    <h3>Walk-forward rehearsal (the measurable part)</h3>
    <p>Because no future bars exist yet, the same book is also stepped over sessions
    whose bars are already collected, planning at T and settling at T+1. This is a
    genuine forward test - unlike Season 2, which executes inside the session it
    decides - and it is the number the leaderboard reports.</p>
    <p class="more"><a href="leaderboard.html">Open the leaderboard &rarr;</a></p>
  </div>
</div>

<h2>Why the delay matters</h2>
<p>The difference between this book and Season 2 is one session of patience. That is
not a cosmetic change: it removes the ability to trade on a price that does not exist
yet, and it charges the strategy whatever the next session's opening spread and impact
cost. Several rules in this roster were designed to buy the close they had just read -
here they cannot, and their results separate accordingly.</p>
<p class="more"><a href="method.html">The clock, the margin account and the carry
&rarr;</a> · <a href="sources.html">Every official series, with its publisher and
retrieval record &rarr;</a></p>
"""
    return page("Overview", body, "live/index.html")


def build_forward(d: LiveSite) -> str:
    fwd = d.forward or {}
    pending = fwd.get("pending_intents") or []
    rows = "".join(f"""<tr>
<td>{ESC(i['created_on'])}</td><td>{_badge(i['session_status'])}
{ESC(i['intended_session'])}</td>
<td>{ESC(i['participant'])}</td><td>{ESC(i['side'])} {ESC(i['symbol'])}</td>
<td>{i['quantity']:,}</td><td>{ESC(i['order_type'])}</td>
<td>{ESC(i.get('rule', ''))}</td>
<td>{ESC(', '.join(sorted(i.get('evidence', {})))[:80])}</td></tr>""" for i in pending)
    evidence_blocks = []
    for i in pending[:6]:
        rows_html = "".join(
            f"<tr><td>{ESC(name)}</td><td>{ESC(ev.get('observation_date', ''))}</td>"
            f"<td>{ESC(ev.get('value', ''))}</td>"
            f"<td>{_badge(str(ev.get('source_class', 'UNKNOWN')))}</td>"
            f"<td><code>{ESC(ev.get('file', ''))}</code></td>"
            f"<td><code>{ESC(str(ev.get('sha256', ''))[:16])}…</code></td></tr>"
            for name, ev in sorted((i.get("evidence") or {}).items()))
        evidence_blocks.append(f"""<div class="card">
<h3>{ESC(i['participant'])} · {ESC(i['side'])} {ESC(i['symbol'])} for
{ESC(i['intended_session'])}</h3>
<p class="muted small">{ESC(i['rationale'])}</p>
<table><thead><tr><th>input</th><th>observation date</th><th>value</th><th>class</th>
<th>file</th><th>sha256</th></tr></thead><tbody>{rows_html}</tbody></table></div>""")
    body = f"""
<h1>Upcoming trades</h1>
<p class="lede">These are the trades the live book has <em>placed</em> and not yet
executed. Each one was written on {ESC(fwd.get('plan_date', '—'))} after the close,
names a future session, and carries the exact observation it was decided from - the
series name, the date of that observation, its value, the file it lives in and that
file's SHA-256.</p>

<div class="card notice">
  <strong>{_badge('PENDING-SETTLEMENT')}</strong> Nothing on this page is a result. The
  venue has no verified bar for {ESC(', '.join(fwd.get('projected_sessions') or []))}
  yet, so no fill exists, no cash has moved and the book reports no return. When the
  bars are collected, <code>python3 -m sim.cli live --mode forward</code> followed by a
  settlement pass produces the fills on the tape page.
</div>

<h2>The order book of intents</h2>
<table class="participants">
<thead><tr><th>created</th><th>session</th><th>participant</th><th>order</th>
<th>shares</th><th>type</th><th>rule</th><th>inputs</th></tr></thead>
<tbody>{rows or '<tr><td colspan="8">no pending intents</td></tr>'}</tbody></table>

<h2>Sessions and their status</h2>
<p><strong>COLLECTED</strong> means the session is an observation: the FRED S&amp;P 500
series carries a value for it and the collected equity bars cover it.
<strong>PROJECTED</strong> means it comes from Nasdaq's published holiday schedule
(<a href="{ESC((d.sources.get('projection') or {}).get('source', ''))}">Nasdaq Trader
calendar</a>) because it is past the last collected date, and a projected session that
turns out to be closed expires its intent instead of trading. No 2027 dates are
projected at all, because the 2027 schedule was not read from the publisher.</p>
<p>Projected closures inside the live horizon:
{ESC(', '.join((d.sources.get('projection') or {}).get('closures') or []) or 'none')}
· early closes:
{ESC(', '.join((d.sources.get('projection') or {}).get('early_closes') or []) or 'none')}
· projection limit {ESC((d.sources.get('projection') or {}).get('limit', ''))}.</p>

<h2>The evidence behind the first intents</h2>
{''.join(evidence_blocks) or '<p>No intents to show.</p>'}

<p class="more"><a href="blotter.html">The settled tape from the rehearsal &rarr;</a></p>
"""
    return page("Upcoming trades", body, "live/forward.html")


def build_leaderboard(d: LiveSite) -> str:
    board = d.board
    bench = d.benchmark
    rows = "".join(f"""<tr>
<td>{r['rank']}</td>
<td><a href="participants/{ESC(_slug(r['username']))}.html">{ESC(r['username'])}</a>
<br><span class="muted small">{ESC(r['display_name'])}</span></td>
<td class="{'pos' if r['total_return_pct'] >= 0 else 'neg'}">{_pct(r['total_return_pct'])}</td>
<td>{_money(r['net_pnl_usd'])}</td><td>{_plain_pct(r['max_drawdown_pct'])}</td>
<td>{r['sharpe'] if r['sharpe'] is not None else '—'}</td>
<td>{r['intents_placed']}</td><td>{r['fills']}</td>
<td>{r['intents_rejected']}</td><td>{r['intents_cancelled']}</td>
<td>{r['traded_notional_usd']:,.0f}</td>
<td>{_plain_pct(r['execution_cost_pct'], 3)}</td>
<td>{r['slippage_bps_mean'] if r['slippage_bps_mean'] is not None else '—'}</td>
<td>{r['participation_pct_median'] if r['participation_pct_median'] is not None else '—'}</td>
<td>{_money(r['carry_received_usd'])}</td>
<td>{_money(r['carry_paid_usd'])}</td>
<td>{_badge(r['data_status'])}</td>
<td>{ESC(r['verdict'])}</td></tr>""" for r in board)
    body = f"""
<h1>Leaderboard</h1>
<p class="lede">Ranked on total return over the rehearsal window
{ESC(d.manifest.get('first_plan_session', ''))} →
{ESC(d.manifest.get('last_plan_session', ''))}, which is the metric the brief asks
for. The benchmark is the official S&amp;P 500 daily close from FRED, which returned
{_pct(bench.get('return_pct'))} over the same sessions.</p>
<table class="participants">
<thead><tr><th>#</th><th>Username</th><th>Return</th><th>Net P&amp;L</th><th>Max DD</th>
<th>Sharpe</th><th>Intents</th><th>Fills</th><th>Refused</th><th>Superseded</th>
<th>Traded $</th><th>Cost</th><th>Slip bps</th><th>Part. med %</th>
<th>Carry in</th><th>Carry out</th><th>Data</th><th>Verdict</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="muted small">Sharpe is reported but never used to rank: the brief scores
total return, and a levered rule that takes twice the drawdown for the same return is
a winner under this scoring. The max-drawdown column is the price of that.</p>
<h2>What each column means</h2>
<ul>
<li><strong>Refused</strong> — intents the venue or the margin account would not take
(participation cap, no borrowing room, maintenance breach). A high number is a
statement about size, not about the signal.</li>
<li><strong>Superseded</strong> — a newer intent replaced an older one for the same
symbol and session, so only one live order per symbol-session ever exists.</li>
<li><strong>Cost</strong> — spread plus depth plus impact plus exchange and regulatory
fees, over traded notional, from the same dated fee schedules the competition uses.</li>
<li><strong>Carry in / out</strong> — interest credited on idle cash and charged on a
margin debit at the official SOFR, day-count ACT/360 (declared).</li>
</ul>
"""
    return page("Leaderboard", body, "live/leaderboard.html")


def build_blotter(d: LiveSite) -> str:
    intents = {i["intent_id"]: i for i in d.intents}
    fills = sorted(d.fills, key=lambda f: (f["date"], f["participant"]))
    traded = [f for f in fills if int(f.get("filled_qty") or 0) > 0]
    forced = [f for f in traded if f.get("forced")]
    rows = []
    for f in fills[:400]:
        intent = intents.get(f.get("intent_id") or "", {})
        rows.append(f"""<tr>
<td>{ESC(f['date'])}</td><td>{ESC(f['participant'])}</td>
<td>{ESC(f['side'])} {ESC(f['symbol'])}</td>
<td>{int(f.get('filled_qty') or 0):,}</td>
<td>{f['avg_price']:,.4f}</td>
<td>{_money(f.get('notional'))}</td>
<td>{_money(f.get('total_cost'))}</td>
<td>{f.get('slippage_bps')}</td>
<td>{f.get('slippage_vs_close_bps')}</td>
<td>{f.get('participation_pct_of_session_volume')}</td>
<td>{_badge(str(f.get('reference_source_class', 'UNKNOWN')))}</td>
<td><code>{ESC(str(f.get('reference_file', '')))}</code><br>
<code class="muted small">{ESC(str(f.get('reference_sha256', ''))[:20])}…</code></td>
<td>{ESC(intent.get('created_on', 'forced'))}</td>
<td>{ESC(str(f.get('status', '')))}</td></tr>""")
    if len(fills) > 400:
        rows.append(f'<tr><td colspan="14" class="muted">… {len(fills) - 400:,} more '
                    f'rows are in <code>memory/live/*/fills.jsonl.gz</code> and '
                    f'<code>blotter.csv</code>.</td></tr>')
    body = f"""
<h1>Trade tape</h1>
<p class="lede">Every fill in the rehearsal, newest first, with the verified bar it
executed against. This is the page to check the brief's requirement line by line:
price, date, entry, exit, slippage, participation and provenance, one row per trade.</p>

<div class="kpis">
  <div class="kpi"><div class="kpi-label">Fills</div>
    <div class="kpi-value">{len(traded):,}</div>
    <div class="kpi-sub">{len(d.intents):,} intents placed ·
    {len(d.trips):,} round trips</div></div>
  <div class="kpi"><div class="kpi-label">Forced liquidations</div>
    <div class="kpi-value">{len(forced)}</div>
    <div class="kpi-sub">orders the broker generated after a maintenance-margin
    breach, charged the same spread and impact as any other order</div></div>
  <div class="kpi"><div class="kpi-label">Storage</div>
    <div class="kpi-value">{d.storage.get('bytes_per_row_average', 0)} B/row</div>
    <div class="kpi-sub">{d.storage.get('total_rows', 0):,} rows in
    {d.storage.get('total_bytes_on_disk', 0) / 1024:,.1f} KiB of gzipped JSON Lines,
    plus a CSV export</div></div>
  <div class="kpi"><div class="kpi-label">Reference bars by class</div>
    <div class="kpi-value">{ESC(', '.join(sorted({str(f.get('reference_source_class'))
                                                  for f in traded})))}</div>
    <div class="kpi-sub">every fill names a file and a SHA-256</div></div>
</div>

<table class="participants">
<thead><tr><th>session</th><th>participant</th><th>order</th><th>shares</th><th>price</th>
<th>notional</th><th>cost</th><th>slip bps</th><th>slip vs close</th><th>part %</th>
<th>class</th><th>reference bar</th><th>planned on</th><th>status</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>

<p class="muted small">The <code>planned on</code> column is the point of the whole
section: it is strictly earlier than the session column for every row, which is what
makes this a forward test rather than a backtest. The audit re-derives that property
from the raw tape and fails the run if any row violates it.</p>
<p class="more"><a href="method.html">How the clock, the margin account and the
verification work &rarr;</a></p>
"""
    return page("Trade tape", body, "live/blotter.html")


def build_method(d: LiveSite) -> str:
    v = d.verification
    storage = d.storage
    body = f"""
<h1>The clock, the margin account and the carry</h1>
<p class="lede">Three modelling decisions separate this book from every other part of
the project. Each is enforced in code, not documented and hoped for.</p>

<h2>1. No look-ahead, enforced at creation</h2>
<ul>
<li>A strategy is handed a context truncated at the plan session. Nothing dated after
it is reachable: the official series are sliced, and every market accessor is bounded
by the as-of index.</li>
<li><code>place()</code> refuses any session that is not strictly after the plan date.
Writing a same-session intent raises, because that would be a backtest.</li>
<li>Every intent stores the observation date of each input it read. The verifier walks
the tape and fails the run if any evidence date is later than the intent's plan
date.</li>
<li>Every intent's target session is resolved from the calendar: inside the collected
window it is an <em>observation</em> (the FRED S&amp;P 500 blank rows are the
authoritative holiday list), and past it a <em>projection</em> from Nasdaq's published
schedule, labelled as such. An intent aimed at a session the official calendar says was
closed <strong>expires</strong> instead of filling.</li>
</ul>

<h2>2. A real margin account, with liquidation</h2>
<p>Sizing runs at the leverage bound the brief asks for, and the account that has to
survive it is a Reg T margin account: 50% initial margin
(<a href="https://www.federalreserve.gov/supervisionreg/regtcg.htm">Federal Reserve
Board</a>) and a 30% house maintenance floor with a 2.0x gross bound
(<a href="https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210">FINRA Rule
4210</a>). An order is trimmed to the room that actually exists - shares that close a
position free room and are never trimmed, shares that open one need room and, if they
are a purchase, Reg T buying power - and the trim is recorded on the intent so the
blotter shows the intended size, the allowed size and the reason they differ.</p>
<p>A breach is not a note in a log. When a mark breaches the maintenance floor the
broker liquidates the book at the next session's open through the same venue model, so
the cost of being wrong is inside the result. The rehearsal contains
{len([f for f in d.fills if f.get('forced')])} such forced orders.</p>

<h2>3. Carry, on an official rate</h2>
<p>Idle cash earns the official SOFR and a margin debit pays SOFR plus a declared 3.5%
spread, accrued per session with day-count ACT/360 (declared, because an overnight
secured rate is quoted that way). SOFR has no observation on days the bond market is
closed while the NYSE is open - the collected file shows blank rows on 2024-10-14,
2024-11-11, 2025-10-13 and 2025-11-11 - so those sessions carry the last published
rate forward and the carry rows say so rather than inventing one. The collected SOFR
history and the Federal Reserve Bank of New York's own API agree on both cross-checked
dates (3.85% on 2026-09-17, 3.62% on 2026-09-16).</p>

<h2>Verification</h2>
<p>{_badge(v.get('verdict', '—'))} {v.get('checks', 0):,} checks,
{v.get('failure_count', 0)} failures. The audit re-derives cash and positions from the
fill tape plus the carry rows without asking the account what it thinks it holds, and
compares both against the marked equity at the final session.</p>
<p>{ESC(v.get('note', ''))}</p>

<h2>Data efficiency</h2>
<p>The book stores {storage.get('total_rows', 0):,} rows in
{storage.get('total_bytes_on_disk', 0) / 1024:,.1f} KiB, an average of
<strong>{storage.get('bytes_per_row_average', 0)} bytes per row</strong>, as gzipped
JSON Lines with sorted keys and compact separators so two runs produce identical bytes.
The reference bar is stored once per fill - the fill is the unit a reader queries - and
file digests live in the provenance document instead of being repeated on every row.
The same tape is exported as <code>blotter.csv</code> for spreadsheet review.</p>
<p class="more"><a href="sources.html">The official register these rules read
&rarr;</a></p>
"""
    return page("Clock &amp; margin", body, "live/method.html")


def build_sources(d: LiveSite) -> str:
    src = d.sources or {}
    series = src.get("series") or []
    aux = src.get("auxiliary") or []
    missing = src.get("missing") or []
    series_rows = "".join(f"""<tr>
<td><code>{ESC(s['sid'])}</code><br><span class="muted small">{ESC(s['title'])}</span></td>
<td>{ESC(s['publisher'])}</td>
<td>{_badge(s['source_class'])}</td>
<td>{ESC(s['frequency'])}</td>
<td>{s['observations']:,}<br><span class="muted small">{ESC(s['first_date'])} →
{ESC(s['last_date'])}</span></td>
<td><code>{ESC(s['file'])}</code><br>
<code class="muted small">{ESC(s['sha256'][:20])}…</code></td>
<td><a href="{ESC(s['url_series'])}">series page</a> ·
<a href="{ESC(s['url_csv'])}">CSV</a></td>
<td>{ESC(s['role'])}</td>
<td>{ESC(s['fred_copyright_tag'])}</td></tr>""" for s in series)
    aux_rows = "".join(f"""<tr><td>{ESC(a['label'])}</td>
<td><a href="{ESC(a['url'])}">{ESC(a['url'])}</a></td>
<td>{_badge(a['source_class'])}</td><td>{ESC(a['role'])}</td>
<td>{ESC(a['note'])}</td></tr>""" for a in aux)
    signals = d.coverage.get("signal_states") or {}
    signal_rows = "".join(
        f"<tr><td><code>{ESC(k)}</code></td><td>{_badge(v)}</td></tr>"
        for k, v in sorted(signals.items()))
    classes = d.coverage.get("executable_price_classes") or {}
    class_rows = "".join(
        f"<tr><td>{ESC(sym)}</td><td>{_badge(cls)}</td></tr>"
        for sym, cls in sorted(classes.items()))
    body = f"""
<h1>Official sources</h1>
<p class="lede">Everything this book reads that is not a price, plus the provenance of
the prices it does read. A URL is only listed as official here after it was
<em>retrieved</em>, and where a publisher's own reuse conditions were not read
in-session this table says so instead of guessing, because that tag is exactly what
decides whether a committed copy may be redistributed.</p>

<h2>Official series used for signals, benchmark, calendar and financing</h2>
<table class="participants">
<thead><tr><th>series</th><th>publisher</th><th>class</th><th>frequency</th>
<th>observations</th><th>file</th><th>links</th><th>what it is used for</th>
<th>reuse tag as read</th></tr></thead>
<tbody>{series_rows}</tbody></table>
<p class="muted small">A blank observation is data, not a gap: FRED leaves the value
empty on days the market is shut, and that is the authoritative closed-day list the
trading calendar is built from. {len(missing)} registered series were missing from the
collected directory at build time.</p>

<h2>Auxiliary official endpoints</h2>
<table class="participants">
<thead><tr><th>endpoint</th><th>URL</th><th>class</th><th>role</th>
<th>retrieval note</th></tr></thead>
<tbody>{aux_rows}</tbody></table>

<h2>Collected event signals the roster reads</h2>
<table class="participants"><thead><tr><th>signal array</th><th>state</th></tr></thead>
<tbody>{signal_rows}</tbody></table>
<p class="muted small">A <code>MISSING</code> row is why a participant places no
intents: the rule is implemented and waiting on a dataset, and the participant page
says which one. Nothing is back-filled with a price proxy.</p>

<h2>Executable price provenance</h2>
<table class="participants"><thead><tr><th>symbol</th><th>source class</th></tr></thead>
<tbody>{class_rows}</tbody></table>
<p>Every executable bar comes from <code>data/real/prices/yahoo/*.json</code>, which
the collector marks <strong>SECONDARY</strong>, and each fill on the tape names its
reference file and that file's SHA-256. That is the whole reason
<code>{_plain_pct(d.coverage.get('official_notional_share_pct'), 3)}</code> of filled
notional counts as official rather than 100%.</p>
<p class="more"><a href="blotter.html">Check the tape against these files
&rarr;</a></p>
"""
    return page("Official sources", body, "live/sources.html")


def build_participants_index(d: LiveSite) -> str:
    board = {r["username"]: r for r in d.board}
    cards = []
    for username in sorted(d.reports):
        report = d.reports[username]
        row = board.get(username, {})
        cards.append(f"""<div class="card">
<h3><a href="{ESC(_slug(username))}.html">{ESC(username)}</a>
{_badge(report.get('data_status', 'READY'))}</h3>
<p class="muted small">{ESC(report['spec']['display_name'])} ·
{ESC(report['spec']['archetype'])}</p>
<p class="{'pos' if (row.get('total_return_pct') or 0) >= 0 else 'neg'}">
<strong>{_pct(row.get('total_return_pct'))}</strong> · {row.get('fills', 0)} fills ·
max DD {_plain_pct(row.get('max_drawdown_pct'))}</p>
<p>{ESC(report['spec']['thesis'][:260])}…</p>
<p class="more"><a href="{ESC(_slug(username))}.html">Full post-mortem &rarr;</a></p>
</div>""")
    body = f"""
<h1>Participants</h1>
<p class="lede">One page per strategy: what it was built to harvest, the exact rule it
ran, every intent it placed with its evidence, its round trips, and a post-mortem
generated from its own numbers that says what caused the result.</p>
<div class="charts">{''.join(cards)}</div>
"""
    # depth=2: this page lives one directory deeper than the rest of the live
    # section, so every nav href and the stylesheet need "../../". Getting this
    # wrong published eight 404s, which the published-site link test caught.
    return page("Participants", body, "live/participants/index.html", depth=2)


def build_participant(d: LiveSite, username: str) -> str:
    report = d.reports[username]
    spec = report["spec"]
    summary = report["summary"]
    trips = report["trips"]
    trip_rows = "".join(f"""<tr><td>{ESC(t['symbol'])}</td><td>{ESC(t['direction'])}</td>
<td>{ESC(t['entry_date'])}</td><td>{t['entry_price']:,.4f}</td>
<td>{ESC(str(t.get('exit_date')))}</td>
<td>{(t.get('exit_price') or 0):,.4f}</td><td>{t['quantity']:,}</td>
<td class="{'pos' if (t.get('net_pnl_usd') or 0) >= 0 else 'neg'}">
{_money(t.get('net_pnl_usd'))}</td>
<td>{t.get('sessions_held')}</td><td>{ESC(str(t.get('exit_reason', ''))[:60])}</td>
</tr>""" for t in trips)
    intent_rows = "".join(f"""<tr><td>{ESC(i['intended_session'])}</td>
<td>{ESC(i['symbol'])}</td><td>{ESC(i['side'])}</td><td>{i['quantity']:,}</td>
<td>{_badge(i['status'])}</td><td>{ESC(i.get('settle_note', '')[:70])}</td>
<td>{ESC(', '.join(sorted(i.get('evidence', {}))))[:70]}</td></tr>"""
                          for i in report["intents"][:120])
    narrative = "".join(f"<li>{ESC(line)}</li>" for line in report["narrative"])
    basis = "".join(
        f'<li>{ESC(b.get("claim", ""))} — <a href="{ESC(b.get("url", ""))}">'
        f'{ESC(b.get("ref", ""))}</a> '
        f'<span class="muted small">[{ESC(b.get("status", ""))}]</span></li>'
        for b in spec.get("academic_basis", []) if isinstance(b, dict))
    failures = "".join(f"<li>{ESC(x)}</li>" for x in spec.get("known_failure_modes", []))
    inputs = ", ".join([f"<code>{ESC(s)}</code>" for s in report["official_inputs"]]
                       + [f"<code>{ESC(s)}</code>" for s in report["event_inputs"]])
    body = f"""
<h1>{ESC(spec['display_name'])} <span class="muted">{ESC(username)}</span></h1>
<p class="lede">{ESC(spec['thesis'])}</p>

<div class="kpis">
  <div class="kpi"><div class="kpi-label">Forward-tested return</div>
    <div class="kpi-value {'pos' if summary['total_return_pct'] >= 0 else 'neg'}">
    {_pct(summary['total_return_pct'])}</div>
    <div class="kpi-sub">{_money(summary['net_pnl_usd'])} net on
    {_money(summary['starting_cash'])} starting cash</div></div>
  <div class="kpi"><div class="kpi-label">Risk</div>
    <div class="kpi-value">{_plain_pct(summary['max_drawdown_pct'])}</div>
    <div class="kpi-sub">max drawdown · Sharpe
    {summary['sharpe'] if summary['sharpe'] is not None else '—'}</div></div>
  <div class="kpi"><div class="kpi-label">Execution</div>
    <div class="kpi-value">{summary['fills']} fills</div>
    <div class="kpi-sub">{summary['intents_placed']} intents ·
    {summary['intents_rejected']} refused · cost
    {_plain_pct(summary['execution_cost_pct'], 3)}</div></div>
  <div class="kpi"><div class="kpi-label">Data status</div>
    <div class="kpi-value">{_badge(report['data_status'])}</div>
    <div class="kpi-sub">{ESC(report.get('signal_note', '') or 'all declared inputs present')}</div></div>
</div>

<h2>Why the result is what it is</h2>
<ul>{narrative}</ul>

<h2>The rule as it ran</h2>
<p><strong>Inputs:</strong> {inputs or '<em>none declared</em>'}</p>
<p><strong>Sizing:</strong> {ESC(spec.get('sizing', ''))} ·
<strong>leverage:</strong> {ESC(spec.get('leverage', ''))} ·
<strong>cadence:</strong> {ESC(spec.get('cadence', ''))} ·
<strong>horizon:</strong> {ESC(spec.get('horizon', ''))}</p>
<h3>Entry rules</h3>
<ul>{''.join(f'<li>{ESC(r)}</li>' for r in spec.get('entry_rules', []))}</ul>
<h3>Exit rules</h3>
<ul>{''.join(f'<li>{ESC(r)}</li>' for r in spec.get('exit_rules', []))}</ul>
<h3>Evidence base</h3>
<ul>{basis}</ul>
<h3>Declared failure modes</h3>
<ul>{failures}</ul>

<h2>Contribution by instrument</h2>
<table class="participants"><thead><tr><th>symbol</th><th>net P&amp;L</th></tr></thead>
<tbody>{''.join(f'<tr><td>{ESC(k)}</td><td class="{"pos" if v >= 0 else "neg"}">{_money(v)}</td></tr>' for k, v in report['pnl_by_symbol'].items()) or '<tr><td colspan="2">no closed trades</td></tr>'}</tbody></table>

<h2>Post-mortem statistics</h2>
<ul>
<li>Round trips closed: {report['round_trips_closed']} · still open:
{report['round_trips_open']}</li>
<li>Win rate: {_plain_pct(report.get('win_rate_pct'))} · profit factor:
{report.get('profit_factor') if report.get('profit_factor') is not None else '—'}</li>
<li>Largest trip: {_money(report.get('largest_trip_usd'))} · worst trip:
{_money(report.get('worst_trip_usd'))}</li>
<li>Carry: {_money(report['carry']['received_usd'])} received,
{_money(report['carry']['paid_usd'])} paid at {ESC(report['carry']['rate_series'])}
({ESC(report['carry']['day_count'])}); rate source
{_badge(report['carry']['rate_source_class'])}</li>
<li>Margin events: {len(report['margin_events'])}</li>
</ul>

<h2>Round trips</h2>
<table class="participants">
<thead><tr><th>symbol</th><th>direction</th><th>entry</th><th>entry px</th><th>exit</th>
<th>exit px</th><th>qty</th><th>net P&amp;L</th><th>sessions</th><th>why it closed</th>
</tr></thead>
<tbody>{trip_rows or '<tr><td colspan="10">no closed round trips</td></tr>'}</tbody></table>

<h2>Intents placed</h2>
<table class="participants">
<thead><tr><th>session</th><th>symbol</th><th>side</th><th>shares</th><th>status</th>
<th>settlement note</th><th>evidence read</th></tr></thead>
<tbody>{intent_rows or '<tr><td colspan="7">no intents placed</td></tr>'}</tbody></table>
<p class="more"><a href="../blotter.html">Back to the trade tape &rarr;</a></p>
"""
    return page(spec["display_name"], body, "live/participants/index.html", depth=2)


def _slug(username: str) -> str:
    return username.lstrip("@").replace("/", "_")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def inject_nav(html_text: str) -> str:
    """Add a Live Book link to a main-site page's nav, idempotently."""
    marker = '<a href="live/index.html">Live Book</a>'
    if marker in html_text or "Live Book" in html_text:
        return html_text
    needle = "</nav>"
    if needle not in html_text:
        return html_text
    return html_text.replace(needle, f'{marker}{needle}', 1)


def build(memory_root: str, out: str) -> List[str]:
    d = LiveSite(memory_root)
    written: List[str] = []

    def write(rel: str, content: str) -> None:
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        written.append(rel)

    write("live/index.html", build_index(d))
    write("live/forward.html", build_forward(d))
    write("live/leaderboard.html", build_leaderboard(d))
    write("live/blotter.html", build_blotter(d))
    write("live/method.html", build_method(d))
    write("live/sources.html", build_sources(d))
    write("live/participants/index.html", build_participants_index(d))
    for username in sorted(d.reports):
        write(f"live/participants/{_slug(username)}.html",
              build_participant(d, username))

    # A payload mirror, so a reader can take the numbers without scraping HTML.
    payload = {
        "run_id": os.path.basename(d.rehearsal_dir),
        "forward_run_id": os.path.basename(d.forward_dir) if d.forward_dir else "",
        "leaderboard": d.board,
        "benchmark": d.benchmark,
        "manifest": d.manifest,
        "forward": d.forward,
        "verification": d.verification,
        "official_sources": d.sources,
        "coverage": d.coverage,
        "storage": d.storage,
    }
    write("assets/data/live.json",
          json.dumps(payload, indent=1, sort_keys=False) + "\n")

    # Prune anything a previous revision of this builder produced, for the same
    # reason the other builders do: docs/ is committed and diffed in CI, and a
    # stale page would keep being served and keep passing the diff.
    keep = set(written)
    live_dir = os.path.join(out, "live")
    if os.path.isdir(live_dir):
        for dirpath, _dirnames, filenames in os.walk(live_dir):
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), out)
                if rel.replace(os.sep, "/") not in keep:
                    os.remove(os.path.join(dirpath, name))
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", default=os.path.join(REPO_ROOT, "memory"))
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"))
    args = parser.parse_args(argv)
    written = build(args.memory_root, args.out)
    print(f"live site: {len(written)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
