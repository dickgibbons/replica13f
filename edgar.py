"""EDGAR 13F ingestion.

Pulls 13F-HR information tables for a CIK, normalizes the value-unit change
(SEC mandated whole-dollar reporting for filings on/after 2023-01-03; before
that, value is in thousands), and collapses amendments so each period is
represented by its latest filing.
"""
from __future__ import annotations
import html as html_mod
import os, json, re, time, datetime as dt
import xml.etree.ElementTree as ET
import requests

UA = os.environ.get("EDGAR_UA", "Replica13F research contact@example.com")
H = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
CACHE = os.path.join(os.path.dirname(__file__), "cache")
SEC_TICKERS = os.path.join(CACHE, "sec_company_tickers.json")
DOLLAR_RULE_DATE = dt.date(2023, 1, 3)  # filings on/after this report value in whole $

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

_last_call = [0.0]
def _throttle(min_interval=0.12):
    delta = time.time() - _last_call[0]
    if delta < min_interval:
        time.sleep(min_interval - delta)
    _last_call[0] = time.time()

def _get(url, as_json=False, retries=4):
    for attempt in range(retries):
        _throttle()
        try:
            r = requests.get(url, headers=H, timeout=45)
            if r.status_code == 200:
                return r.json() if as_json else r.text
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"EDGAR fetch failed: {url}")

def _load_company_tickers() -> dict:
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(SEC_TICKERS):
        with open(SEC_TICKERS) as f:
            return json.load(f)
    raw = _get("https://www.sec.gov/files/company_tickers.json", as_json=True)
    with open(SEC_TICKERS, "w") as f:
        json.dump(raw, f)
    return raw


def search_entities(query: str, limit: int = 10) -> list[dict]:
    """Search SEC registrants by company title. Returns [{name, cik, ticker}]."""
    q = _norm(query)
    if not q:
        return []
    tokens = q.split()
    raw = _load_company_tickers()
    scored: list[tuple[float, dict]] = []
    for row in raw.values():
        title = row.get("title", "")
        norm = _norm(title)
        if not norm:
            continue
        score = 0.0
        if q in norm:
            score += 10.0
        overlap = sum(1 for t in tokens if t in norm)
        if overlap == 0:
            continue
        score += overlap * 2.0
        if norm.startswith(q):
            score += 3.0
        cik = str(row.get("cik_str", row.get("cik", ""))).zfill(10)
        scored.append((score, {
            "name": title,
            "cik": cik,
            "ticker": (row.get("ticker") or "").upper(),
        }))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    seen = set()
    out = []
    for _, item in scored:
        if item["cik"] in seen:
            continue
        seen.add(item["cik"])
        out.append(item)
        if len(out) >= limit:
            break
    return out


# Ownership filings: 13D = activist stake >5% (filed within days), 13G = passive.
# EDGAR renamed the forms from "SC 13D" to "SCHEDULE 13D" in late 2024.
OWNERSHIP_FORMS = {
    "13D": ("SC 13D", "SCHEDULE 13D"),
    "13G": ("SC 13G", "SCHEDULE 13G"),
}
SUBJECTS_CACHE = os.path.join(CACHE, "filing_subjects.json")


def list_ownership_filings(cik: str, kinds: tuple[str, ...] = ("13D",)):
    """All 13D/13G filings BY this manager (incl. amendments).

    Returns [{form, kind, filing_date, accession}] newest first."""
    prefixes = tuple(p for k in kinds for p in OWNERSHIP_FORMS[k])
    cik = str(cik).zfill(10)
    data = _get(f"https://data.sec.gov/submissions/CIK{cik}.json", as_json=True)
    blocks = [data["filings"]["recent"]]
    for extra in data["filings"].get("files", []):
        blocks.append(_get(f"https://data.sec.gov/submissions/{extra['name']}", as_json=True))
    out = []
    for b in blocks:
        for form, fdate, acc in zip(b["form"], b["filingDate"], b["accessionNumber"]):
            fu = form.upper()
            if fu.startswith(prefixes):
                kind = "13D" if "13D" in fu else "13G"
                out.append({
                    "form": form, "kind": kind,
                    "filing_date": fdate, "accession": acc,
                })
    out.sort(key=lambda f: f["filing_date"], reverse=True)
    return out


def filing_index_url(cik: str, accession: str) -> str:
    acc_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{accession}-index.htm"


def filing_subject(cik: str, accession: str) -> str | None:
    """Target company named on a 13D/13G filing (cached on disk)."""
    os.makedirs(CACHE, exist_ok=True)
    subjects = {}
    if os.path.exists(SUBJECTS_CACHE):
        with open(SUBJECTS_CACHE) as f:
            subjects = json.load(f)
    if accession in subjects:
        return subjects[accession]
    html = _get(filing_index_url(cik, accession))
    subject = None
    for span in re.findall(r'<span class="companyName">([^<]*)', html):
        if "(Subject)" in span:
            subject = html_mod.unescape(span.split("(Subject)")[0].strip())
            break
    subjects[accession] = subject
    with open(SUBJECTS_CACHE, "w") as f:
        json.dump(subjects, f)
    return subject


