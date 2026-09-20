"""Build the significance page and its JSON artifact from committed memory.

This is the site half of the P1 "formal significance testing" item
(``sim/significance.py`` holds the estimators).  Like
``scripts/build_site_summary.py`` it is invoked by ``build_site.main`` so a
single ``python3 -m sim.cli build-site`` produces it, and it reads only
committed run memory - the equity panel of the primary run, the market data
file and ``memory/robustness_panel.json`` - so the page regenerates without
re-running the season.

Outputs:

* ``docs/assets/data/significance.json``  - the full bundle (page data);
* ``docs/significance.html``              - the reader page.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from sim import memory as memstore                     # noqa: E402
from sim import significance as sig                    # noqa: E402


def _load_robustness(memory_root: str) -> dict:
    path = os.path.join(memory_root, "robustness_panel.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("by_participant") or []
    return {r["username"]: r.get("returns_by_scenario_pct") or {}
            for r in rows if r.get("username")}


def _market_returns(memory_root: str, dates: list) -> list:
    path = os.path.join(memory_root, "runs")
    run_ids = sorted(os.listdir(path))
    market_data = None
    for run_id in run_ids:
        p = os.path.join(path, run_id, "market_data.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                market_data = json.load(fh)
            break
    if not market_data:
        return []
    md_dates = market_data.get("dates") or []
    spx = market_data.get("spx") or []
    index = {d: i for i, d in enumerate(md_dates)}
    levels = []
    for d in dates:
        i = index.get(d)
        levels.append((d, spx[i]) if (i is not None and i < len(spx)) else None)
    levels = [lv for lv in levels if lv is not None]
    return levels


def build(memory_root: str, out: str) -> list:
    store = memstore.MemoryStore(root=memory_root)
    runs = store.runs()
    if not runs:
        raise SystemExit(f"no runs in {memory_root}")
    run_ids = [r["run_id"] for r in runs]
    prim = [r for r in run_ids if "primary" in r and r.startswith("season1")]
    run_id = sorted(prim)[-1] if prim else sorted(run_ids)[-1]
    panel = store.equity_panel(run_id)
    dates = sorted(panel)
    users = sorted({u for row in panel.values() for u in row})
    curves = {u: [(d, panel[d][u]) for d in dates if panel.get(d, {}).get(u)]
              for u in users}
    market_rets = _market_returns(memory_root, dates)
    scenario_returns = _load_robustness(memory_root)
    bundle = sig.season_significance(
        curves,
        market_rets,
        scenario_returns_pct=scenario_returns or None,
    )
    bundle["run_id"] = run_id
    bundle["window"] = [dates[0], dates[-1]] if dates else None
    board_doc = store.load(run_id, "leaderboard.json") or {}
    leader = ((board_doc.get("leaderboard") or [{}])[0].get("username"))
    bundle["published_leader"] = leader

    data_dir = os.path.join(out, "assets", "data")
    os.makedirs(data_dir, exist_ok=True)
    data_path = os.path.join(data_dir, "significance.json")
    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=True)
        fh.write("\n")

    html = _render_page(bundle, out)
    page_path = os.path.join(out, "significance.html")
    with open(page_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return [os.path.relpath(data_path, out), os.path.relpath(page_path, out)]


def _esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _render_page(bundle: dict, out: str) -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_site", os.path.join(REPO_ROOT, "scripts", "build_site.py"))
    build_site = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(build_site)

    parts = bundle.get("participants") or {}
    board = []
    for username, row in sorted(parts.items(),
                                key=lambda kv: kv[0].lower()):
        sharpe = row.get("sharpe") or {}
        alpha = row.get("alpha") or {}
        dd = row.get("max_drawdown") or {}
        dsr = row.get("deflated_sharpe") or {}
        sig_star = ""
        if sharpe.get("ci95_low", 0) > 0:
            sig_star = ' <span class="badge badge-ok">CI&gt;0</span>'
        elif sharpe.get("p_sharpe_le_zero") is not None and \
                sharpe.get("p_sharpe_le_zero", 1.0) > 0.5:
            sig_star = ' <span class="badge badge-muted">CI straddles 0</span>'
        board.append([
            f"<code>{_esc(username)}</code>",
            f"{_esc(sharpe.get('sharpe_annualised'))}{sig_star}",
            f"[{_esc(sharpe.get('ci95_low'))}, {_esc(sharpe.get('ci95_high'))}]",
            f"{_esc(round(alpha.get('alpha_annual_pct') or 0, 1))}%",
            f"[{_esc(alpha.get('ci95_low_pct'))}, {_esc(alpha.get('ci95_high_pct'))}]",
            f"{_esc(dd.get('max_drawdown_pct'))}%",
            f"[{_esc(dd.get('ci95_low_pct'))}, {_esc(dd.get('ci95_high_pct'))}]",
            _esc(dsr.get("dsr")),
        ])
    rank = bundle.get("rank_test") or {}
    leader = bundle.get("published_leader")
    leader_wins = ((rank.get("rows") or {}).get(leader, {})
                   .get("scenario_wins", "-") if leader else "-")
    rank_rows = []
    for username, r in sorted((rank.get("rows") or {}).items(),
                              key=lambda kv: -kv[1].get("scenario_wins", 0)):
        p = r.get("p_value")
        badge_cls = "badge-ok" if (p is not None and p < 0.05) else "badge-muted"
        rank_rows.append([
            f"<code>{_esc(username)}</code>",
            str(r.get("scenario_wins")),
            f"{100.0 * (r.get('win_rate') or 0):.0f}%",
            f"{100.0 * (r.get('chance_rate') or 0):.0f}%",
            f'<span class="badge {badge_cls}">{_esc(p)}</span>',
        ])
    method = bundle.get("method") or {}
    n_users = len(parts)
    n_sig = sum(1 for row in parts.values()
                if (row.get("sharpe") or {}).get("ci95_low", 0) > 0)
    body = f"""
