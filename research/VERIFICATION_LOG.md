# Verification log

Every external claim in this project was checked against a source, and this file
records **what was checked, when, from where, what came back, and what was
corrected as a result**. It exists because the brief asked for line-by-line
verification from official sources with links a human can re-check, and because
"the code says so" is not a source.

Statuses use the vocabulary defined in `sim/config.py`:

| Status | Meaning |
|---|---|
| `FETCHED-VERIFIED` | retrieved in this environment **and** stored under `data/real/` - the download itself for market data, a dated verbatim excerpt for a page (since 2026-09-17, IR-33: a citation with no stored artefact goes stale in silence) |
| `FETCHED` | retrieved in this environment; content used, not stored |
| `FETCHED-VIA-SEARCH` | reached only as text inside search results (weaker - the page itself was not fetched) |
| `SECONDARY` | only a secondary source was reached; the primary was not retrievable |
| `KNOWN-NOT-FETCHED` | cited from the literature; **not** retrieved here - verify manually before relying on it |
| `ADAPTER-DOCS` | vendor documentation for a live-data adapter (not a market fact) |

Environment: all checks below were made on **2026-09-17** from a sandbox whose
direct network allowlist was `pypi.org`, `files.pythonhosted.org`,
`api.github.com`, `github.com`, `codeload.github.com`. Everything else was
reached through the page-fetch tool or through search-result text. Several hosts
refused the connection outright; those refusals are recorded rather than worked
around, and each one is reflected in `research/IRREGULARITIES.json`.

---

## 1. Real market data (stored, checksummed, used as the market factor)

| What | Source | Result | Status |
|---|---|---|---|
| S&P 500 daily closes, 2025-09-17 → 2026-09-16 | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd=2025-09-17&coed=2026-09-16` | 261 rows, 10 blank (closures). Stored verbatim at `data/real/fred/SP500_2025-09-17_2026-09-16.csv`. Window return **+14.415%**, annualised log vol **0.1290** | `FETCHED-VERIFIED` |
| CBOE VIX daily closes, same window | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd=2025-09-17&coed=2026-09-16` | 261 rows. Mean **18.157**, max **31.05 on 2026-03-27**. Stored at `data/real/fred/VIXCLS_2025-09-17_2026-09-16.csv` | `FETCHED-VERIFIED` |
| SPY monthly OHLCV, 1 year | `https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&range=1y` | 13 monthly bars. Used to fit the SPY/index ratio (**10.02785**, max error **0.1451%** vs real monthly closes) and to audit the simulated intraday range. Stored at `data/real/yahoo/SPY_monthly_1y.json` | `FETCHED-VERIFIED` |
| AAPL snapshot (price anchors + dividends) | `https://query1.finance.yahoo.com/v8/finance/chart/AAPL` | Start anchor **236.70**, end anchor **332.41**, four dividend ex-dates (0.26, 0.26, 0.27, 0.27). Stored at `data/real/yahoo/AAPL_snapshot_2026-09-17.json` | `FETCHED-VERIFIED` |
| Yahoo "real-time" quote endpoint | `https://query1.finance.yahoo.com/v7/finance/quote` | **HTTP 401 Unauthorized** - needs a crumb paired with a cookie. Flagged as **IR-02** | failed, flagged |
| Stooq end-of-day CSV | `https://stooq.com/q/d/l/?s=aapl.us&i=d` | **"Access denied"** - host refused the client. Flagged in the provider catalogue and **IR-03** | failed, flagged |
| Bulk daily OHLCV for all 17 names | several hosts | Not retrievable inside the environment's fetch budget. **No bars were invented**: 15 of 17 names are explicitly labelled simulations anchored to the real index factor, the real VIX regime and declared scenario parameters. Flagged as **IR-03** and **L-01** | not obtained, flagged |

Cross-check on the FRED gaps: the ten blank SP500 dates were compared against
the published Nasdaq holiday schedule
(`https://www.nasdaq.com/market-activity/stock-market-holiday-schedule`). All ten
match a published full closure (2025-11-27, 2025-12-25, 2026-01-01, 2026-01-19,
2026-02-16, 2026-04-03, 2026-05-25, 2026-06-19, 2026-07-03, 2026-09-07), and the
two early closes (2025-11-28, 2025-12-24, 13:00 ET) are modelled. VIXCLS
nevertheless prints on seven of those dates, because the VIX is computed from
SPX **option** prices and CBOE conventions do not line up one-for-one with the
cash-index calendar; those seven observations are dropped and counted. Flagged as
**IR-01**.

---

## 2. Regulation: the rules the venue model has to obey

### Rule 612 - minimum pricing increment (tick size)

The current codified text was fetched from the eCFR on 2026-09-17
(title 17 "up to date as of 9/15/2026"):
`https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612`

What 17 CFR 242.612 actually says:

* `(b)(2)(i)` **$0.01** where the Time Weighted Average Quoted Spread (TWAQS)
  for the Evaluation Period was **greater than $0.015**;
