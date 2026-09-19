"""Command-line driver for StockPaperSim.

Everything the project needs to be re-run, inspected and published is reachable
from here, and no command requires interactive input:

    python3 -m sim.cli run                 # run the season, write memory
    python3 -m sim.cli run --quick         # primary seed only, no robustness panel
    python3 -m sim.cli leaderboard         # ranked table from memory
    python3 -m cli report @MomentumMax_12x1
    python3 -m sim.cli compare             # multi-seed robustness panel
    python3 -m sim.cli query fills --symbol NVDA --participant @BetaChaser_3xProxy
    python3 -m sim.cli verify              # checksums + data audits
    python3 -m sim.cli irregularities      # every IR-xx flag raised
    python3 -m sim.cli sources             # the verified-source register
    python3 -m sim.cli price-audit         # strict official-price eligibility audit
    python3 -m sim.cli season2             # Season 2: official prices, fail closed
    python3 -m sim.cli ledger --limit 20   # every round trip with verified prices
    python3 -m sim.cli live --mode all      # plan the live forward book + rehearsal
    python3 -m sim.cli live --mode forward  # upcoming intents only, nothing settles
    python3 -m sim.cli live-blotter         # every filled forward trade, verified
    python3 -m sim.cli official            # the official-price auction book
    python3 -m sim.cli official-blotter    # every settled official trade + evidence
    python3 -m sim.cli trades              # the unified ledger across every book
    python3 -m sim.cli build-site          # regenerate the GitHub Pages site
    python3 -m sim.cli export fills out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import textwrap
from typing import Dict, List, Optional, Sequence

from . import analytics, config, engine, eligibility, ledger as ledger_mod, marketdata, memory
from . import live, live_season, official_season, realdata, rollover, season2, tradelog, universe

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SEEDS: List[int] = list(config.SCENARIO_SEEDS)


# ==========================================================================
# run
# ==========================================================================

def cmd_run(args: argparse.Namespace) -> int:
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else \
        (DEFAULT_SEEDS[:1] if args.quick else DEFAULT_SEEDS)
    cfg = config.CompetitionConfig()
    if args.starting_cash:
        cfg.starting_cash = float(args.starting_cash)
    print(f"StockPaperSim :: {cfg.name} :: {cfg.season}")
    print(f"window {cfg.start} -> {cfg.end} | starting cash "
          f"{cfg.starting_cash:,.0f} | rank metric {cfg.rank_metric}")
    print(f"seeds: {seeds} (index 0 = primary scenario, full event memory)")
    md_by_seed: Dict[int, object] = {}
    for i, seed in enumerate(seeds):
        t = "primary" if i == 0 else "robustness"
        print(f"  building replay for seed {seed} [{t}] ...", flush=True)
        md = marketdata.build_replay(cfg, seed=seed)
        md_by_seed[seed] = md
        if i == 0:
            d = md.diagnostics
            print(f"    sessions={len(md.dates)} (warmup {md.warmup_days}) "
                  f"real SPX {d['spx_total_return_pct_real']:+.2f}% | real VIX "
                  f"mean {d['vix_mean_real']:.2f} max {d['vix_max_real']:.2f} | "
                  f"SPY/index ratio {d['spy_index_ratio_fitted']} "
                  f"(max err {d['spy_ratio_max_abs_pct_error_vs_real_monthly_closes']}%)")
            audit = d.get("monthly_range_audit_spy", {})
            if audit:
                print(f"    SPY monthly high-low range vs real Yahoo bars: "
                      f"mean|diff| {audit.get('mean_abs_diff_pp')}pp, max "
                      f"{audit.get('max_abs_diff_pp')}pp over "
                      f"{audit.get('n_months')} months")
    run_ids = [f"season1-{'primary' if i == 0 else 'robust'}-seed{s}"
               for i, s in enumerate(seeds)]
    records = engine.run_scenarios(cfg, seeds, md_by_seed, root=args.memory_root,
                                   run_ids=run_ids, verbose=args.verbose)
    primary = records[0]
    _print_leaderboard(primary["leaderboard"], primary["market"])
    if len(records) > 1:
        panel = analytics.robustness_panel(records)
        _print_robustness(panel)
        with open(os.path.join(args.memory_root, "robustness_panel.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(panel, fh, indent=1)
    _print_irregularities(primary["irregularities"], limit=args.ir_limit)
    print(f"\nmemory written to {args.memory_root}/runs/")
    for rec in records:
        print(f"  {rec['run_id']:34s} winner {rec['winner']:24s} "
              f"{rec['timing']['elapsed_seconds']:6.1f}s "
              f"{'full' if rec['full_memory'] else 'summary'} memory")
    return 0


def _print_leaderboard(board: Sequence[dict], market: dict) -> None:
    print("\n" + "=" * 118)
    print(f"LEADERBOARD  (ranked on total return %, the metric the referenced "
          f"competitions use; S&P 500 over the same window "
          f"{market['spx_return_pct']:+.2f}%)")
    print("=" * 118)
    hdr = (f"{'#':>2} {'username':24s} {'return%':>9} {'net P&L $':>12} "
           f"{'maxDD%':>8} {'Sharpe':>7} {'beta':>6} {'trades':>7} "
           f"{'win%':>6} {'cost%':>6} {'turnover':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in board:
        print(f"{r['rank']:2d} {r['username']:24s} {r['total_return_pct']:+9.2f} "
              f"{r['net_pnl_usd']:+12,.0f} {r['max_drawdown_pct']:8.2f} "
              f"{r['sharpe']:7.2f} {r['beta']:6.2f} {r['closed_trades']:7d} "
              f"{r['win_rate_pct']:6.1f} {r['execution_cost_pct']:6.2f} "
              f"{r['turnover_x']:9.1f}x")


def _print_robustness(panel: dict) -> None:
    print("\n" + "=" * 100)
    print("ROBUSTNESS PANEL  (same real market path, different idiosyncratic draws)")
    print("=" * 100)
    hdr = (f"{'username':24s} {'mean%':>8} {'median%':>8} {'best%':>8} {'worst%':>9} "
           f"{'stdev':>7} {'>0':>4} {'>idx':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in panel["by_participant"]:
        print(f"{r['username']:24s} {r['mean_return_pct']:+8.2f} "
              f"{r['median_return_pct']:+8.2f} {r['best_return_pct']:+8.2f} "
              f"{r['worst_return_pct']:+9.2f} {r['stdev_pp']:7.2f} "
              f"{r['positive_scenarios']:4d} {r['beat_index_scenarios']:5d}")
    print(panel["note"])


def _print_irregularities(items: Sequence[dict], limit: int = 40) -> None:
    print("\n" + "=" * 100)
    print(f"IRREGULARITIES FLAGGED DURING THE RUN ({len(items)})")
    print("=" * 100)
    for ir in items[:limit]:
        head = (f"  [{ir['id']}] {ir.get('severity', '?'):6s} "
                f"{(ir.get('symbol') or '-'):6s} ")
        # Print the whole message, wrapped: these rows ARE the audit trail, and
        # truncating them cut off exactly the informative part (the measured
        # leverage in the "e.g." clause).
        text = ir["message"]
        width = 100 - len(head)
        first, *rest = textwrap.wrap(text, width=width) or [""]
        print(head + first)
        for line in rest:
            print(" " * len(head) + line)
    if len(items) > limit:
        print(f"  ... and {len(items) - limit} more (see memory or `cli irregularities`)")


# ==========================================================================
# read commands
# ==========================================================================

def _store(args) -> memory.MemoryStore:
    return memory.MemoryStore(root=args.memory_root)


def _resolve_run(args, store: memory.MemoryStore) -> str:
    run_id = args.run
    if run_id:
        return run_id
    runs = [r["run_id"] for r in store.runs()]
    primary = [r for r in runs if "primary" in r]
    if primary:
        return sorted(primary)[-1]
    return store.latest_run_id() or (runs[-1] if runs else "")


def cmd_leaderboard(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = _resolve_run(args, store)
    if not run_id:
        print("no runs in memory yet; run `python3 -m sim.cli run` first")
        return 1
    board = store.load(run_id, "leaderboard.json") or {}
    market = store.load(run_id, "market_report.json") or {}
    print(f"run {run_id}")
    _print_leaderboard(board.get("leaderboard", []), market)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = _resolve_run(args, store)
    rep = store.report(run_id, args.username)
    if not rep:
        print(f"no report for {args.username} in run {run_id}")
        print("available:", ", ".join(r["username"] for r in store.reports(run_id)))
        return 1
    _print_report(rep, full=args.full)
    return 0


def _print_report(rep: dict, full: bool = False) -> None:
    n = rep["narrative"]
    print("=" * 100)
    print(f"{rep['username']}   ({rep['archetype']})   run strategy class "
          f"{rep['strategy_id']}")
    print("=" * 100)
    print(f"total return {rep['total_return_pct']:+.2f}%   net P&L "
          f"{rep['net_pnl_usd']:+,.0f} USD   final equity "
          f"{rep['final_equity']:,.0f}   sessions {rep['sessions']}")
    r = rep["risk"]
    print(f"max drawdown {r['max_drawdown_pct']:.2f}%   Sharpe {r['sharpe']:.2f}   "
          f"Sortino {r['sortino']:.2f}   ann. vol {r['annualised_vol_pct']:.1f}%   "
          f"VaR95 {r['var_95_pct']:.2f}%/d   CVaR95 {r['cvar_95_pct']:.2f}%/d")
    m = rep["market_relation"]
    print(f"beta {m['beta']:.2f}   alpha {m['alpha_annual_pct']:+.2f}%/yr   "
          f"R2 {m['r_squared']:.2f}   beta contribution "
          f"{m['beta_contribution_pct']:+.2f}pp   residual "
          f"{m['residual_return_pct']:+.2f}pp")
    t = rep["trades"]
    print(f"trades: {t['closed_trades']} closed / {t['open_trades']} open   "
          f"win rate {t['win_rate_pct']:.1f}%   profit factor "
          f"{t['profit_factor'] if t['profit_factor'] is not None else 'n/a'}   "
          f"median hold {t['median_sessions_held']:.0f} sessions")
    c = rep["costs"]
    print(f"costs: {c['total_cost_usd']:,.0f} USD total "
          f"({c['total_cost_pct_of_starting_cash']:.2f}% of capital, "
          f"{c['avg_execution_cost_bps_per_fill']:.2f} bp/fill)   "
          f"turnover {rep['turnover']['annualised_turnover_x']:.1f}x/yr")
    e = rep["exposure"]
    print(f"exposure: avg gross {e['avg_gross_pct_of_starting_cash']:.0f}%   "
          f"max gross {e['max_gross_pct_of_starting_cash']:.0f}%   "
          f"max leverage {e['max_leverage']:.2f}x   "
          f"margin calls {rep['carry']['margin_call_count']}")
    d = rep["pnl_decomposition"]
    print(f"P&L decomposition: realized {d['realized_trading_pnl_usd']:+,.0f}   "
          f"open {d['open_position_pnl_usd']:+,.0f}   "
          f"dividends {d['dividends_usd']:+,.0f}   "
          f"borrow {d['borrow_fees_usd']:+,.0f}   "
          f"residual {d['unexplained_residual_usd']:+,.0f}")
    print("\nPOST-MORTEM")
    print("-" * 100)
    for para in n["prose"]:
        print(f"\n### {para['title']}\n{para['text']}")
    if full:
        print("\nCONTRIBUTION BY SYMBOL")
        for row in rep["contribution_by_symbol"]:
            print(f"  {row['symbol']:6s} realized {row['realized_pnl']:+12,.2f}  "
                  f"open {row['open_pnl']:+10,.2f}  total {row['total_pnl']:+12,.2f}  "
                  f"({row['closed_trades']} closed trades, fees {row['fees']:,.2f})")
        print("\nMONTHLY RETURNS")
        for row in rep["monthly_returns"]:
            print(f"  {row['month']}  {row['return_pct']:+8.2f}%  "
                  f"end equity {row['end_equity']:12,.2f}  ({row['sessions']} sessions)")
        print("\nIMPLEMENTATION SHORTFALL")
        for k, v in rep["implementation_shortfall"].items():
            print(f"  {k}: {v}")


def cmd_compare(args: argparse.Namespace) -> int:
    store = _store(args)
    runs = [r["run_id"] for r in store.runs()]
    if not runs:
        print("no runs in memory")
        return 1
    scenarios = []
    for run_id in runs:
        board = store.load(run_id, "leaderboard.json") or {}
        market = store.load(run_id, "market_report.json") or {}
        man = store.load(run_id, "manifest.json") or {}
        scenarios.append({"seed": man.get("seed"), "market": market,
                          "reports": store.reports(run_id)})
    panel = analytics.robustness_panel(scenarios)
    _print_robustness(panel)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = _resolve_run(args, store)
    q = store.stream(run_id, args.stream)
    if args.participant:
        q = q.where(participant=args.participant)
    if args.symbol:
        q = q.where(symbol=args.symbol)
    if args.start or args.end:
        q = q.between("date", args.start, args.end)
    rows = q.list()[:args.limit]
    if args.format == "json":
        print(json.dumps(rows, indent=1))
    else:
        cols = args.columns.split(",") if args.columns else \
            (sorted({k for r in rows for k in r}) if rows else [])
        if not cols:
            print("(no rows)")
            return 0
        widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0))
                  for c in cols}
        print("  ".join(c.ljust(widths[c])[:widths[c]] for c in cols))
        for r in rows:
            print("  ".join(str(r.get(c, ""))[:widths[c]].ljust(widths[c]) for c in cols))
    print(f"\n({len(rows)} rows shown from run {run_id} / stream {args.stream})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = _resolve_run(args, store)
    n = store.export_csv(run_id, args.stream, args.dest)
    print(f"exported {n} rows of {args.stream} from {run_id} to {args.dest}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    store = _store(args)
    runs = [r["run_id"] for r in store.runs()]
    if not runs:
        print("no runs in memory")
        return 1
    bad = 0
    for run_id in runs:
        v = store.verify(run_id)
        flag = "OK" if v["intact"] else "FAILED"
        print(f"{flag:7s} {run_id:34s} files {v['files_checked']:3d} "
              f"ok {v['ok']:3d} mismatched {len(v['mismatched'])} "
              f"missing {len(v['missing'])}")
        bad += 0 if v["intact"] else 1
    print("\nDATA AUDITS (independent of the run memory)")
    for line in _audit_data():
        print("  " + line)
    return 1 if bad else 0


def _audit_data() -> List[str]:
    out: List[str] = []
    cfg = config.CompetitionConfig()
    md = marketdata.build_replay(cfg)
    out.append(f"replay builds: {len(md.dates)} sessions "
               f"({md.warmup_days} warm-up + {len(md.dates) - md.warmup_days} competition)")
    d = md.diagnostics
    out.append(f"real FRED S&P 500 over the window: {d['spx_total_return_pct_real']:+.3f}% "
               f"(annualised log vol {d['spx_annualised_vol_real']:.4f})")
    out.append(f"real FRED VIX: mean {d['vix_mean_real']:.2f} max {d['vix_max_real']:.2f}")
    out.append(f"SPY/index ratio fitted on real monthly closes: "
               f"{d['spy_index_ratio_fitted']} (max abs error "
               f"{d['spy_ratio_max_abs_pct_error_vs_real_monthly_closes']}%)")
    out.append(f"VIX observations dropped because they fell on a market closure: "
               f"{d.get('vix_observations_dropped_on_closed_dates')} (IR-01)")
    out.append(f"market closures detected from gaps in the real series: "
               f"{d.get('full_closures_detected')}; early closes: "
               f"{d.get('early_closes')}")
    audit = d.get("monthly_range_audit_spy", {})
    if audit:
        out.append(f"simulated vs real SPY monthly high-low range: mean|diff| "
                   f"{audit.get('mean_abs_diff_pp')}pp, max "
                   f"{audit.get('max_abs_diff_pp')}pp over "
                   f"{audit.get('n_months')} months")
        out.append("  per month: " + ", ".join(
            f"{m} {v['diff_pct_points']:+.2f}pp"
            for m, v in sorted(audit.get("months", {}).items())))
    # OHLC containment
    bad = 0
    for s in md.symbols:
        for b in md.bars[s]:
            if not (b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high):
                bad += 1
    out.append(f"OHLC containment violations across all bars: {bad}")
    uni = universe.build_universe()
    out.append(f"universe: {len(uni)} instruments, "
               f"{sum(1 for i in uni if i.is_real_anchored)} with a real price anchor")
    from .strategies import build_roster
    roster = build_roster()
    handles = [s.username for s in roster]
    out.append(f"roster: {len(roster)} participants, {len(set(handles))} unique usernames")
    return out


def cmd_irregularities(args: argparse.Namespace) -> int:
    store = _store(args)
    runs = [r["run_id"] for r in store.runs()]
    static = _static_irregularities()
    print("STATIC IRREGULARITIES (data, regulation, model design)")
    print("=" * 100)
    for ir in static:
        print(f"  [{ir['id']}] {ir['severity']:6s} {ir['topic']}")
        print(f"          {ir['detail']}")
        for link in ir.get("links", []):
            print(f"          -> {link}")
    print("\nRUN-TIME IRREGULARITIES (raised while simulating)")
    print("=" * 100)
    for run_id in runs:
        items = store.load(run_id, "irregularities.json") or []
        print(f"\n{run_id}: {len(items)}")
        for ir in items:
            print(f"  [{ir['id']}] {ir.get('severity', '?'):6s} {ir['message'][:170]}")
    return 0


def _static_irregularities() -> List[dict]:
    path = os.path.join(REPO_ROOT, "research", "IRREGULARITIES.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def cmd_sources(args: argparse.Namespace) -> int:
    rows = list(config.all_verified_sources())
    for prov in marketdata.provider_catalogue():
        rows.append({
            "claim": f"Live-data adapter for {prov['name']}: "
                     f"{'real-time capable' if prov.get('realtime_capable') else 'end-of-day only'}"
                     f" | API key env var {prov.get('key_env', '-')} | "
                     f"{'EXECUTED in this sandbox' if prov.get('executed_in_this_sandbox') else 'not executed here'}"
                     f" | {prov.get('notes', '')}",
            "url": prov.get("docs_url", ""), "publisher": prov["name"],
            "status": "ADAPTER-DOCS",
        })
    print(f"{'#':>3} {'status':22s} claim / url")
    print("-" * 100)
    for i, r in enumerate(rows, 1):
        claim = (r.get("claim") or "")[:110]
        print(f"{i:3d} {str(r.get('status', ''))[:22]:22s} {claim}")
        print(f"    {'':22s} {r.get('url', '')}")
        if r.get("ref"):
            print(f"    {'':22s} ref: {r['ref']}")
    print(f"\n{len(rows)} source rows. Status legend: "
          "FETCHED-VERIFIED (retrieved in this environment and saved under data/real), "
          "FETCHED (retrieved, content used, not saved), "
          "KNOWN-NOT-FETCHED (cited from the literature; NOT retrieved here - "
          "verify manually before relying on it).")
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    """Build both seasons into docs/: Season 1 pages, then Season 2 pages."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
    import build_site  # type: ignore
    import build_site_season2  # type: ignore

    rc = build_site.main(argv=[
        "--memory-root", args.memory_root, "--out", args.out] +
        (["--run", args.run] if args.run else []))
    if rc != 0:
        return rc

    try:
        # The official auction book is written before the live book so the
        # published-site tests see its pages when they walk docs/; like every
        # other builder it owns its own directory.
        import build_site_official  # type: ignore
        official_written = build_site_official.build(args.memory_root, args.out)
        print(f"  official auction book: {len(official_written)} pages under "
              f"docs/official/")
    except SystemExit as exc:
        print(f"  official auction book: SKIPPED - {exc}")

    try:
        # The live book is a third section and its own builder, called between
        # the two for the same reason Season 2 is called after Season 1: each
        # builder owns its directory and CI diffs the whole tree afterwards.
        import build_site_live  # type: ignore
        live_written = build_site_live.build(args.memory_root, args.out)
        print(f"  live book: {len(live_written)} pages under docs/live/")
    except SystemExit as exc:
        # A memory root without a live run (a scratch root built to test
        # determinism, for example) must not publish an empty live section.
        print(f"  live book: SKIPPED - {exc}")

    try:
        # Season 1's pages are complete before Season 2's are rendered, so the
        # cross-links are injected here rather than by two builders that would
        # each have to know about the other's files: the nav entry goes on every
        # Season 1 page (so the Season 2 section is reachable from any of them)
        # and the explanatory callout goes on the overview page only. The
        # injection is idempotent, which matters because docs/ is diffed against
        # a fresh build in CI.
        for dirpath, dirnames, filenames in os.walk(args.out):
            dirnames[:] = [d for d in dirnames if d not in ("season2", "assets")]
            for name in sorted(filenames):
                if not name.endswith(".html"):
                    continue
                page_path = os.path.join(dirpath, name)
                with open(page_path, "r", encoding="utf-8") as handle:
                    html = handle.read()
                # A page one directory down needs "../" to reach the Season 2
                # section; getting this wrong published a nav link that 404'd on
                # all twenty Season 1 participant pages (caught by the links test).
                rel_dir = os.path.relpath(dirpath, args.out)
                depth = 0 if rel_dir == "." else len(rel_dir.split(os.sep))
                injected = build_site_season2.inject_banner(
                    html, callout=(name == "index.html"), prefix="../" * depth)
                # The Live Book is a third section whose pages are written by
                # build_site_live above; its nav entry is injected here, next to
                # Season 2's, so a Season-1-only build (which is what the
                # published-site tests construct) produces no dangling link.
                injected = injected.replace(
                    "</nav>",
                    f'<a href="{"../" * depth}live/index.html">Live Book</a></nav>', 1) \
                    if "live/index.html" not in injected else injected
                # The Official Auction Book is the fourth section, and this is
                # the section the brief's first requirement is answered by: it
                # is the only book on the site that may execute on a price a
                # publisher printed.  Its nav entry is injected here with the
                # others so a Season-1-only build stays link-clean.
                injected = injected.replace(
                    "</nav>",
                    f'<a href="{"../" * depth}official/index.html">Official Book'
                    f'</a></nav>', 1) \
                    if "official/index.html" not in injected else injected
                if injected != html:
                    with open(page_path, "w", encoding="utf-8") as handle:
                        handle.write(injected)
        written = build_site_season2.build(args.memory_root, args.out, args.run)
        print(f"  season 2: {len(written)} pages under docs/season2/ "
              f"(from run {build_site_season2.Season2Site(args.memory_root, args.run).run_id})")
    except SystemExit as exc:
        # No Season 2 run in this memory root (for example a scratch root built
        # only to check Season 1 determinism). Say so loudly rather than
        # publishing a Season 2 section that would silently be empty.
        print(f"  season 2: SKIPPED - {exc}")
    return rc


