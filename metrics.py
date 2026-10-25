"""Performance statistics for a daily NAV series.

All inputs are [(date_iso, value)] lists on trading days. Ratios are
annualized with 252 trading days; returns are simple daily returns.
"""
from __future__ import annotations
import datetime as dt
import math
from typing import Optional

TRADING_DAYS = 252


def daily_returns(nav: list) -> list:
    """[(date, return)] for each day after the first."""
    out = []
    for (_, v0), (d1, v1) in zip(nav, nav[1:]):
        if v0:
            out.append((d1, v1 / v0 - 1.0))
    return out


def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def years_between(d0: str, d1: str) -> float:
    return (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days / 365.25


def cagr(nav: list) -> float:
    if len(nav) < 2 or nav[0][1] <= 0:
        return float("nan")
    yrs = years_between(nav[0][0], nav[-1][0])
    if yrs <= 0:
        return float("nan")
    return (nav[-1][1] / nav[0][1]) ** (1 / yrs) - 1


def drawdown_series(nav: list) -> list:
    """[(date, drawdown)] where drawdown <= 0 is the fall from the running peak."""
    out, peak = [], float("-inf")
    for d, v in nav:
        peak = max(peak, v)
        out.append((d, v / peak - 1.0 if peak > 0 else 0.0))
    return out


def max_drawdown(nav: list) -> dict:
    """Deepest peak-to-trough fall with its peak, trough and recovery dates."""
    best = {"max_drawdown": 0.0, "peak": None, "trough": None,
            "recovery": None, "days_to_recover": None}
    if not nav:
        return best
    peak_v, peak_d = nav[0][1], nav[0][0]
    for d, v in nav:
        if v > peak_v:
            peak_v, peak_d = v, d
        dd = v / peak_v - 1.0 if peak_v > 0 else 0.0
        if dd < best["max_drawdown"]:
            best.update(max_drawdown=dd, peak=peak_d, trough=d)
    if best["trough"]:
        peak_value = dict(nav)[best["peak"]]
        for d, v in nav:
            if d > best["trough"] and v >= peak_value:
                best["recovery"] = d
                best["days_to_recover"] = (dt.date.fromisoformat(d)
                                           - dt.date.fromisoformat(best["peak"])).days
                break
    return best


def sharpe(rets: list, rf: float = 0.0) -> float:
    excess = [r - rf / TRADING_DAYS for r in rets]
    sd = _std(excess)
    return _mean(excess) / sd * math.sqrt(TRADING_DAYS) if sd else float("nan")


def sortino(rets: list, rf: float = 0.0) -> float:
    """Mean excess return over downside deviation (target = risk-free)."""
    excess = [r - rf / TRADING_DAYS for r in rets]
    if not excess:
        return float("nan")
    downside = math.sqrt(sum(min(0.0, x) ** 2 for x in excess) / len(excess))
    return _mean(excess) / downside * math.sqrt(TRADING_DAYS) if downside else float("nan")


def _aligned_returns(nav: list, bench: list) -> tuple:
    b = dict(daily_returns(bench))
    pairs = [(r, b[d]) for d, r in daily_returns(nav) if d in b]
    return [p for p, _ in pairs], [q for _, q in pairs]


def beta_alpha(nav: list, bench: list) -> tuple:
    """(beta, annualized Jensen alpha with rf = 0) versus the benchmark."""
    p, b = _aligned_returns(nav, bench)
    if len(p) < 2:
        return float("nan"), float("nan")
    mp, mb = _mean(p), _mean(b)
    var = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    if not var:
        return float("nan"), float("nan")
    cov = sum((x - mp) * (y - mb) for x, y in zip(p, b)) / (len(b) - 1)
    beta = cov / var
    return beta, (mp - beta * mb) * TRADING_DAYS


def monthly_returns(nav: list) -> list:
    """[(YYYY-MM, return)] using month-end values."""
    month_end: dict = {}
    for d, v in nav:
        month_end[d[:7]] = v
    months = sorted(month_end)
    first = nav[0][1] if nav else None
    out, prev = [], first
    for m in months:
        v = month_end[m]
        if prev:
            out.append((m, v / prev - 1.0))
        prev = v
    return out


def capture(nav: list, bench: list) -> tuple:
    """(up capture, down capture) on monthly returns, as ratios of
    geometric-average returns in benchmark up and down months."""
    p = dict(monthly_returns(nav))
    b = monthly_returns(bench)

    def geo(xs):
        if not xs:
            return float("nan")
        prod = 1.0
        for x in xs:
            prod *= 1 + x
        return prod ** (1 / len(xs)) - 1

    up = [(p[m], r) for m, r in b if m in p and r > 0]
    dn = [(p[m], r) for m, r in b if m in p and r < 0]
    up_cap = geo([x for x, _ in up]) / geo([y for _, y in up]) if up else float("nan")
    dn_cap = geo([x for x, _ in dn]) / geo([y for _, y in dn]) if dn else float("nan")
    return up_cap, dn_cap


def calendar_years(nav: list) -> dict:
    """{year: return}. The first year is partial (from the first NAV date)."""
    year_end: dict = {}
    for d, v in nav:
        year_end[int(d[:4])] = v
    out, prev = {}, nav[0][1] if nav else None
    for y in sorted(year_end):
        if prev:
            out[y] = year_end[y] / prev - 1.0
        prev = year_end[y]
    return out


def rolling_returns(nav: list, window: int = TRADING_DAYS) -> list:
    return [(nav[i][0], nav[i][1] / nav[i - window][1] - 1.0)
            for i in range(window, len(nav)) if nav[i - window][1]]


def trading_stats(trades: list, closed: list, nav: list) -> dict:
    """Turnover (one-way, annualized, excluding the initial purchase),
    average holding period and win rate of fully closed positions."""
    stats = {"turnover": float("nan"), "avg_holding_days": float("nan"),
             "win_rate": float("nan"), "closed_positions": len(closed)}
    if nav and len(nav) > 1:
        yrs = years_between(nav[0][0], nav[-1][0])
        avg_nav = _mean([v for _, v in nav])
        traded = sum(t["notional"] for t in trades if t["reason"] != "initial")
        if yrs > 0 and avg_nav:
            stats["turnover"] = traded / 2 / avg_nav / yrs
    if closed:
        stats["avg_holding_days"] = _mean([c["days"] for c in closed])
        stats["win_rate"] = sum(1 for c in closed if c["pnl"] > 0) / len(closed)
    return stats


def summarize(nav: list, bench: Optional[list] = None, trades: Optional[list] = None,
              closed: Optional[list] = None, rf: float = 0.0) -> dict:
    """All headline statistics for one NAV series."""
    if len(nav) < 2:
        return {}
    rets = [r for _, r in daily_returns(nav)]
    dd = max_drawdown(nav)
    c = cagr(nav)
    years = calendar_years(nav)
    rolling = [r for _, r in rolling_returns(nav)]
    out = {
        "start": nav[0][0],
        "end": nav[-1][0],
        "years": round(years_between(nav[0][0], nav[-1][0]), 2),
        "start_value": nav[0][1],
        "end_value": nav[-1][1],
        "total_return": nav[-1][1] / nav[0][1] - 1.0,
        "cagr": c,
        "volatility": _std(rets) * math.sqrt(TRADING_DAYS),
        "sharpe": sharpe(rets, rf),
        "sortino": sortino(rets, rf),
        "calmar": c / abs(dd["max_drawdown"]) if dd["max_drawdown"] else float("nan"),
        **dd,
        "best_year": max(years.values()) if years else float("nan"),
        "worst_year": min(years.values()) if years else float("nan"),
        "rolling_12m_min": min(rolling) if rolling else float("nan"),
        "rolling_12m_median": sorted(rolling)[len(rolling) // 2] if rolling else float("nan"),
        "rolling_12m_max": max(rolling) if rolling else float("nan"),
    }
    if bench:
        beta, alpha = beta_alpha(nav, bench)
        up, down = capture(nav, bench)
        out.update(beta=beta, alpha=alpha, up_capture=up, down_capture=down)
    if trades is not None:
        out.update(trading_stats(trades, closed or [], nav))
    return out