* `(b)(2)(ii)` **$0.005** where the TWAQS was **equal to or less than $0.015**;
* `(b)(3)` **$0.0001** for quotations and orders priced **below $1.00**;
* `(b)(1)` increments become operative on the **first business day of May**
  (January-March evaluation) and the **first business day of November**
  (July-September evaluation);
* source note: **89 FR 81774, Oct. 8, 2024**.

So the amended half-penny tier **is** in the CFR. It is nevertheless **not in
force**, and that was verified rather than assumed:

| Date | Action | Source |
|---|---|---|
| 2024-09-18 | Amendments adopted (Release 34-101070); compliance date for Rules 612/610 and the round-lot definition set at the first business day of November 2025 | `https://www.sec.gov/newsroom/press-releases/2024-137`, `https://www.sec.gov/files/34-101070-fact-sheet.pdf` |
| 2024-10-08 | Adopting release published, 89 FR 81774 | `https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders`, `https://www.govinfo.gov/content/pkg/FR-2024-10-08/pdf/2024-21867.pdf` |
| 2024-12-12 | **Partial stay** of the amendments to Rules 600(b)(89)(i)(F), 610 and 612 pending judicial review (Release 34-101899) | recorded in the recitals of Release 34-105656 |
| 2025-10 | D.C. Circuit **denies** the petitions for review | recorded in the recitals of Release 34-105656 |
| 2025-10-31 | **Temporary exemptive relief** from the amended compliance dates until the first business day of **November 2026** | recorded in Release 34-105656 and in `https://www.sec.gov/files/rules/exorders/2026/34-105058.pdf` |
| 2026-06-11 | **Release 34-105656** extends that relief for Rules 600(b)(89)(i)(F), 610(c) and 612 until the first business day of **November 2027** | `https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf`, published 2026-06-15 as `https://www.federalregister.gov/documents/2026-06-15/2026-11997/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of`, plus `https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126` |

**Consequence for this project.** The entire Season 1 window (2025-09-17 →
2026-09-16) sits inside the exemption period, so the operative grid is
**$0.01 / $0.0001** and the operative Rule 610(c) access-fee cap is
**$0.003 per share**. That is what `config.minimum_tick()` and
`config.ACCESS_FEE_CAP_PER_SHARE` implement. **IR-04** was rewritten from
"genuinely uncertain" to "resolved from primary sources" and downgraded from
medium to low as a result.

Two things the relief did **not** cover, and which are therefore modelled as
live: the **tiered round-lot definition** (Rule 600(b)(93): 100 shares ≤ $250,
40 to $1,000, 10 to $10,000, 1 above) went live on **2025-11-03**, and odd-lot
dissemination is scheduled for **2026-05-01**.

Also flagged: on 2026-06-11 the Commission **proposed rescinding Rule 611** (the
order protection / trade-through rule). Season 1 is unaffected - Rule 611 was in
force for every session - but a future season would need the venue model
re-derived. Recorded as **IR-25**.

### Fees

| Fee | Value used | Source | Status |
|---|---|---|---|
| SEC Section 31 (sells) | **$0.00 per $1,000,000** from 2025-09-01; **$20.60 per $1,000,000** from 2026-04-04 | FY2026 annual adjustment order, `https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates`; corroborated by Nasdaq `https://www.nasdaqtrader.com/MicroNews.aspx?id=OTA2026-14` | `FETCHED` |
| FINRA Trading Activity Fee (sells) | **$0.000166/share** to 2025-12-31, **$0.000195/share** from 2026-01-01; per-trade cap **$8.30** then **$9.79** | FINRA primary fee-adjustment schedule (`https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule`), fetched 2026-09-18; general TAF guidance confirms the fee is assessed on sales | `FETCHED` - **IR-05 resolved** |

Both are **dated schedules**, not constants (`config.SEC31_PER_MILLION`,
`config.FINRA_TAF_PER_SHARE`, `config.FINRA_TAF_MAX_PER_TRADE`), because a
one-year competition straddles two rate changes. `config.fee_coverage_start()`
returns the earliest date any schedule is documented for (**2025-09-01**) and
`config.validate_fee_coverage()` refuses to run a season that opens before it,
so no trade can ever be costed at a rate this project cannot cite.

The per-trade **cap** was a scalar until this pass; it changed with the rate on
2026-01-01, so applying the 2026 cap to a 2025 sale would have overstated the
fee on very large orders. It is a schedule now. (For Season 1 the cap never
binds: the largest pre-2026 sell was 13,472 shares, and the cap starts to bind
above 50,000 shares.)

### Other rules relied on

* Rule 610 access-fee cap and Rule 611 trade-through protection:
  `https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610`,
  `.../section-242.611` (Reg NMS is **17 CFR Part 242** - two citations in this
  repository previously said Part 240 and were corrected).
* Regulation SHO Rule 203(b) locate requirement for short sales:
  `https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203`,
  `https://www.sec.gov/regulation-sho`.
* Pattern day trader / $25,000 minimum equity: 17 CFR 240.3b-1 (**Part 240** is
  correct for this one) and
  `https://www.finra.org/investors/learn-to-invest/types-investments/margin-investing/pattern-day-trader`.
