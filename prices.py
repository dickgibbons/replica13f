"""Adjusted daily closes via the Yahoo chart endpoint, disk-cached per symbol.

Adjusted close is used so returns include dividends and splits (total return),
which is the correct basis for a replica-return comparison. as_of() returns the
last close on or before a date, so a portfolio formed on a filing date is
priced at the most recent available trading day.

Split events are cached alongside the series: 13F share counts are not
split-adjusted, so comparing a fund's shares across quarters needs them.
"""
from __future__ import annotations
import os, json, time, datetime as dt
import requests

CACHE = os.path.join(os.path.dirname(__file__), "cache", "prices")
os.makedirs(CACHE, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (replica13f)"}
HISTORY_START = dt.date(2010, 1, 1)
STALE_DAYS = 3

def _epoch(d: dt.date) -> int:
    return int(time.mktime(d.timetuple()))

def _yahoo_symbol(t: str) -> str:
    # Yahoo uses '-' for share classes (BRK.B -> BRK-B); '/' appears in some
    # filings' ticker fields and would break the cache path
    return t.replace(".", "-").replace("/", "-").upper()

def _needs_refetch(cached: dict, start: dt.date, end: dt.date) -> bool:
    if "splits" not in cached or "fetched" not in cached:
        return True
    if cached.get("start", "9999-12-31") > start.isoformat():
        return True
    fetched = dt.date.fromisoformat(cached["fetched"])
    if not cached.get("ok"):
        # unknown/delisted symbols rarely come back; recheck monthly
        return dt.date.today() > fetched + dt.timedelta(days=30)
    horizon = min(end, dt.date.today())
    return horizon > fetched + dt.timedelta(days=STALE_DAYS)

def _fetch(sym: str, start: dt.date):
    """Return (ok, series, splits) from Yahoo for [start, today]."""
    p1 = _epoch(start - dt.timedelta(days=10))
    p2 = _epoch(dt.date.today() + dt.timedelta(days=3))
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={p1}&period2={p2}&interval=1d&events=splits")
    series, splits = [], []
    ok = False
    for attempt in range(4):
        try:
            r = requests.get(url, headers=H, timeout=35)
            if r.status_code == 200:
                j = r.json()
                res = (j.get("chart", {}).get("result") or [None])[0]
                if res and res.get("timestamp"):
                    ts = res["timestamp"]
                    ind = res["indicators"]
                    adj = (ind.get("adjclose") or [{}])[0].get("adjclose")
                    cl = ind["quote"][0].get("close")
                    px = adj if adj else cl
                    for t, v in zip(ts, px):
                        if v is None:
                            continue
                        series.append(
                            (dt.date.fromtimestamp(t).isoformat(), float(v)))
                    for ev in (res.get("events", {}).get("splits") or {}).values():
                        num, den = ev.get("numerator"), ev.get("denominator")
                        if num and den:
                            splits.append((
                                dt.date.fromtimestamp(ev["date"]).isoformat(),
                                float(num) / float(den),
                            ))
                ok = True  # valid response; empty means delisted/unknown
                break
            if r.status_code in (429, 503, 502):
                time.sleep(2 * (attempt + 1)); continue
            break
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    series.sort()
    splits.sort()
    return ok, series, splits

def _load(ticker: str, start: dt.date, end: dt.date) -> dict:
    sym = _yahoo_symbol(ticker)
    cpath = os.path.join(CACHE, f"{sym}.json")
    cached = None
    if os.path.exists(cpath):
        with open(cpath) as f:
            cached = json.load(f)
        if not _needs_refetch(cached, start, end):
            return cached
    fetch_start = min(start, HISTORY_START)
    ok, series, splits = _fetch(sym, fetch_start)
    if not ok and cached is not None:
        return cached  # keep the old copy on a transient failure
    out = {"ok": ok, "series": series, "splits": splits,
           "start": fetch_start.isoformat(),
           "fetched": dt.date.today().isoformat()}
    with open(cpath, "w") as f:
        json.dump(out, f)
    time.sleep(0.15)
    return out

def get_series(ticker: str, start: dt.date, end: dt.date):
    """Return sorted [(date_iso, adjclose)]. Cached per symbol from 2010 so
    repeated calls don't refetch."""
    cached = _load(ticker, start, end)
    if not cached.get("ok"):
        return []
    return [(d, p) for d, p in cached["series"]]

def get_splits(ticker: str, start: dt.date, end: dt.date):
    """Return sorted [(date_iso, ratio)], e.g. ratio 4.0 for a 4-for-1 split."""
    cached = _load(ticker, start, end)
    return [(d, r) for d, r in cached.get("splits", [])]

def split_factor(splits, after: dt.date, through: dt.date) -> float:
    """Product of split ratios with after < split date <= through. Multiply
    a share count reported as of `after` by this to compare it with one
    reported as of `through`."""
    lo, hi = after.isoformat(), through.isoformat()
    f = 1.0
    for d, ratio in splits:
        if lo < d <= hi:
            f *= ratio
    return f

class PriceBook:
    """Caches series in-process and answers as-of close queries."""
    def __init__(self, start: dt.date, end: dt.date):
        self.start, self.end = start, end
        self._mem = {}
        self._splits = {}

    def _get(self, ticker):
        if ticker not in self._mem:
            self._mem[ticker] = get_series(ticker, self.start, self.end)
        return self._mem[ticker]

    def series(self, ticker: str):
        return self._get(ticker)

    def splits(self, ticker: str):
        if ticker not in self._splits:
            self._splits[ticker] = get_splits(ticker, self.start, self.end)
        return self._splits[ticker]

    def as_of(self, ticker: str, date: dt.date, max_gap_days: int = 12):
        """Last adjusted close on/before `date`; None if no price within gap."""
        s = self._get(ticker)
        if not s:
            return None
        target = date.isoformat()
        lo, hi, ans = 0, len(s) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if s[mid][0] <= target:
                ans = s[mid]; lo = mid + 1
            else:
                hi = mid - 1
        if ans is None:
            return None
        gap = (date - dt.date.fromisoformat(ans[0])).days
        return ans[1] if 0 <= gap <= max_gap_days else None

if __name__ == "__main__":
    pb = PriceBook(dt.date(2021, 1, 1), dt.date(2026, 5, 16))
    for tk in ["AAPL", "MSFT", "NVDA"]:
        v0 = pb.as_of(tk, dt.date(2021, 5, 17))
        v1 = pb.as_of(tk, dt.date(2026, 5, 15))
        print(f"{tk}: 2021-05-17={v0}  2026-05-15={v1}  "
              f"5yr={None if not (v0 and v1) else round((v1/v0-1)*100,1)}%  "
              f"splits={pb.splits(tk)}")
