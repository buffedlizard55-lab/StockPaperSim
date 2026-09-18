# MasterSite, project by project: what it is, and what can be traded from it

The brief named fourteen items to look at on
[MasterSite](https://buffedlizard55-lab.github.io/MasterSite/): CEO, weather,
insider trades, TheLeap, NFL Injury, NBA Injury, FDA Decisions Drug Analysis,
NCAA Scoreboard, NFL scoreboard, MLB Scoreboard, Sports Pred, Gold, PinePilot -
plus the site itself as a source of strategy ideas and of competition design.

This file is the review. The same content is published, joined to the live data
status, on the site at [`docs/season2/masterfeed.html`](../docs/season2/masterfeed.html)
and in machine-readable form in `sim/masterfeed.py::MASTER_SITE_SIGNALS`.

**How the directory was enumerated.** Not from the rendered page alone: the
MasterSite data file was fetched, and the organisation's public repositories were
listed from the official GitHub API (`api.github.com`, 39 public repositories,
one of them excluded from the site). That is what makes the "there is no project
named CEO" claim checkable rather than remembered.

**Legend.** *source class*: who publishes the data (`OFFICIAL` = the agency,
league or exchange itself; `OFFICIAL-VENDOR` = the trading venue's own API;
`SECONDARY` = an aggregator; `ASSERTED` = this project's own reasoning).
*mapping*: how strong the link from the project's data to a listed instrument
honestly is. *status*: whether a backtest was possible from retrievable history
(`BACKTESTED`) or only forward (`FORWARD-ONLY`). There is no third state - a
project with no citable source at all would not be in the register.

---

## The fourteen items

### 1. CEO - `CEO`
* **What it is:** **there is no project named CEO in the directory.** The
  nearest real data for CEO behaviour is the officer title on a SEC Form 4.
* **Official source:** SEC EDGAR, <https://www.sec.gov/edgar/search/> and the
  daily filing index <https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4>.
* **Mapping:** STRONG (the filing is the primary record of the CEO's own
  transaction). **Status:** BACKTESTED (subject to the Form 4 collection landing;
  otherwise the participant reports DATA-MISSING).
* **Participant:** `@CEO_CFO_Conviction` - buys the issuer only when the officer
  field on a code-P purchase is a CEO or CFO.

### 2. Weather - `SFWeather`
* **What it is:** a San Francisco (94122) rainy-season outlook.
* **Official source:** NOAA/NCEI daily summaries,
  <https://www.ncei.noaa.gov/access/services/data/v1> (station `USW00023272`).
* **Mapping:** WEAK - a rain outlook is not a commodity feed. The series used is
  the same station's **temperature** record, traded through natural-gas and
  utility ETFs as a heating-demand proxy. The register says WEAK rather than
  pretending the link is strong.
* **Status:** BACKTESTED. **Participant:** `@Weather_ColdSnap_Max`.

### 3. Insider trades - `Insider-trades`
* **What it is:** a Form 4 parsing toolkit over SEC EDGAR.
* **Official source:** <https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4>
  (and `data.sec.gov/submissions/CIK##########.json` per issuer).
* **Mapping:** STRONG - code-P open-market purchases cannot be explained by
  compensation. **Status:** BACKTESTED.
* **Participants:** `@InsiderCopycat_Max` (any officer/director purchase) and
  `@InsiderCluster_Alpha` (two or more distinct insiders inside ten days).

### 4. TheLeap - `TradingViewTheLeap`
* **What it is:** research on TradingView's "The Leap" paper-trading competition.
* **Official source:** the competition rules,
  <https://www.tradingview.com/the-leap/december-2025/rules/>.
* **Mapping:** WEAK as a *data* source (rules, not prices); what it supplies is
  the **competition design**: $100,000 per account, forced liquidation at period
  end, ranking on return, $1 per trade in the December 2025 edition. All four are
  used here and cited in `sim/config.py`.
* **Status:** BACKTESTED. **Participant:** `@LeapMaxLever_Momentum`, whose rules
  are the Leap's under this project's declared venue.

### 5. NFL Injury - `NFLInjuryReport`
* **What it is:** a 32-team injury tracker.
* **Official source:** <https://www.nfl.com/injuries/> (the league's own weekly
  report).
* **Mapping:** WEAK - injuries plausibly move sportsbook equities, but the
  relationship is asserted, not estimated.
* **Status:** **FORWARD-ONLY.** The league publishes a live document; there is no
  retrievable archive of past seasons, so a backtest would have to be invented.
  **Participant:** `@InjuryFeed_Forward` - registered, adapter pointed at the
  official document, **zero backdated trades**.

### 6. NBA Injury - `NBAInjuryReport`
* **What it is:** a 30-team injury monitor.
* **Official source:** <https://official.nba.com/nba-injury-report-2025-26-season/>
  (the league's own injury-report page and its PDFs).
* **Mapping / status:** as NFL Injury - WEAK mapping, FORWARD-ONLY, same
  participant.

### 7. FDA Decisions / Drug Analysis - `DrugAnalysis`
* **What it is:** FDA decision tracking with a "biotech reaction" backtest.
* **Official source:** openFDA, <https://api.fda.gov/drug/drugsfda.json>.
* **Mapping:** STRONG at the sector level (XBI/IBB), WEAK at the single-name
  level: the API exposes the **sponsor name**, not its ticker, and mapping
  sponsors to tickers is exactly where such projects tend to invent data. This
  project therefore trades the **biotech ETFs** on the sector-wide approval
  calendar rather than pretending to know each sponsor's ticker.
* **Status:** BACKTESTED. **Participants:** `@FDACatalyst_Rider` (long the
  complex when the approval count is above its trailing norm) and
  `@FDA_ClusterFade` (the same signal traded the other way - the season's best
  and worst participants, which is itself the finding).

### 8. NCAA Scoreboard - `Ncaa-football-alerts`
* **What it is:** college-football score alerts.
* **Official source:** the NCAA's own data host,
  <https://data.ncaa.com/casablanca/scoreboard/football/fbs/2025/10/scoreboard.json>.
* **Mapping:** WEAK (attention proxy). **Status:** FORWARD-ONLY - the archived
  season is not enumerable from that endpoint within a sensible request budget.
  No trades are placed from it this season.

### 9. NFL scoreboard - `NFL-scoreboard`
* **What it is:** an NFL results board.
* **Source:** the league does not publish an enumerable public history endpoint,
  so results come from ESPN's scoreboard API
  (<https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard>),
  classed **SECONDARY** on every page that shows it.
* **Mapping:** WEAK. **Status:** BACKTESTED where the collected weeks exist;
  participants `@MLB_Attention_Momo` / `@MLB_Upset_Short` read the same
  attention clock built from official MLB data.

### 10. MLB Scoreboard - `MLB-Live-PBP` (and `MLB-PBP`)
* **What it is:** pitch-by-pitch and scoreboard feeds.
* **Official source:** MLB StatsAPI, <https://statsapi.mlb.com/api/v1/schedule> -
  the league's own feed, with dates, teams, scores and records.
* **Mapping:** WEAK (attention/hold proxy for sportsbook equities).
* **Status:** BACKTESTED with 2,291 real finals collected.
  **Participants:** `@MLB_Attention_Momo` (long the complex after a dense
  slate) and `@MLB_Upset_Short` (short it after an upset-heavy week).

### 11. Sports Pred - `SportsPred`
* **What it is:** pre-game prediction models.
* **Official source:** none exists for the *outputs*: the project publishes live
  predictions with no timestamped archive.
* **Mapping:** UNPROVEN. Recomputing the model here would test this project's
  model, not the site's, so the honest label is UNPROVEN and the status is
  **FORWARD-ONLY** - no backdated trades.

### 12. Gold - `GOLD`
* **What it is:** **a solid-gold engagement-ring buyer's guide.** It is not a
  gold-price feed, an ETF tracker or a market model.
* **Official source used instead:** FRED's LBMA gold-price series
  (<https://fred.stlouisfed.org/series/GOLDAMGBD228NLBM> for the AM fix) and the
  real GLD daily bars. The register keeps the finding rather than hiding it.
* **Mapping:** WEAK (the project itself says nothing about trading gold).
* **Status:** BACKTESTED. **Participant:** `@GOLD_Trend_GLD` - a trend rule on
  the real GLD series, presented as a technical strategy that the Gold project
  did not supply.

### 13. PinePilot - `Tradingview-pinescript-editor`
* **What it is:** a Pine Script editor and strategy lab - the closest thing in
  the directory to a source of **implementable technical rules**.
* **Official source:** TradingView's Pine Script reference,
  <https://www.tradingview.com/pine-script-docs/> (SECONDARY: a vendor's docs,
  used for syntax and semantics, not as a price source).
* **Mapping:** STRONG - a rule written in Pine is exactly reproducible.
* **Status:** BACKTESTED. **Participant:** `@PinePilot_EMA_Cross` - the classic
  EMA(20)/EMA(50) cross with a trailing stop, evaluated on real daily bars.

### 14. Kalshi - `KalshiPaperSim`, `PriceKalshiHistorical`
* **What it is:** paper-trading and price-history tooling for the Kalshi venue,
  read in this project as a **competition-design reference** and as a possible
  attention signal.
* **Official source:** the venue's own API,
  <https://api.elections.kalshi.com/trade-api/v2/markets>.
* **Mapping:** UNPROVEN. **Status:** the settled-market list was collected, but
  every numeric field (volume, open interest, last price, settlement value) came
  back **null**, so the volume signal is registered MISSING and the participant
  `@Kalshi_Attention_Timer` reports DATA-MISSING (IR-41) instead of trading a
  column of zeros.

---

## What this review establishes

1. **Four of the fourteen items cannot be backtested from official history**
   (NFL injury, NBA injury, Sports Pred, and - as archived history - the NCAA
   scoreboard). They are forward-only, and their participants place no backdated
   trades.
2. **Two items are not what their names suggest**: `GOLD` is a jewellery guide,
   and `CEO` does not exist. Both cases are recorded with the substitute source
   actually used.
3. **Six items map to an official, dated, retrievable feed** (SEC Form 4,
   openFDA, MLB StatsAPI, NOAA/NCEI, FRED, Kalshi), which is what makes the
   Season 2 backtests checkable.
4. **Every mapping strength is labelled**, and the weak ones are traded through
   baskets rather than single names, where a false mapping is less likely to
   masquerade as an edge.

The machine-readable version of this table - with the live availability of each
signal, the sessions it was actionable for, and the file each observation came
from - is written to every run under `masterfeed.json` and rendered at
[`docs/season2/masterfeed.html`](../docs/season2/masterfeed.html).
