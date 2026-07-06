"""Market-wide daily 13D/13G feed built from EDGAR daily form indexes.

Every business day EDGAR publishes form.YYYYMMDD.idx listing all filings.
We keep every SC/SCHEDULE 13D and 13G (and amendments) with the target
company, its ticker, and who filed — persisted in data/feed13d.json so a
daily cron (scripts/pull_13d.py) can keep it current. 13D = activist
stake >5%; 13G = the passive (short-form) equivalent.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
from typing import Callable

import requests

import edgar

ROOT = os.path.dirname(__file__)
FEED_PATH = os.path.join(ROOT, "data", "feed13d.json")
KEEP_DAYS = 120          # prune rows older than this
FORM_RE = re.compile(r"^(SC 13[DG](?:/A)?|SCHEDULE 13[DG](?:/A)?)\s")

ProgressFn = Callable[[str, str], None]


def _form_label(form: str) -> str:
    return form.upper().replace("SCHEDULE ", "").replace("SC ", "").strip()


def _ticker_map() -> dict[int, str]:
    raw = edgar._load_company_tickers()
    out: dict[int, str] = {}
    for row in raw.values():
        cik = int(row.get("cik_str", row.get("cik", 0)) or 0)
        if cik and cik not in out:
            out[cik] = (row.get("ticker") or "").upper()
    return out


def _day_accessions(day: dt.date) -> dict[str, dict]:
    """13D lines of one daily index, grouped by accession.

    Returns {accession: {form, date, path_cik}} or {} if no index (weekend,
    holiday, or not yet published)."""
    q = (day.month - 1) // 3 + 1
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}"
           f"/QTR{q}/form.{day.strftime('%Y%m%d')}.idx")
    r = requests.get(url, headers=edgar.H, timeout=45)
    if r.status_code != 200:
        return {}
    out: dict[str, dict] = {}
    for line in r.text.splitlines():
        m = FORM_RE.match(line)
        if not m:
            continue
        parts = line.rsplit(None, 3)
        if len(parts) != 4 or not parts[3].startswith("edgar/data/"):
            continue
        path = parts[3]
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        path_cik = path.split("/")[2]
        out.setdefault(accession, {
            "form": m.group(1),
            "date": day.isoformat(),
            "path_cik": path_cik,
        })
    return out


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


def update_feed(days_back: int = 7, on_progress: ProgressFn | None = None) -> dict:
    """Pull the last N days of daily indexes into the feed (idempotent).

    Past days are fetched once; today is always refetched (its index grows
    during the day). Rows older than KEEP_DAYS are pruned."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    feed = load_feed()
    rows = feed["rows"]
    done = set(feed.get("days_done", []))
    today = dt.date.today()
    tickers = _ticker_map()
    new_count = 0

    for offset in range(days_back, -1, -1):
        day = today - dt.timedelta(days=offset)
        if day.weekday() >= 5:  # weekend
            continue
        day_s = day.isoformat()
        if day_s in done and day != today:
            continue
        _prog(day_s, "fetching index…")
        accs = _day_accessions(day)
        fresh = {a: meta for a, meta in accs.items() if a not in rows}
        for i, (acc, meta) in enumerate(sorted(fresh.items())):
            _prog(day_s, f"resolving {i + 1}/{len(fresh)}…")
            try:
                parties = edgar.filing_parties(meta["path_cik"], acc)
            except Exception:
                parties = {"subject": None, "filed_by": []}
            subject = parties.get("subject") or {}
            scik = subject.get("cik")
            filed_by = parties.get("filed_by") or []
            label = _form_label(meta["form"])
            rows[acc] = {
                "filed": meta["date"],
                "form": label,
                "kind": "13G" if label.startswith("13G") else "13D",
                "company": subject.get("name") or "—",
                "company_cik": scik,
                "ticker": tickers.get(int(scik), "") if scik else "",
                "filers": [f["name"] for f in filed_by],
                "filer_ciks": [f["cik"] for f in filed_by if f.get("cik")],
                "url": edgar.filing_index_url(meta["path_cik"], acc),
            }
            new_count += 1
        if day != today:
            done.add(day_s)
        _prog(day_s, f"done ({len(fresh)} new)")

    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    feed["rows"] = {a: r for a, r in rows.items() if r["filed"] >= cutoff}
    feed["days_done"] = sorted(d for d in done if d >= cutoff)
    feed["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    _save_feed(feed)
    feed["new_count"] = new_count
    return feed


def recent_rows(feed: dict, days: int = 30) -> list[dict]:
    """Feed rows within the lookback window, newest first."""
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = [r for r in feed.get("rows", {}).values() if r["filed"] >= cutoff]
    rows.sort(key=lambda r: r["filed"], reverse=True)
    return rows


def row_kind(r: dict) -> str:
    """'13D' or '13G' (works for rows saved before the kind field existed)."""
    return r.get("kind") or ("13G" if r["form"].startswith("13G") else "13D")


def filter_family(rows: list[dict], family: str) -> list[dict]:
    """family: '13D' | '13G' | 'both'."""
    if family == "both":
        return rows
    return [r for r in rows if row_kind(r) == family]


def universe_rows(rows: list[dict], funds: list[dict]) -> list[dict]:
    """Feed rows filed by a universe fund (matched by filer CIK).

    Each returned row gains a 'fund' key with the universe fund's name."""
    by_cik = {str(f["cik"]).zfill(10): f["name"] for f in funds}
    out = []
    for r in rows:
        matched = [by_cik[c] for c in r.get("filer_ciks", []) if c in by_cik]
        if matched:
            out.append({**r, "fund": ", ".join(matched)})
    return out


def most_targeted(rows: list[dict], limit: int = 20) -> list[dict]:
    """Companies with the most 13D activity in the window."""
    by_company: dict[str, dict] = {}
    for r in rows:
        key = r.get("company_cik") or r["company"]
        e = by_company.setdefault(key, {
            "company": r["company"], "ticker": r["ticker"],
            "filings": 0, "new_13d": 0, "amendments": 0,
            "filers": set(), "last_filed": r["filed"],
        })
        e["filings"] += 1
        if r["form"].endswith("/A"):
            e["amendments"] += 1
        else:
            e["new_13d"] += 1
        for fname in r["filers"]:
            e["filers"].add(fname)
        e["last_filed"] = max(e["last_filed"], r["filed"])
    out = []
    for e in by_company.values():
        out.append({**e, "filers": len(e["filers"])})
    out.sort(key=lambda x: (-x["filings"], x["company"]))
    return out[:limit]


def most_active_filers(rows: list[dict], limit: int = 20) -> list[dict]:
    """Funds/people filing the most 13Ds in the window."""
    by_filer: dict[str, dict] = {}
    for r in rows:
        for fname in r["filers"]:
            e = by_filer.setdefault(fname, {
                "filer": fname, "filings": 0,
                "companies": set(), "last_filed": r["filed"],
            })
            e["filings"] += 1
            e["companies"].add(r["company"])
            e["last_filed"] = max(e["last_filed"], r["filed"])
    out = []
    for e in by_filer.values():
        out.append({**e, "companies": len(e["companies"])})
    out.sort(key=lambda x: (-x["filings"], x["filer"]))
    return out[:limit]