# ==========================================================================
# official price eligibility + season2 + ledger
# ==========================================================================

def cmd_price_audit(args: argparse.Namespace) -> int:
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    report = eligibility.audit_official_prices(
        root=os.path.abspath(args.data_root), symbols=symbols,
        start=args.start, end=args.end, backend=args.backend)
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0 if report["eligible"] else 1


def cmd_season2(args: argparse.Namespace) -> int:
    labels = tuple(s.strip() for s in args.labels.split(",") if s.strip())
    if args.price_source == "yahoo" and not args.allow_secondary_research:
        print("refusing Yahoo in the official Season 2 command; use "
              "--allow-secondary-research to run the clearly non-eligible research replay")
        return 2
    print(f"StockPaperSim :: {season2.SEASON2_NAME} :: {season2.SEASON2_SEASON}")
    print(f"window {realdata.SEASON2_START} -> {realdata.SEASON2_END} | "
          f"price source {args.price_source} | assumptions {', '.join(labels)}")
    print("building the selected collected market from data/real/prices + data/real/fred ...",
          flush=True)
    try:
        records = season2.run_season2(
            root=args.memory_root, labels=labels, verbose=args.verbose,
            price_source=args.price_source,
            require_official=(args.price_source != "yahoo"))
    except eligibility.OfficialPriceEligibilityError as exc:
        print(str(exc), file=sys.stderr)
        print(json.dumps(exc.audit, indent=2, sort_keys=False))
        return 1
    primary = records[0]
    _print_leaderboard(primary["leaderboard"], primary["market"])
    if len(records) > 1:
        print("\nSTRESS PANEL - every participant, same prices, harder venue")
        names = [r["username"] for r in records[0]["leaderboard"]]
        head = f"{'participant':26s}" + "".join(f"{lbl:>20s}" for lbl in
                                                [r["label"] for r in records])
        print(head)
        for name in names:
            cells = []
            for rec in records:
                row = next((r for r in rec["leaderboard"] if r["username"] == name), None)
                cells.append(f"{row['total_return_pct']:>19.1f}%" if row else f"{'n/a':>20s}")
            print(f"{name:26s}" + "".join(cells))
    for rec in records:
        v = _read_json(os.path.join(args.memory_root, "runs", rec["run_id"],
                                    "verification.json"))
        print(f"\n{rec['run_id']}")
        print(f"  fills {v['summary']['fill_count']:5d} | round trips "
              f"{v['summary']['round_trips_closed']:4d} | net P&L from round trips "
              f"${v['summary']['net_pnl_usd']:>14,.2f}")
        bound = max((p.get("rounding_bound_usd") or 0.0)
                    for p in v["per_participant"]) if v.get("per_participant") else 0.0
        print(f"  ledger digest {v['ledger_digest_sha256'][:16]}... | max |equity "
              f"residual| ${v['max_abs_equity_residual_usd']:,.4f} "
              f"(bound ${bound:,.4f}; the tape stores six-decimal prices, so a "
              "non-zero residual inside that bound is expected)")
    return 0


