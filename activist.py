"""13D/13G ownership filings for the fund universe.

A 13D is filed within days of a fund taking an activist stake above 5% of a
company — much fresher than the quarterly 13F. 13G is the passive variant.
"""
from __future__ import annotations
from typing import Callable

import edgar

ProgressFn = Callable[[str, str], None]


def _form_label(form: str) -> str:
    """'SCHEDULE 13D/A' / 'SC 13D/A' -> '13D/A'."""
    return form.upper().replace("SCHEDULE ", "").replace("SC ", "").strip()


def snapshot(
    funds: list[dict],
    include_13g: bool = False,
    per_fund_limit: int = 10,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Latest 13D (and optionally 13G) filings per fund, with target names."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    kinds = ("13D", "13G") if include_13g else ("13D",)
    out = {
        "meta": {"include_13g": include_13g, "per_fund_limit": per_fund_limit},
        "funds": {},
        "rows": [],
    }
    for fund in funds:
        name = fund.get("name") or fund["cik"]
        cik = str(fund["cik"]).zfill(10)
        _prog(name, "listing filings…")
        try:
            filings = edgar.list_ownership_filings(cik, kinds=kinds)
        except Exception as e:
            out["funds"][cik] = {"name": name, "error": str(e)}
            _prog(name, "error")
            continue
        filings = filings[:per_fund_limit]
        rows = []
        for f in filings:
            try:
                subject = edgar.filing_subject(cik, f["accession"])
            except Exception:
                subject = None
            rows.append({
                "fund": name,
                "cik": cik,
                "form": _form_label(f["form"]),
                "kind": f["kind"],
                "subject": subject or "—",
                "filed": f["filing_date"],
                "url": edgar.filing_index_url(cik, f["accession"]),
            })
        out["funds"][cik] = {"name": name, "count": len(rows)}
        out["rows"].extend(rows)
        _prog(name, f"done ({len(rows)} filings)")
    out["rows"].sort(key=lambda r: r["filed"], reverse=True)
    return out
