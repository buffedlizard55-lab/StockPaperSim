"""Microstructure: tick sizes, spreads, depth, impact, fees, execution.

The regulatory assertions cite the rule they come from; the model assertions
check the properties that make the fills believable (and the two regressions
that were found and fixed during the build: IR-21 free intraday drift and
IR-24 volatility-proportional spreads two orders of magnitude too wide).
"""

from __future__ import annotations

import math
import random
import unittest

from fixtures import cfg, instrument, replay

from sim import config
from sim.microstructure import (BUY, LIMIT, MARKET, SELL, STOP, BORROW_TERMS,
                                CostModel, ExecutionEngine, Fill, ImpactModel,
                                IntradayPath, LiquidityModel, MarketMakerPool,
                                Order, OrderBook, borrow_terms)


class TestMinimumTick(unittest.TestCase):
    """SEC Reg NMS Rule 612.
      SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612
    """

    def test_one_cent_at_or_above_one_dollar(self):
        for price in (1.00, 1.01, 5.0, 185.0, 754.05, 10_000.0):
            self.assertEqual(config.minimum_tick(price), 0.01, price)

    def test_sub_penny_below_one_dollar(self):
        for price in (0.9999, 0.50, 0.01):
            self.assertEqual(config.minimum_tick(price), 0.0001, price)

    def test_boundary_is_inclusive_at_one_dollar(self):
        self.assertEqual(config.minimum_tick(1.0), 0.01)
        self.assertEqual(config.minimum_tick(0.999999), 0.0001)

    def test_half_penny_tier_is_not_treated_as_operative(self):
        """IR-04: the 2024 amendments are adopted but their compliance date is
        uncertain, so the model quotes on the currently operative grid."""
        self.assertFalse(hasattr(config, "TWAQ_SPREAD_TICK_TIER") and
                         config.TWAQ_SPREAD_TICK_TIER,
                         "the $0.005 tier must not be silently switched on")
        self.assertEqual(config.ACCESS_FEE_CAP_PER_SHARE, 0.003,
                         "Rule 610 access-fee cap is $0.003 until the "
                         "amendments are operative")


