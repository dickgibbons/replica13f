"""CUSIP -> US ticker resolution.

Primary path: OpenFIGI (exact, CUSIP-keyed). Works without a key at a low
rate limit; set OPENFIGI_KEY for 10x throughput.
Fallback path: fuzzy match the 13F issuer name against the SEC public
company_tickers file, used only when OpenFIGI has no match. The fallback is
approximate -- it collapses share classes and misses post-filing renames/M&A.
Each path has its own persistent disk cache.
"""
from __future__ import annotations
import os, json, time, re
import requests

CACHE = os.path.join(os.path.dirname(__file__), "cache")
MAP_PATH = os.path.join(CACHE, "cusip_ticker.json")
FIGI_PATH = os.path.join(CACHE, "cusip_figi.json")
SEC_TICKERS = os.path.join(CACHE, "sec_company_tickers.json")
KEY = os.environ.get("OPENFIGI_KEY")
FIGI_URL = "https://api.openfigi.com/v3/mapping"
UA = os.environ.get("EDGAR_UA", "Replica13F research contact@example.com")

SUFFIXES = {"INC", "INC.", "CORP", "CORP.", "CO", "CO.", "LTD", "LTD.", "LLC",
            "PLC", "LP", "L P", "L.P.", "SA", "NV", "AG", "THE", "HLDGS",
            "HOLDINGS", "HLDG", "HOLDING", "GROUP", "GRP", "CL", "CLASS",
            "COM", "COMMON", "STK", "SHS", "ADR", "ADS", "NEW", "CALL", "PUT"}

def _norm(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"[^A-Za-z0-9 ]", " ", name.upper())
    toks = [t for t in s.split() if t and t not in SUFFIXES]
    return " ".join(toks)

def _load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _save(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

# ---- OpenFIGI ------------------------------------------------------------
# Keyless access is limited to 10 jobs/request and 25 requests/minute.
FIGI_BATCH, FIGI_PAUSE = (100, 0.3) if KEY else (10, 2.5)


def _figi(cusips, figi_cache):
    """Fill figi_cache {cusip: ticker or ""} ("" = OpenFIGI has no match)."""
    todo = [c for c in cusips if c not in figi_cache]
    if not todo:
        return
    headers = {"Content-Type": "application/json"}
    if KEY:
        headers["X-OPENFIGI-APIKEY"] = KEY
    for i in range(0, len(todo), FIGI_BATCH):
        chunk = todo[i:i + FIGI_BATCH]
        body = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        res = None
        for attempt in range(3):
            try:
                r = requests.post(FIGI_URL, headers=headers, json=body, timeout=40)
                if r.status_code == 429:
                    time.sleep(15 * (attempt + 1))
                    continue
                r.raise_for_status()
                res = r.json()
                break
            except requests.RequestException:
                time.sleep(5)
        if res is None:
            continue
        for c, item in zip(chunk, res):
            if not isinstance(item, dict):
                continue
            err = item.get("error") or item.get("warning")
            if err and "no identifier found" not in str(err).lower():
                continue
            data = item.get("data")
            tk = ""
            if data:
                us = [d for d in data if d.get("exchCode") in
                      ("US", "UN", "UW", "UQ", "UR", "UA")]
                pool = us or data
                tk = (pool[0].get("ticker") or "").replace("/", "-")
            figi_cache[c] = tk
        _save(figi_cache, FIGI_PATH)
        time.sleep(FIGI_PAUSE)

# ---- SEC name-match fallback --------------------------------------------
def _sec_index():
    raw = _load(SEC_TICKERS)
    if not raw:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                          headers={"User-Agent": UA}, timeout=40)
        r.raise_for_status()
        raw = r.json()
        _save(raw, SEC_TICKERS)
    idx = {}
    for row in raw.values():
        idx.setdefault(_norm(row["title"]), row["ticker"].upper())
    return idx

def _name_match(holdings, cache):
    idx = None
    for h in holdings:
        c = h["cusip"]
        if cache.get(c) is not None:
            continue
        if idx is None:
            idx = _sec_index()
        key = _norm(h.get("issuer", ""))
        tk = idx.get(key)
        if tk is None and key:
            parts = key.split()
            for n in range(len(parts) - 1, 1, -1):
                tk = idx.get(" ".join(parts[:n]))
                if tk:
                    break
        cache[c] = tk

# ---- public API ----------------------------------------------------------
def resolve_holdings(all_holdings):
    """all_holdings: iterable of {cusip, issuer}. Returns {cusip: ticker|None}."""
    cache = _load(MAP_PATH)
    uniq = {}
    for h in all_holdings:
        uniq.setdefault(h["cusip"].upper(), h.get("issuer", ""))
    cusips = list(uniq)
    figi = _load(FIGI_PATH)
    _figi(cusips, figi)
    # OpenFIGI suffixes retired tickers with "*" (e.g. "HHC*"); prefer the
    # issuer's current SEC ticker for those.
    def firm(c):
        tk = figi.get(c) or ""
        return tk if tk and not tk.endswith("*") else ""

    missing = [{"cusip": c, "issuer": uniq[c]} for c in cusips
               if not firm(c) and cache.get(c) is None]
    if missing:
        _name_match(missing, cache)
        _save(cache, MAP_PATH)
    return {c: firm(c) or cache.get(c) or (figi.get(c) or "").rstrip("*") or None
            for c in cusips}

if __name__ == "__main__":
    sample = [
        {"cusip": "023135106", "issuer": "AMAZON COM INC"},
        {"cusip": "594918104", "issuer": "MICROSOFT CORP"},
        {"cusip": "11271J107", "issuer": "BROOKFIELD CORP"},
        {"cusip": "90353T100", "issuer": "UBER TECHNOLOGIES INC"},
        {"cusip": "67066G104", "issuer": "NVIDIA CORPORATION"},
        {"cusip": "874039100", "issuer": "TAIWAN SEMICONDUCTOR MANUFACTURING"},
    ]
    print(json.dumps(resolve_holdings(sample), indent=2))