<h1>Are the results statistically real?</h1>
<p class="lede">A ranking is a point estimate. This page tests the published
season's own numbers for three failure modes of a one-year contest: a Sharpe
ratio that a lucky path produces, an alpha that vanishes under resampling, and
a winner who is only the winner of the one scenario that actually happened.
The estimators live in <code>sim/significance.py</code>; every number below is
re-derived from the committed equity panel of
<code>{_esc(bundle.get('run_id'))}</code> ({_esc(bundle.get('window') and bundle['window'][0])}
&rarr; {_esc(bundle.get('window') and bundle['window'][1])}).</p>

<div class="cards">
  <div class="stat"><strong>{n_sig}/{n_users}</strong>
    <span>participants whose 95% bootstrap Sharpe interval excludes zero</span></div>
  <div class="stat"><strong>{_esc(leader_wins)}</strong>
    <span>wins across {_esc(rank.get('scenarios'))} scenarios for the published leader
    (<code>{_esc(leader or 'n/a')}</code>) &mdash; chance is
    {100.0 / max(1, rank.get('participants') or 1):.0f}% per scenario</span></div>
</div>

<h2>What is computed, and what each test assumes</h2>
<div class="card">
<ul>
<li><strong>Bootstrap intervals</strong> &mdash; {_esc(method.get('bootstrap'))}.</li>
<li><strong>Deflated Sharpe Ratio</strong> &mdash; {_esc(method.get('deflated_sharpe'))}.
The trial count is the roster size: twenty strategies were run against the same
tape, so the bar a winner must clear is the expected best of twenty, not zero.</li>
<li><strong>Rank test</strong> &mdash; {_esc(method.get('rank_test'))}.</li>
</ul>
<p class="muted small">Seeded with {_esc(method.get('seed'))}; the JSON behind this
page is <code>assets/data/significance.json</code> and regenerates byte-identically
from the same memory.</p>
</div>

<h2>Per-participant tests</h2>
<div class="table-scroll"><table class="data">
<thead><tr><th>Participant</th><th>Sharpe (ann.)</th><th>Sharpe 95% CI</th>
<th>Alpha (ann.)</th><th>Alpha 95% CI</th><th>Max DD</th><th>Max DD 95% CI</th>
<th>Deflated Sharpe</th></tr></thead>
<tbody>{''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in board)}</tbody>
</table></div>
<p class="muted small">A negative-alpha participant whose interval excludes zero
on the downside is significantly <em>negative</em>, which is a real finding about
that strategy, not a footnote. The Deflated Sharpe column is the probability the
participant's Sharpe clears the expected best-of-roster bar; values near 1.0
survive selection, values near 0.5 or below do not.</p>

<h2>Does the winner repeat across scenarios?</h2>
<div class="table-scroll"><table class="data">
<thead><tr><th>Participant</th><th>Scenario wins</th><th>Win rate</th>
<th>Chance rate</th><th>P(wins this many by luck)</th></tr></thead>
<tbody>{''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rank_rows)}</tbody>
</table></div>
<p class="muted small">Read the p-values as one-sided: the probability that a
participant with no skill wins at least this many of the {_esc(rank.get('scenarios'))}
scenarios by chance. A winner confirmed in every scenario still holds one
calendar year's worth of evidence (L-17) &mdash; the test bounds the luck, it does
not repeal it.</p>
"""
    return build_site.page("Statistical significance", body, "significance.html")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory-root",
                    default=os.path.join(REPO_ROOT, "memory"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"))
    args = ap.parse_args(argv)
    written = build(args.memory_root, args.out)
    for rel in written:
        print("wrote", rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