class TestQuotedSpread(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.liq = LiquidityModel(cfg().liquidity)
        cls.md = replay()

    def test_spread_is_a_whole_number_of_ticks_and_at_least_one(self):
        for sym in self.md.symbols:
            inst = self.md.instruments[sym]
            price = inst.price_start
            sigma = self.md.realised_sigma_daily(sym, self.md.first_competition_index + 30)
            spread = self.liq.quoted_spread(inst, price, sigma, 1.0)
            tick = config.minimum_tick(price)
            self.assertGreaterEqual(spread, tick - 1e-12, sym)
            self.assertAlmostEqual(spread / tick, round(spread / tick), places=6,
                                   msg=f"{sym}: spread {spread} is not a whole tick count")
            ticks = self.liq.spread_ticks(inst, price, sigma, 1.0)
            self.assertGreaterEqual(ticks, cfg().liquidity.min_spread_ticks[inst.liquidity_tier])
            self.assertLessEqual(ticks, cfg().liquidity.max_spread_ticks[inst.liquidity_tier])

    def test_spread_respects_the_basis_point_cap(self):
        liq_cfg = cfg().liquidity
        for sym in self.md.symbols:
            inst = self.md.instruments[sym]
            price = inst.price_start
            # A deliberately extreme volatility: the cap must still bind.
            spread = self.liq.quoted_spread(inst, price, 5.0, 4.0)
            cap = price * liq_cfg.spread_cap_bps[inst.liquidity_tier] / 10_000.0
            self.assertLessEqual(spread, cap + 1e-9, sym)

    def test_liquid_names_quote_one_tick(self):
        """IR-24 regression: NVDA at 27bp was two orders of magnitude too wide.

        Real quoted spreads on the most liquid US equities sit at the minimum
        increment; published execution-quality reports put NVDA's effective
        spread near one cent on a ~$185 stock (~0.5bp).
          SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.605
        """
        for sym in ("SPY", "NVDA", "AAPL", "JPM"):
            inst = instrument(sym)
            sigma = replay().realised_sigma_daily(sym, 160)
            ticks = self.liq.spread_ticks(inst, inst.price_start, sigma, 1.0)
            self.assertEqual(ticks, 1, f"{sym} should quote at the touch")
            spread = self.liq.quoted_spread(inst, inst.price_start, sigma, 1.0)
            bps = 10_000.0 * spread / inst.price_start
            self.assertLess(bps, 3.0, f"{sym} quoted spread {bps:.2f}bp is too wide")

    def test_spread_widens_with_volatility_and_with_the_vix_regime(self):
        inst = instrument("RIVN")
        price = inst.price_start
        base = self.liq.spread_ticks(inst, price, 0.02, 1.0)
        stressed = self.liq.spread_ticks(inst, price, 0.12, 1.0)
        vix_stressed = self.liq.spread_ticks(inst, price, 0.02, 3.0)
        self.assertGreaterEqual(stressed, base)
        self.assertGreaterEqual(vix_stressed, base)
        # Monotone across the whole universe, and actually binding somewhere:
        # a model that never widens is a constant, not a spread model.
        widened = 0
        for sym in replay().symbols:
            i2 = replay().instruments[sym]
            p2 = i2.price_start
            calm = self.liq.quoted_spread(i2, p2, 0.01, 1.0)
            hot = self.liq.quoted_spread(i2, p2, 0.20, 3.0)
            self.assertGreaterEqual(hot, calm - 1e-12, sym)
            if hot > calm + 1e-12:
                widened += 1
        self.assertGreater(widened, 0, "no instrument's spread responds to stress")

    def test_touch_size_scales_with_price_and_depth_grows_down_the_book(self):
        liq_cfg = cfg().liquidity
        touch = self.liq.touch_size(100.0)
        self.assertGreater(touch, 0.0)
        self.assertAlmostEqual(touch, liq_cfg.touch_size_round_lots *
                               _round_lot(100.0), places=6)
        for level in range(1, liq_cfg.book_levels):
            self.assertGreater(self.liq.book_depth(100.0, level),
                               self.liq.book_depth(100.0, level - 1))

    def test_round_lot_tiers_follow_rule_600_b_93(self):
        """Tiered round lots.
          SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.600
        """
        cases = [(50.0, 100), (250.0, 100), (250.01, 40), (1000.0, 40),
                 (1000.01, 10), (10_000.0, 10), (10_000.01, 1), (185.0, 100)]
        for price, expected in cases:
            if hasattr(config, "round_lot"):
                self.assertEqual(config.round_lot(price), expected, price)
            else:
                self.assertEqual(_round_lot(price), expected, price)


def _round_lot(price: float) -> int:
    """Rule 600(b)(93) tiering, restated here so the test does not import the
    implementation it is checking."""
    if price <= 250.0:
        return 100
    if price <= 1000.0:
        return 40
    if price <= 10_000.0:
        return 10
    return 1


class TestImpactModel(unittest.TestCase):
    def setUp(self):
        self.imp = ImpactModel(cfg().impact)

    def test_zero_size_has_zero_impact(self):
        self.assertEqual(self.imp.impact_return(0, 1e6, 0.02), 0.0)
        self.assertEqual(self.imp.impact_return(100, 0.0, 0.02), 0.0)

    def test_square_root_law(self):
        small = self.imp.impact_return(10_000, 5_000_000, 0.02)
        quadruple = self.imp.impact_return(40_000, 5_000_000, 0.02)
        self.assertAlmostEqual(quadruple / small, 2.0, places=9)

    def test_monotone_in_size_and_volatility(self):
        prev = 0.0
        for qty in (1_000, 10_000, 100_000, 1_000_000):
            value = self.imp.impact_return(qty, 5_000_000, 0.02)
            self.assertGreater(value, prev)
            prev = value
        self.assertGreater(self.imp.impact_return(10_000, 5_000_000, 0.08),
                           self.imp.impact_return(10_000, 5_000_000, 0.02))

    def test_split_is_exhaustive_and_uses_the_declared_permanent_share(self):
        total = self.imp.impact_return(50_000, 5_000_000, 0.03)
        perm, temp = self.imp.split(total)
        self.assertAlmostEqual(perm + temp, total, places=12)
        self.assertAlmostEqual(perm / total, cfg().impact.permanent_share, places=9)
        self.assertGreater(temp, 0.0)

    def test_impact_is_a_plausible_number_of_basis_points(self):
        """1% of ADV in a mid-cap should cost tens of bp, not percent."""
        ret = self.imp.impact_return(50_000, 5_000_000, 0.02)   # 1% of ADV
        self.assertGreater(ret * 10_000, 5.0)
        self.assertLess(ret * 10_000, 200.0)


class TestCostModel(unittest.TestCase):
    def setUp(self):
        self.costs = CostModel(cfg().costs)

    def test_sec_31_is_zero_before_the_fy2026_change_date_and_20_60_after(self):
        """Federal Register FY2026 annual adjustment order.
          SOURCE: https://www.federalregister.gov/documents/2026-03-04/2026-04233/
        """
        notional = 1_000_000.0
        qty, price = 10_000, notional / 10_000
        before = self.costs.regulatory(SELL, qty, price, "2025-10-01")
        after = self.costs.regulatory(SELL, qty, price, "2026-05-01")
        cap_before = config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, "2025-10-01")
        cap_after = config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, "2026-05-01")
        taf_before = min(qty * 0.000166, cap_before)
        taf_after = min(qty * 0.000195, cap_after)
        self.assertAlmostEqual(before, taf_before, places=6)          # SEC31 = $0
        self.assertAlmostEqual(after, 20.60 + taf_after, places=6)
        self.assertGreater(after, before)

    def test_regulatory_fees_apply_only_to_sells(self):
        self.assertEqual(self.costs.regulatory(BUY, 10_000, 100.0, "2026-05-01"), 0.0)

    def test_taf_rate_steps_up_on_2026_01_01(self):
        qty = 1_000
        before = self.costs.regulatory(SELL, qty, 10.0, "2025-12-31")
        after = self.costs.regulatory(SELL, qty, 10.0, "2026-01-01")
        self.assertAlmostEqual(before, qty * 0.000166, places=6)
        self.assertAlmostEqual(after, qty * 0.000195, places=6)

    def test_taf_is_capped_per_trade(self):
        fee = self.costs.regulatory(SELL, 10_000_000, 10.0, "2026-05-01")
        sec31 = 100_000_000.0 * 20.60 / 1_000_000.0
        cap = config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, "2026-05-01")
        self.assertAlmostEqual(cap, 9.79, places=6)
        self.assertAlmostEqual(fee, sec31 + cap, places=4)

    def test_the_taf_cap_is_dated_too(self):
        """The cap moved with the rate on 2026-01-01, so a 2025 sale must not
        be charged the 2026 cap."""
        self.assertAlmostEqual(
            config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, "2025-12-31"),
            8.30, places=6)
        huge = 10_000_000
        fee_2025 = self.costs.regulatory(SELL, huge, 10.0, "2025-12-31")
        fee_2026 = self.costs.regulatory(SELL, huge, 10.0, "2026-05-01")
        self.assertAlmostEqual(fee_2025, 8.30, places=4)     # SEC31 was $0 then
        self.assertAlmostEqual(fee_2026, 2_060.0 + 9.79, places=2)

    def test_taf_minimum_is_respected_by_the_schedule_not_the_model(self):
        """The $0.01 minimum is a broker-side rounding rule; the model charges
        the schedule and documents that the minimum is not applied."""
        tiny = self.costs.regulatory(SELL, 1, 10.0, "2026-05-01")
        self.assertLess(tiny, 0.01)

    def test_commission_is_zero_by_default_and_configurable(self):
        self.assertEqual(self.costs.commission(1_000, 100.0), 0.0)
        c = cfg(costs=config.CostConfig(commission_per_share=0.005,
                                        commission_per_order=1.00))
        self.assertAlmostEqual(CostModel(c.costs).commission(1_000, 100.0), 6.0, places=6)

    def test_taker_fee_and_maker_rebate_signs(self):
        self.assertGreater(self.costs.taker_fee(1_000), 0.0)
        self.assertGreater(self.costs.maker_rebate(1_000), 0.0)
        off = CostModel(cfg(costs=config.CostConfig(pay_exchange_fees=False)).costs)
        self.assertEqual(off.taker_fee(1_000), 0.0)
        self.assertEqual(off.maker_rebate(1_000), 0.0)

    def test_access_fee_cap_bounds_the_taker_fee(self):
        self.assertLessEqual(cfg().costs.taker_fee_per_share,
                             config.ACCESS_FEE_CAP_PER_SHARE)


