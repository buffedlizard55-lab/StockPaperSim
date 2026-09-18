# Strategies that circulate on Reddit, YouTube, X and the trading forums

The brief asked for two things that are easy to conflate:

1. **What strategies do people actually trade and post about?** - a survey, which
   can be done by reading the communities.
2. **Do they work?** - a question the communities are not the right source for.
   A strategy that is popular is not evidence that it pays, and the honest way to
   answer (2) is to look for the primary research and then test the rule here,
   on real prices, with the same ledger every other participant gets.

So this file lists each strategy family that circulates in the communities, the
**primary or peer-reviewed source** that speaks to whether it works, and - for
the ones this project implemented - which username trades it and what the season
actually measured. Where the honest answer is "the evidence is against it", the
strategy is still in the roster (the brief is to measure, not to curate) and the
verdict column says so.

Nothing in this file is a trading recommendation, and none of the community
claims are repeated as fact. Every row has a link a reader can follow.

---

## 1. The families, and what the evidence says

| # | Strategy family (as posted) | Where it circulates | Primary / peer-reviewed evidence | Verdict on the evidence |
|---|---|---|---|---|
| S1 | **Follow the crowd** - copy the most-mentioned tickers | r/wallstreetbets, r/algotrading, X | Chacon, Morillon & Wang, *Will the Reddit rebellion take you to the moon?*, Financial Markets and Portfolio Management 37 (2023) 10.1057/s11369-023-00297-4 | Long-short portfolios formed on WSB recommendations **failed to produce alpha distinguishable from zero** across holding periods; the long leg was negative. Following the crowd is not an edge. |
| S2 | **Fade the crowd** ("inverse WSB") | same communities | Long, Lüdtke, Nolte & Zhou, *"I just like the stock": The role of Reddit sentiment in the GameStop share rally*, Financial Review 2023 10.1111/fire.12328 | Reddit sentiment had **real but limited** intraday impact on one name and was **weak in falling markets**. Fading a crowd requires knowing the crowd's horizon, which is minutes. |
| S3 | **Social sentiment as a signal** | r/algotrading, Medium, YouTube | *Robinhood, Reddit, and the news* (2024) 10.1016/j.jbef.2024.100937 | Posts move **next-day retail buying**, and the effect decays within days. Tradable only with data and execution far faster than a daily simulator. |
| S4 | **Opening-range breakout (ORB)**, "first 5 minutes decide the day" | YouTube, r/daytrading | Zarattini & Aziz, *Can Day Trading Really Be Profitable?* (2023, SSRN 4416622); Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy for the U.S. Equity Market* (2024) | A plain ORB on QQQ showed a large **annualised alpha (33%, net of commissions)** in-sample 2016-2023; the broader cross-sectional study found an unfiltered ORB weak and the edge concentrated in **abnormally active** names. Not replicable here: it needs intraday bars, and the paper itself stresses leverage/margin constraints. |
| S5 | **Momentum / trend following** ("buy strength, ride the trend") | everywhere | Jegadeesh & Titman (1993) 10.1111/j.1540-6261.1993.tb04702.x; Moskowitz, Ooi & Pedersen (2012) 10.1016/j.jfineco.2011.11.003 | The best-documented cross-sectional and time-series anomaly there is; it also has long documented crash episodes. Implemented here (see S5 rows below). |
| S6 | **Post-earnings-announcement drift (PEAD)** | r/algotrading, quant blogs | Bernard & Thomas (1989) 10.2307/2491062 | Prices drift in the direction of the earnings surprise for weeks. Well documented, badly crowded since the 2000s. |
| S7 | **Short-term reversal** ("buy the dip", RSI-2) | YouTube, r/options | Jegadeesh (1990) 10.1111/j.1540-6261.1990.tb05110.x; Lehmann (1990) 10.2307/2937816 | Robust at daily/weekly horizons; fragile after costs, which is exactly what a cost-modelled simulator can measure. |
| S8 | **Low-volatility / "boring is better"** | Reddit, Bogleheads-adjacent | Ang, Hodrick, Xing & Zhang (2006) 10.1111/j.1540-6261.2006.00836.x | The low-vol anomaly is real but it *reduces* return per unit of risk - the opposite of this brief's objective, so it appears only as a factor, never as a participant. |
| S9 | **Leveraged ETFs / 3x proxies** ("max leverage") | YouTube, X, WSB | FINRA Reg T margin rules (initial 50%, maintenance 25%: https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210); ETFs' own prospectuses for daily-reset decay | The brief asks for the highest returns, so leverage is modelled properly (Reg T, maintenance, margin calls, borrow fees) rather than forbidden. Decay is then a measured result, not an opinion. |
| S10 | **Day trading as a business** | YouTube, courses | Barber, Lee, Liu & Odean (2014), *The cross-section of speculator skill*, Journal of Financial Markets 10.1016/j.finmar.2013.05.001; Barber & Odean (2000) 10.1111/0022-1082.00246 | Roughly 1% of day traders predictably profit over a year; active retail underperforms after costs. The roster includes a deliberately aggressive day-trading participant so the claim can be measured here. |
| S11 | **Insider following** ("copy the CEO's buys") | Reddit, X, financial press | SEC Form 4 filings (primary record: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4) | The filings are the primary data; the *edge* depends on what you can do with the code-P purchases. Implemented as two participants. |
| S12 | **Event/attention trading** (sports calendars, weather, prediction markets) | finserv newsletters, Kalshi/Discord | NOAA/NCEI station records (https://www.ncei.noaa.gov/access/services/data/v1), MLB StatsAPI (https://statsapi.mlb.com/api/v1/schedule), Kalshi API (https://api.elections.kalshi.com/trade-api/v2/markets) | The *data* is official and dated; the mapping from the event to a listed instrument is the weak link and is labelled as such in the MasterFeed register. |

## 2. What this project did with each family

Season 1 (calibrated replay) and Season 2 (real collected prices) between them
implement the families above as 34 participants with unique usernames. The
mapping is deliberate: a family without a defensible instrument mapping is either
implemented with the mapping labelled WEAK/UNPROVEN or excluded and said so.

| Family | Username(s) | Season | What the season measured |
|---|---|---|---|
| S1/S2 crowd following/fading | `@MomentumMax_12x1`, `@OverreactionFade_LT`, `@GapAndGo_YOLO` | 1 | Momentum and reversal premia paid; the fade and the gap-and-go day trader lost after costs. |
| S3 sentiment | `@Kalshi_Attention_Timer` | 2 | Collects the venue's settled-market list as an attention clock; the payload's volume fields came back null, so the participant reports **DATA-MISSING** instead of trading a zero column (IR-41). |
| S4 opening-range breakout | not implemented | - | Requires intraday bars; the venue model has one decision point per session, so an ORB would be a different simulation. Listed in REMAINING_WORK as a P2 item, not faked. |
| S5 momentum/trend | `@MomentumMax_12x1`, `@TrendSurfer_GoldenX`, `@DonchianBreakout_20`, `@SectorRotator_AlphaX`, `@LeapMaxLever_Momentum`, `@PinePilot_EMA_Cross`, `@GOLD_Trend_GLD` | 1+2 | Both seasons: momentum paid on this path, and the levered variants captured most of the return with much larger drawdowns. |
| S6 PEAD | `@DriftRider_PEAD` | 1 | Drift was positive but small relative to the index on this window; costs ate a visible share. |
| S7 reversal | `@MeanRev_Z2Sigma`, `@Contrarian_DeepValue`, `@OverreactionFade_LT`, `@SqueezeHunter_TF` | 1 | The z-score reverter finished third with the smallest drawdown; the deep-value contrarian gave most of its peak back. |
| S8 low-vol | factor only | 1 | Published as a factor, never as a participant: the brief is highest returns. |
| S9 leverage | `@BetaChaser_3xProxy`, `@LeapMaxLever_Momentum`, `@OneBigBet_Concentra`, `@KitchenSink_AllIn` | 1+2 | Leverage dominated ranking in both seasons, and the margin/day-trade machinery fired rather than being assumed away. |
| S10 day trading | `@GapAndGo_YOLO`, `@SpreadHarvester_MM`, `@OvernightCarry_NO` | 1 | The aggressive intraday participant lost 91.8% after costs, consistent with the literature rather than with the clips. |
| S11 insider filing | `@InsiderCopycat_Max`, `@InsiderCluster_Alpha`, `@CEO_CFO_Conviction` | 2 | Implemented and gated on the collected Form 4 file; if the SEC collection does not land, they report DATA-MISSING and trade nothing. |
| S12 event/attention | `@FDACatalyst_Rider`, `@FDA_ClusterFade`, `@MLB_Attention_Momo`, `@MLB_Upset_Short`, `@Weather_ColdSnap_Max`, `@YieldCurve_Rotator` | 2 | Traded on real event dates from official sources; the FDA participants are the season's best and worst, which is the honest result of one signal with two opposite implementations. |

## 3. What the Live Book did with the same families

The Live Book (`docs/live/`, `sim/strategies_live.py`) runs the same families with
one structural difference: a rule plans after the close of session `T` and its order
is settled against the bar of a **later** session. That removes the ability to trade
the price a rule has just read, and it is why several of these results separate from
their Season 2 counterparts.

| Family | Username | What the forward test measured |
|---|---|---|
| S4 opening-range breakout | `@ORB_NextOpen_Probe` | **Not implemented**, declared FORWARD-ONLY with the reason on the page: the rule needs the first thirty minutes and this venue has one decision point per session. Placed no intents, which is the honest outcome. |
| S5 momentum/trend | `@NasdaqMomentum_Max` (+19.00% on 12 trades), `@PinePilot_EMA_Live` (−8.23%), `@LeapStyle_AutoLiquidate` (−35.34%) | The slowest rule in the roster (a 210-session official-index trend) beat the fastest (a 9/21 EMA cross) by 27pp, and the contest-style rotation into the fastest-trailing asset lost a third of the account. Execution delay punished the high-turnover rules hardest. |
| S6 PEAD | not implemented in the live roster | No earnings-date feed exists in the collected data, so the family is absent rather than approximated. It is registered as a P1 gap. |
| S7 reversal | `@CrowdFade_Live` (−45.90%), `@FDA_Fade_Live` (+10.60%) | The crowd-fade rule was liquidated by the brokerage: two maintenance calls, four forced orders, −61.34% peak-to-trough. The FDA fade made money. Reversal is a coin toss here, which is what the literature says about it after costs. |
| S9 leverage | every participant runs at the Reg T bound by design | The account that has to survive it is modelled: 2.0x gross cap, 50% initial margin, 30% house maintenance, liquidation at the next open on a breach. |
| S11 insider filing | `@InsiderCluster_Live` | Implemented, gated, **DATA-MISSING**: the Form 4 stream is not in `data/real/sec/`, and the runner-based collection could not be dispatched from this session (HTTP 403, IR-54). It placed no intents and reports a measurement gap. |
| S12 event/attention | `@MLB_Attention_Live` (+19.46%), `@Weather_ColdSnap_Live` (+1.09%), `@FDA_PDUFA_Drifter` (+65.18%), `@InjuryFeed_Forward` (FORWARD-ONLY) | Season 2's winner repeated: the openFDA approval clock levered into biotech. The weather proxy made almost nothing and the schedule-density proxy made 19%, which is a result about proxies, not about forecasts. |
| New: official macro series | `@DowNasdaq_SpreadMax` (+22.50%), `@SOFRPivot_Rider` (+8.94%), `@VIXRegime_LiveMax` (+8.88%), `@VolControl_MaxLev` (−9.71%), `@CurveSteepener_MaxBeta` (+3.99%), `@OilDollar_FadeUNG` (−17.10%), `@GoldVsRealRate_Live` (−7.50%), `@KitchenSink_Official` (+4.09%) | Eight rules that only became testable once the Nasdaq Composite, Dow Jones and SOFR histories were collected. The relative-strength spread between the two official indices was the best of them; the volatility-scaled maximum-leverage rule lost 9.7%, which is what the literature would predict once the scaling is stripped of the diversification it was designed around. |

## 3. How the community claims were checked

* **The strategies are implemented as declared, before the run.** Each roster
  entry writes its entry rules, exit rules, sizing, leverage, cadence, horizon and
  known failure modes into the run memory, and the site prints them next to the
  result. A post-hoc rule change would be visible as a diff.
* **The return is decomposed.** Every participant page splits the return into the
  market factor, the factor premia the strategy is built to harvest, the residual,
  and the execution costs actually charged, so "it worked" is never asserted
  without the arithmetic.
* **The failure modes are checked against the data.** The narrative library fires
  a clause only on a measured quantity (a drawdown, a cost share, a factor
  return, a trade count), never on the strategy's own priors.
* **Where the community claim cannot be tested, the file says so.** ORB (S4) and
  social-sentiment trading (S3) need intraday or platform data this project does
  not have; they are named in the register and in `REMAINING_WORK.json` instead of
  being approximated by a daily proxy and reported as if it were the same thing.
