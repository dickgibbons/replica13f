"""Simulator rules on synthetic filings and prices (no network).

    python -m unittest discover -s tests
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import simulator  # noqa: E402
from simulator import Rules  # noqa: E402

CAL = []
_d = dt.date(2020, 1, 1)
while _d <= dt.date(2020, 12, 31):
    if _d.weekday() < 5:
        CAL.append(_d.isoformat())
    _d += dt.timedelta(days=1)

TMAP = {"AAA000001": "A", "BBB000002": "B", "CCC000003": "C"}
CUSIP = {v: k for k, v in TMAP.items()}


def row(ticker, shares, price=10.0):
    return {"cusip": CUSIP[ticker], "issuer": ticker, "shares": float(shares),
            "value_usd": float(shares) * price}


def filing(period, accepted, rows):
    return period, {"filing_date": accepted[:10], "accepted": accepted,
                    "form": "13F-HR", "holdings": rows}


def flat(price=10.0):
    return lambda t: [(d, price) for d in CAL]


def no_splits(t):
    return []


def run(hp_items, rules=None, series_fn=None, splits_fn=no_splits, **kw):
    rules = rules or Rules(cost_bps=0.0, min_trade_frac=0.0)
    series_fn = series_fn or flat()
    hp = dict(hp_items)
    events = simulator.build_events(hp, TMAP, CAL, rules, splits_fn, series_fn)
    return events, simulator.simulate(events, CAL, series_fn, rules, **kw)


def units_after(result, ticker):
    u = 0.0
    for t in result.trades:
        if t["ticker"] == ticker:
            u += t["units"] if t["side"] == "buy" else -t["units"]
    return u


Q1 = ("2019-12-31", "2020-02-14T21:30:00.000Z")   # 4:30pm ET Friday
Q2 = ("2020-03-31", "2020-05-15T14:00:00.000Z")   # 10am ET Friday
Q3 = ("2020-06-30", "2020-08-14T15:00:00.000Z")


class TimingTests(unittest.TestCase):
    def test_trades_next_trading_day_after_acceptance(self):
        events, res = run([filing(*Q1, [row("A", 100), row("B", 100)])])
        self.assertEqual(events[0].exec_date, "2020-02-17")
        self.assertEqual(res.first_event, "2020-02-17")
        self.assertTrue(all(t["date"] >= "2020-02-17" for t in res.trades))
        before = [v for d, v in res.nav if d < "2020-02-17"]
        self.assertTrue(all(abs(v - 100_000) < 1e-6 for v in before))

    def test_late_evening_utc_is_previous_et_day(self):
        # 03:00Z Saturday is 10pm ET Friday -> still trades Monday
        self.assertEqual(simulator.accepted_et_date("2020-02-15T03:00:00.000Z", "2020-02-15"),
                         dt.date(2020, 2, 14))

    def test_no_trade_before_price_move_is_known(self):
        # Stock doubles the day of acceptance; the copier only buys at the next close.
        def series(t):
            return [(d, 10.0 if d <= "2020-02-14" else 20.0) for d in CAL]
        _, res = run([filing(*Q1, [row("A", 100)])], series_fn=series)
        self.assertAlmostEqual(res.trades[0]["price"], 20.0)
        self.assertAlmostEqual(res.nav[-1][1], 100_000, places=4)

    def test_period_end_execution(self):
        rules = Rules(cost_bps=0.0, min_trade_frac=0.0, execution="period_end")
        events, _ = run([filing(*Q2, [row("A", 100)])], rules=rules)
        self.assertEqual(events[0].exec_date, "2020-04-01")


class RuleTests(unittest.TestCase):
    def test_initial_weights_follow_fund_value(self):
        _, res = run([filing(*Q1, [row("A", 300), row("B", 100)])])
        self.assertAlmostEqual(units_after(res, "A"), 7500)
        self.assertAlmostEqual(units_after(res, "B"), 2500)

    def test_proportional_trim(self):
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 50), row("B", 100)])])
        self.assertAlmostEqual(units_after(res, "A"), 2500)
        self.assertAlmostEqual(units_after(res, "B"), 5000)
        trim = [t for t in res.trades if t["reason"] == "trim"]
        self.assertEqual(len(trim), 1)
        self.assertEqual(trim[0]["date"], "2020-05-18")

    def test_full_exit_rule_sells_whole_position_on_any_cut(self):
        rules = Rules(cost_bps=0.0, min_trade_frac=0.0, sell_rule="full_exit")
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 90), row("B", 100)])], rules=rules)
        self.assertAlmostEqual(units_after(res, "A"), 0.0)

    def test_exit_when_name_disappears(self):
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("B", 100)])])
        self.assertAlmostEqual(units_after(res, "A"), 0.0)
        self.assertEqual([c["ticker"] for c in res.closed], ["A"])
        self.assertEqual(res.closed[0]["pnl"], 0.0)

    def test_add_is_funded_by_cash_from_earlier_trim(self):
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 50), row("B", 100)]),
                      filing(*Q3, [row("A", 100), row("B", 100)])])
        self.assertAlmostEqual(units_after(res, "A"), 5000)
        adds = [t for t in res.trades if t["reason"] == "add"]
        self.assertEqual(len(adds), 1)
        self.assertAlmostEqual(adds[0]["notional"], 25_000)

    def test_buys_scale_pro_rata_to_available_cash(self):
        # Trim raises 25k; the fund doubles B (wants 50k) -> only half is bought.
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 50), row("B", 200)])])
        self.assertAlmostEqual(units_after(res, "B"), 7500)
        self.assertAlmostEqual(res.nav[-1][1], 100_000, places=4)

    def test_no_forced_sells_for_new_names(self):
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 100), row("B", 100), row("C", 100)])])
        self.assertAlmostEqual(units_after(res, "C"), 0.0)
        self.assertFalse([t for t in res.trades if t["side"] == "sell"])

    def test_rebalance_policy_moves_to_new_weights(self):
        rules = Rules(cost_bps=0.0, min_trade_frac=0.0, cash_policy="rebalance")
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                      filing(*Q2, [row("A", 100), row("B", 100), row("C", 100)])],
                     rules=rules)
        for t in "ABC":
            self.assertAlmostEqual(units_after(res, t), 100_000 / 3 / 10, places=4)

    def test_costs_reduce_nav(self):
        rules = Rules(cost_bps=10.0, min_trade_frac=0.0)
        _, res = run([filing(*Q1, [row("A", 100)])], rules=rules)
        self.assertLess(res.nav[-1][1], 100_000)
        self.assertGreater(res.nav[-1][1], 99_800)

    def test_top_n_limits_names(self):
        rules = Rules(cost_bps=0.0, min_trade_frac=0.0, top_n=2)
        _, res = run([filing(*Q1, [row("A", 300), row("B", 200), row("C", 100)])],
                     rules=rules)
        self.assertEqual({t["ticker"] for t in res.trades}, {"A", "B"})


class SplitTests(unittest.TestCase):
    def test_split_does_not_look_like_an_add(self):
        splits = lambda t: [("2020-03-02", 4.0)] if t == "A" else []
        events, res = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                           filing(*Q2, [row("A", 400, 2.5), row("B", 100)])],
                          splits_fn=splits)
        self.assertAlmostEqual(events[1].ratios["A"], 1.0)
        self.assertEqual([t["reason"] for t in res.trades], ["initial", "initial"])

    def test_split_then_real_cut_is_a_trim(self):
        splits = lambda t: [("2020-03-02", 4.0)] if t == "A" else []
        events, _ = run([filing(*Q1, [row("A", 100), row("B", 100)]),
                         filing(*Q2, [row("A", 200, 2.5), row("B", 100)])],
                        splits_fn=splits)
        self.assertAlmostEqual(events[1].ratios["A"], 0.5)

    def test_split_outside_window_is_ignored(self):
        splits = lambda t: [("2020-04-01", 4.0)] if t == "A" else []
        events, _ = run([filing(*Q1, [row("A", 100)]),
                         filing("2020-03-31", "2020-05-15T14:00:00.000Z", [row("A", 100)])],
                        splits_fn=splits)
        self.assertAlmostEqual(events[1].ratios["A"], 1.0)


class WindowTests(unittest.TestCase):
    def test_start_mid_history_uses_latest_disclosed_portfolio(self):
        _, res = run([filing(*Q1, [row("A", 100)]),
                      filing(*Q2, [row("B", 100)])], start="2020-06-01")
        self.assertEqual(res.first_event, "2020-06-01")
        self.assertEqual({t["ticker"] for t in res.trades}, {"B"})
        self.assertFalse(res.late_start)

    def test_window_before_any_filing_is_late_start(self):
        _, res = run([filing(*Q2, [row("A", 100)])], start="2020-01-02")
        self.assertTrue(res.late_start)
        self.assertEqual(res.first_event, "2020-05-18")

    def test_stale_price_liquidates(self):
        def series(t):
            return [(d, 10.0) for d in CAL if t != "A" or d <= "2020-03-31"]
        rules = Rules(cost_bps=0.0, min_trade_frac=0.0, stale_days=5)
        _, res = run([filing(*Q1, [row("A", 100), row("B", 100)])],
                     rules=rules, series_fn=series)
        sold = [t for t in res.trades if t["reason"] == "no_price"]
        self.assertEqual(len(sold), 1)
        self.assertEqual(sold[0]["ticker"], "A")
        self.assertAlmostEqual(res.nav[-1][1], 100_000, places=4)


class CombineTests(unittest.TestCase):
    def test_equal_blend_of_sleeves(self):
        a = [("d1", 100.0), ("d2", 200.0)]
        b = [("d1", 50.0), ("d2", 50.0)]
        self.assertEqual(simulator.combine({"a": a, "b": b}), [("d1", 1.0), ("d2", 1.5)])


if __name__ == "__main__":
    unittest.main()