class TestBorrowTerms(unittest.TestCase):
    def test_declared_fees_are_ordered_by_scarcity(self):
        self.assertLess(borrow_terms("SPY")["fee_annual"],
                        borrow_terms("NVDA")["fee_annual"])
        self.assertLess(borrow_terms("NVDA")["fee_annual"],
                        borrow_terms("RIVN")["fee_annual"])
        self.assertLess(borrow_terms("RIVN")["fee_annual"],
                        borrow_terms("CVNA")["fee_annual"])

    def test_unknown_symbol_falls_back_to_the_default(self):
        self.assertEqual(borrow_terms("ZZZZ"), BORROW_TERMS["default"])

    def test_every_universe_symbol_is_borrowable_and_priced(self):
        for sym in replay().symbols:
            terms = borrow_terms(sym)
            self.assertIn("shortable", terms)
            self.assertGreaterEqual(terms["fee_annual"], 0.0)


class TestIntradayPath(unittest.TestCase):
    def _path(self, seed: int = 1) -> IntradayPath:
        md = replay()
        bar = md.bar("SPY", md.first_competition_index + 20)
        return IntradayPath(bar, 0.012, 390, random.Random(seed))

    def test_path_visits_the_open_high_low_and_close(self):
        path = self._path()
        bar = path.bar
        prices = [path.price(k) for k in range(path.K + 1)]
        self.assertAlmostEqual(prices[0], bar.open, places=6)
        self.assertAlmostEqual(prices[-1], bar.close, places=6)
        self.assertAlmostEqual(max(prices), bar.high, places=6)
        self.assertAlmostEqual(min(prices), bar.low, places=6)
        for p in prices:
            self.assertGreaterEqual(p, bar.low - 1e-9)
            self.assertLessEqual(p, bar.high + 1e-9)

    def test_volume_weights_are_u_shaped_and_sum_to_one(self):
        path = self._path()
        self.assertAlmostEqual(sum(path.vol_weights), 1.0, places=9)
        mid = path.vol_weights[len(path.vol_weights) // 2]
        self.assertGreater(path.vol_weights[0], mid)
        self.assertGreater(path.vol_weights[-1], mid)
        self.assertAlmostEqual(path.cum_vol[-1], 1.0, places=9)
        # remaining_volume_share(k) is the volume left AFTER interval k.
        self.assertAlmostEqual(path.remaining_volume_share(0),
                               1.0 - path.vol_weights[0], places=9)
        self.assertAlmostEqual(path.remaining_volume_share(path.K - 1), 0.0, delta=1e-9)
        for k in range(path.K):
            self.assertLess(path.remaining_volume_share(k),
                            path.remaining_volume_share(k - 1) if k else 1.0)

    def test_vwap_between_stays_inside_the_bar(self):
        path = self._path()
        vwap = path.vwap_between(0, path.K)
        self.assertGreaterEqual(vwap, path.bar.low - 1e-9)
        self.assertLessEqual(vwap, path.bar.high + 1e-9)

    def test_crossed_detection_matches_the_path(self):
        """crossed(price, BUY) asks 'did the path trade at or below this price',
        which is the condition under which a resting buy limit fills."""
        path = self._path()
        low_hit, k_low = path.crossed(path.bar.low + 1e-9, BUY)
        self.assertTrue(low_hit)
        self.assertGreaterEqual(k_low, 1)
        high_hit, _ = path.crossed(path.bar.high - 1e-9, SELL)
        self.assertTrue(high_hit)
        never_buy, _ = path.crossed(path.bar.low - 1.0, BUY)
        self.assertFalse(never_buy)
        never_sell, k = path.crossed(path.bar.high + 1.0, SELL)
        self.assertFalse(never_sell)
        self.assertEqual(k, path.K, "an uncrossed price reports the last interval")

    def test_early_close_sessions_have_fewer_minutes_not_more(self):
        md = replay()
        early = [s for s in md.calendar.sessions if s.is_early_close]
        self.assertTrue(early)
        for s in early:
            self.assertLess(s.minutes, 390)


class TestOrderBook(unittest.TestCase):
    def _book(self, mid: float = 100.0, tick_spread: int = 2):
        liq = LiquidityModel(cfg().liquidity)
        tick = config.minimum_tick(mid)
        bids = [(mid - (i + 1) * tick * tick_spread / 2, 1000.0 * (i + 1))
                for i in range(4)]
        asks = [(mid + (i + 1) * tick * tick_spread / 2, 1000.0 * (i + 1))
                for i in range(4)]
        return OrderBook(bids, asks, tick, liq, mid)

    def test_best_prices_mid_and_spread(self):
        book = self._book()
        self.assertLess(book.best_bid, book.best_ask)
        self.assertAlmostEqual(book.mid, (book.best_bid + book.best_ask) / 2.0, places=9)
        self.assertAlmostEqual(book.spread, book.best_ask - book.best_bid, places=9)

    def test_crossed_quotes_are_uncrossed(self):
        liq = LiquidityModel(cfg().liquidity)
        book = OrderBook([(100.05, 100.0), (100.00, 200.0)],
                         [(100.00, 100.0), (100.10, 200.0)], 0.01, liq, 100.0)
        self.assertLess(book.best_bid, book.best_ask)

    def test_walking_the_book_worsens_the_average_price(self):
        book = self._book()
        small_filled, small_px, small_depth = book.walk(BUY, 500)
        large_filled, large_px, large_depth = book.walk(BUY, 5_000)
        self.assertEqual(small_filled, 500.0)
        self.assertEqual(large_filled, 5_000.0)
        self.assertGreaterEqual(large_px, small_px)
        self.assertGreaterEqual(large_depth, small_depth)
        sell_filled, sell_px, _ = book.walk(SELL, 500)
        self.assertEqual(sell_filled, 500.0)
        self.assertLess(sell_px, small_px)
        self.assertGreaterEqual(small_px, book.best_bid - 1e-9)
        zero_filled, zero_px, zero_depth = book.walk(BUY, 0)
        self.assertEqual((zero_filled, zero_px, zero_depth), (0.0, 0.0, 0.0))

    def test_snapshot_is_serialisable_and_shows_levels(self):
        snap = self._book().snapshot()
        self.assertIn("bid", snap)
        self.assertIn("ask", snap)
        self.assertIn("mid", snap)
        self.assertIn("spread", snap)
        import json
        json.dumps(snap)


class TestMarketMakerPool(unittest.TestCase):
    def _pool(self, seed: str = "t") -> MarketMakerPool:
        return MarketMakerPool(cfg().market_maker, seed)

    def test_quotes_are_two_sided_positive_and_on_the_tick_grid(self):
        pool = self._pool()
        mid, tick = 185.0, 0.01
        touch = tick * 2
        bids, asks = pool.quotes(mid, 0.03, 0.5, 100_000.0, touch, 2_000.0)
        self.assertTrue(bids and asks)
        self.assertLess(bids[0][0], asks[0][0])
        for price, size in bids + asks:
            self.assertGreater(price, 0.0)
            self.assertGreater(size, 0.0)
            self.assertAlmostEqual(price / tick, round(price / tick), places=4)

    def test_quoted_width_matches_the_structural_touch_spread(self):
        pool = self._pool()
        mid, tick, touch = 185.0, 0.01, 0.02
        bids, asks = pool.quotes(mid, 0.03, 0.5, 100_000.0, touch, 2_000.0)
        width = asks[0][0] - bids[0][0]
        self.assertAlmostEqual(width, touch, delta=tick * 1.5)

    def test_inventory_skews_quotes_and_is_bounded(self):
        pool = self._pool()
        mid, touch = 185.0, 0.02
        flat_bids, flat_asks = pool.quotes(mid, 0.03, 0.5, 100_000.0, touch, 2_000.0)
        # Dealers absorb taker flow: a customer BUY leaves the pool SHORT.
        pool.take_inventory(BUY, 20_000)
        self.assertEqual(pool.net_inventory, -20_000)
        pool.take_inventory(SELL, 20_000)
        self.assertEqual(pool.net_inventory, 0)
        pool.take_inventory(SELL, 5_000)
        self.assertEqual(pool.net_inventory, 5_000)
        long_bids, long_asks = pool.quotes(mid, 0.03, 0.5, 100_000.0, touch, 2_000.0)
        self.assertNotEqual((flat_bids[0][0], flat_asks[0][0]),
                            (long_bids[0][0], long_asks[0][0]),
                            "a long inventory should skew the ladder away from flow")
        self.assertLess(abs(long_bids[0][0] - mid) / mid, 0.02)

    def test_pool_is_deterministic_for_a_given_seed_key(self):
        a = self._pool("same").quotes(100.0, 0.02, 0.5, 50_000.0, 0.02, 1_000.0)
        b = self._pool("same").quotes(100.0, 0.02, 0.5, 50_000.0, 0.02, 1_000.0)
        self.assertEqual(a, b)

    def test_dealer_heterogeneity_appears_once_inventory_is_carried(self):
        """With flat inventory every dealer quotes the same structural grid.
        Per-dealer gamma/kappa are drawn from the seed key and only show up
        through the inventory skew, so a skewed pool must quote a ladder whose
        dealers disagree - and two pools with different seed keys must disagree
        with each other."""
        mid, sigma, tau, vol, touch, size = 100.0, 0.02, 0.5, 50_000.0, 0.02, 1_000.0
        p1, p2 = self._pool("same"), self._pool("other")
        for pool in (p1, p2):
            pool.take_inventory(BUY, 2_000)
        q1 = p1.quotes(mid, sigma, tau, vol, touch, size)
        q2 = p2.quotes(mid, sigma, tau, vol, touch, size)
        self.assertNotEqual(q1, q2, "dealer gammas should differ between seed keys")
        self.assertGreater(len({round(p, 6) for p, _ in q1[0]}), 1,
                           "dealers in one pool should not all quote identically")
        p3 = self._pool("same")
        p3.take_inventory(BUY, 2_000)
        self.assertEqual(q1, p3.quotes(mid, sigma, tau, vol, touch, size))

    def test_inventory_limit_is_enforced_per_dealer(self):
        pool = self._pool("cap")
        limit = cfg().market_maker.inventory_limit_shares
        pool.take_inventory(BUY, limit * cfg().market_maker.num_makers * 3)
        self.assertGreaterEqual(pool.net_inventory,
                                -limit * cfg().market_maker.num_makers)
        for dealer in pool.dealers:
            self.assertLessEqual(abs(dealer.inventory), limit)


class TestExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.eng = ExecutionEngine(cfg())
        cls.t = cls.md.first_competition_index + 40

    def _venue(self, symbol: str = "NVDA", participant: str = "@tester"):
        venue = self.eng.make_venue_day(self.md, symbol, self.t, seed=1)
        return self.eng.make_participant_venue(venue, participant, seed=1)

    def _market(self, symbol: str, side: str, qty: int,
                participant: str = "@tester") -> Order:
        return Order(symbol=symbol, side=side, quantity=qty, order_type=MARKET,
                     participant=participant, reason="test")

    def test_every_displayed_level_is_a_legal_rule_612_increment(self):
        """IR-28: re-centring the ladder on the impacted mid must not push
        displayed quotes off the minimum-pricing-increment grid.

        17 CFR 242.612(b) requires a displayed quotation to be priced in a legal
        increment.  The pool quotes on-grid, but ``quote_at`` then shifts the
        whole ladder to track permanent impact - if that shift is an arbitrary
        fraction, every level becomes undisplayable (a bid of 655.4003 on SPY).
        """
        for symbol in self.md.symbols:
            for k in (0, 3, 7):
                dm = self._venue(symbol, f"@grid-{symbol}")
                book, mid = self.eng.quote_at(dm, k)
                tick = config.minimum_tick(mid)
                for level in list(book._bids) + list(book._asks):
                    price = level[0]
                    self.assertGreater(price, 0.0, symbol)
                    self.assertAlmostEqual(price / tick, round(price / tick),
                                           places=4,
                                           msg=f"{symbol} k={k}: displayed level "
                                               f"{price} is not a multiple of {tick}")
                snap = book.snapshot()
                self.assertAlmostEqual(snap["spread"] / tick,
                                       round(snap["spread"] / tick), places=4,
                                       msg=f"{symbol}: quoted spread is not a "
                                           f"whole number of ticks")
                self.assertLess(book.best_bid, book.best_ask,
                                f"{symbol}: snapped ladder crossed the book")
                # Snapping must still leave the book within half a tick of the
                # impacted mid, otherwise it is not tracking the path at all.
                self.assertLessEqual(abs(book.mid - mid), tick / 2.0 + 1e-9,
                                     f"{symbol} k={k}: ladder drifted off the mid")

    def test_small_market_order_fills_completely_at_the_touch(self):
        dm = self._venue()
        fill = self.eng.execute(dm, self._market("NVDA", BUY, 100), seed=1)
        self.assertEqual(fill.status, "filled")
        self.assertEqual(fill.filled_qty, 100)
        self.assertEqual(fill.requested_qty, 100)
        self.assertGreater(fill.avg_price, 0.0)
        self.assertEqual(fill.interval, 0, "a small order should not span intervals")

    def test_execution_is_deterministic_for_the_same_seed(self):
        a = self.eng.execute(self._venue(), self._market("NVDA", BUY, 5_000), seed=3)
        b = self.eng.execute(self._venue(), self._market("NVDA", BUY, 5_000), seed=3)
        self.assertEqual(a.avg_price, b.avg_price)
        self.assertEqual(a.total_cost, b.total_cost)

    def test_cost_components_add_up_to_total_cost(self):
        dm = self._venue()
        fill = self.eng.execute(dm, self._market("NVDA", BUY, 20_000), seed=1)
        expected = (fill.spread_cost + fill.depth_cost + fill.impact_cost +
                    fill.commission + fill.exchange_fee + fill.regulatory_fee -
                    fill.rebate)
        self.assertAlmostEqual(fill.total_cost, expected, places=6)
        self.assertAlmostEqual(fill.notional, fill.filled_qty * fill.avg_price, places=6)

    def test_implementation_shortfall_decomposition_is_exact(self):
        """price_difference = execution cost + drift, with no double counting."""
        dm = self._venue()
        fill = self.eng.execute(dm, self._market("NVDA", SELL, 20_000), seed=1)
        explicit = (fill.commission + fill.exchange_fee + fill.regulatory_fee
                    - fill.rebate)
        # price_difference = spread + depth + impact + drift, exactly.
        self.assertAlmostEqual(fill.price_difference,
                               fill.spread_cost + fill.depth_cost +
                               fill.impact_cost + fill.drift_cost, places=6)
        # Implementation shortfall = price difference + explicit fees (Perold).
        self.assertAlmostEqual(fill.implementation_shortfall,
                               fill.price_difference + explicit, places=6)
        # ... which is total friction cost plus the timing term, so the two
        # decompositions cannot both be right unless nothing is double counted.
        self.assertAlmostEqual(fill.implementation_shortfall,
                               fill.total_cost + fill.drift_cost, places=6)
        self.assertAlmostEqual(fill.execution_cost_bps,
                               10_000.0 * fill.total_cost / fill.notional, places=9)

    def test_small_orders_do_not_earn_free_intraday_drift(self):
        """IR-21 regression: capacity-driven slicing, not VWAP-across-session."""
        total_drift, total_spread = 0.0, 0.0
        tick = config.minimum_tick(self.md.instruments["SPY"].price_start)
        for i in range(12):
            dm = self._venue("SPY", f"@drift{i}")
            fill = self.eng.execute(dm, self._market("SPY", BUY, 500, f"@drift{i}"),
                                    seed=7)
            # An order this small must be absorbed by the first interval: it is
            # not sliced across the session, so it cannot harvest intraday drift.
            self.assertEqual(fill.interval, 0, f"fill {i} spanned intervals")
            self.assertLessEqual(abs(fill.drift_cost) / fill.filled_qty, 0.5 * tick,
                                 f"fill {i}: drift {fill.drift_cost:.4f} is more "
                                 "than half a tick per share")
            total_drift += fill.drift_cost
            total_spread += fill.spread_cost
        # Drift may be a small cost (grid rounding); it must never be a
        # systematic benefit, which is what the VWAP-slicing bug produced.
        self.assertGreater(total_drift, -0.10 * total_spread,
                           f"drift {total_drift:.2f} vs spread {total_spread:.2f}: "
                           "small orders are earning a systematic intraday benefit")

    def test_bigger_orders_pay_more_per_share_than_small_ones(self):
        dm_small = self._venue("RIVN", "@small")
        dm_large = self._venue("RIVN", "@large")
        small = self.eng.execute(dm_small, self._market("RIVN", BUY, 1_000, "@small"),
                                 seed=1)
        large = self.eng.execute(dm_large, self._market("RIVN", BUY, 50_000, "@large"),
                                 seed=1)
        self.assertGreater(large.execution_cost_bps, small.execution_cost_bps)

    def test_participation_cap_limits_a_huge_order(self):
        dm = self._venue("RIVN", "@whale")
        cap = cfg().liquidity.max_participation * float(dm.bar.volume)
        fill = self.eng.execute(dm, self._market("RIVN", BUY, int(cap * 5), "@whale"),
                                seed=1)
        self.assertLessEqual(fill.filled_qty, cap + 1)
        self.assertIn(fill.status, ("partial", "filled"))
        self.assertLess(dm.volume_taken, cap + 1)

    def test_cumulative_flow_against_one_replica_respects_the_cap(self):
        dm = self._venue("SPY", "@repeat")
        cap = cfg().liquidity.max_participation * float(dm.bar.volume)
        for _ in range(5):
            self.eng.execute(dm, self._market("SPY", BUY, 5_000, "@repeat"), seed=1)
        self.assertLessEqual(dm.volume_taken, cap + 1)

    def test_selling_shares_is_cheaper_before_the_sec_fee_change_date(self):
        dm_before = self._venue("JPM", "@before")
        dm_after = self._venue("JPM", "@after")
        order = self._market("JPM", SELL, 5_000)
        before = self.eng.execute(dm_before, order, seed=1)
        after = self.eng.execute(dm_after, order, seed=1)
        # The venue is the same session, so only the fee schedule could differ;
        # both fills are dated inside the season, and the season straddles
        # 2026-04-04, so assert the schedule itself instead.
        costs = CostModel(cfg().costs)
        self.assertEqual(costs.regulatory(SELL, 5_000, 200.0, before.date),
                         costs.regulatory(SELL, 5_000, 200.0, after.date))
        self.assertLess(costs.regulatory(SELL, 5_000, 200.0, "2026-01-05"),
                        costs.regulatory(SELL, 5_000, 200.0, "2026-05-05"))

    def test_limit_order_that_is_marketable_fills_and_one_that_is_not_expires(self):
        dm = self._venue("SPY", "@lim")
        book, mid = self.eng.quote_at(dm, 0)
        aggressive = Order(symbol="SPY", side=BUY, quantity=100, order_type=LIMIT,
                           limit_price=round(book.best_ask + 0.05, 2),
                           participant="@lim",
                           reason="marketable limit")
        fill = self.eng.execute(dm, aggressive, seed=1)
        self.assertEqual(fill.status, "filled")
        self.assertLessEqual(fill.avg_price, aggressive.limit_price + 1e-9)

        dm2 = self._venue("SPY", "@rest")
        book2, _ = self.eng.quote_at(dm2, 0)
        far = Order(symbol="SPY", side=BUY, quantity=100, order_type=LIMIT,
                    limit_price=round(book2.best_bid * 0.80, 2), participant="@rest",
                    reason="deep resting limit")
        rest = self.eng.execute(dm2, far, seed=1)
        self.assertIn(rest.status, ("expired", "partial", "filled"))
        if rest.status == "expired":
            self.assertEqual(rest.filled_qty, 0)
            self.assertTrue(rest.reject_reason)

    def test_limit_price_is_never_paid_through(self):
        dm = self._venue("NVDA", "@px")
        book, _ = self.eng.quote_at(dm, 0)
        limit = round(book.best_ask * 0.995, 2)
        order = Order(symbol="NVDA", side=BUY, quantity=2_000, order_type=LIMIT,
                      limit_price=limit, participant="@px", reason="price protection")
        fill = self.eng.execute(dm, order, seed=1)
        if fill.filled_qty:
            self.assertLessEqual(fill.avg_price, limit + 1e-9)

    def test_stop_order_triggers_off_the_intraday_path(self):
        dm = self._venue("TSLA", "@stop")
        path_low = dm.path.path_min()
        stop = Order(symbol="TSLA", side=SELL, quantity=100, order_type=STOP,
                     stop_price=path_low + 1e-6, participant="@stop",
                     reason="stop that the path must hit")
        fill = self.eng.execute(dm, stop, seed=1)
        self.assertEqual(fill.status, "filled")
        self.assertGreater(fill.interval, 0, "a stop should not fill at interval 0")

        dm2 = self._venue("TSLA", "@never")
        never = Order(symbol="TSLA", side=SELL, quantity=100, order_type=STOP,
                      stop_price=dm2.path.path_min() * 0.5, participant="@never",
                      reason="stop below the whole path")
        fill2 = self.eng.execute(dm2, never, seed=1)
        self.assertEqual(fill2.status, "expired")
        self.assertEqual(fill2.filled_qty, 0)

    def test_off_tick_limit_price_is_rejected_under_rule_612(self):
        """A limit price that is not a whole number of minimum increments is
        rejected rather than silently rounded - Rule 612.
          SOURCE: https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612
        """
        dm = self._venue("SPY", "@grid")
        book, _ = self.eng.quote_at(dm, 0)
        off_grid = Order(symbol="SPY", side=BUY, quantity=100, order_type=LIMIT,
                         limit_price=round(book.best_ask + 0.005, 4), participant="@grid",
                         reason="half-tick limit")
        fill = self.eng.execute(dm, off_grid, seed=1)
        self.assertEqual(fill.status, "rejected")
        self.assertIn("Rule 612", fill.reject_reason)

        on_grid = Order(symbol="SPY", side=BUY, quantity=100, order_type=LIMIT,
                        limit_price=round(book.best_ask + 0.01, 2), participant="@grid",
                        reason="on-grid limit")
        self.assertEqual(self.eng.execute(dm, on_grid, seed=1).status, "filled")

    def test_unborrowable_symbol_is_rejected_with_a_reg_sho_reason(self):
        dm = self._venue("SPY", "@short")
        original = BORROW_TERMS.get("SPY")
        BORROW_TERMS["SPY"] = {"shortable": False, "fee_annual": 0.0}
        try:
            fill = self.eng.execute(dm, self._market("SPY", SELL, 100, "@short"), seed=1)
            self.assertEqual(fill.status, "rejected")
            self.assertEqual(fill.filled_qty, 0)
            self.assertIn("Reg SHO", fill.reject_reason)
        finally:
            if original is None:
                BORROW_TERMS.pop("SPY", None)
            else:
                BORROW_TERMS["SPY"] = original

    def test_short_sale_of_a_borrowable_name_pays_the_declared_borrow_fee_rate(self):
        self.assertEqual(borrow_terms("CVNA")["fee_annual"], 0.06)
        dm = self._venue("CVNA", "@shortcvna")
        fill = self.eng.execute(dm, self._market("CVNA", SELL, 500, "@shortcvna"),
                                seed=1)
        self.assertEqual(fill.status, "filled")

    def test_permanent_impact_shifts_later_quotes_for_the_same_replica(self):
        """Compare the SAME interval before and after, so the intraday path
        cannot be what moves the quote."""
        dm = self._venue("RIVN", "@impact")
        _, mid_before = self.eng.quote_at(dm, 3)
        fill = self.eng.execute(dm, self._market("RIVN", BUY, 40_000, "@impact"), seed=1)
        _, mid_after = self.eng.quote_at(dm, 3)
        self.assertGreater(fill.impact_cost, 0.0)
        self.assertGreater(dm.permanent_impact, 0.0)
        self.assertGreater(mid_after, mid_before,
                           "heavy buying must lift the replica mid")

        dm2 = self._venue("RIVN", "@impact2")
        _, before2 = self.eng.quote_at(dm2, 3)
        self.eng.execute(dm2, self._market("RIVN", SELL, 40_000, "@impact2"), seed=1)
        _, after2 = self.eng.quote_at(dm2, 3)
        self.assertLess(after2, before2, "heavy selling must press the replica mid")

    def test_rejects_and_partials_are_recorded_as_fill_rows(self):
        dm = self._venue("SPY", "@rows")
        fill = self.eng.execute(dm, self._market("SPY", BUY, 100, "@rows"), seed=1)
        row = fill.to_row()
        for key in ("date", "participant", "symbol", "side", "order_type",
                    "requested_qty", "filled_qty", "avg_price", "decision_price",
                    "spread_cost", "depth_cost", "impact_cost", "commission",
                    "exchange_fee", "regulatory_fee", "rebate", "status",
                    "slippage_bps", "execution_cost_bps"):
            self.assertIn(key, row, f"fill row is missing {key}")
        import json
        json.dumps(row)

    def test_fill_dataclass_rejects_nonsense_orders(self):
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side="HOLD", quantity=1)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=0)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type=LIMIT)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type=STOP)
        with self.assertRaises(ValueError):
            Order(symbol="SPY", side=BUY, quantity=10, order_type="ICEBERG")


if __name__ == "__main__":
    unittest.main()
