# StockPaperSim

> **2026-09-19 third pass — every brief item now has its own measured participant.**
> The Season 2 research roster grew from **14 to 19 personas** so that each item
> the brief named (CEO, weather, insider trades, TheLeap, NFL Injury, NBA
> Injury, FDA Decisions, NCAA/NFL/MLB Scoreboard, Sports Pred, Gold, PinePilot)
> maps to at least one strategy with its own username, rules and post-mortem:
> `@NFL_Slate_Attention`, `@NBA_Slate_Attention`, `@NCAA_Upset_Blitz`,
> `@NBAInjury_Forward` and `@SportsPred_Forward` are new. The SEC insider
> signal builder now reads the **quarterly bulk data sets** (merging them with
> the per-filing walk, de-duplicated by accession), which is what unblocks
> `@InsiderCopycat_Max`, `@InsiderCluster_Alpha` and `@CEO_CFO_Conviction` the
> moment the runner-collected files land. A **weekly dated injury-snapshot
> archive** (`.github/workflows/injury-archive.yml`) starts accumulating the
> observations the forward-only injury probes need. The two licensing blockers
> were re-verified against live pages today: Nasdaq's terms (updated May 11,
> 2026) still prohibit automated capture, and no redistribution grant could be
> located for Stooq — so **zero strict equity fills remains the designed
> state** ([feed review](research/EQUITY_FEED_REVIEW.md)).
>
> **2026-09-19 second pass — the desk now has a scheduler and a pilot.**
> [Forward Pilot](https://buffedlizard55-lab.github.io/StockPaperSim/docs/pilot/):
> a weekday scheduler submits **timestamped** paper orders into the strict
> hash-chained journal, gates them, journals every refusal, and reconciles the
> journal before anything is published. Trading (NYSE) and settlement
> (FRBservices) calendars, the official halts parser and the re-verified
> SEC §31 / FINRA TAF fees are integrated in `sim/venue_admin.py`; market
> making stays disabled until quote-size and queue-priority evidence exists.
> **Equity fills remain zero on purpose:** the approved-feed registry is
> empty, and the candidate review with usage-rights links is
> [`research/EQUITY_FEED_REVIEW.md`](research/EQUITY_FEED_REVIEW.md). The
> hypothesis registry grew to **55 strategies** (18 implementable), all with
> explicit data blockers.
>
> **Current release — strict official US-equities desk (2026-09-19).**
> [Open the new desk](https://buffedlizard55-lab.github.io/StockPaperSim/docs/desk/)
> for **55 strategy hypotheses**, **18 implemented signal prototypes**, source
> links, upcoming research candidates, and the row-level legacy-fill audit.
> **No official-price stock performance is claimed:** the 27 legacy forward
> fills use a model that lacks observed quote-size and submission-time evidence,
> and some recorded price sources are SECONDARY. They are excluded from the
> strict competition. There are **zero strict fills**, not an invented return.
>
> The new `sim/strict_equities.py` is a tested execution/storage library, **not a
> connected real-time service**. It provides evidence gates, timestamped orders,
> partial fills, shared displayed-capacity accounting, FIFO P&L, explicit fee
> assumptions, simulated settlement, fresh-mark requirements and JSONL exports.
> No official feed is approved. The desk never accepts manually invented prices.
> Read the [three-pass review and requirements matrix](research/strict/REVIEW.md)
> for what is implemented, what remains blocked, and the next-session plan.
> The legacy results below are retained as research, not certified stock trades.


A one-year paper-trading stock competition between **return-seeking strategy
personas**, with a real venue model (depth, spreads, market making, dated tick
size, dated fees), a full audit trail, and a published GitHub Pages site.

* **Season 1** - 20 personas on a replay of the **real S&P 500 and VIX path**.
* **The legacy Live Book** - 19 personas in a **forward-style research model**:
  intent dates precede model execution dates, but date-only records do not prove
  real-time submission. “Settled” here means processed by the legacy model,
  not verified T+1 cash settlement. Official, free series (Nasdaq Composite, Dow Jones, S&P 500, VIX,
  SOFR, four macro series) drive the signals, the benchmark, the calendar and the
  financing; the executable bars are still the collected Yahoo files marked
  `SECONDARY`, and the book publishes that share as a number.
* **Season 2** - 19 personas in a **reproducible research replay** on collected
  daily bars. The committed price files are Yahoo Finance data marked
  `SECONDARY`, so this run is **not eligible as an official-price competition**.
  The official Nasdaq historical adapter, raw-response custody chain and fail-closed
  audit are implemented for the next run; no official run starts until source,
  coverage, checksums and redistribution status pass. This is not advertised as a
  live real-time feed: the current competition engine works from completed daily
  bars and models intraday execution explicitly.

**Live site:** <https://buffedlizard55-lab.github.io/StockPaperSim/> →
[`docs/index.html`](docs/index.html)

Pages for this repository is configured to publish the **root** of `main`, not
`/docs`, and changing that needs admin rights (the API returns 403 for this
project's automation). So the root carries an `index.html` that redirects to
`docs/index.html`, plus a root `.nojekyll`. Nothing is duplicated: every page,
stylesheet and JSON file is served from under `docs/`, and all internal links are
relative, so the site works under either base path. Deep links in this README
include `/docs/` for that reason; an admin can drop it by setting
**Settings → Pages → source: `main`, `/docs`** and deleting the two root files.

> **Read this first.** Season 1 is a **replay of real anchor data through a
> calibrated simulated venue**, not a live feed. Two of the seventeen price
> series are real observations (SPY via Yahoo Finance, and the index factor and
> VIX regime via FRED); the other fifteen are explicitly labelled simulations
> built from those real factors plus declared parameters. No bar was invented
> silently, no result here is investment advice, and nothing on the site should
> be read as evidence about a strategy's real future performance. The site says
> this on every page, and [`research/IRREGULARITIES.json`](research/IRREGULARITIES.json)
> carries the 76 flags this project raised against itself.
>
> **Season 2 is reproducible research, not yet official-price eligible.** The
> historical run is matched to collected Yahoo daily bars (the page prints the
> file and SHA-256 next to each fill), but Yahoo is an aggregator and every such
> file is `SECONDARY`. The independent audit re-derives the run, yet agreement with
> Nasdaq or FRED cannot promote a secondary file to an official primary source.
> The official Nasdaq adapter preserves raw responses and collection checksums; its
> strict eligibility gate also requires complete dates, valid OHLCV, verified
> dividend status and explicit redistribution permission. Until those checks pass,
> `python3 -m sim.cli season2` refuses to trade. Read
> [`docs/season2/index.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/season2/index.html)
> for the custody chain, audit flags and stress panels.

---

## Season 1 result

Window **2025-09-17 → 2026-09-16** (251 sessions), starting capital **$100,000**
each, ranked on total return. Benchmark: the real S&P 500 returned **+14.41%**
over the same window (FRED `SP500`); **13 of 20 participants beat it**.

| # | Participant | Return | Max DD | Sharpe | Beta | Trades | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `@BetaChaser_3xProxy` | **+108.4%** | −43.5% | 1.40 | 3.92 | 66 | 0.24% | beat the market |
| 2 | `@SectorRotator_AlphaX` | +100.9% | −32.2% | 1.91 | 2.44 | 25 | 0.05% | beat the market |
| 3 | `@MeanRev_Z2Sigma` | +74.3% | −7.0% | 3.36 | 0.54 | 53 | 0.26% | beat the market |
| 4 | `@MomentumMax_12x1` | +51.2% | −45.0% | 1.07 | 3.25 | 9 | 0.03% | beat the market |
| 5 | `@BuyHold_MaxBeta` | +45.3% | −32.0% | 0.89 | 3.17 | 3 | 0.11% | beat the market |
| 6 | `@OverreactionFade_LT` | +42.1% | −12.6% | 1.65 | 0.47 | 71 | 1.19% | beat the market |
| 7 | `@OneBigBet_Concentra` | +40.6% | −22.1% | 1.37 | 0.41 | 50 | 0.62% | beat the market |
| 8 | `@TrendSurfer_GoldenX` | +30.1% | −26.9% | 0.97 | 2.23 | 55 | 0.04% | beat the market |
| 9 | `@VIXRegime_Timer` | +28.4% | −14.7% | 1.46 | 1.34 | 35 | 0.06% | beat the market |
| 10 | `@DriftRider_PEAD` | +20.7% | −14.8% | 0.91 | 0.23 | 40 | 0.35% | beat the market |
| 11 | `@Contrarian_DeepValue` | +18.9% | −51.2% | 0.62 | 2.78 | 3 | 0.02% | roughly matched the market |
| 12 | `@DonchianBreakout_20` | +18.2% | −19.4% | 0.67 | 1.57 | 8 | 0.05% | roughly matched the market |
| 13 | `@IlliquidRocket_Degen` | +15.8% | −45.0% | 0.59 | 1.02 | 13 | 1.87% | roughly matched the market |
| 14 | `@OvernightCarry_NO` | +9.0% | −4.9% | 1.25 | 0.04 | 247 | 1.16% | made money but lagged the index |
| 15 | `@KitchenSink_AllIn` | +9.0% | −31.1% | 0.41 | 1.55 | 367 | 0.38% | made money but lagged the index |
| 16 | `@PairsArb_ZScore2` | +1.0% | −0.4% | 1.01 | −0.01 | 3 | 0.01% | made money but lagged the index |
| 17 | `@SqueezeHunter_TF` | −1.8% | −10.8% | −0.09 | 0.13 | 30 | 0.48% | lost money |
| 18 | `@VolCarry_LowHigh` | −30.0% | −48.2% | −0.50 | −1.31 | 36 | 0.06% | lost money |
| 19 | `@SpreadHarvester_MM` | −34.4% | −53.6% | −0.52 | −1.93 | 537 | −0.07% | lost money |
| 20 | `@GapAndGo_YOLO` | **−91.8%** | −91.9% | −8.09 | 0.82 | 203 | 1.23% | lost money |

> **These numbers were re-derived, not patched.** Season 1 has been republished
> once, after two accounting errors were found in a line-by-line reading of the
> engine rather than by a failing test: dividend entitlement was settled on the
> position left *after* the ex-date's trades, and short positions were never
> charged the manufactured dividend a stock loan requires (IR-31). Two
> strategies also turned out to be unable to write the exit rule they documented,
> because the venue could only execute at the opening bell (IR-34), and the
> pattern-day-trader counter was a stub that returned `True` for any second fill
> (IR-32). Fixing the accounting changed P&L, so the honest response was to
> re-run the season and republish, not to edit the table in prose. The direct
> cash correction was small - dividends received went from $11,299.61 to
> $11,600.83 while $1,852.31 of manufactured dividends began to be charged, a net
> $1,551.09 across 20 accounts, about $78 each - and the individual effects were
> not: `@OverreactionFade_LT` moved 31.3pp because a $2.12 difference on one
> ex-date changed the size of an order a month later and every later trade
> compounded from it. Six of twenty ranks moved. Ranks 1 to 3 held, but the
> middle of the table did not, and three participants changed rank without any
> change in their own return at all, displaced by someone else's correction. That is the same knife-edge IR-29
> measures from the other side, and it is why this README ranks nothing by skill
> and why `scripts/independent_audit.py` exists: 763 checks re-derive every
> published number from the raw event trail using code that never imports the
> engine, and CI runs it on every push.

**What actually caused these returns** (each participant page carries the full
post-mortem, generated from that participant's own numbers):

* The winner did not pick stocks well. `@BetaChaser_3xProxy` ran **beta 3.92** into a market
  that rose 14.4%, and the report splits it: **+56.5pp from beta** and
  **+51.9pp residual** (annualised alpha +44.1%, R² 0.50). It took a
  **−43.5% drawdown** to get there. `@BuyHold_MaxBeta` is the control
  experiment: same idea, three trades, +45.3%.
* `@GapAndGo_YOLO` lost 91.8% not because the gap signal was
  wrong but because it chased gaps with full size into a **1.23%
  of capital in execution cost** over 203 round trips, every one of them opened
  and closed inside a single session (FINRA's counting rule, Notice 21-13). The
  venue charges spread, impact and fees twice a day to a strategy that holds for
  hours, and that is where the account went. It also now receives **$0.00** of
  dividends, against $80.46 before IR-34, which is what a book that is genuinely
  flat by the close must receive.
* `@SpreadHarvester_MM` is the market maker. It **earned the rebate** (negative
  cost, −0.07%) and still lost 34.4%, because quoting both
  sides of a trending, fat-tailed tape accumulates inventory it cannot unwind
  at the mid - adverse selection, modelled after Glosten-Milgrom. It is the most
  instructive failure in the season.

* `@MeanRev_Z2Sigma` is the best risk-adjusted result in the season (**Sharpe
  3.36, max DD −7.0%**) and third on return: mean reversion in
  a range-bound year with a cheap venue (0.26% of capital).


Because one calendar path cannot separate skill from luck, every participant is
replayed across **six scenarios** (the real calendar path plus five synthetic
seeds) over the same real index path. The panel publishes mean / median / best /
worst / standard deviation per participant — `docs/assets/data/robustness.json`,
and the "Scenario robustness" section of each page. It reorders the table: the
winner's return ranges **+82.7% to +407.9%** across the six scenarios (mean
+184.5%, median +141.3%, σ **124pp**), which is the honest way to read a
single-season contest. It beat the index in all six, but a σ of 124pp on a
one-year horizon is the number that should decide how much weight the ranking
gets.

---

## Season 2 result: reproducible research, but not official-price eligible

This historical run is retained for research and audit development. It used
Yahoo Finance daily files marked `SECONDARY`; therefore it does **not** satisfy the
official-price competition requirement, even where a Nasdaq cross-check agrees.
Window **2025-09-17 → 2026-09-16**
(251 sessions), $100,000 each, ranked on total return. Benchmark: the real S&P 500 returned **+14.41%** over the same window
(FRED `SP500`); **4 of 19 participants beat it** and **7 never traded at all**,
which the site reports as *no trades placed* rather than as a 0.00% performance.
These table values must not be presented as an official-source backtest until the
Nasdaq adapter's full raw-response and redistribution gate passes.

| # | Participant | Return | Max DD | Sharpe | Beta | Trades | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `@FDACatalyst_Rider` | **+74.3%** | −18.3% | 1.81 | 1.16 | 46 | 0.82% | beat the market |
| 2 | `@LeapMaxLever_Momentum` | +38.1% | −22.3% | 1.06 | 2.73 | 4 | 0.02% | beat the market |
| 3 | `@GOLD_Trend_GLD` | +32.8% | −36.3% | 0.88 | 0.53 | 43 | 0.21% | beat the market |
| 4 | `@MLB_Attention_Momo` | +23.8% | −28.1% | 0.68 | 0.36 | 105 | 2.59% | beat the market |
| 5 | `@PinePilot_EMA_Cross` | +18.8% | −12.1% | 0.79 | 1.77 | 49 | 0.03% | roughly matched the market |
| 6 | `@FDA_ClusterFade` | +2.8% | −22.3% | 0.25 | −0.16 | 34 | 0.45% | made money but lagged the index |
| 7 | `@InsiderCopycat_Max` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 8 | `@InsiderCluster_Alpha` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 9 | `@CEO_CFO_Conviction` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 10 | `@NBA_Slate_Attention` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 11 | `@InjuryFeed_Forward` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 12 | `@NBAInjury_Forward` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 13 | `@SportsPred_Forward` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed |
| 14 | `@Kalshi_Attention_Timer` | −2.0% | −6.2% | −0.16 | −0.06 | 14 | 0.37% | lost money |
| 15 | `@MLB_Upset_Short` | −9.6% | −11.3% | −0.78 | −0.02 | 22 | 0.59% | lost money |
| 16 | `@YieldCurve_Rotator` | −10.1% | −12.9% | −0.64 | 0.26 | 4 | 0.04% | lost money |
| 17 | `@Weather_ColdSnap_Max` | −11.2% | −40.4% | −0.07 | −0.19 | 49 | 1.36% | lost money |
| 18 | `@NCAA_Upset_Blitz` | −38.1% | −46.0% | −1.21 | 0.35 | 50 | 0.74% | lost money |
| 19 | `@NFL_Slate_Attention` | −56.6% | −57.3% | −2.05 | 0.40 | 60 | 0.76% | lost money |

**What that says, honestly.** The winner is a leveraged biotech bet and one of
the deepest losers is *the same signal traded the other way*
(`@FDA_ClusterFade`, which fights the approval-count trend) - one real signal, two
opposite implementations, and the difference between first and near-last. Nothing here
is a claim that FDA approvals predict XBI. The sports-attention family is the
2026-09-19 addition and it lost across the board: `@NFL_Slate_Attention`
(−56.6%) and `@NCAA_Upset_Blitz` (−38.1%) bought the sportsbook complex in
season on an attention clock while the complex itself de-rated - the
post-mortems attribute the losses to the basket, not to the signal timing, and
`@MLB_Upset_Short`'s −9.6% shows the same complex was a bad short too. The idle
seven are the other half of the result:
`@InsiderCopycat_Max`, `@InsiderCluster_Alpha` and `@CEO_CFO_Conviction` still
await the SEC insider collection (the quarterly bulk data sets are collected on
a runner - see the 2026-09-19 note below); `@NBA_Slate_Attention` awaits the
ESPN NBA scoreboard collection; `@InjuryFeed_Forward`, `@NBAInjury_Forward` and
`@SportsPred_Forward` are declared forward-only probes that place no backdated
trades by design. The site labels each one *DATA-MISSING* with the URL and the
reason, which is the difference between "we tested it and it did not work" and
"we could not test it".

> **2026-09-19 note.** The SEC insider signal builder now reads the quarterly
> bulk data sets (`data/real/insider_bulk/`) as well as the per-filing walk,
> de-duplicated by accession and transaction facts, and the trailing
> buy/sell ratio became genuinely trailing-30d (it was a whole-file constant
> before any insider data ever landed, so no published number depends on the
> old behaviour). Three new slate personas (NFL, NBA, NCAA) and two new
> forward-only probes (NBA injury, Sports Pred) bring the roster to 19; a
> weekly dated injury-snapshot archive (`.github/workflows/injury-archive.yml`)
> starts accumulating the observations the injury probes need.

**Verification.** 976 fills and 480 round trips, net round-trip P&L
**$52,994.38** on $23.9m of traded notional, ledger digest `35b5aa7d…`. The
account is re-derived from the raw fill tape by code that never imports the
engine: max |equity residual| **$0.0072** against a per-account rounding bound of
$2.925 (the tape stores six-decimal prices). Median participation is 0.00044% of a
session's volume and the largest single fill is 0.23% of it. The independent audit
re-reads every published number: **3,393 checks, 0 failures**, plus 1,222 more in
the Season 1 audit. Cost sensitivity is published as a panel, not a footnote:
doubling spreads, impact and fees costs the leader 1.2pp and halving the venue's
depth costs 0.1pp, while `@Weather_ColdSnap_Max` loses 0.8pp to the fee-doubling
alone.

**Data custody.** 26 instruments, 502 sessions (251 warm-up + 251 competition),
the inventoried files under `data/real/`, every request logged with status, bytes and SHA-256 in `data/real/collection_manifest.json`. Season 2's own registers live at
`docs/season2/masterfeed.html` (the 14 MasterSite projects the brief named, each
with source class, mapping strength and whether history was retrievable),
`docs/season2/ledger.html` (every fill with the reference bar, the file and the
participation) and `docs/season2/data.html` (the full custody chain).

**How Season 2 is built and checked.**

```bash
python3 scripts/collect_real_data.py --out data/real   # on a runner with network
python3 -m sim.cli season2 --labels primary,stress-costs2x,stress-thinliquidity \
  --price-source yahoo --allow-secondary-research     # the committed run is the research replay
python3 -m sim.cli ledger --run season2-primary-seed20260918 --participant @FDACatalyst_Rider
python3 scripts/independent_audit_season2.py           # 3,393 checks, no project imports
python3 -m sim.cli build-site                          # Season 1 + Season 2 into docs/
```

Three assumption sets run the same real prices through the same participants:
`primary`, `stress-costs2x` (double spreads, double impact, $1/order, double the
per-share taker fee) and `stress-thinliquidity` (half the participation cap and
half the touch size). Real prices are one realisation, so the sensitivity comes
from the venue assumptions rather than from a fabricated seed panel.

---

## The Official Auction Book: trades that executed at a published price

Seasons 1 and 2 trade equities on a *simulated* price path; the Live Book trades
collected bars that are real but **SECONDARY**, so neither can claim an official
executed price. The brief's first requirement was the opposite: *simulated
settled trades built from real verified official pricing, dates and liquidity*.

The **Official Auction Book** (`sim/treasury.py`, `sim/officialbook.py`,
`sim/strategies_official.py`, `sim/official_season.py`) is that book. It trades
instruments whose price the **U.S. Treasury publishes**, and it cannot reach any
other price: there is no code path from a Yahoo or Stooq file into this engine,
and the verification block fails the run if one is introduced.

| Where a price comes from | Class | Example |
|---|---|---|
| The Treasury's published auction result for that CUSIP | **OFFICIAL** | `pricePer100` / `highPrice` on a 4-week bill or a 10-year note |
| The Treasury's official par yield curve, or the H.15 bill discount rates | **OFFICIAL-DERIVED** | a secondary mark, by the formula printed on the venue page |
| Nothing at all | — | the order waits, and says so, rather than filling at a modelled price |

**The competition, as declared before the first session.** 14 strategies, one
username each, $100,000 each, 2025-09-17 → 2026-09-16 (250 official sessions),
ranked on ending equity and nothing else. No risk-management rule: a participant
may run at its own declared leverage cap and may be wiped out — the venue closes
positions at the official mark when equity falls below 5% of gross exposure, and
an account that reaches zero is wound up at exactly −100% and stops trading.

**Result.** 189 settled round trips from 302 fills and 2,898 intents; the best
return is `@FrontEndRollDown_13W` at **+4.42%**, and it is not a duration call: the
account holds two short bills and its gain is their carry. The median return is
**-39.07%**, and every participant that took duration risk lost money: the
inflation rule's 30-year TIPS is marked $43,801.28 below what it paid and ends at
**-59.77%**, and `@LongBond_MaxDur` ends at **-90.10%**. Financing is SOFR + 25 bp
on debits and shorts and idle cash earns SOFR, which is why a participant that
never traded can still show a small positive return. Every trade row carries its
entry and exit price, both dates, the auction's own offering amount and
bid-to-cover, the field name the price came from and that file's SHA-256.

**What the inflation rule did when it could finally see its own horizon.** The
rule compares the breakeven at a security's remaining maturity with realised
inflation over the same number of years, and it spent the whole window holding a
29-year TIPS bought at 98.66 in September 2025 and marked 87.07 a year later, for
a mark-to-market of **-$43,801.28** and a financing charge of $16,528.92 - which
is where its -59.77% comes from. Two things had to be fixed before it could even
be judged: the rule used to read its nominal leg at a fixed ten years and its
inflation leg over a fixed five while holding a thirty-year security (IR-61), and
the CPI file held one year of observations, so the honest answer to "what has
inflation actually been over this horizon" was *nothing* (L-36). The collector
now asks for the index from 1990, and the page records, session by session, the
reason the rule gave whenever it stood aside.

**Verification, twice.** `sim/official_season.py` re-reads the tape and checks
eight families of property (published price on every primary fill, bill price
formula on every published bill price, two-publisher agreement, no look-ahead, no
equity residual, maturity dates, price classes, and no reachable secondary path):
**8,045 checks, 0 failures**. Then `scripts/independent_audit_official.py` - which
imports **nothing** from `sim/` and re-derives everything from the raw Treasury
tapes and the run's own streams - adds **1,870 checks**, including the
two-publisher comparison on every primary price the book actually executed. CI
rewrites that report and fails if the committed copy changes, so the number here
cannot drift from the run.

**What it cannot do, and says so.** An equity or ETF price that is official *and*
redistributable does not exist for this project (Nasdaq's normalised archive
needs an entitlement; the free publisher pages forbid redistribution), so the
equity books stay SECONDARY and the coverage number stays visible. The official
lane answers that part of the brief with the instrument family where a publisher
prints the price of every trade. The remaining gaps are registered as **L-27** to
**L-39** — the withheld SEC insider extracts (HTTP 403 from the collection
runner), the derived secondary leg, TIPS marked without inflation indexation,
same-day settlement of auction awards, a house maintenance rule rather than a
cited one, assumed secondary depth, the mixed-class totals in the unified trade
store, a CPI file too short for the horizon the inflation rule needs, and a bid on
an auction that has not priced yet being sized from the newest published price of
the same term.

---

## The Live Book: trades placed for sessions that have not happened yet

Seasons 1 and 2 both decide *and* execute inside the same session: a strategy
reads the history strictly before session `t` and its orders fill at session `t`'s
open. That is look-ahead free, but it is still not something a person can do —
nobody places an order at today's opening print using information that only
exists after today's close.

The **Live Book** (`sim/live.py`, `sim/live_season.py`,
`sim/strategies_live.py`) closes that gap, and it is the part of this project
that answers the brief's question directly. A participant **plans after the close
of session `T`** and writes an **intent** aimed at a future session. The intent is
appended to a ledger the moment it is created, carrying every input the rule read
— series name, observation date, value, file, SHA-256 — and it can never be
edited. When the target session finally has a verified bar, the venue settles the
intent through exactly the same microstructure model the competition uses: Rule
612 tick grid, quoted spread, displayed depth, market-maker quotes, square-root
impact, participation cap, dated exchange and regulatory fees.

It runs in two states, and both are published:

| | Planned on | Settled against | Published as |
|---|---|---|---|
| **Rehearsal** | 2025-09-17 → 2026-09-16, one session at a time | the next session's verified bar | a measurable leaderboard — this is the forward test |
| **Forward book** | 2026-09-16, the last session with a verified equity bar | nothing yet: 16 intents target 2026-09-17 and are `PENDING` | a status page, and **no return at all** |

That second row is the honest state of a live competition on day one. The page
reports 16 open intents and the exact evidence behind each one, and it reports no
performance, because none exists.

**Rehearsal result (251 sessions, decide at `T`, settle at `T+1`).** 19
participants, $100,000 each, ranked on total return. The official S&P 500 daily
close returned **+14.42%** over the same window; **4 of 19 beat it**, the median
return was **+3.87%**, and **three participants placed nothing at all** and say
why instead of printing a zero.

| # | Participant | Return | Max DD | Fills | Cost | Slip (bps) | Data |
|---|---|---|---|---|---|---|---|
| 1 | `@FDA_PDUFA_Drifter` | **+65.18%** | −15.63% | 97 | 0.013% | 0.76 | READY |
| 2 | `@DowNasdaq_SpreadMax` | +22.50% | −15.83% | 62 | 0.002% | 0.10 | READY |
| 3 | `@MLB_Attention_Live` | +19.46% | −31.75% | 108 | 0.075% | 5.17 | READY |
| 4 | `@NasdaqMomentum_Max` | +19.00% | −18.54% | 12 | 0.002% | 0.11 | READY |
| 5 | `@FDA_Fade_Live` | +10.60% | −16.77% | 34 | 0.013% | 0.73 | READY |
| 6 | `@SOFRPivot_Rider` | +8.94% | −16.45% | 179 | 0.005% | 0.23 | READY |
| 7 | `@VIXRegime_LiveMax` | +8.88% | −19.13% | 53 | 0.003% | 0.15 | READY |
| 8 | `@KitchenSink_Official` | +4.09% | −16.48% | 200 | 0.005% | 0.24 | READY |
| 9 | `@CurveSteepener_MaxBeta` | +3.99% | −14.78% | 195 | 0.006% | 0.33 | READY |
| 10–12 | `@InsiderCluster_Live` · `@InjuryFeed_Forward` · `@ORB_NextOpen_Probe` | +3.87% (cash only) | 0.00% | 0 | 0.000% | — | DATA-MISSING / FORWARD-ONLY |
| 13 | `@Weather_ColdSnap_Live` | +1.09% | −19.29% | 23 | 0.098% | 3.42 | READY |
| 14 | `@GoldVsRealRate_Live` | −7.50% | −25.37% | 123 | 0.008% | 0.48 | READY |
| 15 | `@PinePilot_EMA_Live` | −8.23% | −24.79% | 29 | 0.003% | 0.19 | READY |
| 16 | `@VolControl_MaxLev` | −9.71% | −17.30% | 85 | 0.003% | 0.13 | READY |
| 17 | `@OilDollar_FadeUNG` | −17.10% | −33.19% | 136 | 0.086% | 3.66 | READY |
| 18 | `@LeapStyle_AutoLiquidate` | −35.34% | −47.80% | 24 | 0.005% | 0.35 | READY |
| 19 | `@CrowdFade_Live` | **−45.90%** | −61.34% | 12 | 0.005% | 0.33 | READY |

**What caused these returns.**

* `@FDA_PDUFA_Drifter` is the rehearsal's winner and it is Season 2's winner
  again: the openFDA approval-flow rule, levered into XBI/IBB. One session of
  execution delay cost it a little and did not change the diagnosis — a hot
  approval regime and a re-rating biotech sector.
* `@CrowdFade_Live` is the clear failure, and the interesting part is *why*: it
  faded three-session sigma extremes, drew a maintenance call, and the broker
  liquidated it. Twelve fills, two margin events, −61% peak-to-trough. The rule
  was not unlucky; fading a trending tape with size is the documented way to
  lose, and here the venue charged it properly instead of letting the account
  keep marking a negative balance.
* `@LeapStyle_AutoLiquidate` −35.34% is the contest rule the brief named: rotate
  into the fastest-trailing asset at the leverage bound and liquidate at the end.
  It bought the top of the 42-session leader repeatedly. That is the honest
  result of performance chasing measured rather than asserted.
* `@NasdaqMomentum_Max` took **12 trades** to make +19.00%: the official Nasdaq
  Composite's own 210-session trend, levered into QQQ. The fewest decisions in
  the roster produced the fourth-best return.
* The three idle participants are the other half of the result. Two are declared
  forward-only because the data does not exist to backtest them, and one is
  waiting on the SEC Form 4 stream. Their +3.87% is official SOFR credited on
  idle cash, and their verdict column says *no trades placed* rather than
  letting a cash return masquerade as a strategy result.

**Verification and the clock.** 1,368 intents, 1,372 fills (four of them forced
liquidations the broker generated), 607 round trips, audit **PASS on 9,377
checks with zero failures**. The audit re-derives cash and positions from the
fill tape plus the carry rows without asking the account what it thinks it holds;
max cash residual **$0.000000**. Three clock properties are enforced in code:
a forward intent can never target its own plan session; no input an intent
records may be dated after the plan date; and a session the official calendar
says was closed **expires** its intent instead of filling.

**Where the official data is, and where it still is not.** The signals, the
benchmark, the trading calendar and the financing rate are official, free and
publicly available: the Nasdaq Composite, the Dow Jones Industrial Average, the
S&P 500, VIX, SOFR and four macro series, retrieved from FRED's CSV downloads and
recorded with publisher, observation range and SHA-256 (`sim/live.py`,
`docs/live/sources.html`). Two of the three new series were cross-checked against
a second official publisher: FRED's SOFR and the Federal Reserve Bank of New
York's own reference-rate API agree on 3.85% for 2026-09-17 and 3.62% for
2026-09-16. The **executable** prices are still the collected Yahoo research
files marked `SECONDARY`, so the book publishes its official-price coverage as a
number — **0.00% of filled notional** — rather than implying otherwise, and the
official-price gate stays ineligible.

**Data efficiency.** The whole rehearsal — intents, fills, round trips, equity
marks, carry rows and settlement summaries — is **13,135 rows in 527 KiB
(41.05 bytes per row)** of gzipped JSON Lines with sorted keys and compact
separators, so two runs produce identical bytes; plus a `blotter.csv` export for
spreadsheet review. The reference bar is stored once per fill, because the fill is
the unit a reader queries, and file digests live in the provenance document
instead of being repeated on every row.

**Review it line by line:**

```bash
python3 -m sim.cli live --mode all        # plan the forward book, run the rehearsal
python3 -m sim.cli live-blotter --export /tmp/live.csv
python3 -m sim.cli live-report @CrowdFade_Live
```

→ <https://buffedlizard55-lab.github.io/StockPaperSim/docs/live/index.html>

---

## What the venue models

| Piece | Implementation | Source |
|---|---|---|
| Tick size | dated Rule 612 grid. **$0.01 / $0.0001** is what was actually in force all season: the 2024 amendments' $0.005 tier is codified but exempted until the first business day of **November 2027** (Release 34-105656) | [17 CFR 242.612](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612), [34-105656](https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf) |
| Round lots | tiered Rule 600(b)(93) definition, live since 2025-11-03 | same Part 242 |
| Access fees | Rule 610(c) cap **$0.003/share** (the amended $0.001 cap is exempted) | [17 CFR 242.610](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610) |
| Regulatory fees | **dated schedules**: SEC §31 $0.00/M → **$20.60/M from 2026-04-04**; FINRA TAF $0.000166/sh → **$0.000195/sh from 2026-01-01** with the per-trade cap dated too ($8.30 → $9.79) | [FY2026 fee order](https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates) |
| Depth and fill | standing limit-order book per instrument, partial fills, rejects, queue position | `sim/microstructure.py` |
| Market making | Avellaneda-Stoikov reservation price and optimal spread, inventory penalty, maker rebate | [10.1007/s10436-008-0121-7](https://doi.org/10.1007/s10436-008-0121-7) |
| Impact | square-root temporary + permanent impact, Almgren-Chriss framework | [10.21314/JOR.2001.041](https://doi.org/10.21314/JOR.2001.041) — with the 3/5-power-law dissent recorded as [IR-26](research/IRREGULARITIES.json) |
| Intraday shape | each daily bar expanded into intervals visiting O/H/L/C with a U-shaped volume profile | Admati & Pfleiderer (1988) |
| Cost accounting | Perold implementation shortfall per order: spread, depth, impact, fees, intraday drift, missed trade | [10.2469/faj.v44.5.28](https://doi.org/10.2469/faj.v44.5.28) |
| Calendar | 10 real closures and 2 early closes detected from gaps in the FRED series, cross-checked against the Nasdaq holiday schedule | [Nasdaq](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule) |
| Shorting | Reg SHO locate requirement, borrow fee, Reg T 50% initial margin, maintenance margin, margin calls | [17 CFR 242.203](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203) |

---

## Layout

```
sim/            the engine - pure standard library, no third-party imports
  config.py         dated fee schedules, tick grid, margin, impact; the
                    78-row verified-source register
  calendar.py       sessions, closures, early closes
  universe.py       the 17-instrument whitelist with real/simulated labels
  marketdata.py     replay generator: real factor + real VIX regime -> bars
  microstructure.py depth book, quoting, market maker, fills, Rule 612 grid
  engine.py         the competition loop, accounts, margin, liquidation
  strategies.py     the 20 participants, each with academic_basis and thesis
  analytics.py      risk, attribution, robustness, post-mortem narratives
  treasury.py       the official lane's sources: auction tape, par curve, awards,
                    31 CFR 356 helpers, the official-source register
  officialbook.py   the Official Auction Book: intents, netted fills, coupons,
                    repo financing, the declared maintenance and ruin rules
  strategies_official.py the 14 official participants, each with thesis and sources
  official_season.py run + verify + post-mortem narratives + forward snapshot
  tradelog.py       the unified trade store: every closed trade, every book,
                    price class per leg, CSV + JSONL + coverage
  live.py           the forward book: official series, intents, settlement, margin
  live_season.py    the rehearsal and the real forward book, published side by side
  strategies_live.py the 19 live participants, each with plan(ctx) and a data status
  memory.py         append-only checksummed event store
  cli.py            run / leaderboard / report / verify / query / export /
                    irregularities / sources / build-site
scripts/        build_site.py (the GitHub Pages generator), check_purity.py,
                independent_audit.py and independent_audit_official.py
                (re-derive every published number from the raw streams and the
                Treasury's own tapes; import no project code)
tests/          633 tests - engine, venue, memory, site, live book, official book, registers, docs, README,
                official-price eligibility, sensitivity and trade simulation
data/real/      verbatim FRED and Yahoo research downloads, plus any official
                adapter responses only when their raw custody and status are recorded
memory/         the audit trail: one directory per run, gzipped event streams,
                per-file SHA-256 manifest, per-participant reports; memory/live/
                holds the forward book and its walk-forward rehearsal
docs/           the published site (GitHub Pages serves this directory);
                docs/live/ is the Live Book section and docs/official/ the
                Official Auction Book section
research/       VERIFICATION_LOG.md, COMPETITION_SITES.md,
                IRREGULARITIES.json (76), LIMITATIONS.json (43),
                REMAINING_WORK.json (52), MASTER_SITE_SIGNALS.md,
                SOCIAL_STRATEGY_SOURCES.md
```

## Run it

```bash
python3 -m sim.cli run                  # all six scenarios (~25 s), writes memory/
python3 -m sim.cli leaderboard          # ranked table with the benchmark
python3 -m sim.cli report --user @GapAndGo_YOLO     # full post-mortem
python3 -m sim.cli verify               # checksum every run + data audits
python3 -m sim.cli price-audit         # strict official-price gate (non-zero until eligible data exists)
python3 -m sim.cli season2               # official Nasdaq backend; fails closed if ineligible
python3 -m sim.cli season2 --price-source yahoo --allow-secondary-research
                                        # explicit non-eligible research replay only
python3 -m sim.cli trade-sim --symbol SPY --side buy --qty 500
                                        # simulate placing a real trade with full microstructure
python3 -m sim.cli live --mode all      # plan the forward book + run the rehearsal
python3 -m sim.cli live-blotter         # every live intent with its verified bar
python3 -m sim.cli live-report @FDA_PDUFA_Drifter
python3 -m sim.cli build-site           # regenerate docs/
python3 scripts/independent_audit.py  # re-derive the published numbers from events (763 checks)
python3 -m unittest discover -s tests   # 633 tests
python3 -m sim.cli official             # run the official auction book
python3 -m sim.cli official-blotter     # every settled official trade + evidence
python3 -m sim.cli trades               # the unified store across every book
python3 scripts/independent_audit_official.py   # imports no project code
```

Reproducibility is enforced, not claimed: the same seed and config reproduce the
committed leaderboard **bit for bit — in a different process, not just a
different object**, every memory file is SHA-256 checksummed in its run manifest,
and a deliberately corrupted stream is detected (`tests/test_memory.py`). CI
re-runs the primary season in a scratch memory root, diffs it against the
committed leaderboard, and diffs a rebuilt site against `docs/` byte for byte.

That last gate earned its keep immediately: it caught **IR-30**, three strategies
that iterated a *set* of held positions when building their exit list. CPython
salts string hashing per process, so the exit order — and therefore the cash
available to the entries that followed — changed with `PYTHONHASHSEED`. The same
seed, config and machine produced `@OverreactionFade_LT` at **+39.90%** in one
process and **+73.46%** in another. The in-process determinism test that had been
passing all along could not catch it by construction. Fixed by iterating the
canonical universe order, verified identical across five hash seeds, and now
guarded by a cross-process reproducibility test plus an AST sweep that fails on
any `for` loop over a set.

## Verification stance

The brief was to work line by line from official sources, provide links for
manual review, and flag irregularities rather than paper over them.

* [`research/VERIFICATION_LOG.md`](research/VERIFICATION_LOG.md) — what was
  checked, when, from where, what came back, and what was corrected. Includes the
  citation audit that found and fixed **six** wrong or dead references
  (a Perold DOI that was actually Roll 1984; an Artzner DOI that was actually
  Föllmer & Leukert 2000; a Jegadeesh-Titman DOI that was actually Engle & Ng
  1993; an Almgren 2005 author list, page range and DOI; a PEAD DOI from the
  wrong journal; an unverifiable Amihud DOI).
* [`docs/sources.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/sources.html)
  — the 84-row register (78 source rows plus 6 provider rows), every URL
  clickable, every row carrying an honesty
  status (`FETCHED-VERIFIED`, `FETCHED`, `FETCHED-VIA-SEARCH`, `SECONDARY`,
  `KNOWN-NOT-FETCHED`, `ADAPTER-DOCS`). Nothing is marked verified that was not
  actually retrieved here.
* [`research/COMPETITION_SITES.md`](research/COMPETITION_SITES.md) — the
  reverse-engineering of [TradingView The Leap](https://www.tradingview.com/the-leap/december-2025/rules/),
  [Trade Ideas PM Challenge](https://www.trade-ideas.com/stock-trading-competition/)
  and [CandleCharts Showdown](https://specials.candlecharts.com/contest/): which
  rule was copied, which was widened as a declared SIM CHOICE, and which is
  honestly marked *not applicable*.
* [`docs/irregularities.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/irregularities.html)
  — all 76 flags, including the ones raised against this project's own modelling
  choices.
* [`docs/limitations.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/limitations.html)
  — 43 limitations, 44 items of remaining work in priority order, and what
  success would require.

## Known limits (the short version)

1. **No real intraday market data.** The consolidated tape is not reachable from
   this environment; Yahoo's quote endpoint needs a crumb (401), Stooq refused
   the connection, and no bulk daily OHLCV for all 17 names was retrievable. So
   the venue is calibrated, not observed. Six live-data adapters
   (Alpaca, Polygon, Finnhub, Tiingo, Yahoo, Stooq) ship in `sim/marketdata.py`
   and are the path to a genuinely real-time season — they could not be
   exercised here.
2. **One season, six scenarios, one year.** That is not a statistically
   significant sample of anything, and the site says so.
3. **Declared priors are not measured premia.** Factor returns, borrow rates,
   earnings surprises and 12 of 17 dividend schedules are declared parameters,
   not observations (IR-07 to IR-09, IR-16).
4. **Impact exponent.** The square-root law is the vendor convention; the largest
   empirical study of real orders found a 3/5 power law instead, which would
   charge ~26% less impact at 10% of ADV. Documented as IR-26, not silently
   absorbed.
5. **The live book has no settled forward trades yet.** Its forward state is 16
   pending intents and nothing else; the measurable part is the walk-forward
   rehearsal over already-collected sessions (L-26).
6. **Index histories are not tradable instruments.** Every index rule (Nasdaq
   Composite, Dow Jones, S&P 500, VIX) executes through an ETF proxy, so tracking
   difference and fund fees sit between the signal and the P&L, and no index
   series can ever satisfy the official-price gate for *fills* by itself (L-25).
7. **The official lane's secondary leg is derived.** No free publisher prints a
   secondary Treasury trade tape, so a sale before maturity is priced from the
   official curve by a published formula and labelled OFFICIAL-DERIVED rather
   than OFFICIAL (L-28), and secondary size is assumed rather than measured
   (L-32). Primary awards, which are where most of the notional executes, are
   the published price exactly.
8. **The SEC insider extracts are not in the repository.** Every quarter and both
   path layouts answered HTTP 403 from the collection runner on 2026-09-18
   (L-27), so the insider rules hold no positions and say why instead of
   substituting an aggregator for a filing.
9. **Circadian granularity.** The live book has one decision point per session
   and executes at the open or the close. A rule that needs the first thirty
   minutes (an opening-range breakout) cannot be expressed here at all and is
   published as a forward-only probe rather than approximated (L-24).
