"""Market-wide insider open-market purchases from SEC Form 4 filings.

Screens like openinsider.com: every Form 4 filed each day is fetched and
only open-market purchases (transaction code P, acquired) are kept, with
insider name/title, shares, price, value, and a link to the filing.
Persisted in data/feed_form4.json; scripts/pull_insiders.py runs daily
from cron.

Form 4 volume is ~1,500-2,500/day and there is no purchases-only source,
so each filing must be fetched to learn its transaction codes. A small
thread pool keeps the daily pull to a few minutes while staying inside
the SEC's 10 requests/second guidance.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

import edgar

ROOT = os.path.dirname(__file__)
FEED_PATH = os.path.join(ROOT, "data", "feed_form4.json")
KEEP_DAYS = 90
MAX_WORKERS = 4

ProgressFn = Callable[[str, str], None]

# Global limiter shared by all workers: at most one request per 0.13s
# (~7.5/s), safely inside the SEC's 10 req/s ceiling even from a fast
# datacenter connection. Getting blocked looks like empty days — worse
# than being slow.
_rl_lock = threading.Lock()
_rl_last = [0.0]


def _rate_limited_get(url: str) -> requests.Response:
    """GET with global pacing; retries blocks/5xx; 404 passes through."""
    for attempt in range(5):
        with _rl_lock:
            wait = 0.13 - (time.time() - _rl_last[0])
            if wait > 0:
                time.sleep(wait)
            _rl_last[0] = time.time()
        try:
            r = requests.get(url, headers=edgar.H, timeout=45)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200 or r.status_code == 404:
            return r
        time.sleep(2.0 * (attempt + 1))  # 403/429/5xx: back off and retry
    raise RuntimeError(f"EDGAR fetch failed after retries: {url}")


def _day_form4(day: dt.date) -> dict[str, str]:
    """{accession: archive_path} for all Form 4 (not 4/A) filed that day.

    Raises on fetch failure (rate limit etc.) so the day is NOT silently
    treated as empty; a 404 (weekend/holiday) returns {}."""
    q = (day.month - 1) // 3 + 1
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}"
           f"/QTR{q}/form.{day.strftime('%Y%m%d')}.idx")
    r = _rate_limited_get(url)
    if r.status_code == 404:
        return {}
    out: dict[str, str] = {}
    for line in r.text.splitlines():
        if line[:17].strip() != "4":
            continue
        parts = line.rsplit(None, 3)
        if len(parts) != 4 or not parts[3].startswith("edgar/data/"):
            continue
        acc = parts[3].rsplit("/", 1)[-1].removesuffix(".txt")
        out.setdefault(acc, parts[3])
    return out


def _title(rel: ET.Element | None, officer_title: str | None,
           is_dir: str | None, is_officer: str | None, is_ten: str | None) -> str:
    parts = []
    if officer_title and officer_title.strip():
        parts.append(officer_title.strip())
    elif is_officer in ("1", "true"):
        parts.append("Officer")
    if is_dir in ("1", "true"):
        parts.append("Dir")
    if is_ten in ("1", "true"):
        parts.append("10%")
    return ", ".join(parts) or "—"


def _parse_purchases(txt: str) -> dict | None:
    """Aggregate open-market purchases (code P, acquired) in one Form 4.

    Returns a partial row or None if the filing contains no purchases."""
    m = re.search(r"<XML>(.*?)</XML>", txt, re.S)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group(1).strip())
    except ET.ParseError:
        return None

    shares = 0.0
    cost = 0.0
    owned_after = None
    trade_date = None
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext(".//transactionCode")
        ad = tx.findtext(".//transactionAcquiredDisposedCode/value")
        if code != "P" or ad != "A":
            continue
        sh = float(tx.findtext(".//transactionShares/value") or 0)
        px = float(tx.findtext(".//transactionPricePerShare/value") or 0)
        shares += sh
        cost += sh * px
        d = tx.findtext(".//transactionDate/value")
        if d and (trade_date is None or d < trade_date):
            trade_date = d
        oa = tx.findtext(".//sharesOwnedFollowingTransaction/value")
        if oa:
            owned_after = float(oa)
    if shares <= 0:
        return None

    owners = [
        (o.findtext(".//rptOwnerName") or "").strip()
        for o in root.iter("reportingOwner")
    ]
    owner_ciks = [
        (o.findtext(".//rptOwnerCik") or "").strip().zfill(10)
        for o in root.iter("reportingOwner")
        if (o.findtext(".//rptOwnerCik") or "").strip()
    ]
    rel = root.find(".//reportingOwnerRelationship")
    title = _title(
        rel,
        rel.findtext("officerTitle") if rel is not None else None,
        rel.findtext("isDirector") if rel is not None else None,
        rel.findtext("isOfficer") if rel is not None else None,
        rel.findtext("isTenPercentOwner") if rel is not None else None,
    )
    # 10b5-1 checkbox (since 2023): purchase was pre-scheduled, not a
    # spur-of-conviction decision
    plan = (root.findtext(".//aff10b5One") or "").strip().lower() in ("1", "true")
    return {
        "trade": trade_date or "",
        "company": (root.findtext(".//issuerName") or "—").strip(),
        "ticker": (root.findtext(".//issuerTradingSymbol") or "").strip().upper(),
        "insider": " / ".join(n for n in owners if n) or "—",
        "owner_ciks": owner_ciks,
        "title": title,
        "plan": plan,
        "shares": round(shares),
        "price": round(cost / shares, 2) if shares else None,
        "value": round(cost),
        "owned_after": round(owned_after) if owned_after is not None else None,
    }


def _parse_trades(txt: str, codes: tuple[str, ...] = ("P", "S")) -> list[dict]:
    """All open-market trades in one Form 4, one row per transaction code.

    Unlike _parse_purchases (feed-oriented, P only), this keeps sales too —
    used by the ticker lookup. Returns [{action, trade, shares, price,
    value, owned_after, insider, title, plan}]."""
    m = re.search(r"<XML>(.*?)</XML>", txt, re.S)
    if not m:
        return []
    try:
        root = ET.fromstring(m.group(1).strip())
    except ET.ParseError:
        return []

    agg: dict[str, dict] = {}
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext(".//transactionCode")
        if code not in codes:
            continue
        sh = float(tx.findtext(".//transactionShares/value") or 0)
        px = float(tx.findtext(".//transactionPricePerShare/value") or 0)
        if sh <= 0:
            continue
        e = agg.setdefault(code, {"shares": 0.0, "cost": 0.0,
                                  "trade": None, "owned_after": None})
        e["shares"] += sh
        e["cost"] += sh * px
        d = tx.findtext(".//transactionDate/value")
        if d and (e["trade"] is None or d < e["trade"]):
            e["trade"] = d
        oa = tx.findtext(".//sharesOwnedFollowingTransaction/value")
        if oa:
            e["owned_after"] = float(oa)
    if not agg:
        return []

    owners = [
        (o.findtext(".//rptOwnerName") or "").strip()
        for o in root.iter("reportingOwner")
    ]
    rel = root.find(".//reportingOwnerRelationship")
    title = _title(
        rel,
        rel.findtext("officerTitle") if rel is not None else None,
        rel.findtext("isDirector") if rel is not None else None,
        rel.findtext("isOfficer") if rel is not None else None,
        rel.findtext("isTenPercentOwner") if rel is not None else None,
    )
    plan = (root.findtext(".//aff10b5One") or "").strip().lower() in ("1", "true")
    label = {"P": "Buy", "S": "Sell"}
    out = []
    for code, e in agg.items():
        out.append({
            "action": label.get(code, code),
            "trade": e["trade"] or "",
            "insider": " / ".join(n for n in owners if n) or "—",
            "title": title,
            "plan": plan,
            "shares": round(e["shares"]),
            "price": round(e["cost"] / e["shares"], 2) if e["shares"] else None,
            "value": round(e["cost"]),
            "owned_after": (round(e["owned_after"])
                            if e["owned_after"] is not None else None),
        })
    return out


def _fetch_one(acc: str, path: str, day_s: str) -> tuple[str, dict | None, bool]:
    """Returns (accession, purchase-row-or-None, fetch_ok).

    fetch_ok=False means we could not check this filing — it must NOT be
    cached as 'seen', so a later run retries it."""
    try:
        r = _rate_limited_get("https://www.sec.gov/Archives/" + path)
        if r.status_code != 200:
            return acc, None, False
        row = _parse_purchases(r.text)
    except (RuntimeError, ValueError):
        return acc, None, False
    if not row:
        return acc, None, True
    path_cik = path.split("/")[2]
    row["filed"] = day_s
    row["url"] = edgar.filing_index_url(path_cik, acc)
    return acc, row, True


def load_feed() -> dict:
    if os.path.exists(FEED_PATH):
        with open(FEED_PATH) as f:
            return json.load(f)
    return {"updated": None, "days_done": [], "rows": {}}


def _save_feed(feed: dict) -> None:
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    with open(FEED_PATH, "w") as f:
        json.dump(feed, f, indent=1)
        f.write("\n")


def update_feed(days_back: int = 3, on_progress: ProgressFn | None = None) -> dict:
    """Pull Form 4s for the last N days, keep only purchases (idempotent)."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    feed = load_feed()
    rows = feed["rows"]
    done = set(feed.get("days_done", []))
    seen = set(feed.get("seen", []))  # non-purchase accessions already checked
    today = dt.date.today()
    new_count = 0

    for offset in range(days_back, -1, -1):
        day = today - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        day_s = day.isoformat()
        if day_s in done and day != today:
            continue
        _prog(day_s, "fetching index…")
        try:
            accs = _day_form4(day)
        except RuntimeError:
            _prog(day_s, "index fetch failed — will retry next run")
            continue
        fresh = {a: p for a, p in accs.items() if a not in rows and a not in seen}
        _prog(day_s, f"checking {len(fresh)} Form 4s…")
        checked = 0
        failures = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for acc, row, ok in pool.map(
                lambda item: _fetch_one(item[0], item[1], day_s), fresh.items()
            ):
                checked += 1
                if checked % 200 == 0:
                    _prog(day_s, f"checked {checked}/{len(fresh)}…")
                if row:
                    rows[acc] = row
                    new_count += 1
                elif ok:
                    seen.add(acc)
                else:
                    failures += 1
        # only close the book on a day if every filing was actually checked
        if day != today and failures == 0:
            done.add(day_s)
        _prog(day_s, f"done ({new_count} purchases so far"
                     + (f", {failures} fetch failures — day will re-run" if failures else "")
                     + ")")

    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    feed["rows"] = {a: r for a, r in rows.items() if r["filed"] >= cutoff}
    feed["days_done"] = sorted(d for d in done if d >= cutoff)
    feed["seen"] = sorted(seen)[-100000:]
    feed["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    _save_feed(feed)
    feed["new_count"] = new_count
    return feed


def recent_rows(feed: dict, days: int = 30, min_value: float = 0) -> list[dict]:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = [
        r for r in feed.get("rows", {}).values()
        if r["filed"] >= cutoff and (r.get("value") or 0) >= min_value
    ]
    rows.sort(key=lambda r: (r["filed"], r.get("value") or 0), reverse=True)
    return rows


def cluster_buys(rows: list[dict], limit: int = 20) -> list[dict]:
    """Companies where 2+ distinct insiders bought in the window."""
    by_co: dict[str, dict] = {}
    for r in rows:
        key = r["ticker"] or r["company"]
        e = by_co.setdefault(key, {
            "ticker": r["ticker"], "company": r["company"],
            "insiders": set(), "buys": 0, "total_value": 0.0,
            "last_filed": r["filed"],
        })
        e["insiders"].add(r["insider"])
        e["buys"] += 1
        e["total_value"] += r.get("value") or 0
        e["last_filed"] = max(e["last_filed"], r["filed"])
    out = [
        {**e, "insiders": len(e["insiders"])}
        for e in by_co.values() if len(e["insiders"]) >= 2
    ]
    out.sort(key=lambda x: (-x["insiders"], -x["total_value"]))
    return out[:limit]
