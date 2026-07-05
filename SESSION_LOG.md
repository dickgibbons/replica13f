# SESSION_LOG

## 2026-07-05 — Deployed to VPS + namespace parsing fix

- Deployed Top funds leaderboard to VPS (git pull + service restart)
- Bug: CAS Investment Partners showed empty holdings. Cause: its filer
  writes infotable XML with ns1: namespace prefixes; the parser only
  stripped a default xmlns declaration. Fix in edgar.parse_infotable:
  strip namespaces from all element tags after parsing (any dialect works)
- Cleared stale holdings caches (Mac + VPS) that held the empty results
- Verified on VPS: CAS parses 5 holdings ($1.75B, Carvana-led)

## 2026-05-16 — Streamlit UI

- Added `runner.py`, `universe.py`, `data/universe.json`, `app.py`, `requirements.txt`
- Refactored `run.py` to use shared runner; CSV writes to `outputs/replica_ranking.csv`
- Added `edgar.search_entities()` for SEC company name → CIK lookup
- Streamlit tabs: Universe (CRUD + search), Results (ranking + CSV export), Fund detail

## 2026-07-05 — Single-year standouts added (40 funds total)

- Added 11 funds: Keywise (HK), Castle Hook, Melqart, Discovery Capital,
  AQR, Dymon Asia (Singapore), ExodusPoint, Boothbay, Balyasny, Walleye,
  Schonfeld — all CIKs verified as active 13F-HR filers (May 2026 filings).
  Keywise files under its HK entity (0001474069), not the stale 0001473434
- Excluded LMR Partners: no current 13F filer exists (master fund stopped
  2014; DIFC entity has zero 13F-HRs)
- Updated 1yr figures: Whale Rock 53, Atreides 46, Jericho 49 (gross),
  Tiger Global 41, Bridgewater Pure Alpha II 34, D.E. Shaw (Oculus) 28.2
- Boothbay/Balyasny/Walleye/Schonfeld set to 16.0 as a "mid-to-high teens"
  placeholder — refine in data/top_funds.json when exact figures known
- Sort: multi-year track records first (3yr else 5yr), then 1yr-only funds

## 2026-07-05 — Q1 2026 top-10 three-year performers added

- Added 3yr Ann % column to the Top funds table and `data/top_funds.json`
- Added 9 new funds from the Q1 2026 3yr performance ranking (CAS Investment
  Partners, Redwood Capital, Peconic Partners, Slate Path, Octahedron, Ratan,
  Atreides, RV Capital AG, Jericho); Whale Rock (already listed) got its
  3yr figure. All CIKs verified against EDGAR as active 13F-HR filers —
  browse-edgar matched wrong/stale entities for Redwood and Peconic, so used
  EDGAR full-text search (efts.sec.gov) to find the real filers
- Leaderboard now sorts by best available long-horizon return (3yr, else 5yr)
- Verified in browser: selecting a new fund adds it to the universe

## 2026-07-05 — Top funds leaderboard tab

- Added `leaderboard.py` + `data/top_funds.json`: ~20 top hedge funds with
  approximate 1/5/10yr annualized net returns (curated from public reporting —
  returns are NOT in 13Fs; edit the JSON to update)
- New **Top funds** tab shown first; selecting rows (checkboxes) auto-adds
  funds to the universe, with an "In universe" ✓ column
- Bumped streamlit requirement to >=1.35 (row-selection API)
- Verified in browser: selection adds fund to `data/universe.json` immediately

## 2026-05-16 — Holdings and Moves tabs

- Added `holdings.py`: `latest_snapshot`, `aggregate_holdings`, `quarter_changes`
- Streamlit **Holdings** tab: per-fund top N + aggregate (all vs eligible toggle)
- Streamlit **Moves** tab: increases, decreases, new, exited (QoQ Δ USD)
