"""Shared ranking orchestration for CLI and Streamlit UI."""
from __future__ import annotations
import datetime as dt
from typing import Callable

import classify
import replica
from prices import PriceBook

ProgressFn = Callable[[str, str], None]  # (name, message)


def run_ranking(
    universe: list[dict],
    *,
    top_n: int = 20,
    weighting: str = "equal",
    years: int = 5,
    entry: str = "filing",
    pricebook: PriceBook | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict]:
    """Run replica returns for a fund universe.

    Each fund dict: {name, cik, ww_ref_5yr?}.
    Returns rows sorted by trailing annualized return (rank attached).
    """
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    pb = pricebook or PriceBook(
        dt.date(2010, 1, 1), dt.date.today() + dt.timedelta(days=2)
    )
    rows: list[dict] = []
    key = f"ann_{years}yr_pct"

    for fund in universe:
        name = fund.get("name") or fund["cik"]
        cik = str(fund["cik"]).zfill(10)
        ref = fund.get("ww_ref_5yr")

        _prog(name, "classifying…")
        verdict = classify.classify(cik)
        if not verdict.get("eligible"):
            rows.append({
                "name": name,
                "cik": cik,
                "eligible": False,
                "status": f"ineligible: {verdict['reason']}",
                key: float("nan"),
                "ann_full_pct": "",
                "full_years": "",
                "windows": "",
                "trailing_windows": "",
                "avg_coverage": "",
                "first_filing": "",
                "last_filing": "",
                "ww_ref_5yr": ref,
            })
            _prog(name, "skipped")
            continue

        try:
            _prog(name, "computing replica…")
            r = replica.fund_replica(
                cik,
                top_n=top_n,
                weighting=weighting,
                trailing_years=years,
                pricebook=pb,
                entry=entry,
            )
        except Exception as e:
            rows.append({
                "name": name,
                "cik": cik,
                "eligible": False,
                "status": f"error: {e}",
                key: float("nan"),
                "ww_ref_5yr": ref,
            })
            _prog(name, "error")
            continue

        if not r:
            rows.append({
                "name": name,
                "cik": cik,
                "eligible": False,
                "status": "insufficient data",
                key: float("nan"),
                "ww_ref_5yr": ref,
            })
            _prog(name, "insufficient data")
            continue

        r["name"] = name
        r["eligible"] = True
        r["status"] = "ok"
        r["ww_ref_5yr"] = ref
        rows.append(r)
        _prog(name, "done")

    rows.sort(
        key=lambda x: (x[key] if x[key] == x[key] else -1e9),
        reverse=True,
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