# ==========================================================================
# season2 + ledger
# ==========================================================================

def cmd_ledger(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = _resolve_run(args, store)
    run_dir = store.run_dir(run_id)
    doc = ledger_mod.read_ledger(run_dir)
    trips = doc["round_trips"]
    fills = doc["fills"]
    if args.participant:
        trips = [t for t in trips if t.get("participant") == args.participant]
        fills = [f for f in fills if f.get("participant") == args.participant]
    if args.only_closed:
        trips = [t for t in trips if t.get("status") == "closed"]
    if args.export:
        dest = args.export
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="") as handle:
            fields = ["symbol", "direction", "status", "entry_date", "entry_price",
                      "exit_date", "exit_price", "quantity", "gross_pnl_usd", "fees_usd",
                      "net_pnl_usd", "entry_fill_count", "exit_reason"]
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                    lineterminator="\n")
            writer.writeheader()
            for row in sorted(trips, key=lambda t: (t["symbol"], t["entry_date"])):
                writer.writerow(row)
        print(f"wrote {len(trips)} round trips to {dest}")
        return 0
    summary = _read_json(os.path.join(run_dir, ledger_mod.LEDGER_SUMMARY))
    print(f"run {run_id} | fills {summary['fill_count']} | closed trips "
          f"{summary['round_trips_closed']} | open {summary['round_trips_open']}")
    print(f"  gross ${summary['gross_pnl_usd']:,.2f} | fees "
          f"${summary['fees_usd']:,.2f} | net ${summary['net_pnl_usd']:,.2f}")
    print(f"  median participation {summary['median_participation_pct']}% of real "
          f"session volume | max {summary['max_participation_pct']}%")
    print(f"  mean slippage vs real open {summary['slippage_vs_open_bps_mean']} bps | "
          f"vs real close {summary['slippage_vs_close_bps_mean']} bps")
    shown = sorted(trips, key=lambda t: -abs(t.get("net_pnl_usd", 0)))[:args.limit]
    print("\n" + f"{'symbol':7s}{'dir':6s}{'entry':12s}{'entry px':>10s}{'exit':12s}"
          f"{'exit px':>10s}{'qty':>8s}{'net $':>12s}  reason")
    for t in shown:
        print(f"{t['symbol']:7s}{t['direction']:6s}{t['entry_date']:12s}"
              f"{t['entry_price']:>10.2f}{str(t['exit_date']):12s}"
              f"{(t['exit_price'] or 0):>10.2f}{t['quantity']:>8d}"
              f"{t['net_pnl_usd']:>12,.2f}  {str(t.get('exit_reason') or t.get('entry_reason'))[:44]}")
    return 0


