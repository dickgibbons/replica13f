"""13F copy-trading study over the published-track-record universe.

For each fund: follow its 13F filings with the simulator over 5- and 10-year
windows ending at the latest price date, under three variants:

  base        buy the day after the 13F is public, trim proportionally when
              a later 13F shows fewer shares, no forced sells to fund buys
  rebalance   same timing, but rebalance fully to the new top-N weights
  period_end  trade at quarter end (NOT achievable: shows the cost of the
              45-day filing delay)

Combined portfolios blend the per-fund base sleeves equally (all funds, and
eligible long-biased funds only). The point-in-time check re-selects the
top half of eligible funds each year by trailing 12-month sleeve return,
using only information available at the time. It still draws from a pool
chosen on today's published returns, so it reduces but does not remove
hindsight bias.

    python study.py                      # full study, all funds
    python study.py --eligible-only --top-n 15
    python study.py --ciks 0001336528 0001061165
"""
from __future__ import annotations
import argparse
import csv
import dataclasses
import datetime as dt
import json
import math
import os
from typing import Callable, Optional

import backtest_universe
import classify
import cusip_map
import edgar
import metrics
import simulator
from prices import PriceBook

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(ROOT, "outputs")
BENCH = "SPY"
WINDOWS = (5, 10)
MAX_PERIODS = 44
PRICE_START = dt.date(2014, 1, 1)
PIT_REBALANCE_MMDD = "02-20"
PIT_LOOKBACK = 252

Progress = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


# ---- data -----------------------------------------------------------------
def load_fund(cik: str, top_n: int, max_periods: int = MAX_PERIODS) -> tuple:
    """(holdings_by_period, cusip->ticker map). Only CUSIPs that rank inside
    3x top_n by value in some quarter are mapped; deeper names cannot enter
    the copied portfolio."""
    hp = edgar.holdings_by_period(cik, max_periods=max_periods)
    cands = {}
    for filing in hp.values():
        rows = sorted(filing["holdings"], key=lambda r: -r["value_usd"])[:top_n * 3]
        for r in rows:
            cands.setdefault(r["cusip"].upper(), {"cusip": r["cusip"].upper(),
                                                  "issuer": r.get("issuer", "")})
    tmap = cusip_map.resolve_holdings(cands.values()) if cands else {}
    return hp, tmap


def window_start(calendar: list, end: str, years: int) -> Optional[str]:
    end_d = dt.date.fromisoformat(end)
    try:
        target = end_d.replace(year=end_d.year - years)
    except ValueError:
        target = end_d.replace(year=end_d.year - years, day=28)
    for d in calendar:
        if d >= target.isoformat():
            return d
    return None


def bench_nav(series: list, days: list, capital: float) -> list:
    lookup = dict(series)
    px = [(d, lookup[d]) for d in days if d in lookup]
    if not px:
        return []
    base = px[0][1]
    return [(d, capital * p / base) for d, p in px]


def avg_coverage(result: simulator.Result) -> float:
    """Average share of the fund's top-N dollar value the copy could
    actually trade (ticker known and priced) at each filing."""
    cov = [p["coverage"] * p.get("mapped_share", 1.0) for p in result.positions]
    return sum(cov) / len(cov) if cov else float("nan")


# ---- point-in-time selection ---------------------------------------------
def point_in_time(sleeves: dict, days: list, k: int,
                  lookback: int = PIT_LOOKBACK, capital: float = 100_000.0,
                  full_calendar: Optional[list] = None) -> tuple:
    """Equal-weight the k sleeves with the best trailing return, re-chosen at
    the window start and each year on the first trading day on/after
    PIT_REBALANCE_MMDD (after Q4 13Fs are public). Returns (nav, picks)."""
    full_calendar = full_calendar or days
    idx = {d: i for i, d in enumerate(full_calendar)}
    look = {c: dict(s) for c, s in sleeves.items() if s}
    rebal, seen = set(), set()
    if days:
        rebal.add(days[0])
    for d in days:
        y = d[:4]
        if y not in seen and d[5:] >= PIT_REBALANCE_MMDD:
            seen.add(y)
            rebal.add(d)

    nav, picks, units, value = [], [], {}, capital
    for d in days:
        if units:
            value = sum(u * look[c].get(d, 0.0) for c, u in units.items())
        if d in rebal:
            live = [c for c, s in look.items() if d in s]
            scored = []
            i = idx.get(d)
            for c in live:
                past = full_calendar[i - lookback] if i is not None and i >= lookback else None
                if past and past in look[c] and look[c][past]:
                    scored.append((look[c][d] / look[c][past] - 1.0, c))
            if scored:
                chosen = [c for _, c in sorted(scored, reverse=True)[:k]]
            else:
                chosen = live[:k]
            if chosen:
                units = {c: value / len(chosen) / look[c][d] for c in chosen}
                picks.append({"date": d, "chosen": chosen,
                              "trailing": {c: r for r, c in scored}})
        nav.append((d, value))
    return nav, picks


