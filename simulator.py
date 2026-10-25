"""Follow-the-filing portfolio simulator.

Models an investor who copies a fund's disclosed 13F book:
  - buys when a 13F becomes public (close of the next trading day after
    EDGAR acceptance, Eastern time), never before;
  - cuts a position by the same fraction the fund cut its shares, and sells
    it entirely when the fund exits;
  - adds to a position when the fund adds shares, and buys new names that
    enter the fund's top N, funded only by available cash (no forced sells).

Prices are adjusted closes, so dividends are reinvested in the stock. Fund
share counts are split-adjusted between report dates before comparing them.
The engine is pure Python and takes price/split lookups as functions so it
can run on synthetic data in tests.
"""
from __future__ import annotations
import bisect
import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Optional

from prices import split_factor

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # tz database unavailable
    _ET = None

SeriesFn = Callable[[str], list]   # ticker -> [(date_iso, price)]
SplitsFn = Callable[[str], list]   # ticker -> [(date_iso, ratio)]

EPS_UNITS = 1e-9


@dataclass
class Rules:
    top_n: int = 20
    weighting: str = "value"            # value | equal
    sell_rule: str = "proportional"     # proportional | full_exit
    cash_policy: str = "no_forced_sells"  # no_forced_sells | rebalance
    execution: str = "next_close"       # next_close | period_end
    cost_bps: float = 5.0
    cash_yield: float = 0.0             # annual rate earned on idle cash
    min_trade_frac: float = 0.0005      # skip trades smaller than this share of NAV
    stale_days: int = 10                # trading days without a price before liquidating


@dataclass
class FilingEvent:
    period: str
    exec_date: str                      # trading date the trades happen
    holdings: dict                      # ticker -> value_usd (all mapped positions)
    targets: dict                       # ticker -> weight among top N (sums to 1)
    ratios: dict                        # ticker -> split-adjusted share ratio vs previous filing
    mapped_share: float = 1.0           # share of the fund's top-N value with a known ticker


@dataclass
class Result:
    nav: list = field(default_factory=list)          # [(date_iso, value)]
    trades: list = field(default_factory=list)
    positions: list = field(default_factory=list)    # snapshot after each filing
    closed: list = field(default_factory=list)       # fully exited positions
    first_event: Optional[str] = None
    late_start: bool = False                         # no filing known at window start


