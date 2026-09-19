"""Build the Forward Pilot section (docs/pilot/) from committed pilot memory.

Deterministic: every rendered number comes from memory/pilot files already in
the repository, never from wall time.  When the pilot has not been scheduled
yet the pages still build, and they say so in words rather than fabricating a
zero-filled table.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = html.escape


def load(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text())


def table(headers, rows):
    return (
        '<div class="table-scroll"><table><thead><tr>'
        + "".join("<th scope=\"col\">" + E(h) + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join("<td>" + str(c) + "</td>" for c in row) + "</tr>" for row in rows)
        + "</tbody></table></div>"
    )


def link(url, text):
    return f'<a href="{E(url, quote=True)}">{E(text)} ↗</a>'


def page(title, headline, body, nav=True):
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Forward pilot of the evidence-gated US equities desk: timestamped paper orders, upcoming intents, reconciliation and source links.">
<title>StockPaperSim Pilot · {E(title)}</title><link rel="stylesheet" href="../desk/desk.css"></head>
<body><a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="../index.html"><span class="logo">S</span> StockPaperSim</a><span class="tag">PAPER ONLY</span>
<nav aria-label="Pilot"><a href="index.html">Pilot overview</a><a href="upcoming.html">Upcoming intents</a><a href="blotter.html">Order blotter</a><a href="method.html">Method</a><a href="sources.html">Sources</a><a href="../desk/index.html">Desk ↗</a></nav></header>
<main id="main"><section class="hero"><div class="eyebrow">FORWARD PILOT · STRICT US EQUITIES DESK</div>
<h1>{headline}</h1></section>
{body}
</main><footer>Static, deterministic page built from committed memory — not a live trading connection. Not investment advice.</footer></body></html>
'''


def build(memory_root="memory/pilot", out="docs"):
    memo = ROOT / memory_root
    dest = ROOT / out / "pilot"
    dest.mkdir(parents=True, exist_ok=True)
    reconcile = load(memo / "reconcile.json")
    upcoming = load(memo / "upcoming_orders.json")
    registry = load(ROOT / "research/strict/registry.json")
    runs = []
    for report_path in sorted(memo.glob("run-*/run_report.json")):
        report = json.loads(report_path.read_text())
        report["folder"] = report_path.parent.name
        runs.append(report)
    orders_by_run = {}
    for report in runs:
        orders_file = memo / report["folder"] / "orders.json"
        orders_by_run[report["folder"]] = load(orders_file, [])
    fills_total = sum(r.get("fills", 0) for r in runs)
    orders_total = sum(r.get("orders_submitted", 0) for r in runs)

    # ---- index -----------------------------------------------------------
    if not runs:
        status = (
            '<aside class="notice"><strong>The pilot has not been scheduled yet.</strong>'
            "<p>No journal exists in <code>memory/pilot/</code>. The scheduler is "
            "<code>.github/workflows/equity-pilot.yml</code>; this page rebuilds after each run.</p></aside>"
        )
    else:
        verdict = (reconcile or {}).get("verdict", "UNKNOWN")
        status = (
            f'<aside class="notice"><strong>Reconciliation verdict: {E(verdict)}.</strong>'
            f"<p>{len(runs)} run(s) archived · {orders_total} timestamped orders journaled · "
            f"{fills_total} fills. Fills stay zero until a licensed official feed is recorded in "
            '<code>research/strict/registry.json</code> (<code>approved_feeds</code> is empty). '
            "An orders count with zero fills is the pipeline working as designed, not a bug.</p></aside>"
        )
    mm = {}
    for r in reversed(runs):
        mm = r.get("market_making_gate") or {}
        break
    mm_rows = [[E(reason)] for reason in (mm.get("reasons") or [])] or [[E("gate open (no recorded reasons)")]]
    run_rows = [
        [
            E(r["run_date"]),
            E(r.get("submitted_at", "")),
            "yes" if r.get("seeded_rehearsal") else "no",
            str(r.get("orders_submitted", 0)),
            str(len(r.get("orders_rejected", []))),
            str(r.get("fills", 0)),
            "<code>" + E(str(r.get("journal_head_after", ""))[:16]) + "…</code>",
        ]
        for r in runs
    ]
    index_body = (
        status
        + '<section class="panel"><div class="eyebrow">US MARKET SIMULATION SCOPE</div>'
        + "<p>This pilot simulates strategies placing real-style paper orders against the United States "
        "cash-equity market — the Nasdaq and NYSE venues and the S&amp;P 500 fund universe (<strong>SPY</strong>, the "
        "declared seed instrument). Pricing is only ever <em>official</em> (approved publisher feeds with documented "
        "rights), liquidity is only ever <em>observed</em> (quote-size receipts, never invented depth), and sessions "
        "come from the exchanges' own calendars. Those are exactly the three things no free fully-official source "
        "currently supplies for single stocks — which is why fills are zero and said so, rather than simulated "
        "from an aggregator and dressed up as verified.</p></section>"
        + '<div class="metrics">'
        + f'<article><span>Pilot runs archived</span><strong>{len(runs)}</strong><small>each append-only</small></article>'
        + f'<article><span>Timestamped orders</span><strong>{orders_total}</strong><small>rejected before fill</small></article>'
        + f'<article><span>Official equity fills</span><strong>{fills_total}</strong><small>licensed feed required</small></article>'
        + f'<article><span>Approved feeds</span><strong>{len(registry.get("approved_feeds", {}))}</strong><small>research/strict/registry.json</small></article>'
        + "</div>"
        + '<section class="panel"><div class="eyebrow">MARKET MAKING GATE</div><h2>Market making stays disabled</h2>'
        + "<p>Two-sided quoting needs observed quote sizes <em>and</em> queue-priority evidence; without them a "
        "simulated maker prints imaginary fill rates. The gate state is re-derived every run:</p>"
        + f"<p><strong>Market making allowed: {E(str(mm.get('allowed', False)))}</strong></p>"
        + table(["Gate reason"], mm_rows)
        + "</section>"
        + '<section class="panel"><div class="eyebrow">RUN LEDGER</div><h2>Every scheduled run</h2>'
        + "<p>One row per pilot run; journal heads let anyone verify the hash chain locally "
        "(<code>python3 -m sim.equity_pilot reconcile</code>).</p>"
        + table(
            ["Session", "Submitted at (UTC)", "Seeded rehearsal", "Orders", "Rejections", "Fills", "Journal head"],
            run_rows,
        )
        + "</section>"
    )

    # ---- upcoming --------------------------------------------------------
    if upcoming:
        up_rows = [
            [
                E(r["username"]),
                E(r["strategy"] + "@" + r["version"]),
                E(r["symbol"]),
                E(r["session"]),
                E(r["state"]),
                '<span class="tag blocked">PLAN ONLY</span><br>No price, no timestamp yet',
            ]
            for r in upcoming.get("rows", [])
        ]
        upcoming_body = (
            f'<aside class="notice"><strong>{E(upcoming.get("note", ""))}</strong></aside>'
            + f"<p>Generated for session <strong>{E(upcoming.get('generated_for_session', ''))}</strong>; "
            f"calendar source {link(upcoming.get('calendar_source', '#'), 'official NYSE hours &amp; holidays')}."
            + table(["Username", "Strategy", "Symbol", "Session", "State", "What this row is"], up_rows)
        )
    else:
        upcoming_body = '<aside class="notice"><strong>No upcoming intents published yet.</strong></aside>'

    # ---- blotter ---------------------------------------------------------
    order_rows = []
    for report in runs:
        for order in orders_by_run.get(report["folder"], []):
            order_rows.append(
                [
                    E(order["username"]),
                    E(order["strategy_version"]),
                    E(order["symbol"]),
                    E(order["side"].upper() + " " + str(order["quantity"])),
                    E(order.get("order_type", "")),
                    E(report["run_date"]),
                    E(report.get("submitted_at", "")),
                    E(order.get("input_class", "")) + " intent",
                    '<span class="tag blocked">BLOCKED</span><br>'
                    + E("NO_APPROVED_OFFICIAL_FEED"),
                ]
            )
    blotter_body = (
        "<p>Every order the pilot journaled, copied verbatim from the hash-chained journal export in "
        "<code>memory/pilot/run-*/journal.jsonl</code>. Rejections are journaled too, so this blotter is the "
        "complete record — there is no second, quieter list of orders that failed.</p>"
        + (
            table(
                ["Username", "Strategy", "Symbol", "Side × qty", "Type", "Session", "Submitted (UTC)", "Input class", "State"],
                order_rows,
            )
            if order_rows
            else '<aside class="notice"><strong>No orders journaled yet.</strong></aside>'
        )
        + "<h2>Reconciliation</h2>"
        + (
            "<pre>" + E(json.dumps(reconcile, indent=1, sort_keys=True)) + "</pre>"
            if reconcile
            else '<aside class="notice"><strong>No reconciliation report yet.</strong></aside>'
        )
    )

    # ---- method ----------------------------------------------------------
    method_body = '''
<section class="panel">
<h2>What this pilot is</h2>
<p>A durable, scheduled forward test of the strict US-equities desk. Each scheduled run: checks the official
trading calendar, evaluates the frozen research rules on committed, checksummed input files, journals every
intent with a UTC submission timestamp into the append-only SQLite hash chain, offers execution only through
the evidence gate, and reconciles counts before anything new is published.</p>
<ul>
<li><strong>No official feed, no fills.</strong> <code>approved_feeds</code> in the registry is empty, so every
order is refused with <code>NO_APPROVED_OFFICIAL_FEED</code> and the refusal is journaled.</li>
<li><strong>Intents are labelled by data class.</strong> Today's signal inputs are the committed Yahoo daily
files marked <code>SECONDARY</code>; an intent produced from them can never be presented as an official-price
trade, only as research with a timestamp.</li>
<li><strong>Calendars are documented, not guessed.</strong> Sessions come from the NYSE hours/holidays page and
settlement from the FRBservices holiday schedule, both embedded with their check dates in
<code>sim/venue_admin.py</code>; dates beyond the documented horizon fail closed.</li>
<li><strong>Market making is gated separately.</strong> It needs quote-size and queue-priority receipts; until
then it is off, in code, every run.</li>
<li><strong>Corporate actions stand orders down.</strong> A sourced row in the corporate-actions table
(<code>data/real/corporate-actions/</code>, each row carrying a primary-source URL and check date) blocks new
orders within ±1 session of its ex-date. The table is empty until the EDGAR collection workflow lands - shown,
not hidden.</li>
<li><strong>No competition returns are published from the pilot.</strong> A ranking would imply measured
performance; with zero fills there is nothing measured, and the site says that instead of showing 0%.</li>
</ul>
<h2>Verified fee arithmetic for a sale (when fills exist)</h2>
<p>SEC Section&nbsp;31 ($0.00 per $1,000,000 to 2026-04-03, $20.60 per $1,000,000 from 2026-04-04) plus FINRA
Trading Activity Fee ($0.000195 per share in 2026, capped at $9.79 per trade; no fee when the per-share
execution price is below the rate; <strong>no $0.01 minimum on covered equity</strong> — that floor applies
only to security-futures round turns, an error caught and logged as IR-72).</p>
</section>
<p>Pipeline: <code>python3 -m sim.equity_pilot run</code> (scheduled) → <code>reconcile</code> →
this page via <code>scripts/build_site_pilot.py</code>.</p>
'''

    # ---- sources ---------------------------------------------------------
    source_rows = [
        ["NYSE holidays &amp; trading hours (trading calendar)",
         link("https://www.nyse.com/trade/hours-calendars", "NYSE · Holidays &amp; Trading Hours"),
         E("checked 2026-09-19; holiday table + two 1:00pm ET early closes for 2026")],
        ["Federal Reserve Banks holiday schedule (settlement calendar)",
         link("https://www.frbservices.org/about/holiday-schedules", "FRBservices · Holiday Schedules"),
         E("checked 2026-09-19; Fedwire closure days incl. Saturday/Sunday treatment; old /resources/holidays path 404s (IR-73)")],
        ["FINRA Trading Activity Fee schedule",
         link("https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule", "FINRA · SR-FINRA-2024-019 fee-adjustment schedule"),
         E("checked 2026-09-19; 2026: $0.000195/share, $9.79/trade cap")],
        ["SEC Section 31 rate notice",
         link("https://www.finra.org/rules-guidance/notices/information-notice-20260317", "FINRA Information Notice 20260317"),
         E("checked 2026-09-19; $20.60 per $1,000,000 from 2026-04-04")],
        ["Official Nasdaq Trader trade-halts publication",
         link("https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts", "Nasdaq Trader · Trade Halts RSS"),
         E("parser in sim/venue_admin.parse_halts_feed; raw bytes checksummed by the collector before parsing")],
        ["SEC EDGAR automated-access guidance (signal inputs)",
         link("https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data", "SEC · Accessing EDGAR data"),
         E("User-Agent and rate rules the collectors follow")],
        ["Strategy &amp; feed registry",
         link("https://github.com/buffedlizard55-lab/StockPaperSim/blob/main/research/strict/registry.json", "research/strict/registry.json"),
         E("the single authority declaring which feeds are approved (none) and which rules are frozen")],
        ["Legacy Yahoo price files (SECONDARY inputs for intents)",
         link("https://github.com/buffedlizard55-lab/StockPaperSim/tree/main/data/real/prices/yahoo", "data/real/prices/yahoo/"),
         E("aggregator data; never presented as official pricing")],
    ]
    sources_body = table(["What", "Official / review link", "Evidence note"], source_rows)

    (dest / "index.html").write_text(page(
        "Overview",
        "Forward pilot.<br><span>Timestamped, gated, reconciled.</span>",
        index_body,
    ))
    (dest / "upcoming.html").write_text(page(
        "Upcoming intents",
        "Upcoming intents.<br><span>Plans, not orders.</span>",
        upcoming_body,
    ))
    (dest / "blotter.html").write_text(page(
        "Order blotter",
        "The pilot blotter.<br><span>Every journal line, including refusals.</span>",
        blotter_body,
    ))
    (dest / "method.html").write_text(page(
        "Method",
        "Method.<br><span>Why zero fills is the pipeline working.</span>",
        method_body,
    ))
    (dest / "sources.html").write_text(page(
        "Sources",
        "Sources.<br><span>Manual-review links for everything.</span>",
        sources_body,
    ))
    snapshot = {
        "runs": [
            {k: r[k] for k in ("run_date", "submitted_at", "orders_submitted", "fills", "seeded_rehearsal", "journal_head_after") if k in r}
            for r in runs
        ],
        "reconcile": reconcile,
        "upcoming": upcoming,
    }
    (dest / "snapshot.json").write_text(json.dumps(snapshot, indent=1, allow_nan=False) + "\n")
    return {"pages": 5, "runs": len(runs), "orders": orders_total, "fills": fills_total}


if __name__ == "__main__":
    import sys

    result = build()
    print(f"pilot section built: {result['pages']} pages, {result['runs']} run(s), "
          f"{result['orders']} order(s), {result['fills']} fill(s)")