PARTIES_CACHE = os.path.join(CACHE, "filing_parties.json")


def filing_parties(cik: str, accession: str) -> dict:
    """Subject company and filer(s) named on a 13D/13G filing (cached).

    Returns {"subject": {"name", "cik"} | None,
             "filed_by": [{"name", "cik"}]}."""
    os.makedirs(CACHE, exist_ok=True)
    cache = {}
    if os.path.exists(PARTIES_CACHE):
        with open(PARTIES_CACHE) as f:
            cache = json.load(f)
    hit = cache.get(accession)
    # refetch entries cached before filed_by carried CIKs (plain strings)
    if hit and not (hit.get("filed_by") and isinstance(hit["filed_by"][0], str)):
        return hit
    html = _get(filing_index_url(cik, accession))
    subject = None
    filed_by = []
    for block in re.findall(r'<span class="companyName">(.*?)</span>', html, re.S):
        text = html_mod.unescape(block.split("<")[0].strip())
        m = re.search(r"CIK=(\d{10})", block)
        block_cik = m.group(1) if m else None
        if "(Subject)" in text:
            subject = {
                "name": text.split("(Subject)")[0].strip(),
                "cik": block_cik,
            }
        elif "(Filed by)" in text:
            filed_by.append({
                "name": text.split("(Filed by)")[0].strip(),
                "cik": block_cik,
            })
    parties = {"subject": subject, "filed_by": filed_by}
    cache[accession] = parties
    with open(PARTIES_CACHE, "w") as f:
        json.dump(cache, f)
    return parties


def list_13f_filings(cik: str):
    """Return [{period, filing_date, accepted, accession, form}] for all
    13F-HR / 13F-HR/A. `accepted` is EDGAR's acceptanceDateTime (UTC)."""
    cik = str(cik).zfill(10)
    data = _get(f"https://data.sec.gov/submissions/CIK{cik}.json", as_json=True)
    out = []
    rec = data["filings"]["recent"]
    blocks = [rec]
    # older filings may be paginated into separate files
    for extra in data["filings"].get("files", []):
        ej = _get(f"https://data.sec.gov/submissions/{extra['name']}", as_json=True)
        blocks.append(ej)
    for b in blocks:
        accepted = b.get("acceptanceDateTime") or [None] * len(b["form"])
        for form, fdate, pdate, acc, acc_dt in zip(
            b["form"], b["filingDate"], b["reportDate"], b["accessionNumber"],
            accepted,
        ):
            if form in ("13F-HR", "13F-HR/A"):
                out.append({
                    "period": pdate, "filing_date": fdate, "accepted": acc_dt,
                    "accession": acc, "form": form,
                })
    return out


AMENDMENT_TYPES_CACHE = os.path.join(CACHE, "amendment_types.json")


def amendment_type(cik: str, accession: str) -> str | None:
    """'RESTATEMENT' or 'NEW HOLDINGS' for a 13F-HR/A (cached on disk).

    A NEW HOLDINGS amendment lists only positions added to the original
    report, so it must be merged into the original, not replace it."""
    os.makedirs(CACHE, exist_ok=True)
    cache = {}
    if os.path.exists(AMENDMENT_TYPES_CACHE):
        with open(AMENDMENT_TYPES_CACHE) as f:
            cache = json.load(f)
    if accession in cache:
        return cache[accession]
    acc = accession.replace("-", "")
    idx = _get(
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json",
        as_json=True,
    )
    names = [it["name"] for it in idx["directory"]["item"]]
    primary = next((n for n in names if n.lower() == "primary_doc.xml"), None)
    kind = None
    if primary:
        raw = _get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary}")
        m = re.search(r"<(?:\w+:)?amendmentType>\s*([^<]+?)\s*<", raw)
        if m:
            kind = m.group(1).strip().upper()
    cache[accession] = kind
    with open(AMENDMENT_TYPES_CACHE, "w") as f:
        json.dump(cache, f)
    return kind

def _infotable_url(cik: str, accession: str):
    acc = accession.replace("-", "")
    idx = _get(
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json",
        as_json=True,
    )
    # prefer a file literally named *infotable*.xml; fall back to any non-primary xml
    items = idx["directory"]["item"]
    names = [it["name"] for it in items]
    cand = [n for n in names if n.lower().endswith(".xml") and "infotable" in n.lower()]
    if not cand:
        cand = [n for n in names
                if n.lower().endswith(".xml") and "primary_doc" not in n.lower()
                and not n.lower().startswith("xsl")]
    if not cand:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{cand[0]}"