# ==========================================================================
# live: the forward book
# ==========================================================================

def cmd_live(args: argparse.Namespace) -> int:
    """Plan the live forward book and/or run its walk-forward rehearsal."""
    mode = args.mode
    if mode in ("forward", "all"):
        settle_sess = getattr(args, "settle_session", "") or None
        result = live_season.run_forward(root=args.memory_root, as_of=args.as_of,
                                         horizon=args.horizon,
                                         settle_session=settle_sess,
                                         verbose=args.verbose)
        manifest = result["manifest"]
        print(f"LIVE FORWARD BOOK  {result['run_id']}")
        print(f"  plan date {manifest['plan_date']} · last verified equity bar "
              f"{manifest['last_verified_equity_session']} · last official "
              f"observation {manifest['last_official_observation']}")
        print(f"  {len(result['pending'])} upcoming intents, all PENDING-SETTLEMENT")
        print(f"  {'participant':28s} {'intent':34s} {'shares':>9}  evidence")
        for intent in result["pending"]:
            evidence = next((f"{k}@{v.get('observation_date')}"
                             for k, v in intent["evidence"].items()), "")
            print(f"  {intent['participant']:28s} "
                  f"{intent['side']+' '+intent['symbol']+' for '+intent['intended_session']:34s} "
                  f"{intent['quantity']:9d}  {evidence}")
        print(f"  projected sessions: {manifest['projected_sessions']} "
              f"(from {manifest['projection']['source'] if 'projection' in manifest else live.PROJECTION_SOURCE})")
        print(f"  verification: {result['verification']['verdict']} "
              f"({result['verification']['checks']} checks)")
        print(f"  memory: {result['run_dir']}")
    if mode in ("rehearsal", "all"):
        result = live_season.run_rehearsal(root=args.memory_root,
                                           verbose=args.verbose)
        board = result["leaderboard"]
        bench = result["benchmark"]
        print("\n" + "=" * 118)
        print("LIVE WALK-FORWARD REHEARSAL  (decide at T, execute at T+1 on verified bars; "
              f"benchmark {bench['title']} {bench['return_pct']:+.2f}%)")
        print("=" * 118)
        hdr = (f"{'#':>2} {'username':28s} {'return%':>9} {'net P&L $':>12} "
               f"{'maxDD%':>8} {'fills':>6} {'cost%':>7} {'slip bps':>9} "
               f"{'partial%':>9} {'status':>14}")
        print(hdr)
        print("-" * len(hdr))
        for row in board:
            print(f"{row['rank']:2d} {row['username']:28s} {row['total_return_pct']:+9.2f} "
                  f"{row['net_pnl_usd']:+12,.0f} {row['max_drawdown_pct']:8.2f} "
                  f"{row['fills']:6d} {row['execution_cost_pct']:7.3f} "
                  f"{str(row['slippage_bps_mean']):>9} "
                  f"{str(row['participation_pct_median']):>9} {row['data_status']:>14}")
        print(f"\n  verification: {result['verification']['verdict']} "
              f"({result['verification']['checks']} checks, "
              f"{result['verification']['failure_count']} failures)")
        print(f"  storage: {result['manifest']['storage']['bytes_per_row_average']} "
              f"bytes/row over {result['manifest']['storage']['total_rows']} rows "
              f"({result['manifest']['storage']['total_bytes_on_disk'] / 1024:.1f} KiB)")
        print(f"  official-price coverage of filled notional: "
              f"{result['coverage']['official_notional_share_pct']}% — "
              f"{result['coverage']['verdict']}")
        print(f"  memory: {result['run_dir']}")
    return 0