* Reg T initial margin of 50%: `https://www.federalreserve.gov/supervisionreg/regtcg.htm`.
* Rule 605 execution-quality reporting: `.../part-242/section-242.605`.
* GICS sector labels: `https://www.spglobal.com/spdji/en/index-family/equity/gics/`.

---

## 3. The three paper-trading competitions that were reverse-engineered

Full notes, including the leaderboard schema copied from each and what was
deliberately **not** copied, are in `research/COMPETITION_SITES.md`.

| Site | Fetched | What was taken from it |
|---|---|---|
| TradingView **The Leap** (December 2025 rules) | yes | $100,000 virtual starting capital; ranking on realised P&L; **open positions auto-closed at period end**; leaderboard refreshed at most hourly; minimum 3 active days; ban above 60 orders/minute; fixed instrument whitelist with a maximum number of positions; 1:1 leverage on stocks; $1 commission; accounts deleted 30 days after the end |
| **Trade Ideas** PM Challenge | yes | The leaderboard **schema**: Rank, User, Total Profit, Open Profit, Close Profit, Total Trades, Open Trades, Closed Trades, Account Value, Avg Profit/Trade - and its "**data delayed by 15 minutes**" notice, which is why this project states its own data latency instead of implying real time |
| **CandleCharts** Showdown | yes | $50,000 paper account, hosted through TradingView Community Competitions, prizes for the top three - i.e. a fixed-horizon contest on a paper account, ranked on profit |

---

## 4. Citation audit (what was wrong, and how it was fixed)

Every DOI and URL in `sim/` and `scripts/` was checked against the paper or
document it was attached to. Six defects were found and fixed; all six are
recorded as **IR-27** and are now enforced by
`tests/test_sources_register.py`.

| # | Where | Defect | Verified fact | Fix |
|---|---|---|---|---|
| 1 | `sim/microstructure.py` | Implementation shortfall attributed to "Perold 1988" with `10.2307/2328616` | `10.2307/2328616` is **Roll (1984)**, "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market", *Journal of Finance* 39(4):1127-1139. Perold (1988) is `10.2469/faj.v44.5.28` | Perold now carries the correct DOI; the note in the module docstring records the swap |
| 2 | `sim/config.py` `ImpactConfig` | Cited `10.1093/rfs/1.1.3` as an impact source | That DOI is **Admati & Pfleiderer (1988)** on intraday volume patterns - the source for the U-shaped volume profile in `LiquidityConfig` | Removed from `ImpactConfig`; the framework is now attributed to Almgren & Chriss (2001) |
| 3 | `sim/analytics.py` | CVaR/coherent risk attributed to Artzner et al. with `10.1007/s007800050008` | That DOI is **Föllmer & Leukert (2000)**, "Efficient hedging: cost versus shortfall risk", *Finance and Stochastics* 4:117-146. Artzner, Delbaen, Eber & Heath (1999) is `10.1111/1467-9965.00068` | Corrected |
| 4 | `sim/analytics.py` | Momentum/reversal attributed to Jegadeesh & Titman with `10.1111/j.1540-6261.1993.tb05127.x` | That DOI is **Engle & Ng (1993)**, "Measuring and Testing the Impact of News on Volatility", *Journal of Finance* 48:1749-1778. Jegadeesh & Titman (1993) is `10.1111/j.1540-6261.1993.tb04681.x` | Corrected |
| 5 | four files | Almgren et al. (2005) cited as "Almgren, Thum, Hauptmann & **Pacold**", *Risk* 18(7) pages given as both 57-62 and 105-111, with DOI `10.1088/1469-7688/5/8/005` and a `risk.net` link | The fourth author is **Li**; the article is *Risk* **18(7):58-62**; it is a *Risk* magazine piece, and `10.1088/1469-7688/5/8/005` is a *Quantitative Finance* article. The `risk.net` URL returns "we can't seem to find the page you're looking for" (checked 2026-09-17) and the author-hosted PDF (`courant.nyu.edu`, redirects to `cims.nyu.edu`) returns **HTTP 403** | Author list and pages corrected everywhere; the bogus DOI and the dead link removed; the framework is attributed to **Almgren & Chriss (2001)**, *Journal of Risk* 3(2):21-40, `10.21314/JOR.2001.041` |
| 6 | `sim/strategies.py` | PEAD cited with `10.1016/0165-4101(89)90015-X`; illiquidity premium cited with `10.1016/S1386-4181(02)00011-7`; Ball & Brown cited with `10.2307/2328046` | The JAE DOI does not exist in the 1989 volume listings (the nearest, `...90015-3`, is Bonnier & Bruner on management change in distressed firms); the Elsevier DOI could not be confirmed as Amihud (2002); Ball & Brown (1968), *JAR* 6(2):159-178, is **`10.2307/2490232`** | PEAD now cites Bernard & Thomas (1989) `10.2307/2490899` and Ball & Brown (1968) `10.2307/2490232`; Amihud (2002), *Journal of Financial Markets* 5(1):31-56, is cited with a working copy of the paper at `https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf` |