def accepted_et_date(accepted: Optional[str], filing_date: str) -> dt.date:
    """Eastern-time calendar date of EDGAR acceptance (falls back to filing date)."""
    if not accepted:
        return dt.date.fromisoformat(filing_date)
    ts = dt.datetime.fromisoformat(accepted.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    if _ET is not None:
        return ts.astimezone(_ET).date()
    return (ts - dt.timedelta(hours=5)).date()


def _next_trading_day(calendar: list, after: dt.date) -> Optional[str]:
    i = bisect.bisect_right(calendar, after.isoformat())
    return calendar[i] if i < len(calendar) else None


def _group_by_ticker(rows: list, tmap: dict) -> dict:
    out: dict = {}
    for r in rows:
        tk = tmap.get(r["cusip"].upper())
        if not tk:
            continue
        g = out.setdefault(tk, {"value": 0.0, "shares": 0.0})
        g["value"] += r["value_usd"]
        if g["shares"] is not None:
            g["shares"] = None if r.get("shares") is None else g["shares"] + r["shares"]
    return out


def _mapped_share(rows: list, tmap: dict, top_n: int) -> float:
    """Fraction of the fund's top-N positions (by value, mapped or not)
    whose CUSIP resolved to a ticker."""
    by_key: dict = {}
    for r in rows:
        c = r["cusip"].upper()
        key = tmap.get(c) or "?" + c
        by_key[key] = by_key.get(key, 0.0) + r["value_usd"]
    top = sorted(by_key.items(), key=lambda kv: -kv[1])[:top_n]
    tot = sum(v for _, v in top)
    return sum(v for k, v in top if not k.startswith("?")) / tot if tot else 0.0


def _price_on_or_before(series: list, date_iso: str) -> Optional[float]:
    i = bisect.bisect_right(series, (date_iso, float("inf"))) - 1
    return series[i][1] if i >= 0 else None


def build_events(hp: dict, tmap: dict, calendar: list, rules: Rules,
                 splits_fn: SplitsFn, series_fn: Optional[SeriesFn] = None) -> list:
    """Turn {period: filing} into dated, split-adjusted FilingEvents.

    Share ratios are computed only for tickers that ever enter the top N,
    since those are the only positions the strategy can hold."""
    periods = sorted(hp)
    grouped_by = {p: _group_by_ticker(hp[p]["holdings"], tmap) for p in periods}
    targets_by = {}
    for p in periods:
        top = sorted(grouped_by[p].items(), key=lambda kv: -kv[1]["value"])[:rules.top_n]
        if rules.weighting == "equal":
            targets_by[p] = {tk: 1.0 / len(top) for tk, _ in top} if top else {}
        else:
            tot = sum(g["value"] for _, g in top) or 1.0
            targets_by[p] = {tk: g["value"] / tot for tk, g in top}
    holdable = set().union(*targets_by.values()) if targets_by else set()

    events = []
    prev = None
    prev_period = None
    for period in periods:
        filing = hp[period]
        grouped = grouped_by[period]
        targets = targets_by[period]
        if rules.execution == "period_end":
            exec_date = _next_trading_day(calendar, dt.date.fromisoformat(period))
        else:
            exec_date = _next_trading_day(
                calendar, accepted_et_date(filing.get("accepted"), filing["filing_date"]))

        ratios = {}
        if prev is not None:
            p0 = dt.date.fromisoformat(prev_period)
            p1 = dt.date.fromisoformat(period)
            for tk, g0 in prev.items():
                if tk not in holdable:
                    continue
                g1 = grouped.get(tk)
                if g1 is None:
                    ratios[tk] = 0.0
                elif g0["shares"] and g1["shares"] is not None:
                    adj_prev = g0["shares"] * split_factor(splits_fn(tk), p0, p1)
                    ratios[tk] = g1["shares"] / adj_prev
                elif g0["value"] > 0 and series_fn is not None:
                    s = series_fn(tk)
                    px0 = _price_on_or_before(s, prev_period)
                    px1 = _price_on_or_before(s, period)
                    if px0 and px1:
                        ratios[tk] = (g1["value"] / g0["value"]) / (px1 / px0)

        if exec_date is not None and targets:
            events.append(FilingEvent(
                period=period, exec_date=exec_date,
                holdings={tk: g["value"] for tk, g in grouped.items()},
                targets=targets, ratios=ratios,
                mapped_share=_mapped_share(filing["holdings"], tmap, rules.top_n),
            ))
        prev, prev_period = grouped, period
    events.sort(key=lambda e: (e.exec_date, e.period))
    return events


def _align(series: list, calendar: list) -> tuple:
    """Forward-filled prices on the calendar plus index of the last real print."""
    by_day = dict(series)
    prices, last_idx = [], []
    cur, cur_i = None, -1
    for i, day in enumerate(calendar):
        if day in by_day:
            cur, cur_i = by_day[day], i
        prices.append(cur)
        last_idx.append(cur_i)
    return prices, last_idx


class _Book:
    def __init__(self, cash: float, rules: Rules, result: Result):
        self.cash = cash
        self.units: dict = {}
        self.rules = rules
        self.result = result
        self.lots: dict = {}  # ticker -> {"opened", "invested", "proceeds"}

    def value(self, prices: dict) -> float:
        return self.cash + sum(u * prices[t] for t, u in self.units.items()
                               if prices.get(t) is not None)

    def _cost(self, notional: float) -> float:
        return notional * self.rules.cost_bps / 1e4

    def sell(self, t, units, price, day, reason, period):
        units = min(units, self.units.get(t, 0.0))
        if units <= EPS_UNITS:
            return
        notional = units * price
        cost = self._cost(notional)
        self.cash += notional - cost
        self.units[t] -= units
        lot = self.lots.setdefault(t, {"opened": day, "invested": 0.0, "proceeds": 0.0})
        lot["proceeds"] += notional - cost
        self.result.trades.append({
            "date": day, "ticker": t, "side": "sell", "units": units,
            "price": price, "notional": notional, "cost": cost,
            "reason": reason, "period": period,
        })
        if self.units[t] <= EPS_UNITS:
            del self.units[t]
            lot = self.lots.pop(t)
            pnl = lot["proceeds"] - lot["invested"]
            self.result.closed.append({
                "ticker": t, "opened": lot["opened"], "closed": day,
                "invested": lot["invested"], "proceeds": lot["proceeds"],
                "pnl": pnl,
                "return": pnl / lot["invested"] if lot["invested"] else 0.0,
                "days": (dt.date.fromisoformat(day)
                         - dt.date.fromisoformat(lot["opened"])).days,
            })

    def buy(self, t, notional, price, day, reason, period):
        if notional <= 0 or price is None:
            return
        cost = self._cost(notional)
        units = notional / price
        self.cash -= notional + cost
        self.units[t] = self.units.get(t, 0.0) + units
        lot = self.lots.setdefault(t, {"opened": day, "invested": 0.0, "proceeds": 0.0})
        lot["invested"] += notional + cost
        self.result.trades.append({
            "date": day, "ticker": t, "side": "buy", "units": units,
            "price": price, "notional": notional, "cost": cost,
            "reason": reason, "period": period,
        })


def _funded_buys(book: _Book, wants: list, prices: dict, day: str, period: str,
                 min_notional: float) -> None:
    """Execute (ticker, notional, reason) buys, scaled down pro-rata to cash."""
    wants = [(t, n, r) for t, n, r in wants if n > 0 and prices.get(t)]
    total = sum(n for _, n, _ in wants)
    if total <= 0:
        return
    budget = max(book.cash, 0.0) / (1 + book.rules.cost_bps / 1e4)
    scale = min(1.0, budget / total)
    for t, n, reason in wants:
        amount = n * scale
        if amount >= min_notional:
            book.buy(t, amount, prices[t], day, reason, period)


def _apply_event(book: _Book, ev: FilingEvent, prices: dict, day: str,
                 initial: bool) -> float:
    """Trade one filing. Returns coverage: share of target weight that had a price."""
    rules = book.rules
    nav = book.value(prices)
    min_notional = rules.min_trade_frac * nav
    priced = {t: w for t, w in ev.targets.items() if prices.get(t)}
    coverage = sum(priced.values())
    weights = {t: w / coverage for t, w in priced.items()} if coverage > 0 else {}

    if initial or rules.cash_policy == "rebalance":
        for t in list(book.units):
            if t not in weights and prices.get(t):
                book.sell(t, book.units[t], prices[t], day,
                          "initial" if initial else "rebalance", ev.period)
        nav = book.value(prices)
        wants = []
        for t, w in weights.items():
            gap = w * nav - book.units.get(t, 0.0) * prices[t]
            if gap < -min_notional:
                book.sell(t, -gap / prices[t], prices[t], day, "rebalance", ev.period)
            elif gap > min_notional:
                wants.append((t, gap, "initial" if initial else "rebalance"))
        _funded_buys(book, wants, prices, day, ev.period, min_notional)
        return coverage

    wants, held_before = [], set(book.units)
    for t in list(book.units):
        px = prices.get(t)
        if px is None:
            continue
        if t not in ev.holdings:
            book.sell(t, book.units[t], px, day, "exit", ev.period)
            continue
        r = ev.ratios.get(t)
        if r is None:
            continue
        if r < 1.0:
            frac = 1.0 if rules.sell_rule == "full_exit" else 1.0 - r
            if frac * book.units[t] * px >= min_notional or frac >= 1.0:
                book.sell(t, book.units[t] * frac, px, day,
                          "exit" if frac >= 1.0 else "trim", ev.period)
        elif r > 1.0:
            wants.append((t, (r - 1.0) * book.units[t] * px, "add"))
    for t, w in weights.items():
        if t not in held_before:
            wants.append((t, w * nav, "new"))
    _funded_buys(book, wants, prices, day, ev.period, min_notional)
    return coverage


def simulate(events: list, calendar: list, series_fn: SeriesFn, rules: Rules,
             start: Optional[str] = None, end: Optional[str] = None,
             capital: float = 100_000.0) -> Result:
    """Run the strategy over calendar[start..end]. If `start` is given, the
    investor begins that day with the most recently disclosed portfolio."""
    result = Result()
    if not calendar or not events:
        return result
    i0 = bisect.bisect_left(calendar, start) if start else 0
    i1 = bisect.bisect_right(calendar, end) - 1 if end else len(calendar) - 1
    if i0 > i1:
        return result
    days = calendar[i0:i1 + 1]

    by_day: dict = {}
    known = [e for e in events if e.exec_date <= days[0]]
    if known:
        by_day[days[0]] = [known[-1]]
    else:
        result.late_start = True
    for e in events:
        if days[0] < e.exec_date <= days[-1]:
            by_day.setdefault(e.exec_date, []).append(e)
    if not by_day:
        return result

    tickers = set()
    for evs in by_day.values():
        for e in evs:
            tickers.update(e.targets)
    aligned = {t: _align(series_fn(t), days) for t in tickers}

    book = _Book(capital, rules, result)
    daily_yield = (1 + rules.cash_yield) ** (1 / 252) - 1
    started = False
    for i, day in enumerate(days):
        if book.cash > 0 and daily_yield:
            book.cash *= 1 + daily_yield
        prices = {}
        for t, (px, last) in aligned.items():
            if px[i] is not None and i - last[i] <= rules.stale_days:
                prices[t] = px[i]
        for t in list(book.units):
            if t not in prices:
                px, last = aligned[t]
                if px[i] is not None:  # price went stale: assume liquidation at last print
                    book.sell(t, book.units[t], px[i], day, "no_price", "")
        for ev in by_day.get(day, []):
            coverage = _apply_event(book, ev, prices, day, initial=not started)
            if not started:
                result.first_event = day
            started = True
            nav = book.value(prices)
            result.positions.append({
                "date": day, "period": ev.period, "coverage": coverage,
                "mapped_share": ev.mapped_share,
                "cash_weight": book.cash / nav if nav else 0.0,
                "weights": {t: u * prices[t] / nav for t, u in book.units.items()
                            if prices.get(t) and nav},
            })
        result.nav.append((day, book.value(prices)))
    return result


def combine(navs: dict, weights: Optional[dict] = None) -> list:
    """Buy-and-hold blend of sleeve NAV series ({key: [(date, value)]}) that
    share a calendar. Each sleeve gets weights[key] of the starting capital."""
    keys = [k for k, s in navs.items() if s]
    if not keys:
        return []
    weights = weights or {k: 1.0 / len(keys) for k in keys}
    common = set(d for d, _ in navs[keys[0]])
    for k in keys[1:]:
        common &= set(d for d, _ in navs[k])
    dates = sorted(common)
    if not dates:
        return []
    lookup = {k: dict(navs[k]) for k in keys}
    base = {k: lookup[k][dates[0]] for k in keys}
    return [(d, sum(weights[k] * lookup[k][d] / base[k] for k in keys if base[k]))
            for d in dates]
