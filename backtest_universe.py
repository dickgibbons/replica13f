"""Curated backtest universe: funds with published 5yr/10yr track records.

Built from the leaderboard (data/top_funds.json) and written to
data/universe_published.json, which is the file to edit when adding,
removing, or correcting a fund. Selecting funds by their published record
is a hindsight choice, so the study also runs a point-in-time selection
as a robustness check.
"""
from __future__ import annotations
import json
import os

import leaderboard
import universe

ROOT = os.path.dirname(__file__)
PUBLISHED_PATH = os.path.join(ROOT, "data", "universe_published.json")

DEFAULT_SOURCE = (
    "Approximate annualized net returns compiled from public reporting "
    "(investor letters, LCH Investments rankings, press coverage)"
)
DEFAULT_AS_OF = "2025-12-31"
MAX_FUNDS = 25


def _rank_key(f: dict) -> tuple:
    ten = f.get("published_10y")
    five = f.get("published_5y")
    return (
        -(ten if ten is not None else -1e9),
        -(five if five is not None else -1e9),
    )


def build_from_leaderboard(limit: int = MAX_FUNDS) -> list[dict]:
    rows = []
    for r in leaderboard.load():
        if r.get("ret_5yr") is None and r.get("ret_10yr") is None:
            continue
        rows.append(universe.validate({
            "name": r["name"],
            "cik": r["cik"],
            "published_5y": r.get("ret_5yr"),
            "published_10y": r.get("ret_10yr"),
            "source": r.get("source") or DEFAULT_SOURCE,
            "as_of": r.get("as_of") or DEFAULT_AS_OF,
        }))
    rows.sort(key=_rank_key)
    return rows[:limit]


def save(funds: list[dict]) -> None:
    os.makedirs(os.path.dirname(PUBLISHED_PATH), exist_ok=True)
    with open(PUBLISHED_PATH, "w") as f:
        json.dump([universe.validate(x) for x in funds], f, indent=2)
        f.write("\n")


def load() -> list[dict]:
    """Curated funds, best published 10yr (then 5yr) first."""
    if not os.path.exists(PUBLISHED_PATH):
        save(build_from_leaderboard())
    with open(PUBLISHED_PATH) as f:
        funds = json.load(f)
    funds.sort(key=_rank_key)
    return funds


if __name__ == "__main__":
    for f in load():
        print(f"{f['name'][:38]:38s} {f['cik']}  "
              f"5y={f.get('published_5y')}  10y={f.get('published_10y')}")
