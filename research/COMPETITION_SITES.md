# Reverse-engineering notes: the three paper-trading competitions

The brief was to take the three named contests apart, work out what makes each of
them tick, and build something with the same basic functionality - track
strategies/users, trades and P&L - without copying branding or marketing.

Each contest was fetched on **2026-09-17**. Where a rule could be verified from
the page, it is quoted; where a rule is inferred from what the page exposes (a
leaderboard column implies a stored field), that is marked *inferred*.

| Contest | URL | Status |
|---|---|---|
| TradingView **The Leap** (December 2025 edition) | `https://www.tradingview.com/the-leap/december-2025/rules/` | `FETCHED` |
| **Trade Ideas** PM Challenge | `https://www.trade-ideas.com/stock-trading-competition/` | `FETCHED` |
| **CandleCharts** Showdown | `https://specials.candlecharts.com/contest/` | `FETCHED` |

---

## 1. TradingView The Leap - the rules engine worth stealing

Fetched from the official rules page. What it specifies:

| Rule | The Leap | What StockPaperSim does |
|---|---|---|
| Starting capital | **$100,000** of virtual money per participant | **$100,000** exactly (`config.STARTING_CASH`). Same number, so the leaderboards are comparable |
| Horizon | a fixed calendar window (one month for The Leap) | **one year** (2025-09-17 → 2026-09-16, 251 sessions), per the brief |
| Ranking metric | **realised P&L** | **total return %** (realised + unrealised, both published), with per-trade and per-day P&L underneath |
| Open positions at period end | **auto-closed**, and the closing mark counts toward P&L | **forced liquidation** at the close, costed through the venue model as a real order - `forced_liquidation` in every participant JSON |
| Leaderboard refresh | **at most hourly** | published as a static build; the exact build instant is stamped on every page (`generated_at`, `as_of`) |
| Minimum participation | at least **3 active days** or the entry is not ranked | weaker: `sessions_with_orders` per participant. The Leap's rule exists to stop empty accounts from being ranked; with 20 processes that cannot happen, so the count is published instead of enforced |
| Order-rate limit | banned above **60 orders/minute** | **not applicable, and the published methodology page says so.** This engine has one decision point per session, so it cannot generate 60 orders in a minute. Claiming to model that rule would have been the easy fabrication |
| Instruments | a fixed **whitelist** with a maximum number of open positions | a fixed **17-name whitelist** (3 index ETFs, 14 single names), built by `sim/universe.py`; each strategy declares its own subset |
| Leverage | **1:1** on stocks (no margin) | **widened to a 2x gross cap** as a declared SIM CHOICE, with Reg T initial margin (50%), a maintenance margin, margin calls counted (`carry.margin_call_count`) and shorting gated on a Reg SHO locate. This is the one place the project deliberately goes beyond the contests it was reverse-engineered from, because the brief asked for return-seeking strategies, and return-seeking without leverage cannot express itself |
| Commission | **$1 per trade** | a dated, sourced schedule: zero retail commission, **SEC §31 on sells** and **FINRA TAF on sells**, plus spread and impact |
| After the contest | accounts deleted **30 days** later | the opposite, by design: everything is written to `memory/` with checksums and **kept** |

**The single most useful thing taken from The Leap** is that the rules are
published as a dated document with explicit numbers, and that the leaderboard
states its own refresh latency. Both are reproduced here: the rules live in
`config.py` with a fingerprint (`config.config_fingerprint()`), and the site
states its data latency instead of implying real time - which is the Trade Ideas
lesson below.

---

## 2. Trade Ideas PM Challenge - the leaderboard schema

The contest page exposes its standings as a table. The columns, in order:

```
Rank | User | Total Profit | Open Profit | Close Profit | Total Trades |
Open Trades | Closed Trades | Account Value | Avg Profit / Trade
```

That is the schema copied for this project's leaderboard, extended rather than
replaced:

Field names below are the ones actually in `leaderboard.json` (flat, one row per
participant) and in `memory/runs/<run-id>/reports/<strategy_id>.json` (nested),
so they can be grepped.

| PM Challenge column | StockPaperSim field | Where | Notes |
|---|---|---|---|
| Rank | `rank` | leaderboard row | the ranking metric is stated on the page and in the JSON as `rank_metric` |
| User | `username` | leaderboard row | 20 personas, e.g. `@GapAndGo_YOLO`, `@BetaChaser_3xProxy`, plus `strategy_id` and `archetype` |
| Total Profit | `net_pnl_usd` **and** `total_return_pct` | leaderboard row | dollars and percent both published; percent is what ranks |
| Open Profit | `pnl_decomposition.open_position_pnl_usd` | report | zero at season end because positions are force-closed - and the report says so rather than hiding it |
| Close Profit | `pnl_decomposition.realized_trading_pnl_usd` | report | with `dividends_usd`, `borrow_fees_usd` and `unexplained_residual_usd` beside it, so the column adds up |
| Total Trades | `trades.closed_trades` + `trades.open_trades` | report | round trips, not fills; fills are counted separately in `costs.fills` |
| Open Trades | `trades.open_trades` | report | |
| Closed Trades | `closed_trades` | leaderboard row | |
| Account Value | `final_equity`, and the whole daily series | leaderboard row, `events/equity.jsonl.gz` | |
| Avg Profit / Trade | `trades.expectancy_usd_per_trade` | report | alongside `avg_win_usd`, `avg_loss_usd`, `best_trade`, `worst_trade`, `win_rate_pct`, `profit_factor`, streaks |
| *(not exposed by any of the three)* | `sharpe`, `sortino`, `max_drawdown_pct`, `beta`, `alpha_annual_pct`, `execution_cost_pct`, `turnover_x`, `margin_calls`, `risk.cvar_95_pct`, `costs.impact_cost_usd`, `costs.spread_cost_usd`, `costs.regulatory_fee_usd`, `implementation_shortfall.shortfall_bps_of_paper`, `contribution_by_symbol`, `narrative.prose` | leaderboard row / report | added because a return number cannot explain itself, and explaining it was the point of the brief |

