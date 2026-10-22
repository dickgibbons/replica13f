# Top-funds leaderboard on main screen

- [x] `leaderboard.py` + seed `data/top_funds.json` (~20 funds, 1/5/10yr approx returns, CIKs)
- [x] "Top funds" tab first in app.py; selectable rows auto-add to universe
- [x] Bump streamlit requirement to >=1.35 (row selection needs it)
- [x] Verify: launched app, selected Citadel + Duquesne, both landed in
      data/universe.json with 5yr return carried over as ww_ref_5yr;
      restored original universe after testing
- [x] Update SESSION_LOG.md

## Review

- Returns are NOT available from 13Fs/SEC. The leaderboard is a curated,
  editable JSON (`data/top_funds.json`) compiled from public reporting and
  clearly labeled approximate in the UI. Blank/None = not publicly reported.
- Selection is add-only: unchecking a row does not remove the fund
  (removal stays in the Universe tab, so an accidental uncheck can't
  silently drop a fund from consideration).
