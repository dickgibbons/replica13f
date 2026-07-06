# SESSION_LOG

## 2026-07-05 — 13G (short form) added to the daily feed

- FORM_RE now matches SC/SCHEDULE 13G + amendments; rows carry
  kind: 13D|13G (row_kind() derives it for pre-existing rows)
- UI: "Form family" selector (13D only default / 13G only / both) applied
  to the feed table and both aggregate tables; "Filed by your universe
  funds" always shows both families
- Feed regenerated (days_done reset required — prior days were scanned
  under the 13D-only regime); VPS backfilled 45 days
- Verified: Situational Awareness LP 13G on SharonAI (2026-06-29) captured

## 2026-07-05 — Universe funds surfaced in the daily 13D feed

- `edgar.filing_parties` now records each filer's CIK (old string-only
  cache entries auto-refetch); feed rows carry `filer_ciks`
- `feed13d.universe_rows`: feed rows filed by universe funds, matched by
  CIK (immune to name variations)
- New "Filed by your universe funds" table in the daily feed section
- Feed regenerated (schema change); verified: Millstreet/DBD, Viking/LAB,
  Pershing/HHH matched in the 30-day window

## 2026-07-05 — Market-wide daily 13D feed + cron

- `feed13d.py`: pulls EDGAR daily form indexes (form.YYYYMMDD.idx), keeps
  every SC/SCHEDULE 13D + amendments with target company, ticker (via
  company_tickers.json), filer(s) and link; persisted in data/feed13d.json
  (gitignored — VPS cron owns it), 120-day rolling window
- `edgar.filing_parties`: subject + filers + subject CIK from the filing
  index page, cached in cache/filing_parties.json; HTML entities unescaped
- `scripts/pull_13d.py --days N`: cron entry point, loads .env itself
- 13D tab now has the daily feed first (lookback selector, refresh button,
  "Most 13D'd companies" + "Most active filers" tables), universe funds
  section below
- Gotchas: daily index lists each filing once per party (group by
  accession); filer names contain commas so filers are stored as a list
- VPS cron: daily pull at 11:10 UTC (~7:10am ET)

## 2026-07-05 — 13D filings tab

- New tab: 13D ownership filings per universe fund (activist >5% stakes,
  filed within days — fresher signal than quarterly 13Fs)
- `edgar.list_ownership_filings` handles both form eras ("SC 13D" pre-2024,
  "SCHEDULE 13D" after); `edgar.filing_subject` scrapes the target company
  from the filing index page, cached in cache/filing_subjects.json
- `activist.py` builds the snapshot; UI has 13G toggle, per-fund limit,
  fund filter, EDGAR links, CSV export
- Verified in browser: 46 filings for the 8-fund universe (Viking/Standard
  BioTools 13D 2026-06-12, Pershing/Howard Hughes 13D/A, etc.)

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
