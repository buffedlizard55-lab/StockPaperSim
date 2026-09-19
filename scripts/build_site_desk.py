"""Build the strict equities desk and a per-row audit of legacy forward fills.

This independent audit does not import the legacy execution engine. No legacy
number is promoted to strict performance. Missing fields are visible blockers.
The output is deterministic from committed research and memory, not wall time.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import html
import io
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
E = html.escape


def load(path):
    return json.loads(Path(path).read_text())


def stream(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(memory_root):
    root = Path(memory_root)
    inventory = []
    for path in sorted(root.rglob("manifest.json")):
        m = load(path)
        if not m.get("run_id"):
            continue
        inventory.append({"run_id": m["run_id"], "mode": m.get("mode", "legacy research"),
                          "participants": m.get("participants", []), "counts": m.get("counts", {}),
                          "manifest": path.relative_to(root).as_posix(), "sha256": sha(path),
                          "strict_eligible": False,
                          "reason": "Separate legacy format; no accepted strict quote-receipt/forward-submission audit"})
    candidates = sorted((root / "live").glob("live-forward-*/manifest.json"))
    if not candidates:
        return {"inventory": inventory, "run_id": None, "fills": [], "upcoming": [], "trips": [],
                "participants": [], "files": [], "strict_fills": 0, "issues": ["NO_LEGACY_FORWARD_BOOK"]}
    path = candidates[-1]
    manifest, folder = load(path), path.parent
    files, issues = [], []
    for name in ("intents", "fills", "trips"):
        file = folder / (name + ".jsonl.gz")
        entry = manifest.get("storage", {}).get("streams", {}).get(name, {})
        actual = sha(file) if file.exists() else None
        ok = bool(actual and actual == entry.get("sha256"))
        files.append({"stream": name, "file": file.relative_to(root).as_posix(),
                      "sha256": actual, "manifest_sha256": entry.get("sha256"), "checksum_ok": ok})
        if not ok:
            issues.append("CUSTODY_FAILURE:" + name)
    intents = stream(folder / "intents.jsonl.gz")
    by_id = {r["intent_id"]: r for r in intents}
    fills = []
    for row in stream(folder / "fills.jsonl.gz"):
        reasons = []
        intent = by_id.get(row.get("intent_id"), {})
        if row.get("reference_source_class") not in {"OFFICIAL", "EXCHANGE-PUBLISHED", "SIP"}:
            reasons.append("NON_OFFICIAL_PRICE_SOURCE")
        if not row.get("quote_receipt_sha256"):
            reasons.append("NO_OBSERVED_BID_ASK_SIZE_RECEIPT")
        if not row.get("rights_evidence_url"):
            reasons.append("NO_PRICE_REDISTRIBUTION_EVIDENCE")
        if not intent.get("recorded_at"):
            reasons.append("NO_PRE_EXECUTION_SUBMISSION_TIMESTAMP")
        if not row.get("settlement_date"):
            reasons.append("NO_T_PLUS_ONE_CASH_SETTLEMENT_DATE")
        if not row.get("reference_url"):
            reasons.append("NO_ROW_PRICE_SOURCE_URL")
        price_path = (ROOT / row.get("reference_file", "")).resolve()
        price_hash_ok = (price_path.is_relative_to(ROOT) and price_path.is_file()
                         and sha(price_path) == row.get("reference_sha256"))
        if not price_hash_ok:
            reasons.append("REFERENCE_PRICE_HASH_NOT_CURRENTLY_MATCHED")
        reasons.append("EXECUTION_PRICE_AND_LIQUIDITY_MODELLED")
        if issues:
            reasons.append("RUN_STREAM_CUSTODY_FAILURE")
        # Legacy fields lack this schema even if later populated accidentally.
        reasons.append("LEGACY_MODEL_NOT_STRICT_EXECUTION_ENGINE")
        fills.append({**row, "reference_file_hash_matches_now": price_hash_ok,
                      "strict_eligible": False, "blockers": reasons})
    upcoming = []
    for row in intents:
        if row.get("status") not in {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}:
            upcoming.append({**row, "execution_authorized": False,
                             "strict_status": "RESEARCH_CANDIDATE_NOT_SUBMITTED",
                             "blockers": ["NO_APPROVED_OFFICIAL_FEED", "LEGACY_INTENT_NOT_STRICT_ORDER",
                                          "NO_OBSERVED_DISPLAYED_LIQUIDITY"]})
    return {"inventory": inventory, "run_id": manifest["run_id"],
            "snapshot_at": manifest.get("created_utc"), "fills": fills, "upcoming": upcoming,
            "trips": stream(folder / "trips.jsonl.gz"), "participants": manifest.get("participants", []),
            "files": files, "strict_fills": 0, "issues": issues,
            "audit_scope": "All fills/intents/trips in latest legacy forward run; every legacy run manifest inventoried. No full independent recertification of legacy results."}


def table(headers, rows, id="", search=False):
    return (f'<div class="table-scroll"><table id="{id}"' + (' data-searchable' if search else '') + '><thead><tr>' +
            ''.join('<th scope="col">'+E(h)+'</th>' for h in headers) + '</tr></thead><tbody>' +
            ''.join('<tr>'+''.join('<td>'+str(c)+'</td>' for c in row)+'</tr>' for row in rows) +
            '</tbody></table></div>')


def link(url, text):
    return f'<a href="{E(url, quote=True)}">{E(text)} ↗</a>'


def build(memory_root, out):
    dest = Path(out) / 'desk'
    dest.mkdir(parents=True, exist_ok=True)
    registry = load(ROOT / 'research/strict/registry.json')
    report = audit(memory_root)
    report['approved_feed_count'] = len(registry['approved_feeds'])
    payload = {'registry': registry, 'audit': report}
    (dest / 'snapshot.json').write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    # All raw legacy streams remain linked through GitHub, not republished twice.
    output = io.StringIO()
    fields = ['participant', 'intent_id', 'date', 'symbol', 'side', 'filled_qty', 'avg_price',
              'reference_source_class', 'reference_file', 'reference_sha256', 'strict_eligible', 'blockers']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for row in report['fills']:
        cells = {**row, 'blockers': ';'.join(row['blockers'])}
        # Spreadsheet formula injection is possible even in a username column.
        # JSON retains exact strings; CSV escapes dangerous leading characters.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(
            ('=', '+', '-', '@')) else v) for k, v in cells.items()})
    (dest / 'trade-audit.csv').write_text(output.getvalue())
    sources = {r['id']: r for r in registry['sources']}
    strategies = []
    for s in registry['strategies']:
        sid = hashlib.sha256(json.dumps(s, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        source_links = ' · '.join(link(sources[k]['url'], sources[k]['title']) for k in s['source_ids'])
        searchable = ' '.join(str(s[k]) for k in ('username', 'family', 'entry_rule', 'exit_rule', 'hypothesis', 'failure_modes'))
        searchable += ' ' + ' '.join(sources[k]['title'] for k in s['source_ids'])
        strategies.append(f'''<article class="strategy" data-searchable data-search-text="{E(searchable, quote=True)}" data-status="{E(s['status'])}">
<div class="card-heading"><span class="tag">{E(s['status'].replace('_',' '))}</span><span>v{E(s['version'])}</span></div>
<h3>{E(s['username'])}</h3><p class="hypothesis">{E(s['hypothesis'])}</p>
<dl><dt>Entry</dt><dd>{E(s['entry_rule'])}</dd><dt>Exit</dt><dd>{E(s['exit_rule'])}</dd>
<dt>Frozen parameters</dt><dd><code>{E(json.dumps(s['parameters'], sort_keys=True))}</code></dd>
<dt>Why it may fail</dt><dd>{E(s['failure_modes'])}</dd></dl>
<details><summary>Evidence, sizing &amp; evaluation plan</summary><p>{E(s['sizing'])}</p>
<p>Required: {E('; '.join(s['required_evidence']))}.</p><p>Official backtest: not run. Forward: blocked. Return: not measured.
Post-trade review will separate realized P&amp;L, quote-marked unrealized P&amp;L, fees and spread cost. Association with an event does not prove causation.</p>
<p>Rule SHA-256: <code>{sid}</code></p><p>{source_links}</p></details></article>''')
    future = table(['Strategy / signal', 'Candidate order', 'Requested session', 'Authorization'], [
        [E(r['participant'])+'<br><small>'+E(r.get('rationale',''))+'</small>',
         E(f"{r['side'].upper()} {r['quantity']} {r['symbol']}"), E(str(r.get('intended_session') or 'Unknown')),
         '<span class="tag blocked">BLOCKED</span><br>Legacy candidate, not submitted'] for r in report['upcoming']], search=True)
    fills = table(['Strategy / intent', 'Entry or exit leg', 'Legacy simulated fill', 'Price evidence', 'Strict audit'], [
        [E(r['participant'])+'<br><small>'+E(r.get('intent_id',''))+'</small>', E(f"{r['date']} · {r['side']} {r['symbol']}"),
         E(f"{r['filled_qty']} shares @ {r['avg_price']}"), E(r.get('reference_source_class','Unknown'))+
         '<br><small>'+E(r.get('reference_file',''))+'</small>',
         '<details><summary>Excluded · '+str(len(r['blockers']))+' blockers</summary><ul>'+''.join('<li>'+E(x)+'</li>' for x in r['blockers'])+'</ul></details>'] for r in report['fills']], search=True)
    trips = table(['Strategy / symbol', 'Entry', 'Exit', 'Legacy net P&L (USD)', 'Classification'], [
        [E(r['participant'])+'<br>'+E(r['symbol']), E(str(r.get('entry_date')))+'<br>'+E(str(r.get('entry_price'))),
         E(str(r.get('exit_date')))+'<br>'+E(str(r.get('exit_price') if r.get('exit_price') is not None else 'Not exited')),
         E(str(r.get('net_pnl_usd'))), 'Research only; not strict performance'+ ('; open-row P&L is not a fresh valuation' if r.get('status') == 'open' else '')]
        for r in report['trips']], search=True)
    inventory = table(['Run', 'Classification', 'Evidence'], [
        [E(r['run_id']), 'Excluded from strict ranking · '+E(r['mode']),
         link('https://github.com/buffedlizard55-lab/StockPaperSim/blob/main/memory/'+r['manifest'], 'Manifest / raw-stream paths')+
         '<br><small>SHA-256 '+E(r['sha256'])+'</small>'] for r in report['inventory']], search=True)
    source_table = table(['Source', 'What was actually reviewed', 'Limitation'], [
        [link(r['url'],r['title'])+'<br><small>'+E(r['source_class'])+'</small>', E(r['observation'])+'<br><small>'+E(r['method'])+'</small>',
         E(r['limitation'])+'<br>'+link(r['primary_url'],'Primary / review link')]
        for r in registry['sources']], search=True)
    body = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Evidence-first US stock paper trading: strategy hypotheses, blocked upcoming orders and independently audited legacy trades.">
<title>StockPaperSim · Official Equities Desk</title><link rel="stylesheet" href="desk.css"></head>
<body><a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="../index.html"><span class="logo">S</span> StockPaperSim</a><span class="tag">PAPER ONLY</span>
<nav aria-label="Desk"><a href="#overview">Overview</a><a href="#research">Research lab</a><a href="#orders">Order desk</a><a href="#trades">Trade journal</a><a href="#sources">Evidence</a><a href="../index.html">Legacy archive ↗</a></nav></header>
<main id="main"><section class="hero" id="overview"><div class="eyebrow">OFFICIAL US EQUITIES · COMPETITION 2026–27</div>
<h1>Test the idea.<br><span>Keep the evidence.</span></h1><p>Return-seeking strategies. One-year competitions. An auditable path from research to paper order, fill and settlement.</p>
<div class="actions"><a class="button" href="#research">Explore {len(strategies)} strategies</a><a class="button secondary" href="#orders">Review upcoming orders</a></div></section>
<aside class="notice"><strong>Execution is blocked — no approved official quote feed.</strong><p>This is a static evidence desk, not a real-time trading connection. No official-price stock performance is claimed.
The legacy forward book contains secondary-source prices and modelled liquidity; those results are excluded below. A checksum proves unchanged bytes, not that a price or claim is true.</p></aside>
<div class="metrics"><article><span>Strict stock fills</span><strong>{report['strict_fills']}</strong><small>No fabricated trades</small></article>
<article><span>Research hypotheses</span><strong>{len(strategies)}</strong><small>Not a list of proven winners</small></article>
<article><span>Legacy candidates</span><strong>{len(report['upcoming'])}</strong><small>Unapproved · not submitted</small></article>
<article><span>Legacy fills excluded</span><strong>{len(report['fills'])}</strong><small>Row-level audit below</small></article></div>
<section class="panel"><div class="section-title"><div><div class="eyebrow">THE COMPETITION</div><h2>Highest net return. Evidence first.</h2></div><span class="tag blocked">WAITING FOR DATA</span></div>
<p><strong>{E(registry['competition']['start'])} → {E(registry['competition']['end_exclusive'])} (end exclusive)</strong> · $100,000 simulated starting cash per entrant · rank by net total return, not Sharpe or risk limits.</p>
<p>The strict leaderboard has no ranked entrants yet. No trades is <strong>not measured</strong>, not a verified 0% return. Nasdaq and NYSE are venues; the S&amp;P 500 is an index, not an orderable security. Any constituent or tracking ETF requires its own security and quote evidence.</p>
<p>Rules are versioned before execution. Shorting, margin and passive market making remain blocked until borrow, financing and queue evidence exist. These are execution constraints, not a risk-management objective.</p></section>
<section id="research"><div class="section-title"><div><div class="eyebrow">RESEARCH → HYPOTHESIS → TEST</div><h2>The strategy lab</h2></div><a href="snapshot.json" download>Download all rules + evidence ↓</a></div>
<p>All requested project topics are covered. Technical prototypes have executable signal arithmetic; design-only ideas still need event adapters. Every strategy is blocked from competition orders pending official evidence. Parameters are our hypotheses, not attributed promises of profit.</p>
<div class="filters"><label>Search this desk<input type="search" id="search" placeholder="Try FDA, CEO, partial fills…" autocomplete="off"></label><label>Strategy implementation<select id="status"><option value="">All research statuses</option><option value="PROTOTYPE_BLOCKED_DATA">Code prototype · data blocked</option><option value="DESIGN_ONLY">Design only</option></select></label><button id="reset" type="button">Reset filters</button></div>
<p id="result-count" role="status" aria-live="polite">{len(strategies)} strategies</p><div class="strategy-grid">{''.join(strategies)}</div><p id="empty" hidden>No strategies match these filters.</p></section>
<section id="orders" class="panel"><div class="eyebrow">SEPARATE US STOCK ORDER DESK</div><h2>Can a strategy place an upcoming trade?</h2>
<p><strong>Not into the strict competition today.</strong> The tested ledger supports timestamped intents, market and marketable-limit paper fills, partial quantities, cancellations, shared quote-size consumption and simulated settlement. The feed collector and scheduler are not connected. No manual prices or browser-local trades are accepted as evidence.</p>
<ol class="pipeline"><li>Signal<br><small>Publish + receive times</small></li><li>Order<br><small>Recorded before quote</small></li><li>Quote gate<br><small>Official source + rights + size</small></li><li>Paper fill<br><small>Shared displayed capacity</small></li><li>Settlement<br><small>Separate verified calendar</small></li></ol>
<p>Strict pending orders: <strong>0</strong>. Below are the existing research book's intentions, not instructions to trade and not re-submitted orders. Requested dates may be stale; a fresh valid signal is required.</p>{future}
<details><summary>Execution and accounting rules</summary><p>Reject missing, future, timezone-naive, stale (&gt;5 seconds), locked/crossed, halted or unsupported quotes. Consume bid size on sells and ask size on buys; never reuse the same quote's capacity across competitors. A new quote ID alone does not prove replenishment: the prototype also debits prior simulated consumption at the same feed/symbol/side/price during that session. This conservative cap may underfill. Marketable limits only: touching a passive limit does not establish queue priority.</p>
<p>Fees in the ledger prototype are a declared per-share simulation assumption, not a verified broker charge. Spread cost is calculated versus the observed mid; no synthetic deeper-book liquidity is added. Buys debit available cash, sale proceeds stay unsettled until after the verified settlement day ends, and FIFO cost basis includes entry/exit fees. Settlement is simulated, never DTC confirmation.</p>
<p>Missing fresh marks produce unknown equity and return. Corporate actions, real-time ingestion, strategy scheduling, price corrections, margin/borrow and full queue replay still need integration before an eligible live competition.</p></details></section>
<section id="trades"><div class="section-title"><div><div class="eyebrow">READ EVERY LEG · KEEP THE ORIGINAL</div><h2>Trade journal &amp; exclusion audit</h2></div><a href="trade-audit.csv" download>Export audit CSV ↓</a></div>
<p>Audit snapshot: <code>{E(str(report.get('snapshot_at','Unknown')))}</code>. Book: <code>{E(str(report['run_id']))}</code>. Source files are checked against their recorded manifest hashes. Freshness is not implied by the word “Live” in legacy names.</p>
<div class="notice">{'Custody errors: '+E(', '.join(report['issues'])) if report['issues'] else 'Audited stream hashes match the legacy manifest. This does not establish official pricing, liquidity or pre-trade submission.'}</div>
{fills}<h3>Legacy entries, exits and P&amp;L — excluded from ranking</h3>{trips}
<details><summary>Every archived run and its raw trade streams</summary>{inventory}</details></section>
<section id="sources"><div class="eyebrow">MANUAL REVIEW LINKS · REVIEWED 2026-09-19</div><h2>Sources, not assurances</h2>
<p>Project pages are discovery sources. Only the publisher of a fact can support that specific fact: SEC filings do not verify a later stock quote, and FDA decisions do not verify a profitable stock strategy. No X or Facebook post was established by this search; no return claim was imported from social media.</p>{source_table}</section>
<section class="panel"><div class="eyebrow">NEXT IMPLEMENTATION GATES</div><h2>What still needs to happen</h2><ol>
<li>Obtain an approved official/SIP quote and trade feed with documented access and redistribution rights. Keep restricted raw data outside the public repository.</li>
<li>Implement and independently validate raw-to-normalized adapters, receipt capture, halts, corporate actions and separate trading/settlement calendars.</li>
<li>Connect the strategy scheduler to durable order submission before the executable quote arrives. Preserve revisions and restart-safe capacity state.</li>
<li>Run an out-of-sample paper pilot, reconcile cash/holdings/fees daily, then enable a one-year leaderboard. Add authenticated external timestamps; a local hash chain is not tamper-proof against full history replacement.</li>
<li>Implement remaining event strategies, licensed queue replay for market making, delisting-safe backtests and multiple-testing controls. Report causal explanations only when supported.</li></ol>
<p>{E(registry['disclaimer'])}</p></section>
</main><footer>Paper trading research only · No broker connection · No investment advice · <a href="snapshot.json">Machine-readable snapshot</a> · <a href="https://github.com/buffedlizard55-lab/StockPaperSim">Repository ↗</a></footer>
<script src="desk.js" defer></script></body></html>'''
    (dest / 'index.html').write_text(body, encoding='utf-8')
    for name in ('desk.css', 'desk.js'):
        shutil.copyfile(ROOT / 'site' / name, dest / name)
    return ['desk/' + name for name in ('index.html', 'snapshot.json', 'trade-audit.csv', 'desk.css', 'desk.js')]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-root', default=str(ROOT / 'memory'))
    parser.add_argument('--out', default=str(ROOT / 'docs'))
    args = parser.parse_args()
    build(args.memory_root, args.out)