The audit is now a test, not a memory: `test_no_code_cites_an_unregistered_url`
requires every `http(s)` URL in `sim/` and `scripts/` to appear in the source
register, the provider catalogue, an `academic_basis` entry or a research
register, and `test_no_doi_in_the_code_is_misattributed` requires every known DOI
to sit next to the author it belongs to.

### Model-fidelity finding from the same audit (**IR-26**)

Almgren, Thum, Hauptmann & Li (2005) fitted ~700,000 Citigroup US equity orders
and **rejected the square-root model for temporary impact** in favour of a **3/5
power law**. This project's impact model uses the square-root form (the
convention from Almgren & Chriss 2001 and from every vendor model), which at 10%
of ADV charges about **26% more** impact than the measured 0.6 exponent
(0.316 vs 0.251 sigma-units). The deviation is documented rather than silently
absorbed, and making the exponent a config field is in
`research/REMAINING_WORK.json`.

---

## 4b. Defects found in this project's own code by the same pass

The audit was not only about citations. Checking the venue against the rule it
claims to implement found one real defect, recorded as **IR-28**:

`ExecutionEngine.quote_at()` builds the dealer ladder on the minimum-increment
grid and then re-centres it on the permanently-impacted mid so quotes track the
intraday path. The re-centring added an **arbitrary fraction** to every level, so
the displayed bid and ask came off the grid - the recorded opening quote for SPY
on 2025-09-17 was `bid 655.4003 / ask 655.4103`: a legal one-tick spread at two
prices that 17 CFR 242.612(b) does not permit anyone to display. It affected
8,462 quote levels and 5,902 bid/ask fields on fill rows in the primary run.
Executed fill prices were unaffected (the fill engine rounds separately), which
is why the P&L was never wrong - only the published quotes were.

The existing unit test missed it because it called `MarketMakerPool.quotes()`
directly, which has always been grid-clean; the defect was one level up, and no
test crossed that boundary. Fix: snap the shift to a whole number of increments
(`round((mid - raw_mid) / tick) * tick`), which keeps the ladder within half a
tick of the impacted mid, leaves the spread at exactly `n_ticks`, and cannot
cross the book. Two tests now hold the line - one on the venue code across every
symbol and three intraday intervals, one on the prices actually **written to
memory** - and the season was re-run and the site rebuilt afterwards, so the
numbers published here are post-fix.

**What the re-run cost, measured (IR-29).** Measured twice, because the first
measurement was wrong - see §4c. With `PYTHONHASHSEED` pinned to 0 on both sides
so that the tick snapping is the *only* difference between the two runs, a change
of at most half a tick produced:

| Participant | off-grid | on-grid | Δ pp | closed trades |
|---|---|---|---|---|
| `@OverreactionFade_LT` | +40.58% | +73.46% | **+32.88** | 67 → 70 |
| `@OneBigBet_Concentra` | +20.63% | +48.94% | **+28.30** | 36 → 55 |
| `@MeanRev_Z2Sigma` | +65.48% | +74.85% | +9.37 | 53 → 53 |
| `@IlliquidRocket_Degen` | +23.15% | +15.80% | −7.35 | 16 → 13 |
| `@SqueezeHunter_TF` | +3.80% | −1.85% | −5.65 | 32 → 30 |
| `@DriftRider_PEAD` | +15.91% | +21.03% | +5.12 | 41 → 40 |
| `@KitchenSink_AllIn` | +9.52% | +5.19% | −4.33 | 357 → 354 |
| `@BetaChaser_3xProxy` (winner) | +108.31% | +108.39% | +0.08 | 66 → 66 |

Ten of the twenty participants were unchanged to within 0.05pp, ten of twenty
changed rank, and the mean absolute shift was **4.67pp**. One published verdict
flipped: `@SqueezeHunter_TF` went from "made money but lagged the index" to "lost
money". The mechanism is discrete: whether a resting limit order is marketable
depends on which side of the touch it sits, so a fractional shift flips fills on
and off, and because every strategy is path dependent one flipped fill compounds
for the rest of the year. Rising trade counts are the tell - these are different
trades, not the same trades at slightly different prices. That is recorded as
**IR-29** rather than smoothed over, because it means a single-path ranking is
not a skill ordering; the six-scenario panel and the sensitivity harness in
`research/REMAINING_WORK.json` are the answer to it. `tests/fixtures.py` now pins
the published headline in one place, with a change history, so the next engine
change shows up as one explicit diff.

## 4c. The published season was not reproducible (**IR-30**, high severity)

The CI gate added in this same pass failed on its first run, and it was right to.
Re-running the primary seed in a fresh process produced a **different leaderboard**
from the one committed to `memory/` and published on the site.

Three strategies - `@DriftRider_PEAD`, `@SqueezeHunter_TF`,
`@OverreactionFade_LT` - built their held-position collection as a **set**
comprehension and then looped over it to build their exit list:

```python
held = {s for s in ctx.symbols if ctx.position(s)}   # a set
for s in held:                                       # salted order
    ...
    exits.append(s)
```

CPython salts string hashing per process (`PYTHONHASHSEED`), so set iteration
order differs between interpreters. Exit order determines the order sell orders
are submitted, which determines the cash and margin available to the buy orders
later in the same session, which determines which entries fill - and because
every strategy is path dependent, that difference compounds for a year. Measured
with the pre-fix code, same seed, same config, same machine:

