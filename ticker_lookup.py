"""Single-ticker lookup: insider trades, 13D/G holders, followed-fund positions.

One EDGAR submissions fetch per company gives both the Form 4 stream
(insider buys/sells) and the 13D/G filings on the company; filer names
come from the shared filing_parties cache. Fund positions are matched
against the universe's loaded 13F holdings snapshot by ticker.
"""
from __future__ import annotations
from typing import Callable

import edgar
import form4

ProgressFn = Callable[[str], None]

MAX_FORM4 = 40
MAX_13DG = 25


def resolve(ticker: str) -> dict | None:
    """Ticker -> {cik, name} via the SEC's company-ticker mapping."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    for row in edgar._load_company_tickers().values():
        if (row.get("ticker") or "").upper() == t:
            return {
                "cik": str(row.get("cik_str", row.get("cik", ""))).zfill(10),
                "name": row.get("title", ""),
                "ticker": t,
            }
    return None


def lookup(ticker: str, on_progress: ProgressFn | None = None) -> dict | None:
    """Everything EDGAR knows worth showing for one ticker."""
    def _prog(msg: str):
        if on_progress:
            on_progress(msg)

    co = resolve(ticker)
    if not co:
        return None
    cik = co["cik"]

    _prog(f"fetching filings for {co['name']}…")
    r = form4._rate_limited_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if r.status_code != 200:
        return {**co, "error": f"EDGAR submissions unavailable (HTTP {r.status_code})"}
    rec = r.json()["filings"]["recent"]

    form4_filings = []
    ownership = []
    for form, fdate, acc in zip(rec["form"], rec["filingDate"],
                                rec["accessionNumber"]):
        fu = form.upper()
        if form == "4" and len(form4_filings) < MAX_FORM4:
            form4_filings.append((acc, fdate))
        elif (fu.startswith(("SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
              and len(ownership) < MAX_13DG):
            ownership.append((acc, fdate, fu))

    trades = []
    for i, (acc, fdate) in enumerate(form4_filings):
        _prog(f"insider filings {i + 1}/{len(form4_filings)}…")
        acc_nodash = acc.replace("-", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{acc_nodash}/{acc}.txt")
        try:
            fr = form4._rate_limited_get(url)
            if fr.status_code != 200:
                continue
            for t in form4._parse_trades(fr.text):
                t["filed"] = fdate
                t["url"] = edgar.filing_index_url(cik, acc)
                trades.append(t)
        except RuntimeError:
            continue
    trades.sort(key=lambda t: t["filed"], reverse=True)

    holders = []
    for i, (acc, fdate, form) in enumerate(ownership):
        _prog(f"13D/G filings {i + 1}/{len(ownership)}…")
        try:
            parties = edgar.filing_parties(cik, acc)
        except Exception:
            parties = {"filed_by": []}
        names = [f["name"] if isinstance(f, dict) else f
                 for f in parties.get("filed_by") or []]
        label = form.replace("SCHEDULE ", "").replace("SC ", "").strip()
        holders.append({
            "filed": fdate,
            "form": label,
            "holders": ", ".join(names) or "—",
            "kind": "13D" if "13D" in label else "13G",
            "url": edgar.filing_index_url(cik, acc),
        })

    return {**co, "trades": trades, "holders": holders}


def fund_positions(ticker: str, snapshot: dict | None) -> list[dict]:
    """Universe funds holding this ticker in their latest loaded 13F."""
    if not snapshot or not snapshot.get("funds"):
        return []
    t = (ticker or "").strip().upper()
    out = []
    for cik, fund in snapshot["funds"].items():
        if fund.get("error"):
            continue
        for h in fund.get("holdings", []):
            if (h.get("ticker") or "").upper() == t:
                out.append({
                    "fund": fund["name"],
                    "period": fund.get("period", ""),
                    "value_usd": h["value_usd"],
                    "pct_of_book": h["pct_of_book"],
                    "shares": h.get("shares"),
                })
    out.sort(key=lambda x: -x["value_usd"])
    return out
