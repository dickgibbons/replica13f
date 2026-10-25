#!/usr/bin/env python3
"""Daily 13D feed puller — run from cron.

Usage:
    python scripts/pull_13d.py            # pull last 5 days (default)
    python scripts/pull_13d.py --days 30  # backfill

Loads EDGAR_UA from the repo's .env before touching EDGAR so the SEC
gets a proper user agent.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Load .env (EDGAR_UA etc.) before importing edgar, which reads env at import
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import feed13d  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="days back to pull")
    args = ap.parse_args()

    feed = feed13d.update_feed(
        days_back=args.days,
        on_progress=lambda day, msg: print(f"[{day}] {msg}", flush=True),
    )
    print(f"feed updated: {feed['updated']} · "
          f"{feed['new_count']} new filings · {len(feed['rows'])} total kept")


if __name__ == "__main__":
    main()
