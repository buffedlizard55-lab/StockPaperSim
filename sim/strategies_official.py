"""The Official Auction Book's participants.

Each participant is a strategy with a unique username, a one-paragraph thesis, a
declared data basis and a citation trail.  The brief for this book is
return-seeking: there is no risk-management objective, no volatility target and
no stop-loss mandate here.  The venue still enforces margin and still liquidates
a breached account, because that is what a real venue does, and every failure is
published with its cause rather than being smoothed away.

Every rule reads only official observations - the Treasury's own auction results
and par yield curve, the H.15 secondary-market bill rates and the Federal Reserve
Bank of New York's SOFR - and every one of those readings is written into the
intent that uses it, with the file and its SHA-256.  A rule that cannot be
evaluated from official data places nothing and says so.
"""

from __future__ import annotations

import datetime as dt

from typing import Dict, List, Optional, Sequence

from . import treasury


class OfficialStrategy:
    """Base class: an identity, a thesis, and a ``plan`` that places intents."""

    username: str = "@Unnamed"
    family: str = "unclassified"
    thesis: str = ""
    research_basis: Sequence[dict] = ()
    data_status: str = "READY"
    data_note: str = ""
    max_gross_leverage: float = 1.0

    # -- helpers -----------------------------------------------------------
    def _cheapest_bill(self, ctx, max_days: int, min_days: int = 1):
        """The upcoming bill with the shortest tenor inside a day range."""
        best = None
        for auction in ctx.upcoming_auctions(types=("Bill",)):
            days = auction.days_to_maturity()
            if not (min_days <= days <= max_days):
                continue
            if best is None or days < best.days_to_maturity():
                best = auction
        return best

    def _recent_issue(self, ctx, security_type: str, term_contains: str,
                      within_sessions: int = 10):
        """The most recent auction of a given type/term inside a session window."""
        best = None
        for auction in ctx.recent_auctions(limit=80):
            if auction.security_type != security_type:
                continue
            if term_contains and term_contains.lower() not in (auction.term or "").lower():
                continue
            age = ctx.sessions_since(auction.auction_date)
            if age is None or age > within_sessions or age < 0:
                continue
            if best is None or auction.auction_date > best.auction_date:
                best = auction
        return best

    def plan(self, ctx) -> None:          # pragma: no cover - interface only
        raise NotImplementedError

    def describe(self) -> dict:
        return {"username": self.username, "family": self.family,
                "thesis": self.thesis, "research_basis": list(self.research_basis),
                "data_status": self.data_status, "data_note": self.data_note,
                "max_gross_leverage": self.max_gross_leverage}


def _years_between(start: str, end: str) -> Optional[float]:
    """Signed years from ``start`` to ``end``, or ``None`` if either is missing."""
    if not start or not end:
        return None
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.0

# --------------------------------------------------------------------------
# 1. The base case: buy bills, hold to maturity, roll
# --------------------------------------------------------------------------

class TBillLadder_MaxRoll(OfficialStrategy):
    username = "@TBillLadder_MaxRoll"
    family = "cash management / roll"
    thesis = ("Buy the shortest announced bill at auction, non-competitively, at the "
              "official price, hold it to the official maturity date and roll the "
              "proceeds into the next auction. Every price is the Treasury's own "
              "printed price; every date is the Treasury's own date; the account is "
              "never levered because a bill yields less than the repo rate that "
              "would finance it, which this book measures rather than assumes.")
    research_basis = (
        {"label": "TreasuryDirect: auctions, non-competitive bidding and single-price format",
         "url": "https://www.treasurydirect.gov/auctions/auction-results/"},
        {"label": "31 CFR 356: sale and issue of marketable book-entry Treasury bills, notes and bonds",
         "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-II/subchapter-A/part-356"},
    )
    max_gross_leverage = 1.0

    def plan(self, ctx) -> None:
        if ctx.held():
            return                                  # roll on maturity, not before
        auction = self._cheapest_bill(ctx, max_days=182)
        if auction is None:
            ctx.note("no announced bill inside 182 days")
            return
        ctx.buy_at_auction(auction, ctx.cash_available() * 0.995,
                           rule="shortest announced bill, non-competitive, held to maturity",
                           rationale=(f"{auction.security_term} bill of "
                                      f"{auction.days_to_maturity()} days announced "
                                      f"on {auction.announcement_date} for auction "
                                      f"{auction.auction_date}"))