# ---- per-fund runs ----------------------------------------------------------
def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def run_study(funds: list, rules: simulator.Rules, windows=WINDOWS,
              capital: float = 100_000.0, progress: Progress = _noop,
              do_classify: bool = True, eligible_only: bool = False) -> dict:
    today = dt.date.today()
    pb = PriceBook(PRICE_START, today)
    spy = pb.series(BENCH)
    if not spy:
        raise RuntimeError("no benchmark prices for SPY")
    calendar = [d for d, _ in spy]
    end = calendar[-1]
    series_fn = pb.series
    splits_fn = pb.splits
    rules_reb = dataclasses.replace(rules, cash_policy="rebalance")
    rules_pe = dataclasses.replace(rules, execution="period_end")

    fund_info, sleeves_long, runs = [], {}, []
    for n, f in enumerate(funds, 1):
        cik = str(f["cik"]).zfill(10)
        progress(f"[{n}/{len(funds)}] {f['name']}: classifying")
        info = dict(f, cik=cik)
        if do_classify:
            try:
                c = classify.classify(cik)
                info.update(eligible=c["eligible"], eligibility=c["reason"],
                            positions=c.get("positions"))
            except Exception as e:
                info.update(eligible=None, eligibility=f"classify failed: {e}")
        else:
            info.update(eligible=True, eligibility="not classified")
        fund_info.append(info)
        if eligible_only and info.get("eligible") is False:
            continue

        progress(f"[{n}/{len(funds)}] {f['name']}: loading 13F history")
        try:
            hp, tmap = load_fund(cik, rules.top_n)
        except Exception as e:
            info["error"] = f"history failed: {e}"
            continue
        if not hp:
            info["error"] = "no 13F history"
            continue
        info["periods"] = len(hp)
        info["first_period"], info["last_period"] = min(hp), max(hp)

        progress(f"[{n}/{len(funds)}] {f['name']}: building events + prices")
        ev = simulator.build_events(hp, tmap, calendar, rules, splits_fn, series_fn)
        ev_pe = simulator.build_events(hp, tmap, calendar, rules_pe, splits_fn, series_fn)

        long_run = simulator.simulate(ev, calendar, series_fn, rules, capital=capital)
        sleeves_long[cik] = long_run.nav

        for years in windows:
            start = window_start(calendar, end, years)
            if not start:
                continue
            days = calendar[calendar.index(start):]
            spy_nav = bench_nav(spy, days, capital)
            for variant, r, events in (("base", rules, ev),
                                       ("rebalance", rules_reb, ev),
                                       ("period_end", rules_pe, ev_pe)):
                res = simulator.simulate(events, calendar, series_fn, r,
                                         start=start, end=end, capital=capital)
                if not res.nav:
                    continue
                runs.append({"scope": "fund", "cik": cik, "name": f["name"],
                             "window": years, "variant": variant, "result": res,
                             "spy": spy_nav})
        progress(f"[{n}/{len(funds)}] {f['name']}: done")

    eligible = {i["cik"] for i in fund_info if i.get("eligible")}
    combined = []
    for years in windows:
        start = window_start(calendar, end, years)
        if not start:
            continue
        days = calendar[calendar.index(start):]
        spy_nav = bench_nav(spy, days, capital)
        base = {r["cik"]: r["result"] for r in runs
                if r["window"] == years and r["variant"] == "base"}
        full = {c: res.nav for c, res in base.items() if not res.late_start}
        for label, keys in (("combined_all", list(full)),
                            ("combined_eligible", [c for c in full if c in eligible])):
            if not keys:
                continue
            nav = simulator.combine({c: full[c] for c in keys})
            nav = [(d, capital * v) for d, v in nav]
            combined.append({"scope": "combined", "cik": label, "window": years,
                             "variant": "base", "nav": nav, "spy": spy_nav,
                             "members": keys,
                             "name": ("Equal blend, all funds" if label == "combined_all"
                                      else "Equal blend, eligible funds")})
        pool = {c: s for c, s in sleeves_long.items() if c in eligible}
        if pool:
            k = max(1, math.ceil(len(pool) / 2))
            nav, picks = point_in_time(pool, days, k, capital=capital,
                                       full_calendar=calendar)
            combined.append({"scope": "combined", "cik": "point_in_time",
                             "window": years, "variant": "base", "nav": nav,
                             "spy": spy_nav, "members": list(pool), "picks": picks,
                             "name": f"Point-in-time top {k} of eligible pool"})

    return {"as_of": end, "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "rules": dataclasses.asdict(rules), "capital": capital,
            "funds": fund_info, "runs": runs, "combined": combined}


