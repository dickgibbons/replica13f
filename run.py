"""Run the replica engine across a set of filers and produce a ranking.

Usage:
  python3 run.py                # funds in data/universe.json
  python3 run.py --top 10 --weight value --years 5
  python3 run.py --ciks 0001336528,0001061165

Set OPENFIGI_KEY for exact CUSIP mapping (recommended for any universe beyond
the seed sanity check).
"""
from __future__ import annotations
import argparse
import csv
import os

import runner
import universe

ROOT = os.path.dirname(__file__)
OUT_DIR = os.path.join(ROOT, "outputs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--weight", choices=["equal", "value"], default="equal")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--ciks", type=str, default="")
    args = ap.parse_args()

    if args.ciks:
        funds = [{"name": c.strip(), "cik": c.strip()} for c in args.ciks.split(",")]
    else:
        funds = universe.load()

    rows = runner.run_ranking(
        funds,
        top_n=args.top,
        weighting=args.weight,
        years=args.years,
    )

    key = f"ann_{args.years}yr_pct"
    for r in rows:
        name = r["name"]
        if r.get("eligible"):
            print(
                f"  {name:32s} {args.years}yr={r[key]:6.1f}%  "
                f"full={r['ann_full_pct']:6.1f}% ({r['full_years']}y)  "
                f"cov={r['avg_coverage']:.0%}  "
                f"ref(WW5yr)={r.get('ww_ref_5yr', 'n/a')}"
            )
        else:
            print(f"  {name:32s} {r.get('status', 'skipped')}")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "replica_ranking.csv")
    cols = [
        "rank", "name", "cik", "status", key, "ann_full_pct", "full_years",
        "windows", "trailing_windows", "avg_coverage",
        "first_filing", "last_filing", "ww_ref_5yr",
    ]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {out}")
    return rows


if __name__ == "__main__":
    main()
