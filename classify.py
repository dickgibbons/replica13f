"""Replica-eligibility classifier.

A long-equity replica is only meaningful for relatively long-biased,
concentrated equity managers. It is misleading for:
  - multi-strategy / market-neutral / quant books (huge position counts; the
    13F is only the gross long leg of a heavily shorted, hedged portfolio)
  - option-heavy directional books (13F reports the option's UNDERLYING value,
    not premium or direction, so a put reads as a long)

This scores the latest 13F and returns an eligibility verdict so unsuitable
filers are excluded from the ranking instead of producing garbage.
"""
from __future__ import annotations
import edgar

POS_LIMIT = 500          # above this, treat as multi-strat / quant
OPT_VALUE_LIMIT = 0.20   # option value share of gross 13F above this -> exclude
CONC_FLOOR = 0.25        # top-20 long names must be >= this share of long value

def profile(cik: str):
    fs = edgar.list_13f_filings(cik)
    if not fs:
        return None
    best = {}
    for f in fs:
        p = f["period"]
        if p not in best or f["filing_date"] > best[p]["filing_date"]:
            best[p] = f
    latest = sorted(best)[-1]
    f = best[latest]
    full = edgar.parse_infotable(cik, f["accession"], f["filing_date"],
                                 exclude_options=False)
    longs = [r for r in full if not r["put_call"]]
    opts = [r for r in full if r["put_call"]]
    gross = sum(r["value_usd"] for r in full) or 1.0
    long_val = sum(r["value_usd"] for r in longs) or 1.0
    top20 = sorted((r["value_usd"] for r in longs), reverse=True)[:20]
    return {
        "cik": str(cik).zfill(10),
        "period": latest,
        "positions": len(full),
        "long_positions": len(longs),
        "option_positions": len(opts),
        "option_value_share": round(sum(r["value_usd"] for r in opts) / gross, 3),
        "top20_long_concentration": round(sum(top20) / long_val, 3),
    }

def classify(cik: str):
    p = profile(cik)
    if not p:
        return {"cik": str(cik).zfill(10), "eligible": False,
                "reason": "no 13F filings"}
    reasons = []
    if p["positions"] > POS_LIMIT:
        reasons.append(f"multi-strat/quant ({p['positions']} positions)")
    if p["option_value_share"] > OPT_VALUE_LIMIT:
        reasons.append(f"option-heavy ({p['option_value_share']:.0%} of 13F value)")
    if p["top20_long_concentration"] < CONC_FLOOR:
        reasons.append(f"diversified/index-like (top20={p['top20_long_concentration']:.0%} of long book)")
    p["eligible"] = not reasons
    p["reason"] = "long-biased concentrated equity" if not reasons else "; ".join(reasons)
    return p

if __name__ == "__main__":
    import json
    for name, cik in [("Pershing Square", "0001336528"),
                      ("Citadel Advisors", "0001423053"),
                      ("Scion Asset Mgmt", "0001649339")]:
        c = classify(cik)
        print(f"{name:18s} eligible={str(c['eligible']):5s}  {c['reason']}")
        print(f"   {json.dumps({k: c[k] for k in ('positions','option_value_share','top20_long_concentration')})}")