def cmd_rollover(args: argparse.Namespace) -> int:
    """Step the forward book: settle every session that has a published print."""
    result = rollover.step(root=args.memory_root,
                           plan_date=args.plan_date or None,
                           through=args.through or None,
                           horizon=args.horizon,
                           verbose=args.verbose,
                           write=not args.no_write)
    if args.json:
        print(json.dumps({"run_id": result["run_id"], "status": result["manifest"]["status"],
                          "ladder": result["ladder"],
                          "pending": result["pending"]}, indent=1, default=str))
        return 0
    manifest = result["manifest"]
    print("FORWARD ROLLOVER LADDER")
    print(f"  run {result['run_id']} · plan date {manifest['plan_date']} · "
          f"replayed {len(result['ladder'])} session(s)")
    print(f"  {manifest['status']}")
    roll = manifest.get("rollover", {})
    print(f"  executable prints: {roll.get('price_print_source')} "
          f"({roll.get('price_print_class')}, redistribution "
          f"{roll.get('price_print_redistribution_status')})")
    header = (f"  {'session':11s}{'verification':29s}{'targeted':>9}{'filled':>7}"
              f"{'waiting':>8}{'notional $':>14}  evidence")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in result["ladder"]:
        evidence = ("official series " + ",".join(row["official_series_missing"])
                    if row["official_series_missing"] else "official series complete")
        print(f"  {row['session']:11s}{row['verification']:29s}"
              f"{row['intents_targeted']:>9}{row['filled']:>7}{row['waiting_for_prints']:>8}"
              f"{row['notional_usd']:>14,.2f}  {evidence}")
        for fill in row["fills"]:
            print(f"      {fill['participant']:26s}{fill['side']:5s}{fill['symbol']:6s}"
                  f"{fill['quantity']:>6} @ {fill['price']:>10,.4f}  "
                  f"{fill['reference_source_class']}")
    if result["pending"]:
        print(f"\n  {len(result['pending'])} intents outstanding "
              f"({', '.join(sorted({i['intended_session'] for i in result['pending']}))})")
        for intent in result["pending"][:10]:
            print(f"    {intent['participant']:26s}{intent['side']:5s}"
                  f"{intent['symbol']:6s}{intent['quantity']:>7}  "
                  f"for {intent['intended_session']} · {intent['status']}")
    print(f"\n  verification: {result['verification']['verdict']} "
          f"({result['verification']['checks']} checks, "
          f"{result['verification']['failure_count']} failures)")
    if result.get("dry_run"):
        print(f"\n  dry run: nothing was written (scratch artifacts in "
              f"{result['run_dir'] or 'a temporary directory'})")
    else:
        print(f"  ladder written to memory/live/{rollover.LADDER_FILE}; memory: "
              f"{result['run_dir_rel']}")
        print(f"  append-only run log: memory/live/{rollover.HISTORY_FILE}")
    return 0


def cmd_live_blotter(args: argparse.Namespace) -> int:
    """Print every settled intent with the bar it executed against."""
    base = os.path.join(args.memory_root, live_season.LIVE_MEMORY_SUBDIR)
    run_id = args.run
    if not run_id:
        runs = sorted(d for d in os.listdir(base)
                      if args.kind in d) if os.path.isdir(base) else []
        if not runs:
            print(f"no live run matching kind {args.kind!r} under {base}")
            return 1
        run_id = runs[-1]
    run_dir = os.path.join(base, run_id)
    intents = live.read_live_run(run_dir).get("intents", [])
    fills = {f.get("intent_id"): f for f in live.read_live_run(run_dir).get("fills", [])}
    rows = [i for i in intents
            if (not args.participant or i["participant"] == args.participant)
            and (not args.only_filled or i["status"] in ("FILLED", "PARTIAL"))]
    print(f"run {run_id} · {len(rows)} of {len(intents)} intents"
          + (f" · participant {args.participant}" if args.participant else ""))
    print(f"{'created':11s}{'session':11s}{'participant':26s}{'side':5s}{'symbol':7s}"
          f"{'qty':>7s}{'px':>10s}{'cost $':>11s}{'slip bps':>9s}{'part%':>8s}  "
          f"reference bar")
    shown = sorted(rows, key=lambda r: (r["intended_session"], r["participant"]))
    for intent in shown[:args.limit]:
        fill = fills.get(intent["intent_id"], {})
        price = fill.get("avg_price")
        print(f"{intent['created_on']:11s}{intent['intended_session']:11s}"
              f"{intent['participant']:26s}{intent['side']:5s}{intent['symbol']:7s}"
              f"{intent['quantity']:7d}"
              f"{(f'{price:,.2f}' if price is not None else 'n/a'):>10s}"
              f"{(f'{fill.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(99)+chr(111)+chr(115)+chr(116), 0):,.2f}'):>11s}"
              f"{str(fill.get('slippage_bps', '')):>9s}"
              f"{str(fill.get('participation_pct_of_session_volume', '')):>8s}"
              f"  {fill.get('date', 'not settled')}"
              f" {fill.get('reference_source_class', '')}")
    if args.export:
        import csv as _csv
        fields = ["intent_id", "participant", "created_on", "intended_session",
                  "symbol", "side", "quantity", "status", "settled_on", "avg_price",
                  "filled_qty", "notional", "total_cost", "slippage_bps",
                  "participation_pct_of_session_volume", "reference_close",
                  "reference_file", "reference_sha256", "reference_source_class",
                  "rule", "rationale"]
        with open(args.export, "w", encoding="utf-8", newline="") as handle:
            writer = _csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                     lineterminator="\n")
            writer.writeheader()
            for intent in shown:
                row = dict(intent)
                row.update({k: v for k, v in fills.get(intent["intent_id"], {}).items()
                            if k in fields})
                writer.writerow(row)
        print(f"wrote {len(shown)} rows to {args.export}")
    return 0


def cmd_live_report(args: argparse.Namespace) -> int:
    """The live post-mortem for one participant."""
    base = os.path.join(args.memory_root, live_season.LIVE_MEMORY_SUBDIR)
    reports_path = os.path.join(base, args.run, "reports.json")
    if not os.path.exists(reports_path):
        print(f"no reports.json at {reports_path}; run 'sim.cli live --mode rehearsal' first")
        return 1
    with open(reports_path, "r", encoding="utf-8") as handle:
        reports = json.load(handle)
    key = args.username if args.username.startswith("@") else "@" + args.username
    report = reports.get(key)
    if report is None:
        print(f"unknown participant {key!r}; known: {', '.join(sorted(reports))}")
        return 1
    summary = report["summary"]
    print("=" * 100)
    print(f"{report['spec']['display_name']}  ({key})   [{report['data_status']}]")
    print("=" * 100)
    print(f"archetype: {report['spec']['archetype']}")
    print(f"thesis: {report['spec']['thesis']}")
    print(f"return {summary['total_return_pct']:+.2f}%  net "
          f"${summary['net_pnl_usd']:+,.2f}  maxDD {summary['max_drawdown_pct']:.2f}%  "
          f"Sharpe {summary['sharpe']}  verdict: {summary['verdict']}")
    print(f"intents {summary['intents_placed']}  filled {summary['intents_filled']}  "
          f"rejected {summary['intents_rejected']}  superseded "
          f"{summary['intents_cancelled']}  cost {summary['execution_cost_pct']:.3f}% "
          f"of ${summary['traded_notional_usd']:,.0f}")
    print("\nWhat caused the result:")
    for line in report["narrative"]:
        print(textwrap.fill(line, 96, initial_indent="  • ", subsequent_indent="    "))
    print("\nCarry:", json.dumps(report["carry"], indent=1))
    if report["trips"]:
        print("\nRound trips (first 10):")
        for trip in report["trips"][:10]:
            print(f"  {trip['symbol']:7s}{trip['direction']:6s}{trip['entry_date']:12s}"
                  f"{trip['entry_price']:>10.2f}{str(trip.get('exit_date')):12s}"
                  f"{(trip.get('exit_price') or 0):>10.2f}{trip['quantity']:>8d}"
                  f"{trip['net_pnl_usd']:>12,.2f}")
    return 0