# ---- output -----------------------------------------------------------------
SUMMARY_FIELDS = [
    "scope", "name", "cik", "eligible", "eligibility", "published_5y", "published_10y",
    "window", "variant", "late_start", "first_event", "coverage",
    "start", "end", "end_value", "total_return", "cagr", "volatility", "sharpe",
    "sortino", "calmar", "max_drawdown", "peak", "trough", "recovery",
    "beta", "alpha", "up_capture", "down_capture", "best_year", "worst_year",
    "turnover", "avg_holding_days", "win_rate", "closed_positions",
    "spy_cagr", "spy_max_drawdown", "spy_sharpe", "excess_cagr",
]


def _write_csv(path: str, rows: list, fields: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _clean(r.get(k)) for k in fields})


def _summary_row(base: dict, nav: list, spy: list, trades=None, closed=None) -> dict:
    m = metrics.summarize(nav, spy, trades, closed)
    s = metrics.summarize(spy)
    row = dict(base, **m)
    row["spy_cagr"] = s.get("cagr")
    row["spy_max_drawdown"] = s.get("max_drawdown")
    row["spy_sharpe"] = s.get("sharpe")
    if m.get("cagr") is not None and s.get("cagr") is not None:
        row["excess_cagr"] = m["cagr"] - s["cagr"]
    return row


