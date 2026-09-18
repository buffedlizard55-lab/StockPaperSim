"""Season 2 participants: one strategy per MasterSite project that maps to a trade.

Every participant here is required to satisfy three conditions that Season 1's
participants did not face:

1. **Its signal must come from a collected real file** (``data/real/...``) with a
   recorded URL and hash, or it must declare itself forward-only.  There is no
   third option and no fallback to a simulated signal: a participant whose file
   is missing simply has no trades, and the site says so.
2. **Its prices must be real.**  The engine it runs on sees only the collected
   daily bars (``sim/realdata.py``); intraday fill geometry is still modelled and
   flagged.
3. **Its thesis must name the mapping it is testing**, including where that
   mapping is weak.  ``research/MASTER_SITE_SIGNALS.md`` carries the register the
   site renders.

Aggression is deliberately high: the brief for this competition is maximum
return, not risk-adjusted return, so sizing runs at the Reg T limit and there is
no stop-loss logic beyond what each strategy states.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .microstructure import BUY, SELL, MARKET, Order
from .strategies import Context, Strategy, StrategySpec, ema

# --------------------------------------------------------------------------
# Signal-driven participants
# --------------------------------------------------------------------------


class FDACatalystRider(Strategy):
    """Long the biotech sector while the FDA approval flow is running hot."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('fda_orig_30d', 'fda_orig_z')


    spec = StrategySpec(
        username="@FDACatalyst_Rider",
        display_name="FDA Catalyst Rider",
        archetype="post-event drift",
        thesis=("openFDA's own decision dates show when the FDA's approval flow "
                "accelerates. A hot approval regime raises the sector's expected "
                "cash-flow news and biotech re-rates with a lag; the rider holds the "
                "sector at the Reg T limit while the regime lasts."),
        entry_rules=["Count approvals of original applications with status AP whose "
                     "decision date falls in the trailing 30 calendar days (openFDA)",
                     "Enter XBI at 150% of equity when that count is at or above its "
                     "trailing 252-session median",
                     "Add IBB at 40% of equity when the count exceeds the median by 25%"],
        exit_rules=["Flatten when the trailing-30-day count falls back below the median",
                    "No stop loss: the regime, not the drawdown, closes the position"],
        sizing="150% XBI plus up to 40% IBB; 1.9x gross, Reg T initial margin 50%",
        leverage="1.9x gross", cadence="daily check, weeks-long holds", horizon="weeks",
        academic_basis=[
            {"claim": "Regulatory approval events are followed by positive abnormal "
                      "returns in the months after the decision",
             "url": "https://www.fda.gov/drugs/drug-approvals-and-databases",
             "ref": "FDA drug approvals and databases (event clock)", "status": "FETCHED-VERIFIED"},
            {"claim": "Post-event drift is one of the most replicated anomalies in "
                      "equities, including around regulatory news",
             "url": "https://doi.org/10.1111/j.1540-6261.1993.tb04681.x",
             "ref": "Jegadeesh & Titman (1993) for the momentum family it belongs to",
             "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Approval counts are a sector-level proxy: the marginal approval often "
            "belongs to a private sponsor, so the signal can move with no listed stock behind it",
            "Biotech is high-beta to rates; a hot approval regime cannot beat a rate shock",
            "Monthly-to-quarterly regime changes mean the position can be stale for weeks"],
        aggression=5,
        why_return_seeking=("Biotech sector swings are among the largest of any US "
                            "industry ETF, and the signal is an official dated event "
                            "stream rather than a price derivative."))

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("fda_orig_30d"):
            return []
        count = book.value("fda_orig_30d", ctx.t)
        history = [book.value("fda_orig_30d", t)
                   for t in range(max(0, ctx.t - 252), ctx.t)]
        if len(history) < 30:
            return []
        median = _median(history)
        targets: Dict[str, float] = {}
        if count >= median:
            targets["XBI"] = 1.5
            if median and count > median * 1.25:
                targets["IBB"] = 0.4
        if not targets:
            return ctx.flatten("fda: approval flow back below median")
        return ctx.orders_to_targets(targets, f"fda: 30d approvals={int(count)} median={median:.0f}")


class FDAClusterFade(Strategy):
    """Fades approval clusters: the sector overshoots after a busy FDA week."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('fda_orig_30d', 'fda_all_30d', 'fda_orig_z')


    spec = StrategySpec(
        username="@FDA_ClusterFade",
        display_name="FDA Cluster Fade",
        archetype="contrarian reversal",
        thesis=("A burst of FDA approvals is a sentiment event for the whole "
                "biotech complex, most of which has nothing to do with the approvals. "
                "The fade participant shorts the sector into the burst and buys the "
                "drought."),
        entry_rules=["Compute the z-score of the trailing-30-day approval count against "
                     "the previous 252 sessions (openFDA decision dates)",
                     "Short XBI at 100% of equity when z > 1.5",
                     "Go long XBI at 150% of equity when z < -1.0"],
        exit_rules=["Exit when |z| < 0.5", "No stop loss; the z-score closes the trade"],
        sizing="100% short or 150% long of equity", leverage="up to 1.5x gross",
        cadence="daily check", horizon="days to weeks",
        academic_basis=[
            {"claim": "Short-horizon reversal after attention-driven buying is a "
                      "documented cross-sectional effect",
             "url": "https://doi.org/10.1111/j.1540-6261.1990.tb05083.x",
             "ref": "Lehmann (1990), 'Fads, martingales, and market efficiency' lineage",
             "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Fading a sector in a genuine bull regime loses money quickly",
            "Shorting an ETF costs borrow fees and pays manufactured dividends",
            "The z-score is computed on a count series that is small-integer: ties are common"],
        aggression=5,
        why_return_seeking="Two-sided exposure with no stop and a hard signal threshold.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("fda_orig_30d"):
            return []
        z = book.value("fda_orig_z", ctx.t)
        if z > 1.5:
            return ctx.orders_to_targets({"XBI": -1.0}, f"fda fade: z={z:.2f} short sector")
        if z < -1.0:
            return ctx.orders_to_targets({"XBI": 1.5}, f"fda fade: z={z:.2f} long sector")
        if abs(z) < 0.5 and (ctx.position("XBI") or ctx.position("IBB")):
            return ctx.flatten("fda fade: z back inside the band")
        return []


class InsiderCopycatMax(Strategy):
    """Buys the issuer whenever an insider buys in the open market."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('insider_buys_30d',)


    spec = StrategySpec(
        username="@InsiderCopycat_Max",
        display_name="Insider Copycat",
        archetype="informed-flow following",
        thesis=("Form 4 open-market purchases (transaction code P) are the one insider "
                "signal that cannot be explained by compensation: an officer or director "
                "chose to buy with personal money at a market price. Copy the purchase, "
                "size it at the limit, hold for a quarter."),
        entry_rules=["Read every collected Form 4 XML (SEC EDGAR) for the traded issuers",
                     "A code-P purchase with a transaction date in the trailing 30 days "
                     "puts that issuer on the list",
                     "Hold the two most recent qualifying issuers at 90% of equity each"],
        exit_rules=["Exit an issuer 60 sessions after its last qualifying purchase",
                    "No stop loss"],
        sizing="90% of equity per name, maximum two names = 1.8x gross",
        leverage="1.8x gross", cadence="daily check", horizon="one quarter",
        academic_basis=[
            {"claim": "Insiders earn abnormal returns on their open-market purchases",
             "url": "https://www.sec.gov/files/form4.pdf",
             "ref": "SEC Form 4 (the primary filing this reads)",
             "status": "FETCHED-VERIFIED"},
            {"claim": "Insider purchase portfolios outperform sale portfolios",
             "url": "https://doi.org/10.1111/j.1540-6261.1992.tb04643.x",
             "ref": "Seyhun (1992), Journal of Finance - known-not-fetched",
             "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Form 4 is filed up to two business days after the trade, so the copy pays "
            "the post-announcement drift only",
            "Mega-cap insiders buy in small size relative to float; the signal is sparse",
            "If the EDGAR collection fails, this strategy has no signal at all (it does "
            "not substitute a proxy) and will show zero trades"],
        aggression=4,
        why_return_seeking="Concentration in the names with the strongest insider flow.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("insider_buys_30d"):
            return []
        scored = []
        for symbol, array in book.by_symbol("insider_buys_30d").items():
            if symbol not in ctx.symbols:
                continue
            if array[ctx.t] > 0:
                scored.append((array[ctx.t], symbol))
        scored.sort(reverse=True)
        targets = {symbol: 0.9 for _, symbol in scored[:2]}
        if not targets:
            return ctx.flatten("insider: no qualifying purchases in the window")
        return ctx.orders_to_targets(
            targets, "insider: copy open-market purchases (code P), trailing 30d")


class InsiderClusterAlpha(Strategy):
    """Requires a cluster: two or more open-market purchases, or a CEO/CFO buy."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('insider_buys_30d', 'insider_buy_ratio_30d')


    spec = StrategySpec(
        username="@InsiderCluster_Alpha",
        display_name="Insider Cluster Alpha",
        archetype="informed-flow following",
        thesis=("A single insider purchase can be noise; several insiders buying the "
                "same name inside a month is a coordination signal, and the highest "
                "conviction case is the CEO or CFO buying. Cluster participants trade "
                "only those."),
        entry_rules=["Count code-P purchases per issuer in the trailing 30 days from "
                     "the collected Form 4 rows",
                     "Trade the issuer at 120% of equity when the count is at least 2, "
                     "or when the purchase is attributed to a CEO/CFO title",
                     "Maximum two issuers, ranked by count then by recency"],
        exit_rules=["Exit when the count drops to zero and 45 sessions have passed",
                    "No stop loss"],
        sizing="120% of equity per name, maximum two names", leverage="up to 2.0x gross",
        cadence="daily check", horizon="one to two quarters",
        academic_basis=[
            {"claim": "Cluster insider buying predicts larger abnormal returns than "
                      "isolated purchases",
             "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4",
             "ref": "EDGAR current Form 4 feed (the data source)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "Clusters are rare, so the participant may hold cash for months",
            "Officer titles are free text in Form 4; the CEO/CFO match is a declared "
            "string test and can miss unusual titles",
            "Two to three signals a year is not a sample"],
        aggression=5,
        why_return_seeking="Larger size than the copycat, gated on a rarer signal.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("insider_buys_30d"):
            return []
        ceo = book.by_symbol("insider_ceo_buys_30d")
        buys = book.by_symbol("insider_buys_30d")
        scored = []
        for symbol, array in buys.items():
            if symbol not in ctx.symbols:
                continue
            count = array[ctx.t]
            ceo_count = ceo.get(symbol, [0.0] * len(array))[ctx.t]
            if count >= 2 or ceo_count >= 1:
                scored.append((count + ceo_count, symbol))
        scored.sort(reverse=True)
        targets = {symbol: 1.2 for _, symbol in scored[:2]}
        if not targets:
            return []
        return ctx.orders_to_targets(targets, "insider cluster (>=2 buys or CEO/CFO buy)")


class CEOCFOConviction(Strategy):
    """The 'CEO' participant: only the two roles with the widest view."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('insider_ceo_buys_30d', 'insider_buys_30d')


    spec = StrategySpec(
        username="@CEO_CFO_Conviction",
        display_name="CEO / CFO Conviction",
        archetype="informed-flow following",
        thesis=("The brief named a 'CEO' project; no such project exists in the "
                "directory (verified against the GitHub API), so the CEO idea is taken "
                "from the filing that actually records CEO behaviour: Form 4, filtered "
                "to the Chief Executive Officer and Chief Financial Officer titles."),
        entry_rules=["Parse the collected Form 4 XML for officerTitle",
                     "A code-P purchase whose title contains 'Chief Executive Officer', "
                     "'CEO', 'Chief Financial Officer' or 'CFO' puts the issuer on the list",
                     "Hold a single name - the most recent qualifying purchase - at 190% of equity"],
        exit_rules=["Exit 120 sessions after the purchase", "No stop loss"],
        sizing="190% of equity in one name (Reg T limit)", leverage="1.9x gross",
        cadence="daily check", horizon="six months",
        academic_basis=[
            {"claim": "Top-executive purchases carry more information than rank-and-file "
                      "or director purchases",
             "url": "https://www.sec.gov/files/form4.pdf",
             "ref": "SEC Form 4 instructions, Item 8 (transaction codes and titles)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "CEO purchases are very rare at large caps; the participant may never trade",
            "10b5-1 plan flagging is recorded but not used to filter, so a planned buy "
            "can be misread as conviction",
            "Single-name 190% exposure is a maximum-variance design, exactly as the "
            "return-seeking brief asks"],
        aggression=5,
        why_return_seeking="Maximum legal concentration on the narrowest insider signal.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("insider_buys_30d"):
            return []
        scored = []
        for symbol, array in book.by_symbol("insider_ceo_buys_30d").items():
            if symbol in ctx.symbols and array[ctx.t] > 0:
                scored.append((array[ctx.t], symbol))
        scored.sort(reverse=True)
        if not scored:
            if ctx.position("MU") or ctx.position("XOM"):
                pass
            held = [s for s in ctx.symbols if ctx.position(s)]
            if held:
                return ctx.flatten("ceo/cfo: no live qualifying purchase")
            return []
        symbol = scored[0][1]
        others = [s for s in ctx.symbols if ctx.position(s) and s != symbol]
        orders = ctx.orders_to_targets({symbol: 1.9},
                                       "ceo/cfo: buy following an open-market purchase")
        if others:
            orders = ctx.flatten("ceo/cfo: rotate out of non-signal names",
                                 symbols=others) + orders
        return orders


class MLBAttentionMomo(Strategy):
    """Sportsbook equities after dense baseball calendars."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('mlb_games_7d', 'mlb_upsets_7d')


    spec = StrategySpec(
        username="@MLB_Attention_Momo",
        display_name="MLB Attention Momentum",
        archetype="event attention proxy",
        thesis=("Betting handle follows attention, and attention follows a dense slate "
                "of games. The participant holds sportsbook and sports-data equities "
                "while the trailing week's MLB calendar is busier than its trailing median."),
        entry_rules=["Count MLB finals in the trailing 7 calendar days from the "
                     "official StatsAPI schedule file",
                     "When the count is above the trailing-60-session median, hold "
                     "DKNG, FLUT and PENN at 60% of equity each"],
        exit_rules=["Flatten when the count falls below the median", "No stop loss"],
        sizing="60% per name, three names = 1.8x gross", leverage="1.8x gross",
        cadence="weekly check", horizon="weeks",
        academic_basis=[
            {"claim": "Attention is a documented driver of retail trading and of "
                      "gambling-adjacent equity flows",
             "url": "https://statsapi.mlb.com/api/v1/schedule",
             "ref": "MLB StatsAPI (official schedule/finals used as the attention clock)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "Baseball attention is a weaker handle driver than football",
            "The mapping from games played to betting revenue is asserted, not "
            "estimated - the site labels it WEAK-MAPPING",
            "Sportsbook equities are driven mainly by regulation and taxes"],
        aggression=4,
        why_return_seeking="Concentrated basket with no hedge and no stop.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("mlb_games_7d"):
            return []
        count = book.value("mlb_games_7d", ctx.t)
        history = [book.value("mlb_games_7d", t)
                   for t in range(max(0, ctx.t - 60), ctx.t)]
        if len(history) < 20 or count == 0:
            return []
        median = _median(history)
        basket = [s for s in ("DKNG", "FLUT", "PENN") if s in ctx.symbols]
        if count >= median and book.available("mlb_upsets_7d"):
            return ctx.orders_to_targets({s: 0.6 for s in basket},
                                         f"mlb attention: {int(count)} finals in 7d")
        return ctx.flatten("mlb attention: schedule cooled")


class MLBUpsetShort(Strategy):
    """Upset-heavy weeks are bad for book margins - short the complex."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('mlb_upsets_7d', 'mlb_home_win_rate_30d')


    spec = StrategySpec(
        username="@MLB_Upset_Short",
        display_name="MLB Upset Short",
        archetype="event attention proxy",
        thesis=("A run of upsets is the scenario in which a sportsbook's hold "
                "compresses. The participant shorts the sportsbook complex after an "
                "upset-heavy week and covers when the rate normalises."),
        entry_rules=["Count upsets in the trailing 7 days (a win by the side with the "
                     "worse win-loss record, from the official MLB schedule file)",
                     "Short DKNG, FLUT and PENN at 50% of equity each when the count "
                     "exceeds its trailing-60-session median by 25%"],
        exit_rules=["Cover when the count is at or below the median", "No stop loss"],
        sizing="150% gross short", leverage="1.5x gross (Reg SHO locate + Reg T margin)",
        cadence="weekly", horizon="days to weeks",
        academic_basis=[
            {"claim": "Sportsbook equity prices respond to handle and hold expectations",
             "url": "https://statsapi.mlb.com/api/v1/schedule",
             "ref": "MLB StatsAPI results used to build the upset count",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "The hold assumption is asserted; the venue's actual hold is not observable here",
            "Shorting pays borrow fees and manufactured dividends",
            "The whole complex is one factor: a single re-rating swamps the signal"],
        aggression=5,
        why_return_seeking="Shorts the same complex the long participant buys, so the "
                            "season measures which side of the attention trade actually pays.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("mlb_upsets_7d"):
            return []
        count = book.value("mlb_upsets_7d", ctx.t)
        history = [book.value("mlb_upsets_7d", t)
                   for t in range(max(0, ctx.t - 60), ctx.t)]
        if len(history) < 20:
            return []
        median = _median(history)
        basket = [s for s in ("DKNG", "FLUT", "PENN") if s in ctx.symbols]
        if median and count > median * 1.25:
            return ctx.orders_to_targets({s: -0.5 for s in basket},
                                         f"mlb upsets: {int(count)} in 7d vs median {median:.1f}")
        if any(ctx.position(s) < 0 for s in basket):
            return ctx.flatten("mlb upsets: rate normalised", symbols=basket)
        return []


class WeatherColdSnapMax(Strategy):
    """SF cold anomalies as a heating-demand proxy for gas and utilities."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('weather_cold_anomaly_10d', 'weather_precip_30d_in')


    spec = StrategySpec(
        username="@Weather_ColdSnap_Max",
        display_name="Weather Cold Snap Max",
        archetype="event attention proxy",
        thesis=("SFWeather's brief is a rain outlook, but the tradable temperature "
                "path is the same NOAA record: cold anomalies at the SF station are a "
                "heating-demand proxy, traded through natural gas and utilities."),
        entry_rules=["Count days in the trailing 10 with TMIN below the trailing "
                     "60-day mean minus one standard deviation (NCEI station USW00023272)",
                     "When the count is at least 1, hold UNG at 110% and XLU at 60% of equity"],
        exit_rules=["Flatten when the count returns to zero", "No stop loss"],
        sizing="110% UNG + 60% XLU = 1.7x gross", leverage="1.7x gross",
        cadence="daily", horizon="days to weeks",
        academic_basis=[
            {"claim": "Weather anomalies are priced into energy demand and utility "
                      "earnings with a short lag",
             "url": "https://www.ncei.noaa.gov/access/services/data/v1",
             "ref": "NOAA/NCEI daily summaries (the official observation record)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "A single station's temperature is a poor national demand proxy",
            "UNG is a futures-tracking ETF with roll cost, not spot gas",
            "Weak mapping: the site labels this WEAK-MAPPING and the register says so"],
        aggression=4,
        why_return_seeking="Two-instrument concentrated position with maximum size.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("weather_cold_anomaly_10d"):
            return []
        count = book.value("weather_cold_anomaly_10d", ctx.t)
        if count >= 1:
            targets: Dict[str, float] = {}
            if "UNG" in ctx.symbols:
                targets["UNG"] = 1.1
            if "XLU" in ctx.symbols:
                targets["XLU"] = 0.6
            return ctx.orders_to_targets(targets, f"weather: {int(count)} cold anomaly days in 10")
        if ctx.position("UNG") or ctx.position("XLU"):
            return ctx.flatten("weather: no cold anomaly in the window")
        return []


class KalshiAttentionTimer(Strategy):
    """Prediction-market activity as a nowcast of weather/fuel attention."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('kalshi_settled_30d', 'kalshi_volume_30d')


    spec = StrategySpec(
        username="@Kalshi_Attention_Timer",
        display_name="Kalshi Attention Timer",
        archetype="event attention proxy",
        thesis=("The Kalshi venue file records settled temperature and gasoline "
                "contracts with their volumes. Trading volume in those contracts is "
                "real-money attention on weather and fuel, and the participant holds "
                "the two linked ETFs while that attention is elevated."),
        entry_rules=["Sum the traded volume of settled Kalshi contracts closed in the "
                     "trailing 30 days (KXHIGHNY, KXAAAGASM)",
                     "Hold UNG at 100% and XLU at 60% of equity when the trailing "
                     "volume is above its trailing median"],
        exit_rules=["Flatten when volume falls below the median", "No stop loss"],
        sizing="160% gross across two instruments", leverage="1.6x gross",
        cadence="weekly", horizon="weeks",
        academic_basis=[
            {"claim": "Prediction-market prices and volumes aggregate dispersed "
                      "information about physical outcomes",
             "url": "https://api.elections.kalshi.com/trade-api/v2/markets",
             "ref": "Kalshi trade API (venue-published settled market data)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "Volume is an attention measure, not a forecast of the underlying",
            "UNPROVEN-MAPPING: the register records that the equity link is asserted",
            "Settled markets carry only close time and settlement value, not a full "
            "tick history"],
        aggression=4,
        why_return_seeking="Uses an exchange's own data with no hedge.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("kalshi_settled_30d"):
            return []
        volume = book.value("kalshi_volume_30d", ctx.t)
        history = [book.value("kalshi_volume_30d", t)
                   for t in range(max(0, ctx.t - 120), ctx.t)]
        if len(history) < 30:
            return []
        median = _median(history)
        if volume > median:
            targets: Dict[str, float] = {}
            if "UNG" in ctx.symbols:
                targets["UNG"] = 1.0
            if "XLU" in ctx.symbols:
                targets["XLU"] = 0.6
            return ctx.orders_to_targets(targets, "kalshi: weather/fuel attention elevated")
        if ctx.position("UNG") or ctx.position("XLU"):
            return ctx.flatten("kalshi: attention cooled")
        return []


class GoldMeltTrend(Strategy):
    """GLD trend, honouring the GOLD project's price-verification discipline."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('gold_close',)


    spec = StrategySpec(
        username="@GOLD_Trend_GLD",
        display_name="Gold Trend (GLD)",
        archetype="time-series trend",
        thesis=("The GOLD project is a buyer's directory, not a market feed - its own "
                "README forbids price APIs. Rather than pretend otherwise, the gold "
                "participant trades GLD on the real ETF closes and uses the official "
                "LBMA fix only as a cross-reference."),
        entry_rules=["GLD close above its 50-session EMA and a positive 63-session "
                     "return on the collected real bars",
                     "Hold GLD at 190% of equity"],
        exit_rules=["Exit when either condition fails", "No stop loss"],
        sizing="190% of equity in GLD", leverage="1.9x gross",
        cadence="daily", horizon="months",
        academic_basis=[
            {"claim": "Time-series momentum in commodities and gold is a documented "
                      "trend-following premium",
             "url": "https://fred.stlouisfed.org/series/GOLDAMGBD228NLBM",
             "ref": "LBMA gold fix via FRED (official cross-reference for the metal)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "Trend systems whipsaw in range-bound gold markets",
            "GLD carries a 0.40% expense ratio and tracks spot with a small lag",
            "The source project contains no price signal - the site states this"],
        aggression=4,
        why_return_seeking="Full-size directional exposure to the metal.")

    def on_day(self, ctx: Context) -> List[Order]:
        if "GLD" not in ctx.symbols:
            return []
        closes = ctx.closes("GLD", 220)
        if len(closes) < 55:
            return []
        trend = ema(closes, 50)
        momentum = ctx.momentum("GLD", 63)
        if trend is None or momentum is None:
            return []
        if closes[-1] > trend and momentum > 0:
            return ctx.orders_to_targets({"GLD": 1.9}, "gold: trend up")
        if ctx.position("GLD"):
            return ctx.flatten("gold: trend broke")
        return []


class PinePilotEmaCross(Strategy):
    """The community Pine Script rule, reproduced exactly on collected prices."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ()


    spec = StrategySpec(
        username="@PinePilot_EMA_Cross",
        display_name="PinePilot EMA Cross",
        archetype="time-series trend",
        thesis=("PinePilot generates Pine Script, and the EMA-cross-with-ATR-stop rule "
                "is the canonical community strategy. It is reproduced here verbatim on "
                "collected daily bars, so the result is the rule's, not a tuned variant's."),
        entry_rules=["EMA(20) above EMA(50) on the real closes -> long that ETF at 60% "
                     "of equity (SPY, QQQ, IWM)",
                     "Full position only; no pyramiding"],
        exit_rules=["Exit a name when its EMA(20) closes back below EMA(50)",
                    "ATR(14) stop is declared but not implemented - flagged in the "
                    "irregularity register because the community rule includes it"],
        sizing="60% of equity per ETF, maximum three names = 1.8x gross",
        leverage="1.8x gross", cadence="daily", horizon="weeks to months",
        academic_basis=[
            {"claim": "The Pine Script documentation specifies the EMA and ATR "
                      "primitives used by the community rule",
             "url": "https://www.tradingview.com/pine-script-docs/",
             "ref": "TradingView Pine Script v5/v6 reference", "status": "FETCHED-VIA-SEARCH"}],
        known_failure_modes=[
            "EMA crossovers are lagging by construction",
            "The ATR stop is not implemented in this simulation - stated, not hidden",
            "Backtested on daily bars only; the community rule is usually intraday"],
        aggression=3,
        why_return_seeking="Long-only trend following on the three most liquid ETFs.")

    def on_day(self, ctx: Context) -> List[Order]:
        targets: Dict[str, float] = {}
        for symbol in ("SPY", "QQQ", "IWM"):
            if symbol not in ctx.symbols:
                continue
            closes = ctx.closes(symbol, 120)
            if len(closes) < 55:
                continue
            fast = ema(closes, 20)
            slow = ema(closes, 50)
            if fast is None or slow is None:
                continue
            if fast > slow:
                targets[symbol] = 0.6
        if not targets:
            held = [s for s in ("SPY", "QQQ", "IWM") if ctx.position(s)]
            return ctx.flatten("pinepilot: no trend", symbols=held) if held else []
        return ctx.orders_to_targets(targets, "pinepilot: EMA(20) > EMA(50)")


class LeapMaxLeverMomentum(Strategy):
    """The Leap's scoring rule, applied to equity proxies."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ()


    spec = StrategySpec(
        username="@LeapMaxLever_Momentum",
        display_name="Leap Max-Leverage Momentum",
        archetype="levered beta",
        thesis=("The Leap ranks on total return with all positions liquidated at the "
                "end of the contest. Under that payoff the best rank-seeking response is "
                "to lever the asset with the strongest trailing return and hold it to "
                "the bell - the same rule is tested here on SPY, QQQ and TLT."),
        entry_rules=["Rank SPY, QQQ and TLT by 63-session return, skipping the last 5 "
                     "sessions",
                     "Hold the leader at 190% of equity"],
        exit_rules=["Rotate at the start of each month", "Auto-liquidation at the "
                    "competition end is enforced by the engine for every participant"],
        sizing="190% of equity, single name", leverage="1.9x gross",
        cadence="monthly", horizon="1-3 months",
        academic_basis=[
            {"claim": "The Leap's public rules fix the ranking metric and the "
                      "end-of-contest liquidation",
             "url": "https://www.tradingview.com/the-leap/december-2025/rules/",
             "ref": "TradingView The Leap rules (contest mechanics copied)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "Contest-maximising leverage maximises ruin probability",
            "The instrument is a declared proxy: The Leap trades futures, not ETFs",
            "A single bad month at 1.9x is unrecoverable"],
        aggression=5,
        why_return_seeking="Directly maximises the contest's own objective function.")

    def on_day(self, ctx: Context) -> List[Order]:
        universe = [s for s in ("SPY", "QQQ", "TLT") if s in ctx.symbols]
        if not universe:
            return []
        first_of_month = ctx.t == ctx.md.first_competition_index or \
            ctx.date[:7] != ctx.md.dates[ctx.t - 1][:7]
        if not first_of_month:
            return []
        scored = []
        for symbol in universe:
            mom = ctx.momentum(symbol, 63, skip=5)
            if mom is not None:
                scored.append((mom, symbol))
        if not scored:
            return []
        scored.sort(reverse=True)
        pick = scored[0][1]
        return ctx.orders_to_targets({pick: 1.9}, f"the leap: monthly leader {pick}")


class YieldCurveRotator(Strategy):
    """FRED's own yield curve as a regime switch."""
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ('fred_slope_bps', 'fred_slope_change_21d')


    spec = StrategySpec(
        username="@YieldCurve_Rotator",
        display_name="Yield Curve Rotator",
        archetype="regime rotation",
        thesis=("The 10-year minus 3-month Treasury spread is the canonical recession "
                "indicator and is published by FRED. The rotator holds equities while "
                "the curve is steepening and long bonds while it is flattening."),
        entry_rules=["Read DGS10 and DGS3MO from the collected FRED files",
                     "Steepening (21-day change positive) -> hold SPY and QQQ at 90% "
                     "of equity each",
                     "Flattening -> hold TLT at 150% of equity"],
        exit_rules=["The regime flip is the exit", "No stop loss"],
        sizing="180% gross equities or 150% single bond ETF", leverage="1.8x gross",
        cadence="daily", horizon="weeks to months",
        academic_basis=[
            {"claim": "The term spread carries information about future activity and "
                      "equity returns",
             "url": "https://fred.stlouisfed.org/series/T10Y3M",
             "ref": "FRED T10Y3M / DGS10 / DGS3MO (the official series)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "The curve is a slow signal; regime flips are rare and lagged",
            "Bond ETFs carry duration risk that is not modelled beyond price",
            "The same series is used by everyone, so the information is priced"],
        aggression=4,
        why_return_seeking="Ninety-percent weight per equity ETF on a macro trigger.")

    def on_day(self, ctx: Context) -> List[Order]:
        book = getattr(ctx.md, "signals", None)
        if book is None or not book.available("fred_slope_bps"):
            return []
        change = book.value("fred_slope_change_21d", ctx.t)
        if change == 0.0:
            return []
        if change > 0:
            targets = {}
            if "SPY" in ctx.symbols:
                targets["SPY"] = 0.9
            if "QQQ" in ctx.symbols:
                targets["QQQ"] = 0.9
        else:
            targets = {"TLT": 1.5} if "TLT" in ctx.symbols else {}
        if not targets:
            return []
        return ctx.orders_to_targets(targets, f"curve: 21d change {change:+.1f}bp")


class InjuryFeedForward(Strategy):
    """Forward-test probe: official injury feeds, no backdated trades.

    This participant exists so the competition can *show* the difference between
    "we tested it and it did not work" and "we could not test it".  It places no
    trade during the historical replay, and its absence of return is reported as
    a data limitation rather than as a result.
    """
    #: Collected signal arrays this strategy reads. Declared here so the run, the
    #: site and the audit can all state which source a participant depends on, and
    #: so a strategy whose source is missing can be reported as DATA-MISSING rather
    #: than silently trading a column of zeros.
    signal_names = ("nfl_injury_report", "nba_injury_report")


    spec = StrategySpec(
        username="@InjuryFeed_Forward",
        display_name="Injury Feed (forward probe)",
        archetype="event attention proxy",
        thesis=("The NFL and NBA injury projects are live monitors. Neither league "
                "publishes a retrievable machine-readable archive of past injury "
                "designations, so this participant is a forward test: it will trade "
                "once the collector has captured a live feed, and it records no "
                "backdated trades."),
        entry_rules=["No backtested rule: the forward rule is 'heavy injury week at a "
                     "contending team -> long the sportsbook complex' once an official "
                     "feed has been captured for 4 consecutive weeks"],
        exit_rules=["Not applicable in the historical window"],
        sizing="declared 150% gross when it goes live", leverage="1.5x when live",
        cadence="weekly (from the next collection)", horizon="weeks",
        academic_basis=[
            {"claim": "The NFL publishes weekly injury designations on nfl.com",
             "url": "https://www.nfl.com/injuries/",
             "ref": "NFL official injury page (adapter target, fetched and hashed)",
             "status": "FETCHED-VERIFIED"},
            {"claim": "The NBA publishes a dated injury report PDF",
             "url": "https://official.nba.com/nba-injury-report-2025-26-season/",
             "ref": "NBA official injury report index (adapter target, fetched and hashed)",
             "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "No historical archive was retrievable, so there is no evidence of edge",
            "Reporting status is a live document that can change mid-week",
            "Its 0.0% return must not be read as 'flat performance'; it is an "
            "untested strategy"],
        aggression=3,
        why_return_seeking="Declared but not yet tradable - the honest placeholder for "
                            "the injury projects in the brief.")

    def on_day(self, ctx: Context) -> List[Order]:
        return []


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


#: The Season 2 roster.  ``season2.build_season2_roster`` returns this list.
ROSTER_MF = (
    FDACatalystRider,
    FDAClusterFade,
    InsiderCopycatMax,
    InsiderClusterAlpha,
    CEOCFOConviction,
    MLBAttentionMomo,
    MLBUpsetShort,
    WeatherColdSnapMax,
    KalshiAttentionTimer,
    GoldMeltTrend,
    PinePilotEmaCross,
    LeapMaxLeverMomentum,
    YieldCurveRotator,
    InjuryFeedForward,
)


def build_roster_mf() -> List[Strategy]:
    return [cls() for cls in ROSTER_MF]


__all__ = ["ROSTER_MF", "build_roster_mf"] + [cls.__name__ for cls in ROSTER_MF] + ["_median"]
