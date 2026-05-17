# SESSION_LOG

## 2026-05-16 — Streamlit UI

- Added `runner.py`, `universe.py`, `data/universe.json`, `app.py`, `requirements.txt`
- Refactored `run.py` to use shared runner; CSV writes to `outputs/replica_ranking.csv`
- Added `edgar.search_entities()` for SEC company name → CIK lookup
- Streamlit tabs: Universe (CRUD + search), Results (ranking + CSV export), Fund detail

## 2026-05-16 — Holdings and Moves tabs

- Added `holdings.py`: `latest_snapshot`, `aggregate_holdings`, `quarter_changes`
- Streamlit **Holdings** tab: per-fund top N + aggregate (all vs eligible toggle)
- Streamlit **Moves** tab: increases, decreases, new, exited (QoQ Δ USD)
