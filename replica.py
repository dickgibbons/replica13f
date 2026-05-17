"""Replica-return engine.

Methodology (all parameters explicit and configurable):
  - At each 13F filing date, form a portfolio of the TOP_N holdings by reported
    USD value.
  - WEIGHTING: 'equal' (default, matches the common WhaleWisdom published basis)
    or 'value' (manager-weighted).
  - Entry/exit at the FILING DATE, not the period-end date. Because a 13F is
    public ~45 days after quarter-end, this models acting only on public
    information (the realistic, defensible lag).
  - Hold until the next filing date, then rebalance. Window returns are chained
    geometrically. Names with no price at either endpoint are dropped and the
    surviving weights renormalized; the share of portfolio weight successfully
    priced is reported as COVERAGE so low-quality windows are visible, not
    silently distorting the result.

The output is a proxy for the disclosed long-equity book only. It excludes
shorts, options, non-13F holdings, leverage, and intra-quarter trades, and the
keyless name-match path introduces mild survivorship bias. Treat it as a
reproducible internal ranking signal, not audited fund performance.
"""
from __future__ import annotations
import datetime as dt
import edgar, cusip_map
from prices import PriceBook

def _annualize(cum_mult: float, years: float) -> float:
    if years <= 0 or cum_mult <= 0:
        return float("nan")
    return cum_mult ** (1.0 / years) - 1.0

def fund_replica(cik: str, top_n=20, weighting="equal",
                 trailing_years=5, pricebook=None, verbose=False,
                 max_periods=24, entry="filing"):
    """entry='filing' enters on the filing date (realistic, public-info lag);
    entry='period_end' enters on the report date (optimistic, unrealizable --
    matches how vendor headline numbers are typically computed)."""
    hp = edgar.holdings_by_period(cik, max_periods=max_periods)
    periods = sorted(hp)
    if len(periods) < 2:
        return None

    # resolve every cusip that could enter a top_n basket
    all_h = []
    for p in periods:
        for h in hp[p]["holdings"]:
            all_h.append({"cusip": h["cusip"], "issuer": h.get("issuer", "")})
    tmap = cusip_map.resolve_holdings(all_h)

    # build (filing_date, basket) timeline
    timeline = []
    for p in periods:
        anchor = hp[p]["filing_date"] if entry == "filing" else p
        fd = dt.date.fromisoformat(anchor)
        hs = sorted(hp[p]["holdings"], key=lambda x: -x["value_usd"])
        basket = []
        for h in hs:
            tk = tmap.get(h["cusip"].upper())
            if tk:
                basket.append((tk, h["value_usd"]))
            if len(basket) >= top_n:
                break
        if basket:
            timeline.append((fd, basket))
    timeline.sort(key=lambda x: x[0])
    if len(timeline) < 2:
        return None

    start = timeline[0][0] - dt.timedelta(days=20)
    end = timeline[-1][0] + dt.timedelta(days=5)
    pb = pricebook or PriceBook(start, end)

    windows = []  # (entry_date, exit_date, ret, coverage)
    for (fd0, basket), (fd1, _) in zip(timeline, timeline[1:]):
        if weighting == "value":
            tot = sum(v for _, v in basket)
            w = {t: v / tot for t, v in basket}
        else:
            w = {t: 1.0 / len(basket) for t, _ in basket}
        priced, port_ret = 0.0, 0.0
        for t, _ in basket:
            p0 = pb.as_of(t, fd0)
            p1 = pb.as_of(t, fd1)
            if p0 and p1 and p0 > 0:
                priced += w[t]
                port_ret += w[t] * (p1 / p0 - 1.0)
        if priced <= 0:
            continue
        windows.append((fd0, fd1, port_ret / priced, priced))

    if not windows:
        return None

    def chain(ws):
        m = 1.0
        for *_, r, _cov in [(a, b, r, c) for a, b, r, c in ws]:
            m *= (1.0 + r)
        yrs = (ws[-1][1] - ws[0][0]).days / 365.25
        return m, yrs

    full_mult, full_yrs = chain(windows)
    latest = windows[-1][1]
    cutoff = latest - dt.timedelta(days=int(trailing_years * 365.25))
    tw = [w for w in windows if w[0] >= cutoff] or windows
    tw_mult, tw_yrs = chain(tw)

    avg_cov = sum(c for *_, c in windows) / len(windows)
    return {
        "cik": str(cik).zfill(10),
        f"ann_{trailing_years}yr_pct": round(_annualize(tw_mult, tw_yrs) * 100, 1),
        "ann_full_pct": round(_annualize(full_mult, full_yrs) * 100, 1),
        "full_years": round(full_yrs, 1),
        "windows": len(windows),
        "trailing_windows": len(tw),
        "avg_coverage": round(avg_cov, 3),
        "first_filing": windows[0][0].isoformat(),
        "last_filing": latest.isoformat(),
    }