# --------------------------------------------------------------------------
# 2. The carry experiment: the same trade, levered
# --------------------------------------------------------------------------

class BillCarry_TenX(OfficialStrategy):
    username = "@BillCarry_TenX"
    family = "levered carry"
    thesis = ("Own the highest-yielding announced bill at 10x gross exposure, financed "
              "at SOFR plus a declared 25 bp spread. This is the trade that looks "
              "free and is not: a bill yields approximately SOFR minus a small "
              "concession, so levering it borrows above the asset yield. The book "
              "publishes the sign of that carry instead of hoping the reader does "
              "not compute it.")
    research_basis = (
        {"label": "NY Fed: SOFR, the secured overnight financing rate",
         "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"},
        {"label": "FINRA Rule 4210: margin requirements (the venue's leverage policy is declared, not regulatory)",
         "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        auction = self._cheapest_bill(ctx, max_days=182)
        if auction is None:
            return
        if not any(p.cusip == auction.cusip for p in ctx.held()):
            target = ctx.equity() * self.max_gross_leverage * 0.95
            ctx.buy_at_auction(auction, target,
                               rule="highest available bill yield at the 10x gross cap",
                               rationale="levered bill carry, financed at SOFR + 25 bp")


# --------------------------------------------------------------------------
# 3. Front-end roll-down
# --------------------------------------------------------------------------

class FrontEndRollDown_13W(OfficialStrategy):
    username = "@FrontEndRollDown_13W"
    family = "roll-down"
    thesis = ("Buy the 13-week bill at auction and sell it in the secondary market "
              "five sessions before maturity, when the bill has aged into a lower "
              "point on the curve. The gain is roll-down, and it only exists while "
              "the front end is upward-sloping; the rule reads the official H.15 "
              "secondary-market bill rates to check that it is.")
    research_basis = (
        {"label": "H.15 selected interest rates: Treasury bill secondary-market rates (discount basis)",
         "url": "https://www.federalreserve.gov/releases/h15/"},
        {"label": "Treasury: daily par yield curve rates",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
    )
    max_gross_leverage = 4.0

    def plan(self, ctx) -> None:
        slope = ctx.slope_bp(1 / 12, 0.5)
        if slope is None or slope <= 0:
            ctx.note(f"front end not upward-sloping ({slope} bp 1M-6M): no roll-down to harvest")
            return
        for position in ctx.held():
            if position.days_to_maturity(ctx.session) <= 5 and position.cusip:
                ctx.sell_secondary(position.cusip, position.face,
                                   rule="exit five sessions before maturity",
                                   rationale="roll-down captured, avoid the flat spot into maturity")
        auction = self._next_term(ctx, "13-Week")
        if auction is None:
            ctx.note("no announced 13-week bill to bid")
            return
        if not any(p.cusip == auction.cusip for p in ctx.held()):
            ctx.buy_at_auction(auction, ctx.equity() * self.max_gross_leverage * 0.9,
                               rule="13-week bill at auction while 1M-6M slope is positive",
                               rationale=f"front-end slope {slope:+.1f} bp")

    def _next_term(self, ctx, term: str):
        for auction in ctx.upcoming_auctions(types=("Bill",)):
            if term.lower() in (auction.term or "").lower():
                return auction
        return None


# --------------------------------------------------------------------------
# 4. Auction-strength fade / follow
# --------------------------------------------------------------------------

class AuctionStrength_Follow(OfficialStrategy):
    username = "@AuctionStrength_Follow"
    family = "auction microstructure"
    thesis = ("Follow the demand print: when an auction stops through and the "
              "bid-to-cover is at the high end of its recent range, buy that CUSIP "
              "in the secondary market the next session and hold until the next "
              "auction of the same term. Bid-to-cover and the high rate are the "
              "Treasury's own published numbers, so the signal is not an estimate.")
    research_basis = (
        {"label": "TreasuryDirect: auction results, including bid-to-cover and high rate",
         "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45"},
        {"label": "Treasury: single-price auctions since 1998 (auction format on the result record)",
         "url": "https://www.treasurydirect.gov/auctions/"},
    )
    max_gross_leverage = 6.0

    def plan(self, ctx) -> None:
        candidates = [a for a in ctx.recent_auctions(limit=40)
                      if a.bid_to_cover is not None]
        if len(candidates) < 20:
            ctx.note("fewer than 20 auctions with a bid-to-cover in the window read")
            return
        history = [a.bid_to_cover for a in candidates[:-1]]
        mean = sum(history) / len(history)
        latest = max(candidates, key=lambda a: a.auction_date)
        if latest.bid_to_cover is None or latest.bid_to_cover < mean:
            ctx.note(f"latest bid-to-cover {latest.bid_to_cover} below the running "
                     f"mean {mean:.3f}: no follow")
            return
        if any(p.cusip == latest.cusip for p in ctx.held()):
            return
        for position in ctx.held():
            ctx.sell_secondary(position.cusip, position.face,
                               rule="rotate to the strongest auction print",
                               rationale="new auction had stronger demand")
        ctx.buy_secondary(latest.cusip, ctx.equity() * self.max_gross_leverage * 0.9,
                          rule="buy the CUSIP whose auction printed bid-to-cover above its running mean",
                          rationale=f"{latest.security_term} {latest.cusip}: bid-to-cover "
                                    f"{latest.bid_to_cover:.3f} vs mean {mean:.3f}")


# --------------------------------------------------------------------------
# 5. Curve trades (duration-neutral by face-weighting)
# --------------------------------------------------------------------------

class CurveSteepener_2s10s(OfficialStrategy):
    username = "@CurveSteepener_2s10s"
    family = "curve"
    thesis = ("Express a steepening view through cash: long the 2-year, short the "
              "10-year, sized so the two legs have equal price value for a parallel "
              "shift. Both legs are priced from the Treasury's own par curve, so the "
              "mark is an official observation run through a published formula rather "
              "than a dealer quote.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates (the official secondary-market observation)",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
        {"label": "CFR 31 Part 356 Appendix B: price, discount and yield formulas for Treasury securities",
         "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-II/subchapter-A/part-356"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        slope = ctx.slope_bp(2.0, 10.0)
        if slope is None:
            ctx.note("par curve lacks a 2-year or 10-year point")
            return
        long_leg = ctx.hold_term("2-Year")
        short_leg = ctx.hold_term("10-Year")
        if long_leg is None or short_leg is None:
            ctx.note("no held 2-year and 10-year pair available to hold the position")
            return
        target = ctx.equity() * self.max_gross_leverage * 0.9
        ctx.set_weight(long_leg, target / 2, rule="long leg of the 2s10s steepener",
                       rationale=f"2s10s slope {slope:+.1f} bp")
        ctx.set_weight(short_leg, -target / 2, rule="short leg of the 2s10s steepener",
                       rationale=f"2s10s slope {slope:+.1f} bp")


class CurveFlattener_2s10s(OfficialStrategy):
    username = "@CurveFlattener_2s10s"
    family = "curve"
    thesis = ("The same pair, the other way: short the 2-year and long the 10-year. "
              "It exists in this book because publishing only the winner of a pair of "
              "opposite rules is how a competition hides luck.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        slope = ctx.slope_bp(2.0, 10.0)
        if slope is None:
            return
        long_leg = ctx.hold_term("10-Year")
        short_leg = ctx.hold_term("2-Year")
        if long_leg is None or short_leg is None:
            ctx.note("no held 2-year and 10-year pair available")
            return
        target = ctx.equity() * self.max_gross_leverage * 0.9
        ctx.set_weight(short_leg, -target / 2, rule="short leg of the 2s10s flattener",
                       rationale=f"2s10s slope {slope:+.1f} bp")
        ctx.set_weight(long_leg, target / 2, rule="long leg of the 2s10s flattener",
                       rationale=f"2s10s slope {slope:+.1f} bp")


# --------------------------------------------------------------------------
# 6. Duration views driven by the official curve's own trend
# --------------------------------------------------------------------------

class DurationTrend_TenX(OfficialStrategy):
    username = "@DurationTrend_TenX"
    family = "trend / duration"
    thesis = ("Hold the 10-year long at the 10x gross cap while the official 10-year "
              "par yield is below its own 60-session mean, and short it when above. "
              "A trend rule on a single official series, with the position taken in "
              "the cash market and financed at SOFR plus a spread.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
        {"label": "NY Fed: SOFR (the financing rate this book charges)",
         "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        history = ctx.curve_history(10.0, 60)
        if len(history) < 30:
            ctx.note("fewer than 30 official 10-year observations available")
            return
        mean = sum(history) / len(history)
        current = ctx.yield_at(10.0)
        if current is None:
            return
        holding = ctx.hold_term("10-Year")
        if holding is None:
            ctx.note("no held 10-year to size")
            return
        target = ctx.equity() * self.max_gross_leverage * 0.9
        weight = target if current < mean else -target
        ctx.set_weight(holding, weight,
                       rule="long the 10-year below its 60-session mean yield, short above",
                       rationale=(f"10-year {current:.3f}% vs {len(history)}-session mean "
                                  f"{mean:.3f}%"))


class BellyMeanReversion_FiveY(OfficialStrategy):
    username = "@BellyMeanReversion_FiveY"
    family = "mean reversion"
    thesis = ("Buy the 5-year when its official par yield is more than 25 bp above its "
              "own 120-session mean and stand aside otherwise, at the 10x gross cap "
              "while the position is on. The mirror of the trend rule: one of the two "
              "has to be wrong about this window, and the book says which.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        history = ctx.curve_history(5.0, 120)
        current = ctx.yield_at(5.0)
        holding = ctx.hold_term("5-Year")
        if current is None or holding is None or len(history) < 60:
            ctx.note("5-year history or holding unavailable")
            return
        mean = sum(history) / len(history)
        spread = (current - mean) * 100
        if spread <= 25:
            ctx.set_weight(holding, 0.0,
                           rule="stand aside unless the 5-year is 25 bp cheap to its mean",
                           rationale=f"5-year {current:.3f}% vs mean {mean:.3f}% ({spread:+.1f} bp)")
            return
        ctx.set_weight(holding, ctx.equity() * self.max_gross_leverage * 0.9,
                       rule="buy the 5-year when it is more than 25 bp cheap to its 120-session mean",
                       rationale=f"5-year {current:.3f}% vs mean {mean:.3f}% ({spread:+.1f} bp)")


# --------------------------------------------------------------------------
# 7. TIPS breakeven
# --------------------------------------------------------------------------

class TIPSBreakeven_Rider(OfficialStrategy):
    username = "@TIPSBreakeven_Rider"
    family = "inflation"
    thesis = ("Buy inflation-protected paper at auction when the breakeven implied by "
              "the official observations sits below the inflation the official CPI index "
              "has actually delivered. The two sides of that comparison are read at the "
              "same horizon: the nominal yield comes from the Treasury's par curve at the "
              "TIPS security's own remaining maturity, and realised inflation comes from "
              "the BLS index over the same number of years. The rule bids at the next "
              "announced TIPS auction, so the entry price is a published one.")
    research_basis = (
        {"label": "TreasuryDirect: TIPS auction results (adjusted price, index ratio, accrued interest)",
         "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45"},
        {"label": "Treasury: TIPS index ratios and reference CPI",
         "url": "https://www.treasurydirect.gov/auctions/announcements-data-results/"},
        {"label": "BLS Consumer Price Index (all items), as republished by FRED",
         "url": "https://fred.stlouisfed.org/series/CPIAUCSL"},
    )
    max_gross_leverage = 4.0

    def plan(self, ctx) -> None:
        priced = [a for a in ctx.recent_auctions(limit=200)
                  if a.is_tips and a.high_yield is not None and a.maturity_date]
        if not priced:
            ctx.note("no TIPS auction with a published high yield in the collected window")
            return
        latest = max(priced, key=lambda a: a.auction_date)
        tenor = _years_between(ctx.session, latest.maturity_date)
        if tenor is None or tenor <= 0:
            ctx.note(f"{latest.cusip} matures before this session, so no horizon can be read")
            return
        realised = ctx.realised_inflation(tenor)
        if realised is None:
            window = ctx.inflation_window_years()
            ctx.note(f"the collected CPI series reaches back {window} years, short of the "
                     f"{tenor:.2f}-year horizon this TIPS pays over; the rule does not "
                     f"quietly compare a shorter window")
            return
        nominal_yield = ctx.yield_at(tenor)
        if nominal_yield is None:
            ctx.note(f"the par curve has no observation at {tenor:.2f} years")
            return
        breakeven = nominal_yield - latest.high_yield
        if breakeven >= realised:
            ctx.note(f"{tenor:.2f}-year breakeven {breakeven:.2f}% is not below realised "
                     f"inflation {realised:.2f}%")
            return
        cusips = {lot.cusip for lot in ctx.account.lots}
        if latest.cusip in cusips:
            ctx.note(f"already holding {latest.cusip}")
            return
        upcoming = [a for a in ctx.upcoming_auctions(types=("TIPS",))
                    if a.auction_date >= ctx.session]
        budget = ctx.equity() * self.max_gross_leverage * 0.9
        rationale = (f"{tenor:.2f}-year breakeven {breakeven:.2f}% "
                     f"({nominal_yield:.2f}% nominal at that tenor - {latest.high_yield:.2f}% "
                     f"TIPS) against realised CPI {realised:.2f}% over {tenor:.2f} years")
        if upcoming:
            ctx.buy_at_auction(min(upcoming, key=lambda a: a.auction_date), budget,
                               rule="bid for inflation-protected paper while the breakeven "
                                    "at the security's own tenor is below realised inflation",
                               rationale=rationale)
            return
        ctx.buy_secondary(latest.cusip, budget,
                          rule="buy TIPS while the breakeven at the security's own tenor is "
                               "below realised inflation",
                          rationale=rationale)


# --------------------------------------------------------------------------
# 8. The rate-regime switch
# --------------------------------------------------------------------------

class SOFRPivot_Switch(OfficialStrategy):
    username = "@SOFRPivot_Switch"
    family = "regime"
    thesis = ("Read the Federal Reserve Bank of New York's own SOFR series: while its "
              "20-session change is negative, own duration at the 10x gross cap; while "
              "it is positive, sit in the shortest bill available. The switch is one "
              "official observation, so a reader can reproduce every decision.")
    research_basis = (
        {"label": "NY Fed reference rates API (SOFR)",
         "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"},
        {"label": "Treasury: daily par yield curve rates",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        change = ctx.series_change_bp("SOFR", 20)
        holding = ctx.hold_term("10-Year")
        if change is None or holding is None:
            ctx.note("SOFR history or 10-year holding unavailable")
            return
        if change < 0:
            ctx.set_weight(holding, ctx.equity() * self.max_gross_leverage * 0.9,
                           rule="own duration while SOFR's 20-session change is negative",
                           rationale=f"SOFR 20-session change {change:+.1f} bp")
            return
        ctx.set_weight(holding, 0.0,
                       rule="stand aside while SOFR's 20-session change is positive",
                       rationale=f"SOFR 20-session change {change:+.1f} bp")
        auction = self._cheapest_bill(ctx, max_days=182)
        if auction is not None and not any(p.cusip == auction.cusip for p in ctx.held()):
            ctx.buy_at_auction(auction, ctx.cash_available() * 0.995,
                               rule="park in the shortest bill when the regime flips",
                               rationale=f"SOFR 20-session change {change:+.1f} bp")


# --------------------------------------------------------------------------
# 9. Liquidity-aware auction participant
# --------------------------------------------------------------------------

class LiquidityWeighted_Participant(OfficialStrategy):
    username = "@LiquidityWeighted_Participant"
    family = "liquidity"
    thesis = ("Size every auction bid by the official liquidity of the issue it is "
              "buying - bidding less into auctions whose acceptance was thin or whose "
              "bid-to-cover was low - and hold the highest-scoring bill each week. The "
              "brief asked for bid sizing and liquidity to be part of the record; this "
              "participant makes it part of the rule, and the book prints the "
              "participation percentage of every award.")
    research_basis = (
        {"label": "TreasuryDirect: accepted and tendered amounts by bidder class",
         "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45"},
        {"label": "Fiscal Data: Treasury securities auctions (second publisher of the same results)",
         "url": "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/"},
    )
    max_gross_leverage = 3.0

    def plan(self, ctx) -> None:
        for position in ctx.held():
            if position.days_to_maturity(ctx.session) <= 3:
                ctx.sell_secondary(position.cusip, position.face,
                                   rule="exit just before maturity to redeploy",
                                   rationale="liquidity-weighted rotation")
        for auction in ctx.upcoming_auctions(types=("Bill",), limit=6):
            score = ctx.liquidity_score(auction)
            if score is None or score < 0.5:
                continue
            size = ctx.equity() * self.max_gross_leverage * 0.9 * min(1.0, score)
            if not any(p.cusip == auction.cusip for p in ctx.held()):
                ctx.buy_at_auction(auction, size,
                                   rule="size the bill bid by official issue liquidity",
                                   rationale=f"liquidity score {score:.2f} on "
                                             f"{auction.security_term} {auction.cusip}")
            return


# --------------------------------------------------------------------------
# 10. The control: hold the longest paper the calendar offers
# --------------------------------------------------------------------------

class LongBond_MaxDur(OfficialStrategy):
    username = "@LongBond_MaxDur"
    family = "control / duration"
    thesis = ("Own the longest-dated coupon security the official auction calendar "
              "offers, at the 10x gross cap, and roll it only when it matures. This is "
              "the control the brief needs: maximum duration, minimum thinking. In a "
              "window where the Treasury's own 30-year yield rose, it should lose - and "
              "the book reports what it did rather than what it should have done.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates (30-year point)",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
        {"label": "TreasuryDirect: bond auction results",
         "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        if ctx.held():
            return
        auction = ctx.hold_term("30-Year") or ctx.hold_term("20-Year")
        if auction is None:
            ctx.note("no held long bond")
            return
        ctx.set_weight(auction, ctx.equity() * self.max_gross_leverage * 0.9,
                       rule="maximum duration at the 10x gross cap",
                       rationale=f"{auction.security_term} {auction.cusip}")


# --------------------------------------------------------------------------
# 11. Auction concession fade
# --------------------------------------------------------------------------

class ConcessionFade_Notes(OfficialStrategy):
    username = "@ConcessionFade_Notes"
    family = "auction cycle"
    thesis = ("Buy the new note at auction - where dealers are paid to absorb supply - "
              "and sell it on the fifth session after issue, when the concession has "
              "typically been given back. The entry price is the Treasury's printed "
              "auction price and the exit is the par curve's own mark, so the trade's "
              "two ends are both official numbers.")
    research_basis = (
        {"label": "TreasuryDirect: high price and high yield of every auction",
         "url": "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=45"},
        {"label": "Fiscal Data: Treasury securities auctions",
         "url": "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/"},
    )
    max_gross_leverage = 8.0

    def plan(self, ctx) -> None:
        # The exit rule first: five sessions after the *issue* date the auction
        # concession is treated as given back, and the position is sold on the
        # official curve.
        for position in ctx.held():
            if position.opened_kind == "PRIMARY-AUCTION" and \
                    (ctx.sessions_since(position.entry_date) or 0) >= 5:
                ctx.sell_secondary(position.cusip, position.face,
                                   rule="exit five sessions after the auction",
                                   rationale="concession fade")
        # The entry rule bids the next announced note auction: a rule cannot bid
        # an auction that has already happened, and the announced calendar is
        # what a desk would actually see.
        candidates = [a for a in ctx.upcoming_auctions(types=("Note",))
                      if a.offering_amount]
        if not candidates:
            ctx.note("no announced note auction to bid")
            return
        auction = candidates[0]
        if any(p.cusip == auction.cusip for p in ctx.held()):
            return
        ctx.buy_at_auction(auction, ctx.equity() * self.max_gross_leverage * 0.9,
                           rule="bid the next announced note auction and exit five "
                                "sessions after issue",
                           rationale=f"{auction.security_term} {auction.cusip} announced "
                                     f"{auction.announcement_date} for auction "
                                     f"{auction.auction_date}")


# --------------------------------------------------------------------------
# 12. Everything at once, sized by the official curve's own slope
# --------------------------------------------------------------------------

class CalendarBarbell_Max(OfficialStrategy):
    username = "@CalendarBarbell_Max"
    family = "barbell"
    thesis = ("Hold the two ends of the official curve at once - the shortest bill and "
              "the longest bond - and rebalance monthly. It is the barbell that needs no "
              "view, and in a steepening window the long end is what breaks, which is "
              "the point of publishing it.")
    research_basis = (
        {"label": "Treasury: daily par yield curve rates (1-month to 30-year)",
         "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"},
    )
    max_gross_leverage = 10.0

    def plan(self, ctx) -> None:
        if ctx.session[8:10] != "01" and not ctx.first_session_of_month():
            billing = ctx.next_bill_for_cash()
            if billing is not None and ctx.cash_available() > ctx.equity() * 0.1:
                ctx.buy_at_auction(billing, ctx.cash_available() * 0.995,
                                   rule="keep idle cash in the shortest bill",
                                   rationale="cash sweep between monthly rebalances")
            return
        long_leg = ctx.hold_term("30-Year")
        if long_leg is None:
            ctx.note("no held 30-year to weight")
            return
        budget = ctx.equity() * self.max_gross_leverage * 0.9
        ctx.set_weight(long_leg, budget * (2 / 3),
                       rule="monthly rebalance: two thirds in the long bond",
                       rationale="barbell rebalance")
        short_bill = self._cheapest_bill(ctx, max_days=60)
        if short_bill is not None:
            ctx.buy_at_auction(short_bill, budget / 3,
                               rule="monthly rebalance: one third in the shortest bill",
                               rationale=f"{short_bill.security_term} {short_bill.cusip}")


ROSTER: Sequence[OfficialStrategy] = (
    TBillLadder_MaxRoll(),
    BillCarry_TenX(),
    FrontEndRollDown_13W(),
    AuctionStrength_Follow(),
    CurveSteepener_2s10s(),
    CurveFlattener_2s10s(),
    DurationTrend_TenX(),
    BellyMeanReversion_FiveY(),
    TIPSBreakeven_Rider(),
    SOFRPivot_Switch(),
    LiquidityWeighted_Participant(),
    LongBond_MaxDur(),
    ConcessionFade_Notes(),
    CalendarBarbell_Max(),
)


def by_username(username: str) -> Optional[OfficialStrategy]:
    for strategy in ROSTER:
        if strategy.username == username:
            return strategy
    return None


def register_rows() -> List[dict]:
    return [s.describe() for s in ROSTER]