def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def cmd_trade_sim(args: argparse.Namespace) -> int:
    import math
    from . import microstructure

    symbol = args.symbol.upper()
    side = args.side.lower()
    qty = int(args.qty)
    order_type = args.type.lower()
    custom_price = float(args.price) if args.price is not None else None
    participant = args.participant or "@InteractiveTrader"
    cash = float(args.cash)
    date = args.date or "2026-09-16"

    ref_file = ""
    ref_sha256 = ""
    price = custom_price
    adv = 5_000_000.0
    sigma_annual = 0.25

    # Check real data collection if available
    try:
        if os.path.isdir(os.path.join(realdata.REAL_ROOT, "prices", "yahoo")):
            series = realdata.load_series(symbol)
            by_date = series.by_date()
            if date in by_date:
                bar = by_date[date]
                if price is None:
                    price = bar.close if args.at_close else bar.open
                adv = float(max(100_000, bar.volume))
            meta = realdata.file_digest(realdata.symbol_path(symbol, realdata.REAL_ROOT, "yahoo"))
            ref_file = meta.get("path", "")
            ref_sha256 = meta.get("sha256", "")
    except Exception:
        pass

    uni = universe.universe_by_symbol()
    inst = uni.get(symbol)
    if inst:
        if price is None:
            price = inst.price_start
        adv = float(inst.adv_shares)
        sigma_annual = float(max(0.01, inst.beta * 0.15 + inst.sigma_idio_annual))
    elif price is None:
        price = 100.0

    min_tick = config.minimum_tick(price)
    round_lot_size = config.round_lot(price)

    cost_cfg = config.CostConfig()
    liq_cfg = config.LiquidityConfig()
    imp_cfg = config.ImpactConfig()

    cm = microstructure.CostModel(cost_cfg)
    lm = microstructure.LiquidityModel(liq_cfg)
    im = microstructure.ImpactModel(imp_cfg)

    sigma_daily = sigma_annual / math.sqrt(252)
    if inst:
        quoted_spread = lm.quoted_spread(inst, price, sigma_daily)
    else:
        quoted_spread = min_tick if price > 1.0 else 0.0001
    half_spread = quoted_spread / 2.0

    part_rate = qty / max(1.0, adv)
    impact_ret = im.impact_return(qty, adv, sigma_daily)
    perm_ret, temp_ret = im.split(impact_ret)
    perm_usd = price * perm_ret
    temp_usd = price * temp_ret

    direction = 1.0 if side == "buy" else -1.0
    effective_slippage_usd = half_spread + temp_usd + perm_usd
    effective_price = round(price + direction * effective_slippage_usd, 4)
    if min_tick > 0.0001:
        effective_price = round(round(effective_price / min_tick) * min_tick, 4)

    slippage_bps = round(abs(effective_price - price) / price * 10000.0, 2)
    notional = round(qty * effective_price, 2)

    taker_fee = round(cm.taker_fee(qty), 4)
    reg_fee = round(cm.regulatory(side, qty, effective_price, date), 4)
    total_cost = round(taker_fee + reg_fee + (qty * effective_slippage_usd), 2)

    initial_margin_req = round(notional * 0.50, 2)
    maint_margin_req = round(notional * 0.25, 2)
    margin_ok = cash >= (notional if side == "buy" else initial_margin_req)
    adv_pct = part_rate * 100.0
    adv_status = "PASS (<1% ADV)" if adv_pct < 1.0 else ("WARN (1-5% ADV)" if adv_pct <= 5.0 else "EXCEEDS CAP (>5% ADV)")

    print("================================================================================")
    print(" STOCKPAPERSIM :: US EQUITIES REAL-TRADE SIMULATOR & ORDER AUDIT")
    print("================================================================================")
    print(f" Participant:         {participant}")
    print(f" Date / Session:      {date} ({'At Close 16:00 ET' if args.at_close else 'At Open 09:30 ET'})")
    print(f" Order Specification: {side.upper()} {qty:,} {symbol} ({order_type.upper()})")
    print(f" Decision Mid Price:  ${price:,.4f}")
    print("--------------------------------------------------------------------------------")
    print(" 1. REGULATORY & VENUE CONSTRAINTS (17 CFR Part 242)")
    print(f"    Minimum Tick:      ${min_tick:.4f} (Rule 612 grid)")
    print(f"    Round Lot Size:    {round_lot_size} shares (Rule 600(b)(93))")
    print(f"    ADV Participation: {adv_pct:.4f}% of {adv:,.0f} daily volume [{adv_status}]")
    print(f"    Reg T Initial 50%: ${initial_margin_req:,.2f} (Available cash: ${cash:,.2f}) [{'OK' if margin_ok else 'MARGIN VIOLATION'}]")
    print(f"    Maintenance 25%:   ${maint_margin_req:,.2f}")
    print("--------------------------------------------------------------------------------")
    print(" 2. MICROSTRUCTURE & SLIPPAGE BREAKDOWN (Almgren-Chriss / Perold IS)")
    print(f"    Quoted Spread:     ${quoted_spread:.4f} (Half-Spread: +${half_spread:.4f})")
    print(f"    Temporary Impact:  +${temp_usd:.4f}/sh")
    print(f"    Permanent Impact:  +${perm_usd:.4f}/sh")
    print(f"    Total Slippage:    {slippage_bps:.2f} bps (${effective_slippage_usd * qty:,.2f} total drag)")
    print(f"    Effective Price:   ${effective_price:,.4f}")
    print("--------------------------------------------------------------------------------")
    print(" 3. STATUTORY & EXCHANGE COST STACK")
    print(f"    Exchange Taker:    ${taker_fee:,.4f} (Rule 610(c) $0.003/sh cap)")
    print(f"    Regulatory Fee:    ${reg_fee:,.4f} (SEC §31 $20.60/M + FINRA TAF $0.000195/sh)")
    print(f"    Total Trade Cost:  ${total_cost:,.2f}")
    print(f"    Net Cash Impact:   ${(notional + taker_fee + reg_fee) * direction:,.2f}")
    if ref_file:
        print("--------------------------------------------------------------------------------")
        print(" 4. DATA CUSTODY & AUDIT HASH")
        print(f"    Reference File:    {ref_file}")
        print(f"    SHA-256 Digest:    {ref_sha256[:16]}...{ref_sha256[-8:]}")
    print("================================================================================")
    return 0


