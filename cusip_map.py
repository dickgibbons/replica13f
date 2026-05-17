"""CUSIP -> US ticker resolution.

Primary path: OpenFIGI (exact, CUSIP-keyed) when OPENFIGI_KEY is set.
Fallback path: fuzzy match the 13F issuer name against the SEC public
company_tickers file. The fallback is approximate -- it collapses share
classes and misses post-filing renames/M&A -- so production should always
run with an OpenFIGI key. Both paths share one persistent disk cache.
"""
from __future__ import annotations
import os, json, time, re
import requests

CACHE = os.path.join(os.path.dirname(__file__), "cache")
MAP_PATH = os.path.join(CACHE, "cusip_ticker.json")
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
def _figi(cusips, cache):
    todo = [c for c in cusips if c not in cache]
    if not todo or not KEY:
        return
    headers = {"Content-Type": "application/json", "X-OPENFIGI-APIKEY": KEY}
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        body = [{"idType": "ID_CUSIP", "value": c} for c in chunk]
        try:
            r = requests.post(FIGI_URL, headers=headers, json=body, timeout=40)
            if r.status_code == 429:
                time.sleep(10)
                r = requests.post(FIGI_URL, headers=headers, json=body, timeout=40)
            r.raise_for_status()
            res = r.json()
        except requests.RequestException:
            continue
        for c, item in zip(chunk, res):
            data = item.get("data") if isinstance(item, dict) else None
            tk = None
            if data:
                us = [d for d in data if d.get("exchCode") in
                      ("US", "UN", "UW", "UQ", "UR", "UA")]
                pool = us or data
                tk = pool[0].get("ticker")
            cache[c] = tk
        _save(cache, MAP_PATH)
        time.sleep(0.3)

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
            for n in range(len(parts) - 1, 0, -1):
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
    _figi(cusips, cache)
    missing = [{"cusip": c, "issuer": uniq[c]} for c in cusips
               if cache.get(c) is None]
    if missing:
        _name_match(missing, cache)
        _save(cache, MAP_PATH)
    return {c: cache.get(c) for c in cusips}

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
