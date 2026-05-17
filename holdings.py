"""Holdings snapshots and quarter-over-quarter position changes."""
from __future__ import annotations
from typing import Callable

import classify
import cusip_map
import edgar

ProgressFn = Callable[[str, str], None]


def _enrich_holdings(raw: list[dict], tmap: dict[str, str | None]) -> list[dict]:
    book = sum(h["value_usd"] for h in raw) or 1.0
    out = []
    for h in raw:
        cusip = h["cusip"].upper()
        out.append({
            "issuer": h.get("issuer", ""),
            "cusip": cusip,
            "ticker": tmap.get(cusip) or "—",
            "value_usd": h["value_usd"],
            "shares": h.get("shares"),
            "pct_of_book": round(h["value_usd"] / book, 4),
        })
    out.sort(key=lambda x: -x["value_usd"])
    return out


def latest_snapshot(
    funds: list[dict],
    top_n: int = 20,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Latest 13F holdings per fund with tickers resolved."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    snapshot = {"meta": {"top_n": top_n}, "funds": {}}
    for fund in funds:
        name = fund.get("name") or fund["cik"]
        cik = str(fund["cik"]).zfill(10)
        _prog(name, "fetching holdings…")
        try:
            hp = edgar.holdings_by_period(cik, max_periods=1)
        except Exception as e:
            snapshot["funds"][cik] = {
                "name": name,
                "error": str(e),
                "holdings": [],
                "top_holdings": [],
            }
            _prog(name, "error")
            continue
        if not hp:
            snapshot["funds"][cik] = {
                "name": name,
                "error": "no filings",
                "holdings": [],
                "top_holdings": [],
            }
            _prog(name, "no data")
            continue
        period = sorted(hp)[-1]
        raw = hp[period]["holdings"]
        tmap = cusip_map.resolve_holdings(
            [{"cusip": h["cusip"], "issuer": h.get("issuer", "")} for h in raw]
        )
        enriched = _enrich_holdings(raw, tmap)
        snapshot["funds"][cik] = {
            "name": name,
            "period": period,
            "filed": hp[period]["filing_date"],
            "holdings": enriched,
            "top_holdings": enriched[:top_n],
        }
        _prog(name, "done")
    return snapshot


def _include_fund(cik: str, fund_filter: str) -> bool:
    if fund_filter == "all":
        return True
    return classify.classify(cik).get("eligible", False)


def aggregate_holdings(snapshot: dict, fund_filter: str = "all") -> list[dict]:
    """Sum latest holdings by CUSIP across funds. fund_filter: 'all' | 'eligible'."""
    by_cusip: dict[str, dict] = {}
    for cik, fund in snapshot.get("funds", {}).items():
        if fund.get("error") or not fund.get("holdings"):
            continue
        if not _include_fund(cik, fund_filter):
            continue
        fname = fund["name"]
        for h in fund["holdings"]:
            cusip = h["cusip"]
            if cusip not in by_cusip:
                by_cusip[cusip] = {
                    "cusip": cusip,
                    "issuer": h["issuer"],
                    "ticker": h["ticker"],
                    "total_value_usd": 0.0,
                    "fund_names": [],
                }
            row = by_cusip[cusip]
            row["total_value_usd"] += h["value_usd"]
            if fname not in row["fund_names"]:
                row["fund_names"].append(fname)
            if h["ticker"] != "—":
                row["ticker"] = h["ticker"]

    total = sum(r["total_value_usd"] for r in by_cusip.values()) or 1.0
    out = []
    for row in by_cusip.values():
        out.append({
            "ticker": row["ticker"],
            "issuer": row["issuer"],
            "cusip": row["cusip"],
            "total_value_usd": row["total_value_usd"],
            "fund_count": len(row["fund_names"]),
            "funds": ", ".join(sorted(row["fund_names"])),
            "pct_of_aggregate": round(row["total_value_usd"] / total, 4),
        })
    out.sort(key=lambda x: -x["total_value_usd"])
    return out


def _move_row(
    cik: str,
    fname: str,
    cusip: str,
    prev: dict | None,
    curr: dict | None,
    tmap: dict[str, str | None],
    period_prev: str,
    period_curr: str,
) -> dict:
    prev_val = prev["value_usd"] if prev else 0.0
    curr_val = curr["value_usd"] if curr else 0.0
    delta = curr_val - prev_val
    if prev and curr:
        if delta > 0:
            status = "increased"
        elif delta < 0:
            status = "decreased"
        else:
            status = "unchanged"
    elif curr and not prev:
        status = "new"
    else:
        status = "exited"

    issuer = (curr or prev or {}).get("issuer", "")
    return {
        "fund": fname,
        "cik": cik,
        "cusip": cusip,
        "ticker": tmap.get(cusip) or "—",
        "issuer": issuer,
        "prev_value_usd": prev_val,
        "curr_value_usd": curr_val,
        "delta_usd": delta,
        "delta_pct": round(delta / prev_val, 4) if prev_val > 0 else None,
        "prev_shares": prev.get("shares") if prev else None,
        "curr_shares": curr.get("shares") if curr else None,
        "status": status,
        "period_prev": period_prev,
        "period_curr": period_curr,
    }


def quarter_changes(
    funds: list[dict],
    on_progress: ProgressFn | None = None,
) -> dict:
    """Quarter-over-quarter value changes for each fund."""
    def _prog(name: str, msg: str):
        if on_progress:
            on_progress(name, msg)

    result = {"funds": {}, "all_moves": []}
    for fund in funds:
        name = fund.get("name") or fund["cik"]
        cik = str(fund["cik"]).zfill(10)
        _prog(name, "fetching periods…")
        try:
            hp = edgar.holdings_by_period(cik, max_periods=2)
        except Exception as e:
            result["funds"][cik] = {"name": name, "error": str(e), "moves": []}
            _prog(name, "error")
            continue
        periods = sorted(hp)
        if len(periods) < 2:
            result["funds"][cik] = {
                "name": name,
                "error": "need at least 2 periods",
                "moves": [],
            }
            _prog(name, "insufficient periods")
            continue

        period_prev, period_curr = periods[-2], periods[-1]
        prev_hs = {h["cusip"].upper(): h for h in hp[period_prev]["holdings"]}
        curr_hs = {h["cusip"].upper(): h for h in hp[period_curr]["holdings"]}
        all_cusips = set(prev_hs) | set(curr_hs)
        tmap = cusip_map.resolve_holdings([
            {"cusip": c, "issuer": (curr_hs.get(c) or prev_hs.get(c) or {}).get("issuer", "")}
            for c in all_cusips
        ])
        moves = []
        for cusip in all_cusips:
            row = _move_row(
                cik, name, cusip,
                prev_hs.get(cusip), curr_hs.get(cusip),
                tmap, period_prev, period_curr,
            )
            moves.append(row)
        result["funds"][cik] = {
            "name": name,
            "period_prev": period_prev,
            "period_curr": period_curr,
            "filed_prev": hp[period_prev]["filing_date"],
            "filed_curr": hp[period_curr]["filing_date"],
            "moves": moves,
        }
        result["all_moves"].extend(moves)
        _prog(name, "done")
    return result


def filter_moves(
    moves: list[dict],
    *,
    fund: str | None = None,
    fund_filter: str = "all",
) -> list[dict]:
    """Filter flattened move list by fund name and eligibility."""
    out = moves
    if fund and fund != "All funds":
        out = [m for m in out if m["fund"] == fund]
    if fund_filter == "eligible":
        out = [
            m for m in out
            if classify.classify(m["cik"]).get("eligible", False)
        ]
    return out


def top_increases(moves: list[dict], limit: int = 25) -> list[dict]:
    xs = [m for m in moves if m["status"] not in ("exited", "unchanged") and m["delta_usd"] > 0]
    xs.sort(key=lambda x: -x["delta_usd"])
    return xs[:limit]


def top_decreases(moves: list[dict], limit: int = 25) -> list[dict]:
    xs = [m for m in moves if m["status"] not in ("new", "unchanged") and m["delta_usd"] < 0]
    xs.sort(key=lambda x: x["delta_usd"])
    return xs[:limit]


def new_positions(moves: list[dict], limit: int = 25) -> list[dict]:
    xs = [m for m in moves if m["status"] == "new"]
    xs.sort(key=lambda x: -x["curr_value_usd"])
    return xs[:limit]


def exited_positions(moves: list[dict], limit: int = 25) -> list[dict]:
    xs = [m for m in moves if m["status"] == "exited"]
    xs.sort(key=lambda x: -x["prev_value_usd"])
    return xs[:limit]