| `PYTHONHASHSEED` | `@OverreactionFade_LT` return | closed trades |
|---|---|---|
| 0 | +73.4587% | 70 |
| 1 | +73.5867% | 70 |
| 2 | **+39.8994%** | 71 |
| 3 | +73.4587% | 70 |

The committed Season 1 had been written under whichever salt that process
happened to get. The in-process determinism test that had been passing all along
**could not catch this by construction**: everything inside one interpreter
shares one hash seed.

Fix: all three loops now iterate `ctx.symbols`, the canonical universe order, and
use the set only for membership. Verified identical across `PYTHONHASHSEED`
0, 1, 2, 3 and 7 (leaderboard SHA-256 `04ff3a17fd441347…` in all five), and a
single-seed run now produces a leaderboard identical to the same seed inside the
six-scenario run - which also cleared an earlier suspicion of cross-scenario
contamination. That suspicion was this bug, not scenario ordering.

Two tests hold the line, both checked against a deliberately reintroduced copy of
the bug:

* `test_the_season_is_reproducible_across_processes` runs the short season in two
  subprocesses with different hash seeds and requires identical leaderboards.
* `test_no_strategy_iterates_a_set` is an AST sweep over `sim/strategies.py` that
  fails on any `for` loop over a name bound to a set comprehension or set
  literal. Dicts are insertion-ordered and remain safe to iterate; the sweep does
  not flag them.

**Why this entry is high severity and the others are not.** IR-28 published
quotes no exchange could display, which is a fidelity defect. IR-30 broke the
project's central claim - that the memory is an audit trail someone can re-run -
and it was invisible locally. Every number in §4b of this log was re-measured
after the fix, with hash seeds pinned, and the first (confounded) version of
IR-29 is preserved in the register text rather than quietly replaced, because
"we published a plausible number that turned out to be an artefact" is exactly
the failure mode this log exists to record.

## 4d. Link health of the register itself (**IR-33**, found 2026-09-17)

The register is the deliverable a reviewer actually clicks, so every URL in
`sim/config.py`'s verified-source register and every `links[]` entry in
`research/IRREGULARITIES.json` was re-opened in this pass. Two official URLs
returned 404 while the substance of the claims they carried was correct:

