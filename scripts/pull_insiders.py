#!/usr/bin/env python3
"""Daily insider-purchase feed puller (Form 4, code P) — run from cron.

Usage:
    python scripts/pull_insiders.py            # last 3 days (default)
    python scripts/pull_insiders.py --days 10  # backfill

Loads EDGAR_UA from the repo's .env before touching EDGAR.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import form4  # noqa: E402
import insider_score  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="days back to pull")
    args = ap.parse_args()

    feed = form4.update_feed(
        days_back=args.days,
        on_progress=lambda day, msg: print(f"[{day}] {msg}", flush=True),
    )
    print(f"insider feed updated: {feed['updated']} · "
          f"{feed['new_count']} new purchases · {len(feed['rows'])} total kept")

    scored = insider_score.enrich_feed(
        feed,
        on_progress=lambda tag, msg: print(f"[{tag}] {msg}", flush=True),
    )
    print(f"scored {scored} new purchases")


if __name__ == "__main__":
    main()
