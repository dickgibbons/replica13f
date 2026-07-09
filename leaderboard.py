"""Curated top-hedge-fund leaderboard.

IMPORTANT: hedge funds do not publish audited public returns, and 13F filings
contain holdings only — never performance. The numbers here are APPROXIMATE
annualized net returns compiled from public reporting (investor letters,
LCH Investments rankings, press coverage). 1/5/10yr figures run through
year-end 2025; 3yr figures are the Q1 2026 three-year performance ranking.

Treat them as directional. To update a number, add a fund, or remove one,
edit data/top_funds.json (created from SEED on first load) — the app reads
that file, not this SEED list, once the file exists.

A return of null/None means "not publicly reported" and shows as a dash.
All CIKs verified against SEC EDGAR as active 13F-HR filers.
"""
from __future__ import annotations
import json
import os

ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.join(ROOT, "data")
LEADERBOARD_PATH = os.path.join(DATA_DIR, "top_funds.json")

AS_OF = "3yr: Q1 2026 · 1yr: latest reported year (2024 or 2025) · 5/10yr: 2025-12-31 — all approximate"

# name, cik (10-digit, zero-padded), ret_*yr = annualized net %, approximate
SEED = [
    # Q1 2026 top-10 by three-year performance
    {"name": "CAS Investment Partners", "cik": "0001697591", "ret_1yr": None, "ret_3yr": 74.7, "ret_5yr": None, "ret_10yr": None},
    {"name": "Redwood Capital Management", "cik": "0001316622", "ret_1yr": None, "ret_3yr": 58.5, "ret_5yr": None, "ret_10yr": None},
    {"name": "Peconic Partners", "cik": "0001050464", "ret_1yr": None, "ret_3yr": 46.9, "ret_5yr": None, "ret_10yr": None},
    {"name": "Whale Rock Capital Mgmt", "cik": "0001387322", "ret_1yr": 53.0, "ret_3yr": 44.0, "ret_5yr": 4.0, "ret_10yr": 12.0},
    {"name": "Slate Path Capital", "cik": "0001559706", "ret_1yr": None, "ret_3yr": 42.3, "ret_5yr": None, "ret_10yr": None},
    {"name": "Octahedron Capital Management", "cik": "0001891904", "ret_1yr": None, "ret_3yr": 42.1, "ret_5yr": None, "ret_10yr": None},
    {"name": "Ratan Capital Management", "cik": "0001566887", "ret_1yr": None, "ret_3yr": 41.5, "ret_5yr": None, "ret_10yr": None},
    {"name": "Atreides Management", "cik": "0001777813", "ret_1yr": 46.0, "ret_3yr": 40.0, "ret_5yr": None, "ret_10yr": None},
    {"name": "RV Capital AG", "cik": "0001766596", "ret_1yr": None, "ret_3yr": 39.6, "ret_5yr": None, "ret_10yr": None},
    {"name": "Jericho Capital Asset Management", "cik": "0001525234", "ret_1yr": 49.0, "ret_3yr": 39.3, "ret_5yr": None, "ret_10yr": None},
    # Single-year standouts (2024 or 2025 reported returns)
    {"name": "Keywise Capital Management (HK)", "cik": "0001474069", "ret_1yr": 79.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Castle Hook Partners", "cik": "0001687241", "ret_1yr": 59.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Melqart Asset Management", "cik": "0001712901", "ret_1yr": 45.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Discovery Capital Management", "cik": "0001389507", "ret_1yr": 35.6, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "AQR Capital Mgmt (Adaptive/Apex)", "cik": "0001167557", "ret_1yr": 24.4, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Dymon Asia Capital (Singapore)", "cik": "0001672142", "ret_1yr": 18.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "ExodusPoint Capital Management", "cik": "0001736225", "ret_1yr": 18.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Boothbay Fund Management", "cik": "0001549230", "ret_1yr": 16.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Balyasny Asset Management", "cik": "0001218710", "ret_1yr": 16.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Walleye Capital", "cik": "0001758720", "ret_1yr": 16.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    {"name": "Schonfeld Strategic Advisors", "cik": "0001665241", "ret_1yr": 16.0, "ret_3yr": None, "ret_5yr": None, "ret_10yr": None},
    # Established large funds (1/5/10yr through 2025)
    {"name": "Citadel Advisors (Wellington)", "cik": "0001423053", "ret_1yr": 15.1, "ret_3yr": None, "ret_5yr": 19.5, "ret_10yr": 19.0},
    {"name": "Pershing Square Capital Mgmt", "cik": "0001336528", "ret_1yr": 10.2, "ret_3yr": None, "ret_5yr": 19.8, "ret_10yr": 11.5},
    {"name": "Duquesne Family Office", "cik": "0001536411", "ret_1yr": 25.0, "ret_3yr": None, "ret_5yr": 18.0, "ret_10yr": None},
    {"name": "Appaloosa LP", "cik": "0001656456", "ret_1yr": 18.0, "ret_3yr": None, "ret_5yr": 17.0, "ret_10yr": 13.0},
    {"name": "D. E. Shaw & Co (Oculus)", "cik": "0001009207", "ret_1yr": 28.2, "ret_3yr": None, "ret_5yr": 15.5, "ret_10yr": 12.5},
    {"name": "TCI Fund Management", "cik": "0001647251", "ret_1yr": 15.0, "ret_3yr": None, "ret_5yr": 14.5, "ret_10yr": 15.5},
    {"name": "Greenlight Capital", "cik": "0001079114", "ret_1yr": 10.5, "ret_3yr": None, "ret_5yr": 14.5, "ret_10yr": 6.5},
    {"name": "Millennium Management", "cik": "0001273087", "ret_1yr": 15.1, "ret_3yr": None, "ret_5yr": 13.5, "ret_10yr": 12.5},
    {"name": "Point72 Asset Management", "cik": "0001603466", "ret_1yr": 19.0, "ret_3yr": None, "ret_5yr": 13.0, "ret_10yr": 11.5},
    {"name": "Third Point LLC", "cik": "0001040273", "ret_1yr": 24.2, "ret_3yr": None, "ret_5yr": 12.0, "ret_10yr": 9.0},
    {"name": "Viking Global Investors", "cik": "0001103804", "ret_1yr": 13.9, "ret_3yr": None, "ret_5yr": 11.5, "ret_10yr": 10.5},
    {"name": "Renaissance Technologies (RIEF)", "cik": "0001037389", "ret_1yr": 22.7, "ret_3yr": None, "ret_5yr": 11.0, "ret_10yr": 10.0},
    {"name": "Elliott Investment Management", "cik": "0001791786", "ret_1yr": 10.5, "ret_3yr": None, "ret_5yr": 11.0, "ret_10yr": 10.0},
    {"name": "Two Sigma Investments", "cik": "0001179392", "ret_1yr": 12.0, "ret_3yr": None, "ret_5yr": 9.5, "ret_10yr": 10.0},
    {"name": "Lone Pine Capital", "cik": "0001061165", "ret_1yr": 30.0, "ret_3yr": None, "ret_5yr": 7.5, "ret_10yr": 9.0},
    {"name": "Baupost Group", "cik": "0001061768", "ret_1yr": 10.0, "ret_3yr": None, "ret_5yr": 7.0, "ret_10yr": 6.0},
    {"name": "Bridgewater (Pure Alpha II)", "cik": "0001350694", "ret_1yr": 34.0, "ret_3yr": None, "ret_5yr": 6.5, "ret_10yr": 4.5},
    {"name": "Coatue Management", "cik": "0001135730", "ret_1yr": 20.0, "ret_3yr": None, "ret_5yr": 6.0, "ret_10yr": 10.0},
    {"name": "Tiger Global Management", "cik": "0001167483", "ret_1yr": 41.0, "ret_3yr": None, "ret_5yr": 3.0, "ret_10yr": 8.0},
]


def _sort_key(r: dict) -> tuple:
    """Multi-year track records rank first (by 3yr, else 5yr); funds with
    only a single-year figure follow, sorted by that 1yr number."""
    for key in ("ret_3yr", "ret_5yr"):
        if r.get(key) is not None:
            return (0, -r[key])
    if r.get("ret_1yr") is not None:
        return (1, -r["ret_1yr"])
    return (2, 0)


def load() -> list[dict]:
    """Return the leaderboard, best performers first.

    Creates data/top_funds.json from SEED on first use so it can be edited.
    """
    if not os.path.exists(LEADERBOARD_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LEADERBOARD_PATH, "w") as f:
            json.dump(SEED, f, indent=2)
            f.write("\n")
    with open(LEADERBOARD_PATH) as f:
        rows = json.load(f)
    rows.sort(key=_sort_key)
    return rows
