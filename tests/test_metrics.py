"""Metrics on toy NAV series (no network)."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics  # noqa: E402
import study  # noqa: E402


class MetricTests(unittest.TestCase):
    def test_cagr(self):
        nav = [("2020-01-01", 100.0), ("2022-01-01", 121.0)]
        self.assertAlmostEqual(metrics.cagr(nav), 0.1, places=3)

    def test_max_drawdown_dates(self):
        nav = [("2020-01-01", 100.0), ("2020-01-02", 120.0), ("2020-01-03", 90.0),
               ("2020-01-06", 110.0), ("2020-01-07", 125.0)]
        dd = metrics.max_drawdown(nav)
        self.assertAlmostEqual(dd["max_drawdown"], -0.25)
        self.assertEqual((dd["peak"], dd["trough"], dd["recovery"]),
                         ("2020-01-02", "2020-01-03", "2020-01-07"))
        self.assertEqual(dd["days_to_recover"], 5)

    def test_unrecovered_drawdown(self):
        nav = [("2020-01-01", 100.0), ("2020-01-02", 80.0)]
        self.assertIsNone(metrics.max_drawdown(nav)["recovery"])

    def test_drawdown_series(self):
        nav = [("a", 100.0), ("b", 50.0), ("c", 100.0)]
        self.assertEqual([v for _, v in metrics.drawdown_series(nav)], [0.0, -0.5, 0.0])

    def test_sortino_ignores_upside_volatility(self):
        up_heavy = [0.03, -0.01, 0.03, -0.01]
        symmetric = [0.02, -0.02, 0.02, -0.02]
        self.assertGreater(metrics.sortino(up_heavy), metrics.sortino(symmetric))
        self.assertTrue(math.isnan(metrics.sortino([0.01, 0.02])))

    def test_sharpe_sign(self):
        self.assertGreater(metrics.sharpe([0.01, 0.0, 0.02, -0.005]), 0)

    def test_beta_alpha(self):
        bench, nav2 = [("d0", 100.0)], [("d0", 100.0)]
        rets = [0.01, -0.02, 0.015, 0.005, -0.01]
        for i, r in enumerate(rets, 1):
            bench.append((f"d{i}", bench[-1][1] * (1 + r)))
            nav2.append((f"d{i}", nav2[-1][1] * (1 + 2 * r)))
        beta, alpha = metrics.beta_alpha(bench, bench)
        self.assertAlmostEqual(beta, 1.0)
        self.assertAlmostEqual(alpha, 0.0)
        beta2, _ = metrics.beta_alpha(nav2, bench)
        self.assertAlmostEqual(beta2, 2.0)

    def test_capture_of_benchmark_is_one(self):
        nav = [("2020-01-31", 100.0), ("2020-02-28", 110.0),
               ("2020-03-31", 99.0), ("2020-04-30", 105.0)]
        up, down = metrics.capture(nav, nav)
        self.assertAlmostEqual(up, 1.0)
        self.assertAlmostEqual(down, 1.0)

    def test_calendar_years(self):
        nav = [("2020-01-02", 100.0), ("2020-12-31", 110.0), ("2021-12-31", 99.0)]
        years = metrics.calendar_years(nav)
        self.assertAlmostEqual(years[2020], 0.10)
        self.assertAlmostEqual(years[2021], -0.10)

    def test_turnover_excludes_initial_and_win_rate(self):
        nav = [("2020-01-01", 100.0), ("2021-01-01", 100.0)]
        trades = [{"notional": 100.0, "reason": "initial"},
                  {"notional": 50.0, "reason": "trim"},
                  {"notional": 50.0, "reason": "new"}]
        closed = [{"pnl": 5.0, "days": 100}, {"pnl": -1.0, "days": 300}]
        s = metrics.trading_stats(trades, closed, nav)
        self.assertAlmostEqual(s["turnover"], 0.5, places=2)
        self.assertAlmostEqual(s["win_rate"], 0.5)
        self.assertAlmostEqual(s["avg_holding_days"], 200)

    def test_summarize_keys(self):
        nav = [("2020-01-01", 100.0), ("2020-06-01", 90.0), ("2021-01-01", 120.0)]
        s = metrics.summarize(nav, nav, [], [])
        for k in ("cagr", "sharpe", "sortino", "calmar", "max_drawdown", "beta",
                  "up_capture", "turnover"):
            self.assertIn(k, s)


class PointInTimeTests(unittest.TestCase):
    def test_picks_use_only_trailing_returns(self):
        days = [f"2021-{m:02d}-01" for m in range(1, 13)] + ["2022-03-01", "2022-06-01"]
        good = [(d, 100.0 + i) for i, d in enumerate(days)]
        bad = [(d, 100.0 - i) for i, d in enumerate(days)]
        nav, picks = study.point_in_time({"good": good, "bad": bad}, days, k=1,
                                         lookback=3)
        # first pick has no lookback history: arbitrary but valid
        self.assertEqual(len(nav), len(days))
        later = [p for p in picks if p["date"] == "2022-03-01"]
        self.assertEqual(later[0]["chosen"], ["good"])

    def test_window_start(self):
        cal = ["2015-10-23", "2015-10-26", "2020-10-26", "2025-10-24"]
        self.assertEqual(study.window_start(cal, "2025-10-24", 10), "2015-10-26")


if __name__ == "__main__":
    unittest.main()