def write_outputs(study: dict, out_dir: Optional[str] = None) -> str:
    out_dir = out_dir or os.path.join(OUT_ROOT, f"study_{dt.date.today().isoformat()}")
    os.makedirs(out_dir, exist_ok=True)
    info = {f["cik"]: f for f in study["funds"]}
    summary, series = [], []

    for r in study["runs"]:
        res, f = r["result"], info.get(r["cik"], {})
        base = {"scope": "fund", "name": r["name"], "cik": r["cik"],
                "eligible": f.get("eligible"), "eligibility": f.get("eligibility"),
                "published_5y": f.get("published_5y"),
                "published_10y": f.get("published_10y"),
                "window": r["window"], "variant": r["variant"],
                "late_start": res.late_start, "first_event": res.first_event,
                "coverage": avg_coverage(res)}
        row = _summary_row(base, res.nav, r["spy"], res.trades, res.closed)
        summary.append(row)
        if r["variant"] != "base":
            continue
        tag = f"{r['cik']}_{r['window']}y"
        spy = dict(r["spy"])
        _write_csv(os.path.join(out_dir, f"nav_{tag}.csv"),
                   [{"date": d, "nav": v, "spy": spy.get(d)} for d, v in res.nav],
                   ["date", "nav", "spy"])
        _write_csv(os.path.join(out_dir, f"drawdown_{tag}.csv"),
                   [{"date": d, "drawdown": v} for d, v in metrics.drawdown_series(res.nav)],
                   ["date", "drawdown"])
        if res.trades:
            _write_csv(os.path.join(out_dir, f"trades_{tag}.csv"), res.trades,
                       list(res.trades[0].keys()))
        series.append({"cik": r["cik"], "name": r["name"], "window": r["window"],
                       "nav": res.nav, "spy": r["spy"], "trades": res.trades,
                       "closed": res.closed, "positions": res.positions[-1:],
                       "summary": {k: _clean(v) for k, v in row.items()}})

    for c in study["combined"]:
        base = {"scope": "combined", "name": c["name"], "cik": c["cik"],
                "window": c["window"], "variant": c["variant"]}
        row = _summary_row(base, c["nav"], c["spy"])
        summary.append(row)
        tag = f"{c['cik']}_{c['window']}y"
        spy = dict(c["spy"])
        _write_csv(os.path.join(out_dir, f"nav_{tag}.csv"),
                   [{"date": d, "nav": v, "spy": spy.get(d)} for d, v in c["nav"]],
                   ["date", "nav", "spy"])
        _write_csv(os.path.join(out_dir, f"drawdown_{tag}.csv"),
                   [{"date": d, "drawdown": v} for d, v in metrics.drawdown_series(c["nav"])],
                   ["date", "drawdown"])
        series.append({"cik": c["cik"], "name": c["name"], "window": c["window"],
                       "nav": c["nav"], "spy": c["spy"], "members": c["members"],
                       "picks": c.get("picks", []),
                       "summary": {k: _clean(v) for k, v in row.items()}})

    _write_csv(os.path.join(out_dir, "summary.csv"), summary, SUMMARY_FIELDS)
    payload = {"as_of": study["as_of"], "generated": study["generated"],
               "rules": study["rules"], "capital": study["capital"],
               "funds": study["funds"],
               "summary": [{k: _clean(v) for k, v in r.items()} for r in summary],
               "series": series}
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(payload, f, default=str)
    return out_dir


def latest_study_dir() -> Optional[str]:
    if not os.path.isdir(OUT_ROOT):
        return None
    dirs = sorted(d for d in os.listdir(OUT_ROOT)
                  if d.startswith("study_")
                  and os.path.exists(os.path.join(OUT_ROOT, d, "results.json")))
    return os.path.join(OUT_ROOT, dirs[-1]) if dirs else None


def load_results(out_dir: str) -> dict:
    with open(os.path.join(out_dir, "results.json")) as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--weighting", choices=("value", "equal"), default="value")
    ap.add_argument("--sell-rule", choices=("proportional", "full_exit"),
                    default="proportional")
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--ciks", nargs="*", help="limit to these CIKs")
    ap.add_argument("--eligible-only", action="store_true",
                    help="skip simulation for multi-strat/quant funds")
    ap.add_argument("--no-classify", action="store_true")
    ap.add_argument("--out", help="output directory")
    args = ap.parse_args()

    funds = backtest_universe.load()
    if not funds:
        funds = backtest_universe.build_from_leaderboard()
        backtest_universe.save(funds)
    if args.ciks:
        want = {c.zfill(10) for c in args.ciks}
        funds = [f for f in funds if str(f["cik"]).zfill(10) in want]

    rules = simulator.Rules(top_n=args.top_n, weighting=args.weighting,
                            sell_rule=args.sell_rule, cost_bps=args.cost_bps)
    study = run_study(funds, rules, capital=args.capital, progress=print,
                      do_classify=not args.no_classify,
                      eligible_only=args.eligible_only)
    out = write_outputs(study, args.out)
    print(f"\nWrote {out}")
    rows = load_results(out)["summary"]
    print(f"{'fund':34s} {'win':>4s} {'variant':10s} {'CAGR':>7s} {'SPY':>7s} "
          f"{'MaxDD':>7s} {'Sortino':>7s}")
    for r in rows:
        if r["variant"] != "base" and r["scope"] == "fund":
            continue
        fmt = lambda v: f"{v:7.1%}" if isinstance(v, (int, float)) else f"{'-':>7s}"
        so = r.get("sortino")
        print(f"{r['name'][:34]:34s} {r['window']:>3}y {r['variant']:10s} "
              f"{fmt(r.get('cagr'))} {fmt(r.get('spy_cagr'))} "
              f"{fmt(r.get('max_drawdown'))} "
              f"{so if so is None else round(so, 2)!s:>7s}")


if __name__ == "__main__":
    main()