def cmd_official(args: argparse.Namespace) -> int:
    """Run the Official Auction Book and print what it settled."""
    import sim.official_season as _os  # noqa: F401  (kept explicit for readers)
    start = args.start or official_season.treasury.SEASON_START
    end = args.end or official_season.treasury.SEASON_END
    result = official_season.run_official(
        starting_cash=args.starting_cash or official_season.ob.STARTING_CASH,
        start=start, end=end, verbose=args.verbose)
    summary = result["summary"]
    forward = result["forward"]
    print("=" * 122)
    print("OFFICIAL AUCTION BOOK  -  every execution price is the U.S. Treasury's "
          "own published number")
    print("=" * 122)
    print(f"window {summary['first_session']} -> {summary['last_session']} "
          f"({summary['sessions']} official sessions) · "
          f"{len(summary['participants'])} participants · "
          f"${official_season.ob.STARTING_CASH:,.0f} each")
    header = (f"{'#':>2} {'username':30s} {'return%':>10} {'maxDD%':>9} "
              f"{'trades':>7} {'coupons $':>11} {'fin $':>10} {'status':>7}")
    print(header)
    print("-" * len(header))
    for row in summary["participants"]:
        print(f"{row['rank']:2d} {row['participant']:30s} {row['return_pct']:+10.2f} "
              f"{row['max_drawdown_pct']:9.2f} {row['trades']:7d} "
              f"{row['coupon_income']:11,.2f} "
              f"{row['financing'] + row['debit_interest']:10,.2f} {row['status']:>7}")
    verification = result["verification"]
    print(f"\n  verification: {verification['verdict']} "
          f"({verification['checks']} checks, {verification['failure_count']} failures, "
          f"max equity residual ${verification['max_equity_residual_usd']})")
    coverage = result["coverage"]
    print(f"  unified ledger: {coverage['trades']} closed trades across "
          f"{len(coverage['by_book'])} books · official-price notional share "
          f"{coverage['official_executed_notional_pct']}%")
    print(f"  forward book: {forward['pending_intents']} pending intents as of "
          f"{forward['as_of']} - settled only against the official record")
    print(f"  memory: {result['run_dir']}")
    return 0