| Cited | Status today | Replacement, fetched and read |
|---|---|---|
| `finra.org/rules-guidance/rulebooks/finra-rules/7541` (Trade Activity Fee rate) | **404** - there is no Rule 7541 | [`finra.org/rules-guidance/guidance/trading-activity-fee`](https://www.finra.org/rules-guidance/guidance/trading-activity-fee), which defers rates to Section 1 of Schedule A |
| `finra.org/investors/learn-to-invest/types-investments/margin-investing/pattern-day-trader` | **404** - the investor-education section was restructured | [`finra.org/rules-guidance/notices/21-13`](https://www.finra.org/rules-guidance/notices/21-13) for the counting rule and [`/investors/insights/frequent-intraday-trading`](https://www.finra.org/investors/insights/frequent-intraday-trading) for the plain-language version |
| `finra.org/rules-guidance/rulebooks/finra-rules/4337` (used in a first draft of IR-31) | **404** - the securities-lending rule is 4330 | [`finra.org/rules-guidance/rulebooks/finra-rules/4330`](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4330), paragraph (b)(2)(B)(ii)(g) |

Fetched live in this pass and excerpted to `data/real/regulatory/`, with the
quotation that carries the weight recorded in the file:

| Source | What it settles |
|---|---|
| [FINRA Rule 4330](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4330) | a borrower of customer securities must disclose "payments deemed cash-in-lieu of dividend paid on securities while on loan" - the obligation IR-31 implements |
| [FINRA Rule 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210) | the margin rule body; (f)(8)(B) is the day-trading section the venue's counter implements |
| [FINRA Regulatory Notice 21-13](https://www.finra.org/rules-guidance/notices/21-13) | the day-trade count, with six worked examples, quoted verbatim in `data/real/regulatory/finra-regulatory-notice-21-13.txt` and asserted one by one in `tests/test_portfolio.py::TestFinraDayTradeExamples` |
| [IRS Publication 550](https://www.irs.gov/publications/p550) | a short seller "may have to remit to the lender payments in lieu of the dividends distributed while you maintain your short position" - corroboration from the tax side that the short **pays** |
| [Federal Register API, document 2026-04233](https://www.federalregister.gov/api/v1/documents/2026-04233.json) | FY2026 Section 31 order: citation `91 FR 10643`, published `2026-03-04`, and **`effective_on` empty** - the date is in the prose, not the structured fields |

The Section 31 effective date was re-checked because a broker fee page disagrees
with it: the FY2025 advisory states the old rate runs "until 60 calendar days
after legislation is enacted that sets the amount of the Commission's fiscal
year 2026 appropriation", the FY2026 appropriation was signed 2026-02-03, and
2026-02-03 + 60 calendar days = **2026-04-04**, which is the date
`config.SEC31_PER_MILLION` uses. One vendor page says 04/02/2026; the order and
the arithmetic agree with each other, so the repo is right and the vendor is
approximating. `docs/sources.html` now renders 61 rows (55 source, 6 provider).

## 4e. Two accounting errors and one fidelity error, found by reading, not by testing (**IR-31**, **IR-32**, **IR-34**)

All three were found in a pass that read `sim/portfolio.py`, `sim/engine.py` and
`sim/strategies.py` line by line against the rules they claim to implement. None
of them made a test red - which is the point worth recording, because it means
this project's test suite, as strong as it is on identities, does not detect
semantic errors that leave the arithmetic self-consistent.

| Defect | What was wrong | Effect on the published season | Guard added |
|---|---|---|---|
| **IR-31** dividend entitlement | ex-date dividends were paid on the position left *after* the day's trades | 4 payments totalling $635.91 went to positions opened on the ex-date itself; 18 of 202 payment events fell on a day the symbol was also traded | `test_dividend_entitlement_is_the_position_carried_into_the_ex_date`, `test_dividend_events_match_the_entitlement_recorded_at_the_open` |
| **IR-31** payment in lieu | `pay_dividend()` returned `0.0` for any non-positive quantity, so shorts never paid the dividend they owe | $1,852.31 of manufactured dividends now charged; shorts had been collecting a free tailwind equal to roughly the yield, because `div_drag` removes that yield from the drift | `test_short_position_owes_a_manufactured_dividend`, plus the ledger-closure test now sums every bucket in `analytics.DECOMPOSITION_BUCKETS` |
| **IR-32** day-trade counter | `_day_trade_closes()` was a stub returning `True`, so *any* second fill of a session counted | zero P&L effect (proved: with the dividend fix reverted and this fix kept, the leaderboard reproduced the pre-fix committed bytes exactly), but `@OvernightCarry_NO` - a strategy that never round-trips intraday - was published with 235 "day trades" in 251 sessions | the six Notice 21-13 examples, asserted verbatim |
| **IR-34** fidelity | the venue could only execute at the opening bell, so a documented "market-on-close" exit was unexpressible and silently became a next-open exit | `@OvernightCarry_NO` +14.11% → **+8.97%**, `@GapAndGo_YOLO` −86.62% → **−91.75%**; GapAndGo's dividends received went from $6.95 to exactly **$0.00**, which is what a book that is genuinely flat by the close must earn | `test_at_close_orders_are_worked_at_the_final_interval`, `test_gap_and_go_exits_later_in_the_same_session_it_entered`, `test_only_the_close_of_session_strategies_use_the_ticket` |

The corrections were **not** applied by editing the published table. Each fix was
followed by a full re-run of all six scenarios, a rebuild of `docs/`, and a
re-derivation of the README table from `memory/runs/.../leaderboard.json` by
script - `tests/test_readme_claims.py` now compares that table cell by cell
against the memory it came from, and checks the README's own counts (test total,
register size, three research file sizes) against the files, because three
earlier passes had each left a hand-typed number stale.

Every one of these fixes was verified to be a real guard by reverting it in place
and watching the corresponding tests fail, then restoring from a backup copy.
That is the only way to know a new test tests something.

## 5. What could **not** be verified here


These are not omissions to paper over; each is flagged in
`research/IRREGULARITIES.json` and `research/LIMITATIONS.json` and shown on the
site.

1. **An eligible official individual-security price set** - the Nasdaq endpoint
   was retrieved through page-fetch and an Actions adapter is implemented, but no
   official files are committed here and Nasdaq's public-site terms do not establish
   redistribution permission. The strict gate reports `NOT ELIGIBLE` rather than
   treating Yahoo agreement as proof.
2. **Real intraday quotes, trades and depth** for the 17-name universe - no
   consolidated tape access; the venue is a calibrated simulation (**IR-03**,
   **L-01**).
3. **Real earnings calendars and EPS surprises** - not available offline, so the
   PEAD participant uses a price/volume proxy (**IR-07**).
4. **Real short-borrow rates and locate availability** - declared per instrument
   (**IR-08**).
5. **Dividend schedules** for 12 of the 17 names - declared, not observed
   (**IR-09**); SPY and AAPL dividends are real.
6. **Style-factor premia** - the six factor returns are *declared priors*
   re-simulated on the real index path, not measured from a real cross-section
   (**IR-16**).
7. **The DOI for Almgren & Chriss (2001)** (`10.21314/JOR.2001.041`) came from a
   reference list; resolving it from this environment returned an HTTP 500, so
   that row is `KNOWN-NOT-FETCHED` rather than `FETCHED`.

---

## 4f. The register's own measured figures were re-derived, and two were wrong

This pass did not only audit the code and the citations: every dollar and
percentage point quoted in `research/IRREGULARITIES.json` was recomputed from the
event streams of the season it describes, which is the only way a measured claim
stays checkable. Three did not survive.

| Claim as written | What the data says | Disposition |
|---|---|---|
| IR-31: "4 dividend payments totalling **$635.91** went to positions opened on the ex-date" | **not reproducible** under any definition tried (first trade of the day: 1 event, $14.46; flat at the prior close: 3 events, $92.02). The number apparently came from an intermediate diagnostic whose filter was never recorded | Replaced by the derivation now in the entry - 18 of 202 events mispriced against the entitlement carried into the ex-date, 11 overpayments $1,198.01, 7 underpayments $962.48, $2,160.49 gross, **-$235.53 net**, 7 of 20 accounts - stated with its method so a reviewer can re-run it |
| IR-31: dividends moved "$11,299.61 → **$11,662.74**", net **$1,489.18** | those are the figures for the run **before** IR-34 re-timed two books. The published season is $11,299.61 → **$11,600.83** received, **$1,852.31** charged in lieu, net **$1,551.09** | Both kept, with the attribution spelled out ($1,489.18 of it is this fix alone), because silently re-labelling a stale number as current is how a register becomes fiction |
| IR-34: "no rank changed", and GapAndGo's dividends "$**6.95** → $0.00" | 6 of 20 ranks moved (4-7 and 11-12; three of them purely because *other* accounts changed), and GapAndGo's pre-fix figure on the originally released season is **$80.46**, not $6.95, which was the intermediate run | Corrected in place |

The pattern is worth naming, because it is not a code problem. Every one of these
was true of a run that existed at the moment it was written and stopped being true
when a later fix re-ran the season. A measured claim in a register is a claim
about a specific artefact, so the register entries now name the season they were
measured on, and `tests/test_readme_claims.py` exists so that the human-readable
restatement of those numbers in `README.md` cannot drift from the memory again.

## 4g. The published site's own provenance line, checked after it went live (**IR-35**)

PR #3 merged, CI was green, and the live Pages deployment was read as a human
would read it. Its footer said `git commit 71b3d9062d2c` next to a season that
commit could not have produced: the memory in the repository had been generated
from a working tree holding the then-uncommitted IR-31/IR-32/IR-34 fixes. The
manifest recorded `code.git.dirty = true` and a SHA-256 for every `sim/` module,
so the data was honest; nothing a reader sees said so. Checked out literally,
that commit reproduces the *pre-fix* season, and GapAndGo's published **−91.75%**
would have looked fabricated next to its **−86.57%**.

Three fixes followed, then one verification:

| Change | File |
|---|---|
| The footer renders all three states of `dirty` and names the module hashes as the fingerprint when a commit cannot be trusted | `scripts/build_site.py` (`provenance_sentence`) |
| Provenance is read from the module's own tree, not the parent of the memory root - a scratch `--memory-root` used to resolve to `/tmp` and find nothing | `sim/memory.py` (`source_root`) |
| `dirty` is `True`/`False`/`None`; a failed git call no longer reads as a clean tree | `sim/memory.py` (`_git_state`) |
| Four tests, one of which compares `docs/index.html`'s footer against the committed manifest. It was red before the season was regenerated, which is what makes it a test and not a description | `tests/test_site_builder.py`, `tests/test_memory.py` |

Verification: all six scenarios were re-run from the clean committed tree into a
scratch root. `leaderboard.json` and `market_report.json` came out **byte-identical**
to the published files, so the memory now committed names a commit that contains
the code which ran, and the reproducibility claim was exercised on the published
artefact rather than on a copy. One residual limit is stated instead of fixed: a
squash merge means `main` never contains the PR-head commit a manifest names, so
the durable provenance of a run is its per-module hashes, not the commit string.

## 4h. A second sweep: the register's runtime counts were stale too (**IR-13**, **IR-14**, **IR-15**)

§4f re-derived the dollar figures in the register. Reading the deployed
`docs/irregularities.html` afterwards exposed the same failure in the entries that
quantify *runtime* events, which nobody had recomputed because those numbers are
not in the leaderboard and no test compared them with the run:

| Entry | Register said | The published run says | Corrected |
|---|---|---|---|
| IR-13 (a participant blowing up) | `@GapAndGo_YOLO finished at -86.67%` | **−91.75%** | the number had been overtaken by two re-runs |
| IR-14 (pre-trade rejects) | `@SpreadHarvester_MM had 131 such rejections` | **67**, and **115** across the three participants that hit the cap | the season total is now quoted beside the peak |
| IR-15 (size clips) | `195 clips for @SpreadHarvester_MM and 44 for @KitchenSink_AllIn` | **193** and **44**, **259** in total | ditto |
| L-14 (memory scale) | `about 38,000 event rows and 4.3 MB` | **36,597** rows across 8 streams, **4.08 MB** | exact, since it is cheap to be |
| IR-23 (beta/alpha) | `alpha +44.9%/yr` | **+44.08%/yr** | corrected |
| IR-21, IR-22, IR-29 | measured deltas quoted as if current | those were measured on the memory at commit 71b3d90 | each now **names the artefact it was measured on**, because a measured claim about a regenerable run is only meaningful with its run attached |

`tests/test_sources_register.py::TestRuntimeRegisterClaims` closes this class: it
re-aggregates the published run's own `irregularities.json` (the engine stores one
aggregated row per participant and per normalised reason, with the participant
embedded in the message) and requires IR-14 and IR-15 to state that peak and that
total, and requires IR-13 to quote the leaderboard's actual worst return. It is
the guard that would have caught all five stale numbers on the day they went
stale.

What this pass could *not* verify was also recorded rather than dropped: the
pre-fix figures quoted in IR-31 are now recoverable only through git
(`git show 71b3d90:memory/runs/season1-primary-seed20260917/events/carry.jsonl.gz`),
which is written into the entry so a reviewer is not left trying to re-run a
season from a commit whose memory no longer exists.

## 6. Audits run on the simulation itself




These are internal consistency checks, all reproduced by the test suite
(`python3 -m unittest discover -s tests`) and by `python3 -m sim.cli verify`.

| Audit | Result |
|---|---|
| Fitted SPY/index ratio vs real SPY monthly closes | ratio **10.02785**, max absolute error **0.1451%** |
| Simulated vs real SPY monthly high-low range | mean absolute difference **0.503 pp**, worst **0.993 pp**, over 11 months |
| P&L ledger closure per participant | `final_equity - starting_cash - net_pnl_usd` is **exactly $0.00** for all 20 published reports (`scripts/independent_audit.py`); the `tests/test_memory.py` fixture season closes to **$0.42** over 20 participants, worst **$0.10** |
| Final positions | every account **flat** at the season end (forced liquidation costed through the venue) |
| Executed prices vs the Rule 612 tick grid | on the **published season**: **3,078 / 3,078** fills and **8,534 / 8,534** displayed bid/ask levels are exactly on-grid. The smaller fixture season in `tests/test_memory.py` (2,985 fills) checks the same property on a run whose bytes the test regenerates, so both are quoted to avoid a reader mistaking one for the other |
| Decision prices vs executed prices | the decision mark stays unrounded, so the tick cost is charged rather than handed back (`test_the_decision_price_is_deliberately_off_grid`) |
| Memory checksums | every file in every run manifest verified; a deliberately corrupted stream is detected |
| Determinism, in-process | the same seed reproduces the same leaderboard bit for bit; a different seed changes ranks |
| Determinism, **across processes** | the leaderboard hash is identical under `PYTHONHASHSEED` 0, 1, 2, 3 and 7, and CI re-runs the primary season into a scratch memory root and diffs it against the committed one (IR-30) |
| Order-of-iteration audit | AST sweep: no `for` loop in `sim/strategies.py` iterates a set |
| Site freshness | `docs/` is byte-identical to a fresh `build-site` from the committed memory (CI fails if it drifts), and the published config fingerprint equals `CompetitionConfig.fingerprint()` |
| Look-ahead | strategies see only `t-1` and earlier data when deciding (`tests/test_strategies.py`) |
| **Independent audit** | `scripts/independent_audit.py` re-derives every published number from the raw event streams with code that never imports `sim`: 763 checks on the primary season (cash roll-forward, equity identity, fee components per fill, dividend and borrow ledger, tick grid, round-trip counts, win/loss and profit factor, Sharpe, Sortino, max drawdown, beta, leaderboard ranks). **763 / 763 pass.** Proven to bite: inflating one report's return by 5.0 pp and inventing three trades produced 2 failures; deleting one dividend carry row produced a cash drift of $72.02 on 2025-11-20 plus a ledger mismatch; nudging a fill price by 37 hundredths of a cent produced a Rule 612 grid violation |
| README self-description | `tests/test_readme_claims.py` recomputes the suite's test count, the register size and the three research-file counts, and re-reads all 20 leaderboard rows against `memory/runs/season1-primary-seed20260917/leaderboard.json` |

## 7. Official price eligibility pass (2026-09-18)

The official Nasdaq historical endpoint was fetched through the supported page-fetch
path with ISO dates. A representative two-year AAPL request returned `totalRecords:
503` rows with date, open, high, low, close and volume; the same endpoint pattern was
previously observed for SPY, XOM, JPM, GLD and FLUT. The direct local `urllib` path
still failed with a TLS EOF, so the adapter is run by the GitHub Actions collector,
not by pretending the development network succeeded.

The implementation is in `scripts/collect_real_data.py`. For each tradable symbol it
writes the normalized Nasdaq file, the exact raw historical response, a raw SHA-256,
request URL, HTTP status, retrieval timestamp, symbol/date range, and an official
Nasdaq dividend response/status. `sim/eligibility.py` independently checks those
fields, matches the raw checksum to `collection_manifest.json`, validates OHLCV
relationships and volume, requires every FRED session in the requested window, and
requires an explicit accepted redistribution status.

The audit was run against the committed checkout on 2026-09-18:
`python3 -m sim.cli price-audit --symbols AAPL,SPY --start 2024-09-16 --end 2026-09-16`.
It returned non-zero with `MISSING_FILE` for both symbols because no official Nasdaq
files have been downloaded into this checkout. This is intentional fail-closed
behavior. Even after collection, the adapter records `NOT_AUTHORIZED_BY_TERMS` until
Nasdaq redistribution permission is verified; that status cannot authorize a
competition. The existing Yahoo files remain reproducible `SECONDARY` research data
and are not silently replaced.
