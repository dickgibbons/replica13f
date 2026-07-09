"""Conviction scoring and track records for insider purchases.

Two things live here:

1. score_purchase — a 0-100 heuristic combining the factors research says
   separate conviction buys from noise:
     - purchase size (log scale, up to 25 pts)
     - stake increase: shares bought vs shares already owned (up to 25)
     - who's buying: CEO/CFO > President/COO > officer > director > 10% (up to 15)
     - rarity: an insider who almost never buys, buying now (up to 10)
     - buying weakness: stock well off its 52-week high (up to 10)
     - cluster: 2+ insiders buying the same stock within days (+10)
     - track record: buyer historically beats the market after buying (+15)
     - pre-scheduled (10b5-1 plan) purchases keep only 40% of their score
   The exact weights are judgment calls — treat the score as a ranking
   aid, not a verdict.

2. batting_average — the buyer's historical record: for each of their
   PRIOR purchases (any company), the stock's 3-month return minus SPY's.
   Insiders with n < 3 resolved buys get no record (too few to mean much).

Owner purchase histories are fetched from EDGAR per insider CIK (their
last 60 Form 4s, purchases kept) and cached in cache/insider_hist.json.
Prices come from the app's existing Yahoo-backed prices module.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import os
import re
from typing import Callable

import form4
import prices

ROOT = os.path.dirname(__file__)
HIST_CACHE = os.path.join(ROOT, "cache", "insider_hist.json")
HIST_MAX_FILINGS = 60
HIST_REFRESH_DAYS = 30
PRICE_START = dt.date(2019, 1, 1)

ProgressFn = Callable[[str, str], None]


# ---------------------------------------------------------------- history

def _load_hist() -> dict:
    if os.path.exists(HIST_CACHE):
        with open(HIST_CACHE) as f:
            return json.load(f)
    return {}


def _save_hist(hist: dict) -> None:
    os.makedirs(os.path.dirname(HIST_CACHE), exist_ok=True)
    with open(HIST_CACHE, "w") as f:
        json.dump(hist, f)


def owner_history(owner_cik: str, hist_cache: dict) -> list[dict]:
    """All open-market purchases in the owner's last 60 Form 4s (cached).

    Returns [{trade, ticker, value}] sorted by trade date."""
    owner_cik = str(owner_cik).zfill(10)
    entry = hist_cache.get(owner_cik)
    if entry:
        age = (dt.date.today()
               - dt.date.fromisoformat(entry["fetched"])).days
        if age <= HIST_REFRESH_DAYS:
            return entry["purchases"]

    purchases: list[dict] = []
    try:
        r = form4._rate_limited_get(
            f"https://data.sec.gov/submissions/CIK{owner_cik}.json")
        if r.status_code == 200:
            rec = r.json()["filings"]["recent"]
            form4s = [
                (acc, fdate)
                for form, fdate, acc in zip(
                    rec["form"], rec["filingDate"], rec["accessionNumber"])
                if form == "4"
            ][:HIST_MAX_FILINGS]
            for acc, fdate in form4s:
                acc_nodash = acc.replace("-", "")
                url = (f"https://www.sec.gov/Archives/edgar/data/"
                       f"{int(owner_cik)}/{acc_nodash}/{acc}.txt")
                fr = form4._rate_limited_get(url)
                if fr.status_code != 200:
                    continue
                row = form4._parse_purchases(fr.text)
                if row and row.get("trade"):
                    purchases.append({
                        "trade": row["trade"],
                        "ticker": row["ticker"],
                        "value": row["value"],
                    })
    except (RuntimeError, ValueError, KeyError):
        return entry["purchases"] if entry else []

    purchases.sort(key=lambda p: p["trade"])
    hist_cache[owner_cik] = {
        "fetched": dt.date.today().isoformat(),
        "purchases": purchases,
    }
    return purchases


# ---------------------------------------------------------------- prices

def _ok_ticker(t: str | None) -> str | None:
    """A usable exchange ticker, or None for junk like 'N/A', 'GGO/GGO-A'."""
    t = (t or "").strip().upper()
    if t in ("", "N/A", "NA", "NONE") or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
        return None
    return t


def _series_window(ticker: str, start: dt.date, end: dt.date) -> list:
    s = prices.get_series(ticker, PRICE_START, dt.date.today())
    a, b = start.isoformat(), end.isoformat()
    return [(d, p) for d, p in s if a <= d <= b]


def batting_average(purchases: list[dict], pb: prices.PriceBook,
                    before: str) -> dict | None:
    """3-month market-relative record of the buyer's PRIOR purchases.

    purchases: [{trade, ticker, value}]; only trades strictly before
    `before` count, and only ones old enough for the 3-month window to
    have resolved. Returns {n, hits, avg} or None if n < 3."""
    today = dt.date.today()
    results = []
    for p in purchases:
        t, d = _ok_ticker(p.get("ticker")), p.get("trade")
        if not t or not d or d >= before:
            continue
        try:
            d0 = dt.date.fromisoformat(d)
        except ValueError:
            continue
        d3 = d0 + dt.timedelta(days=91)
        if d3 > today - dt.timedelta(days=5):
            continue  # not resolved yet
        p0, p3 = pb.as_of(t, d0), pb.as_of(t, d3)
        s0, s3 = pb.as_of("SPY", d0), pb.as_of("SPY", d3)
        if not all((p0, p3, s0, s3)):
            continue
        results.append((p3 / p0) - (s3 / s0))
    if len(results) < 3:
        return None
    return {
        "n": len(results),
        "hits": sum(1 for e in results if e > 0),
        "avg": round(sum(results) / len(results), 4),
    }


# ---------------------------------------------------------------- scoring

def _role_pts(title: str) -> int:
    t = (title or "").lower()
    if "ceo" in t or "chief executive" in t:
        return 15
    if "cfo" in t or "chief financial" in t:
        return 15
    if "pres" in t or "coo" in t or "chief operating" in t or "chair" in t:
        return 12
    if "officer" in t or "chief" in t or "vp" in t or "counsel" in t:
        return 9
    if "dir" in t:
        return 7
    if "10%" in t:
        return 3
    return 5


def score_purchase(row: dict, prior_purchases: list[dict],
                   record: dict | None, in_cluster: bool,
                   off_52wk_high: float | None) -> tuple[int, str]:
    """Returns (score 0-100, human-readable reasons)."""
    pts = 0.0
    why = []

    value = row.get("value") or 0
    if value > 0:
        v = min(25.0, max(0.0, 8.0 * math.log10(value / 5_000)))
        pts += v
        if value >= 250_000:
            why.append(f"${value/1000:,.0f}k buy")

    shares = row.get("shares") or 0
    owned_after = row.get("owned_after")
    if owned_after is not None and shares:
        prior = owned_after - shares
        if prior <= 0:
            pts += 20
            why.append("brand-new stake")
        else:
            inc = shares / prior
            pts += min(25.0, 25.0 * inc / 0.5)
            if inc >= 0.25:
                why.append(f"stake +{inc * 100:.0f}%")

    rp = _role_pts(row.get("title", ""))
    pts += rp
    if rp >= 12:
        why.append("top executive")

    two_years_ago = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    recent_prior = [
        p for p in prior_purchases
        if p.get("trade", "") >= two_years_ago
        and p.get("trade", "") < (row.get("trade") or row["filed"])
    ]
    if len(recent_prior) == 0:
        pts += 10
        why.append("first buy in 2yrs")
    elif len(recent_prior) <= 2:
        pts += 6
    elif len(recent_prior) <= 6:
        pts += 3

    if off_52wk_high is not None:
        if off_52wk_high >= 0.4:
            pts += 10
            why.append(f"{off_52wk_high * 100:.0f}% off 52wk high")
        elif off_52wk_high >= 0.2:
            pts += 6
        elif off_52wk_high >= 0.1:
            pts += 3

    if in_cluster:
        pts += 10
        why.append("cluster buy")

    if record and record["n"] >= 4:
        hit_rate = record["hits"] / record["n"]
        if hit_rate >= 0.6:
            pts += 10
            why.append(f"record {record['hits']}/{record['n']} beat mkt")
        if record["avg"] >= 0.05:
            pts += 5

    if row.get("plan"):
        pts *= 0.4
        why.append("pre-scheduled (10b5-1)")

    return min(100, round(pts)), "; ".join(why)


# ---------------------------------------------------------------- enrich

def enrich_feed(feed: dict, on_progress: ProgressFn | None = None) -> int:
    """Score every un-scored purchase in the feed; saves feed + caches.

    Adds to each row: score, why, rec_n/rec_hits/rec_avg (or None)."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    rows = feed.get("rows", {})
    pending = [(a, r) for a, r in rows.items() if "score" not in r]
    if not pending:
        return 0

    hist_cache = _load_hist()
    pb = prices.PriceBook(PRICE_START, dt.date.today())

    # cluster membership: 2+ distinct insiders on one ticker within the feed
    insiders_by_ticker: dict[str, set] = {}
    for r in rows.values():
        if r.get("ticker"):
            insiders_by_ticker.setdefault(r["ticker"], set()).add(r["insider"])

    for i, (acc, r) in enumerate(pending):
        _prog("scoring", f"{i + 1}/{len(pending)}: {r['insider'][:40]}")
        try:
            prior: list[dict] = []
            for cik in (r.get("owner_ciks") or [])[:2]:
                prior.extend(owner_history(cik, hist_cache))

            record = batting_average(prior, pb, before=r["filed"])

            off_high = None
            ticker = _ok_ticker(r.get("ticker"))
            if ticker and r.get("trade"):
                try:
                    d0 = dt.date.fromisoformat(r["trade"])
                    window = _series_window(ticker, d0 - dt.timedelta(days=365), d0)
                    if len(window) >= 60:
                        high = max(p for _, p in window)
                        last = window[-1][1]
                        off_high = max(0.0, 1.0 - last / high)
                except (ValueError, ZeroDivisionError):
                    pass

            in_cluster = len(insiders_by_ticker.get(r.get("ticker") or "", set())) >= 2
            score, why = score_purchase(r, prior, record, in_cluster, off_high)
        except Exception as e:  # one bad row must not sink the batch
            score, why, record = 0, f"scoring failed: {e}", None
        r["score"] = score
        r["why"] = why
        r["rec_n"] = record["n"] if record else None
        r["rec_hits"] = record["hits"] if record else None
        r["rec_avg"] = record["avg"] if record else None
        if (i + 1) % 10 == 0:
            _save_hist(hist_cache)
            form4._save_feed(feed)  # checkpoint — history fetches are slow

    _save_hist(hist_cache)
    form4._save_feed(feed)
    return len(pending)