The page also carries a **"data delayed by 15 minutes"** notice. That is the most
transferable detail in the whole exercise: a contest that cannot honestly claim
real-time data says so on the page. StockPaperSim does the same, more bluntly -
the site states that the venue is a calibrated simulation, that 15 of 17 price
series are synthetic, and which two are real (SPY via Yahoo, the index factor via
FRED). See **IR-06** and the banner on `docs/index.html`.

---

## 3. CandleCharts Showdown - the fixed-horizon paper contest

The page describes a contest run through **TradingView Community Competitions**:

* a **$50,000 paper-trading account** per entrant;
* entry through the TradingView competition hub, i.e. the broker/venue work is
  delegated to a platform rather than built by the organiser;
* **prizes for the top three** finishers;
* a fixed horizon with published start and end dates.

What was taken: the shape of a **fixed-horizon, fixed-capital, paper** contest
whose entire output is a ranked table and a per-entrant trade history. What was
deliberately not taken: prize money (there is none here) and delegation of the
venue to a third party (this project implements the venue itself, because the
brief asked for liquidity, market making and pricing to be modelled rather than
inherited).

---

## 4. What all three do **not** do, and what this project adds

Reverse-engineering the three contests shows a consistent gap. They track
**who won**; none of them explain **why**. Concretely:

1. **No cost decomposition.** A leaderboard shows P&L. It does not show how much
   of that P&L was eaten by spread, impact and fees. This project runs a
   Perold-style **implementation shortfall** decomposition on every trade and
   publishes `fees_paid_usd`, `impact_cost_usd` and `slippage_vs_decision_usd`
   per participant, plus `pnl_decomposition` (market impact, alpha, costs,
   timing).
2. **No post-mortem.** The brief required each strategy to carry an explanation of
   why it worked or failed, and an analysis of what caused the return. Every
   participant page therefore has a "Post-mortem: why it worked, or why it did
   not" section generated from that participant's own numbers, with the declared
   failure modes listed next to whether each one actually fired.
3. **No attribution.** Nothing in the three contests tells you whether a return
   came from market beta, a factor tilt, or a name-picking edge. This project
   publishes per-participant **factor exposures** and a **contribution-by-symbol**
   table, and separates the index return (+14.415%) from each participant's.
4. **One path, one verdict.** A single contest run cannot distinguish skill from
   luck. Six scenario replays (one real calendar path plus five synthetic seeds)
   over the same real index path give a **mean / median / best / worst /
   standard deviation** panel per participant, published as `robustness.json`.
   This is the single biggest structural addition.
5. **No memory.** The Leap deletes accounts 30 days after the end. Here, every
   run is written to `memory/<run-id>/` as append-only JSON streams with a
   per-file checksum and a manifest, so a future season can be replayed,
   re-scored under a different cost model, or audited against the published site.
   `tests/test_memory.py` proves a corrupted stream is detected.

---

## 5. Feature-by-feature mapping (the "basic functionality" checklist)

The brief asked for a working site with basic functionality to track strategies,
users, trades and P&L. Where each of those lives:

| Requirement | Where it is implemented | Where it is published |
|---|---|---|
| Track strategies | `sim/strategies.py` - 20 participant classes, each with `academic_basis`, a declared universe, and `decide()` | `docs/strategies.html`, and the per-participant "The strategy as declared before the season" section |
| Track users | `sim/engine.py` - persona, username, spec, account state | `docs/participants.html`, `docs/participants/<user>.html`, `assets/data/participants.json` |
| Track trades | every order, fill, rejection and quote appended to gzipped event streams under `memory/runs/<run-id>/events/` - `orders`, `fills`, `rejections`, `quotes`, `positions`, `equity`, `carry`, `margin` | `docs/data.html` (stream inventory with row counts), the "Trade statistics" and "Execution cost breakdown" sections of each participant page |
| Track P&L | daily NAV per participant, realised/unrealised split, per-symbol contribution, Perold implementation shortfall, ledger closure to $0.10 | `docs/leaderboard.html`, `assets/data/leaderboard.json`, `assets/data/factors.json`, and `reports/<strategy_id>.json` in memory |
| Competition structure | fixed 251-session year, fixed $100k, forced liquidation, ranked table | `docs/index.html`, `docs/methodology.html` (section 1 is this reverse-engineering table, published) |
| Liquidity / market making / pricing | `sim/microstructure.py` - depth book, U-shaped intraday volume, Avellaneda-Stoikov quoting, dated Rule 612 tick grid, access-fee cap | `docs/methodology.html` sections 2-4, `docs/market.html` |
| Irregularity flags | `research/IRREGULARITIES.json` (29 entries) | `docs/irregularities.html` |
| Limitations and remaining work | `research/LIMITATIONS.json` (16 entries), `research/REMAINING_WORK.json` (16 entries) | `docs/limitations.html` - all 16, all 16 in priority order, plus "What success would require" |
| Verified sources with links | `sim/config.py::all_verified_sources()` (52 rows) plus the 6 live-data adapter entries | `docs/sources.html` - 58 rows, every URL clickable, every row carrying its honesty status badge |