def parse_infotable(cik: str, accession: str, filing_date: str,
                    exclude_options: bool = True):
    """Return list of holdings: {issuer, cusip, value_usd, shares, put_call}.

    value_usd is normalized to whole dollars regardless of filing era. Debt
    (PRN) lines are always skipped. Option lines carry a putCall tag; their
    reported value is the UNDERLYING's market value, not premium or direction,
    so they are excluded from the replica by default (a put would otherwise be
    counted as a long). Set exclude_options=False to inspect them."""
    url = _infotable_url(cik, accession)
    if not url:
        return []
    raw = _get(url)
    root = ET.fromstring(raw)
    # Filers vary in namespace style (default xmlns, ns1: prefixes, or none).
    # Strip namespaces from every tag so lookups work for all dialects.
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    fd = dt.date.fromisoformat(filing_date)
    scale = 1 if fd >= DOLLAR_RULE_DATE else 1000
    rows = []
    for it in root.iter("infoTable"):
        def txt(tag, parent=it):
            e = parent.find(tag)
            return e.text.strip() if e is not None and e.text else None
        cusip = txt("cusip")
        val = txt("value")
        sh_parent = it.find("shrsOrPrnAmt")
        shares = txt("sshPrnamt", sh_parent) if sh_parent is not None else None
        sh_type = txt("sshPrnamtType", sh_parent) if sh_parent is not None else None
        put_call = txt("putCall")
        if not cusip or val is None:
            continue
        try:
            value_usd = float(val) * scale
        except ValueError:
            continue
        # only aggregate equity share lines; skip principal-amount (debt) lines
        if sh_type and sh_type.upper() != "SH":
            continue
        if exclude_options and put_call:
            continue
        rows.append({
            "issuer": txt("nameOfIssuer"),
            "cusip": cusip.upper(),
            "value_usd": value_usd,
            "shares": float(shares) if shares else None,
            "put_call": put_call.upper() if put_call else None,
        })
    return rows

def _collapse_cusips(rows: list[dict]) -> dict[str, dict]:
    """Sum value and shares across duplicate CUSIP lines (share classes,
    multiple managers on one report)."""
    agg: dict[str, dict] = {}
    for row in rows:
        k = row["cusip"]
        if k in agg:
            agg[k]["value_usd"] += row["value_usd"]
            if row.get("shares") is not None:
                agg[k]["shares"] = (agg[k].get("shares") or 0.0) + row["shares"]
        else:
            agg[k] = dict(row)
    return agg


def _period_filings(filings: list[dict], cik: str) -> list[dict]:
    """Filings that make up one period's report, oldest first: the latest
    full report (original or restatement) plus any later NEW HOLDINGS
    amendments, which add positions to it."""
    filings = sorted(filings, key=lambda f: (f["filing_date"], f["accession"]))
    base_idx = 0
    for i, f in enumerate(filings):
        if f["form"] == "13F-HR":
            base_idx = i
        elif amendment_type(cik, f["accession"]) != "NEW HOLDINGS":
            base_idx = i
    return [filings[base_idx]] + [
        f for f in filings[base_idx + 1:]
        if f["form"] == "13F-HR/A"
        and amendment_type(cik, f["accession"]) == "NEW HOLDINGS"
    ]


def holdings_by_period(cik: str, cache=True, max_periods=None):
    """Holdings per period, with amendments applied.

    Returns {period(str): {"filing_date", "accepted", "form", "holdings"}}.
    filing_date/accepted are those of the full report the holdings come
    from, i.e. when the bulk of the information became public.
    max_periods limits to the most recent N periods (cuts EDGAR calls for
    trailing-window runs)."""
    tag = f"_last{max_periods}" if max_periods else ""
    cpath = os.path.join(CACHE, f"holdings_v2_{str(cik).zfill(10)}{tag}.json")
    if cache and os.path.exists(cpath):
        with open(cpath) as f:
            return json.load(f)
    by_period: dict[str, list[dict]] = {}
    for f in list_13f_filings(cik):
        by_period.setdefault(f["period"], []).append(f)
    keep = sorted(by_period)
    if max_periods:
        keep = keep[-max_periods:]
    result = {}
    for p in keep:
        parts = _period_filings(by_period[p], cik)
        agg: dict[str, dict] = {}
        for f in parts:
            rows = _collapse_cusips(
                parse_infotable(cik, f["accession"], f["filing_date"]))
            agg.update(rows)
        base = parts[0]
        result[p] = {"filing_date": base["filing_date"],
                     "accepted": base.get("accepted"),
                     "form": base["form"],
                     "holdings": list(agg.values())}
    if cache:
        os.makedirs(CACHE, exist_ok=True)
        with open(cpath, "w") as fh:
            json.dump(result, fh)
    return result

if __name__ == "__main__":
    import sys
    cik = sys.argv[1] if len(sys.argv) > 1 else "0001336528"
    hp = holdings_by_period(cik, cache=False)
    periods = sorted(hp)
    print(f"CIK {cik}: {len(periods)} periods, {periods[0]} .. {periods[-1]}")
    last = periods[-1]
    hs = sorted(hp[last]["holdings"], key=lambda x: -x["value_usd"])[:5]
    print(f"Top 5 for {last} (filed {hp[last]['filing_date']}):")
    for r in hs:
        print(f"  {r['issuer'][:28]:28s} {r['cusip']}  ${r['value_usd']:,.0f}")