def cmd_official_blotter(args: argparse.Namespace) -> int:
    """Print settled official trades: entry, exit, dates, prices, evidence."""
    base = official_season.OFFICIAL_MEMORY
    run_id = args.run or official_season.REHEARSAL_RUN_ID
    run_dir = os.path.join(base, run_id)
    if not os.path.isdir(run_dir):
        print(f"no official run at {run_dir}; run 'python3 -m sim.cli official' first")
        return 1
    trips = official_season.tradelog.from_official_run(run_dir)
    rows = [t for t in trips
            if not args.participant or t["participant"] == args.participant]
    if args.export:
        fieldnames = ["trade_id", "participant", "instrument", "instrument_name",
                      "side", "quantity", "entry_date", "entry_price", "entry_kind",
                      "exit_date", "exit_price", "exit_kind", "holding_days",
                      "pnl_usd", "coupon_usd", "financing_usd", "price_class"]
        os.makedirs(os.path.dirname(os.path.abspath(args.export)), exist_ok=True)
        with open(args.export, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames,
                                    extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        print(f"wrote {len(rows)} trades to {args.export}")
    print(f"run {run_id} · {len(rows)} closed official trades"
          + (f" · participant {args.participant}" if args.participant else ""))
    print(f"{'participant':24s}{'security':18s}{'cusip':11s}{'entry':11s}"
          f"{'px':>11s}{'exit':11s}{'px':>11s}{'pnl $':>12s}{'class':>18s}")
    for row in sorted(rows, key=lambda r: (r["exit_date"] or "", r["participant"]))[:args.limit]:
        print(f"{(row['participant'] or '')[:23]:24s}"
              f"{(row.get('instrument_name') or '')[:17]:18s}"
              f"{row['instrument']:11s}{row['entry_date']:11s}"
              f"{row['entry_price']:11.6f}{row['exit_date']:11s}"
              f"{row['exit_price']:11.6f}{row['pnl_usd']:12,.2f}"
              f"{row['price_class']:>18s}")
    return 0


def cmd_official_report(args: argparse.Namespace) -> int:
    base = official_season.OFFICIAL_MEMORY
    run_id = args.run or official_season.REHEARSAL_RUN_ID
    path = os.path.join(base, run_id, "reports",
                        f"{args.username.lstrip('@')}.json")
    if not os.path.exists(path):
        print(f"no report at {path}")
        return 1
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    metrics = report["metrics"]
    print(f"{report['participant']}  ({report['family']})")
    print("=" * 96)
    print(textwrap.fill(report["thesis"], 96))
    print()
    print(f"  return {metrics['return_pct']:+.2f}%   max drawdown "
          f"{metrics['max_drawdown_pct']:.2f}%   sharpe {metrics['sharpe']:.2f}   "
          f"status {metrics['status']}")
    print(f"  trades {metrics['trades']}   intents {metrics['intents']}   filled "
          f"{metrics['filled_intents']}   waiting {metrics['waiting_intents']}   "
          f"rejected {metrics['rejected_intents']}")
    print(f"  coupons ${metrics['coupon_income']:,.2f}   financing "
          f"${metrics['financing'] + metrics['debit_interest']:,.2f}   "
          f"cash credit ${metrics['cash_credit']:,.2f}")
    print("\n  what produced this result:")
    for line in report["drivers"]:
        print(textwrap.fill(line, 94, initial_indent="   - ", subsequent_indent="     "))
    if report["closed_trades"]:
        print("\n  trades:")
        for trade in report["closed_trades"][:20]:
            print(f"   {trade['entry_date']} -> {trade['exit_date']}  "
                  f"{trade['security_term']:16s} {trade['cusip']}  "
                  f"{trade['entry_kind']:17s} -> {trade['exit_kind']:20s} "
                  f"pnl ${trade['pnl']:>11,.2f}")
    return 0


def cmd_trades(args: argparse.Namespace) -> int:
    """The unified ledger: every closed trade in every book, with its provenance."""
    trades = tradelog.collect_trades(include_seasons=True)
    if args.participant:
        trades = [t for t in trades if t["participant"] == args.participant]
    if args.official_only:
        trades = [t for t in trades if t.get("official_execution_price")]
    coverage = tradelog.coverage(trades)
    print(f"{len(trades)} closed trades · ${coverage['notional_usd']:,.2f} notional · "
          f"${coverage['pnl_usd']:,.2f} net P&L")
    print(f"{'price class':40s}{'trades':>8s}{'notional $':>16s}{'share %':>10s}"
          f"{'pnl $':>14s}")
    for klass, row in sorted(coverage["by_price_class"].items(),
                             key=lambda kv: -kv[1]["notional_usd"]):
        print(f"{klass:40s}{row['trades']:8d}{row['notional_usd']:16,.2f}"
              f"{row['notional_share_pct']:10.4f}{row['pnl_usd']:14,.2f}")
    print(f"  both legs official: {coverage['official_executed_notional_pct']}% of "
          f"notional · official entry: {coverage['official_entry_notional_pct']}%")
    print("\nby book:")
    for book, row in sorted(coverage["by_book"].items(),
                            key=lambda kv: -kv[1]["notional_usd"]):
        print(f"  {book:34s}{row['trades']:6d} trades  {row['notional_share_pct']:8.3f}%"
              f" of notional  ${row['pnl_usd']:,.2f} P&L")
    if args.out:
        manifest = tradelog.write_ledger(trades, args.out)
        print(f"\nwrote the ledger to {args.out} "
              f"({manifest['storage']['trades']['rows']} rows, "
              f"{manifest['storage']['trades']['bytes_on_disk'] / 1024:.1f} KiB on disk)")
    print(f"\n{'exit':11s}{'participant':26s}{'instrument':10s}{'entry':12s}"
          f"{'exit':12s}{'pnl $':>12s}  {'class':18s}")
    for trade in sorted(trades, key=lambda t: (t.get("exit_date") or "",
                                               t["participant"]))[:args.limit]:
        print(f"{str(trade['exit_date']):11s}{trade['participant'][:25]:26s}"
              f"{trade['instrument'][:9]:10s}{str(trade['entry_date']):12s}"
              f"{str(trade['exit_date']):12s}{trade['pnl_usd'] or 0:12,.2f}  "
              f"{trade['price_class']:18s}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sim.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--memory-root", default=memory.DEFAULT_ROOT,
                   help="root of the run memory store")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the competition and write memory")
    r.add_argument("--seeds", default="", help="comma-separated seeds (index 0 = primary)")
    r.add_argument("--quick", action="store_true", help="primary seed only")
    r.add_argument("--starting-cash", type=float, default=0.0)
    r.add_argument("--verbose", action="store_true")
    r.add_argument("--ir-limit", type=int, default=40)
    r.set_defaults(func=cmd_run)

    for name, fn, helpt in (
            ("leaderboard", cmd_leaderboard, "print the leaderboard from memory"),
            ("compare", cmd_compare, "multi-scenario robustness panel"),
            ("verify", cmd_verify, "checksum memory and audit the data"),
            ("irregularities", cmd_irregularities, "list every IR-xx flag"),
            ("sources", cmd_sources, "print the verified-source register")):
        sp = sub.add_parser(name, help=helpt)
        sp.add_argument("--run", default="")
        sp.set_defaults(func=fn)

    rp = sub.add_parser("report", help="full post-mortem for one participant")
    rp.add_argument("username")
    rp.add_argument("--run", default="")
    rp.add_argument("--full", action="store_true",
                    help="include per-symbol contribution, monthly table and IS detail")
    rp.set_defaults(func=cmd_report)

    q = sub.add_parser("query", help="query one memory event stream")
    q.add_argument("stream", choices=list(memory.EVENT_LOGS))
    q.add_argument("--run", default="")
    q.add_argument("--participant", default="")
    q.add_argument("--symbol", default="")
    q.add_argument("--start", default="")
    q.add_argument("--end", default="")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--columns", default="")
    q.add_argument("--format", choices=("table", "json"), default="table")
    q.set_defaults(func=cmd_query)

    ex = sub.add_parser("export", help="export one event stream to CSV")
    ex.add_argument("stream", choices=list(memory.EVENT_LOGS))
    ex.add_argument("dest")
    ex.add_argument("--run", default="")
    ex.set_defaults(func=cmd_export)

    pa = sub.add_parser("price-audit", help="strictly audit official price provenance and eligibility")
    pa.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data", "real"))
    pa.add_argument("--backend", choices=tuple(realdata.PRICE_BACKENDS), default="nasdaq")
    pa.add_argument("--start", default=realdata.SEASON2_WARMUP_START)
    pa.add_argument("--end", default=realdata.SEASON2_END)
    pa.add_argument("--symbols", default=",".join(realdata.UNIVERSE))
    pa.set_defaults(func=cmd_price_audit)

    s2 = sub.add_parser("season2", help="run Season 2 with the fail-closed price gate")
    s2.add_argument("--labels", default="primary,stress-costs2x,stress-thinliquidity",
                    help="comma-separated assumption sets")
    s2.add_argument("--price-source", choices=tuple(realdata.PRICE_BACKENDS), default="nasdaq",
                    help="official Nasdaq by default; Yahoo requires an explicit research flag")
    s2.add_argument("--allow-secondary-research", action="store_true",
                    help="allow the existing Yahoo-backed, non-eligible research replay")
    s2.add_argument("--verbose", action="store_true")
    s2.set_defaults(func=cmd_season2)

    lg = sub.add_parser("ledger", help="inspect the verified trade ledger")
    lg.add_argument("--run", default="")
    lg.add_argument("--participant", default="")
    lg.add_argument("--limit", type=int, default=25)
    lg.add_argument("--only-closed", action="store_true")
    lg.add_argument("--export", default="", help="write the round trips to this CSV")
    lg.set_defaults(func=cmd_ledger)

    bs = sub.add_parser("build-site", help="regenerate the GitHub Pages site")
    bs.add_argument("--run", default="")
    bs.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"))
    bs.set_defaults(func=cmd_build_site)

    lv = sub.add_parser("live", help="plan the live forward book and run its walk-forward rehearsal")
    lv.add_argument("--mode", choices=("forward", "rehearsal", "all"), default="all")
    lv.add_argument("--as-of", default="", help="plan date (default: the last verified session)")
    lv.add_argument("--horizon", type=int, default=3,
                    help="how many future sessions the projection must cover")
    lv.add_argument("--settle-session", default="",
                    help="settle forward book on this session if verified data exists")
    lv.add_argument("--verbose", action="store_true")
    lv.set_defaults(func=cmd_live)

    lb = sub.add_parser("live-blotter", help="every live intent with the verified bar it executed against")
    lb.add_argument("--run", default="")
    lb.add_argument("--kind", default="live-", help="substring of the run id to open when --run is empty")
    lb.add_argument("--participant", default="")
    lb.add_argument("--only-filled", action="store_true")
    lb.add_argument("--limit", type=int, default=40)
    lb.add_argument("--export", default="")
    lb.set_defaults(func=cmd_live_blotter)

    lr = sub.add_parser("live-report", help="live post-mortem for one participant")
    lr.add_argument("username")
    lr.add_argument("--run", default=f"live-rehearsal-seed{live.LIVE_SEED}")
    lr.set_defaults(func=cmd_live_report)

    off = sub.add_parser("official", help="run the Official Auction Book (official prices only)")
    off.add_argument("--start", default="", help="first session (default: the season start)")
    off.add_argument("--end", default="", help="last session (default: the season end)")
    off.add_argument("--starting-cash", type=float, default=0.0)
    off.add_argument("--verbose", action="store_true")
    off.set_defaults(func=cmd_official)

    ob_ = sub.add_parser("official-blotter", help="every settled official trade with its evidence")
    ob_.add_argument("--run", default="")
    ob_.add_argument("--participant", default="")
    ob_.add_argument("--limit", type=int, default=30)
    ob_.add_argument("--export", default="", help="write every closed trade to this CSV")
    ob_.set_defaults(func=cmd_official_blotter)

    orp = sub.add_parser("official-report", help="post-mortem for one official participant")
    orp.add_argument("username")
    orp.add_argument("--run", default="")
    orp.set_defaults(func=cmd_official_report)

    tr = sub.add_parser("trades", help="the unified trade ledger across every book")
    tr.add_argument("--out", default="", help="directory to write the ledger into")
    tr.add_argument("--limit", type=int, default=25)
    tr.add_argument("--participant", default="")
    tr.add_argument("--official-only", action="store_true",
                    help="only trades whose executed price is an official number")
    tr.set_defaults(func=cmd_trades)

    ro = sub.add_parser("rollover", help="step the forward book over every session that has a published print")
    ro.add_argument("--plan-date", default="", help="first session of the replay (default: the existing book's)")
    ro.add_argument("--through", default="", help="last session to step (default: every collected session)")
    ro.add_argument("--horizon", type=int, default=3, help="how many future sessions to plan")
    ro.add_argument("--json", action="store_true", help="print the ladder as JSON")
    ro.add_argument("--no-write", action="store_true", help="compute the ladder without writing memory")
    ro.add_argument("--verbose", action="store_true")
    ro.set_defaults(func=cmd_rollover)

    ts = sub.add_parser("trade-sim", help="simulate placing a real trade with full microstructure & cost model")
    ts.add_argument("--symbol", default="SPY", help="ticker symbol (e.g. SPY, QQQ, AAPL, NVDA)")
    ts.add_argument("--side", choices=("buy", "sell"), default="buy", help="order side")
    ts.add_argument("--qty", type=int, default=100, help="order share quantity")
    ts.add_argument("--type", choices=("market", "limit", "stop"), default="market", help="order type")
    ts.add_argument("--price", type=float, default=None, help="custom limit or decision price")
    ts.add_argument("--date", default="2026-09-16", help="trading date (YYYY-MM-DD)")
    ts.add_argument("--participant", default="@InteractiveTrader", help="strategy username")
    ts.add_argument("--cash", type=float, default=100000.0, help="available paper cash")
    ts.add_argument("--at-close", action="store_true", help="execute at closing bell instead of open")
    ts.set_defaults(func=cmd_trade_sim)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
