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
    python3 -m sim.cli season2             # Season 2: real collected prices
    python3 -m sim.cli ledger --limit 20   # every round trip with verified prices
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

from . import analytics, config, engine, ledger as ledger_mod, marketdata, memory
from . import realdata, season2, universe

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

    index_path = os.path.join(args.out, "index.html")
    try:
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as handle:
                html = handle.read()
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.write(build_site_season2.inject_banner(html))
        written = build_site_season2.build(args.memory_root, args.out, args.run)
        print(f"  season 2: {len(written)} pages under docs/season2/ "
              f"(from run {build_site_season2.Season2Data(args.memory_root, args.run).run_id})")
    except SystemExit as exc:
        # No Season 2 run in this memory root (for example a scratch root built
        # only to check Season 1 determinism). Say so loudly rather than
        # publishing a Season 2 section that would silently be empty.
        print(f"  season 2: SKIPPED - {exc}")
    return rc


# ==========================================================================
# Argument parsing
# ==========================================================================

# ==========================================================================
# season2 + ledger
# ==========================================================================

def cmd_season2(args: argparse.Namespace) -> int:
    labels = tuple(s.strip() for s in args.labels.split(",") if s.strip())
    print(f"StockPaperSim :: {season2.SEASON2_NAME} :: {season2.SEASON2_SEASON}")
    print(f"window {realdata.SEASON2_START} -> {realdata.SEASON2_END} | "
          f"assumptions {', '.join(labels)}")
    print("building the real market from data/real/prices + data/real/fred ...",
          flush=True)
    records = season2.run_season2(root=args.memory_root, labels=labels,
                                  verbose=args.verbose)
    primary = records[0]
    _print_leaderboard(primary["leaderboard"], primary["market"])
    if len(records) > 1:
        print("\nSTRESS PANEL - every participant, same real prices, harder venue")
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
        print(f"  ledger digest {v['ledger_digest_sha256'][:16]}... | max |equity "
              f"residual| ${v['max_abs_equity_residual_usd']:,.4f} "
              f"(must be 0.00: fills re-derived independently of the engine)")
        idle = [p["username"] for p in v["per_participant"]
                if not p.get("sessions_with_orders")]
        if idle:
            print(f"  no trades at all: {', '.join(idle)}")
        mf = _read_json(os.path.join(args.memory_root, "runs", rec["run_id"],
                                     "masterfeed.json"))
        missing = [k for k, a in mf["availability"].items() if a["state"] != "AVAILABLE"]
        if missing:
            print(f"  signals with NO collected data: {', '.join(sorted(missing))}")
    print(f"\nledgers written under {args.memory_root}/runs/<run_id>/ledger_*.jsonl")
    return 0


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
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
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


def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


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

    s2 = sub.add_parser("season2", help="run the real-price MasterFeed season")
    s2.add_argument("--labels", default="primary,stress-costs2x,stress-thinliquidity",
                    help="comma-separated assumption sets")
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
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
