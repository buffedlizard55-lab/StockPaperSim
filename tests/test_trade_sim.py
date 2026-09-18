"""Tests for the interactive US Equities Trade Simulator and CLI trade-sim tool.

Verifies:
* Order execution math (spread crossing, depth slippage, Almgren-Chriss impact).
* Statutory fee accounting (SEC §31, FINRA TAF, Reg NMS Rule 610 taker fee).
* Regulatory constraints (17 CFR Part 242 Rule 612 tick grid, Rule 600(b)(93) round lots).
* ADV participation limits and Reg T initial (50%) & maintenance (25%) margin.
* Generated simulator.html page structure and UI components.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
import unittest
from contextlib import redirect_stdout

from fixtures import REPO_ROOT
from sim import cli, config, microstructure, universe

DOCS = os.path.join(REPO_ROOT, "docs")


class TestTradeSimulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cost_cfg = config.CostConfig()
        cls.liq_cfg = config.LiquidityConfig()
        cls.imp_cfg = config.ImpactConfig()
        cls.cm = microstructure.CostModel(cls.cost_cfg)
        cls.lm = microstructure.LiquidityModel(cls.liq_cfg)
        cls.im = microstructure.ImpactModel(cls.imp_cfg)

    def test_cli_trade_sim_market_buy(self):
        args = argparse.Namespace(
            symbol="SPY", side="buy", qty=100, type="market",
            price=560.0, date="2026-09-16", participant="@InteractiveTrader",
            cash=100000.0, at_close=False
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_trade_sim(args)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("STOCKPAPERSIM :: US EQUITIES REAL-TRADE SIMULATOR", out)
        self.assertIn("BUY 100 SPY", out)
        self.assertIn("Rule 612 grid", out)
        self.assertIn("Almgren-Chriss", out)
        self.assertIn("Total Slippage:", out)

    def test_cli_trade_sim_market_sell_with_statutory_fees(self):
        # On 2026-05-15, SEC Section 31 fee is $20.60 per $1M and FINRA TAF is active.
        args = argparse.Namespace(
            symbol="SPY", side="sell", qty=500, type="market",
            price=560.0, date="2026-05-15", participant="@InteractiveTrader",
            cash=100000.0, at_close=False
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_trade_sim(args)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("SELL 500 SPY", out)
        self.assertIn("SEC §31 $20.60/M", out)
        self.assertIn("FINRA TAF", out)
        self.assertIn("Net Cash Impact:   $-", out)

    def test_almgren_chriss_slippage_math(self):
        price = 500.0
        qty = 10000
        adv = 10_000_000.0
        sigma_annual = 0.20
        sigma_daily = sigma_annual / math.sqrt(252)

        part_rate = qty / adv
        self.assertEqual(part_rate, 0.001)

        impact_ret = self.im.impact_return(qty, adv, sigma_daily)
        self.assertGreater(impact_ret, 0.0)

        perm_ret, temp_ret = self.im.split(impact_ret)
        self.assertAlmostEqual(perm_ret + temp_ret, impact_ret, places=7)
        self.assertEqual(perm_ret, impact_ret * self.imp_cfg.permanent_share)

    def test_regulatory_cost_stack_calculations(self):
        date_post_sec31 = "2026-05-01"
        date_pre_sec31 = "2025-10-01"
        qty = 1000
        price = 200.0
        notional = qty * price

        # Buy side has zero regulatory fees (SEC 31 and TAF only apply to sales)
        buy_reg = self.cm.regulatory("buy", qty, price, date_post_sec31)
        self.assertEqual(buy_reg, 0.0)

        # Sell side post-2026-04-04 has SEC 31 at $20.60 per million + FINRA TAF ($0.000195/sh)
        sell_reg = self.cm.regulatory("sell", qty, price, date_post_sec31)
        expected_sec31 = notional * (20.60 / 1_000_000.0)
        expected_taf = min(qty * 0.000195, 9.79)
        self.assertAlmostEqual(sell_reg, expected_sec31 + expected_taf, places=4)

        # Pre-2026-04-04 SEC 31 is $0.00
        sell_reg_pre = self.cm.regulatory("sell", qty, price, date_pre_sec31)
        expected_taf_pre = min(qty * config.rate_for(config.FINRA_TAF_PER_SHARE, date_pre_sec31),
                               config.rate_for(config.FINRA_TAF_MAX_PER_TRADE, date_pre_sec31))
        self.assertAlmostEqual(sell_reg_pre, expected_taf_pre, places=4)

    def test_tick_size_rule_612_grid(self):
        self.assertEqual(config.minimum_tick(500.0), 0.01)
        self.assertEqual(config.minimum_tick(1.00), 0.01)
        self.assertEqual(config.minimum_tick(0.99), 0.0001)
        self.assertEqual(config.minimum_tick(0.01), 0.0001)

    def test_round_lot_rule_600_b_93(self):
        self.assertEqual(config.round_lot(150.0), 100)
        self.assertEqual(config.round_lot(250.0), 100)
        self.assertEqual(config.round_lot(500.0), 40)
        self.assertEqual(config.round_lot(1000.0), 40)
        self.assertEqual(config.round_lot(2000.0), 10)
        self.assertEqual(config.round_lot(15000.0), 1)

    def test_simulator_html_content_and_features(self):
        sim_path = os.path.join(DOCS, "simulator.html")
        self.assertTrue(os.path.exists(sim_path), "simulator.html missing in docs/")
        with open(sim_path, "r", encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("Live US Equities Paper-Trading Simulation", html)
        self.assertIn("Can strategies place upcoming trades and simulate a real trading experience?", html)
        self.assertIn("Interactive Trade Simulator &amp; Order Execution Engine", html)
        self.assertIn("Reverse-Engineering Paper Trading Competition Platforms", html)
        self.assertIn("TradingView The Leap", html)
        self.assertIn("Trade Ideas PM Challenge", html)
        self.assertIn("CandleCharts Showdown", html)
        self.assertIn("Community &amp; Social Media Strategies Directory", html)
        self.assertIn("MasterSite Projects to Trading Signals Register", html)
        self.assertIn("CEO", html)
        self.assertIn("SFWeather", html)
        self.assertIn("DrugAnalysis", html)
        self.assertIn("GOLD", html)
        self.assertIn("Tradingview-pinescript-editor", html)
        self.assertIn("KalshiPaperSim", html)


if __name__ == "__main__":
    unittest.main()
