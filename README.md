# replica13f — 13F replica-return engine

Computes a reproducible long-equity replica return per 13F filer, so a
"top performers" ranking is something you own and can defend, rather than a
number lifted from a vendor's opaque leaderboard.

## Layers
- `edgar.py` — lists 13F-HR/13F-HR/A filings per CIK, parses the information
  table, normalizes the value-unit change (thousands pre-2023-01-03, whole
  dollars after), and collapses amendments to the latest filing per period.
- `cusip_map.py` — CUSIP→ticker. OpenFIGI (exact, needs `OPENFIGI_KEY`) with a
  SEC name-match keyless fallback. Persistent disk cache.
- `prices.py` — adjusted daily closes (Yahoo chart endpoint), disk-cached per
  symbol, with as-of lookup so a portfolio is priced at the last trading day
  on/before an anchor date.
- `replica.py` — portfolio construction, quarterly rebalance, geometric
  chaining, trailing/annualized returns.
- `runner.py` / `run.py` — orchestrate across a CIK universe and write
  `outputs/replica_ranking.csv`.
- `app.py` — Streamlit UI to manage funds, run rankings, and inspect results.
- `holdings.py` — latest holdings snapshots, cross-fund aggregate, quarter moves.
- `universe.py` / `data/universe.json` — persistent fund list (name + CIK).

## Methodology (all configurable in `fund_replica`)
- `top_n` (default 20): top holdings by reported USD value.
- `weighting`: `equal` (default) or `value` (manager-weighted).
- `entry`: `filing` (default — enters on the filing date, the realistic
  public-information lag) or `period_end` (the optimistic, unrealizable basis
  most vendor headline numbers use).
- Rebalance at every filing; window returns chained geometrically.
- Names unpriceable at either window endpoint are dropped and surviving
  weights renormalized; `avg_coverage` reports the priced weight share so a
  low-quality window is visible rather than silently distorting the result.

## What the number is and is not
It is a proxy for the disclosed long-equity book only. It excludes shorts,
options, non-13F assets, leverage, and intra-quarter trades. The keyless
name-match path collapses share classes and misses post-filing renames/M&A,
which adds mild survivorship bias — run with an OpenFIGI key for any universe
beyond a sanity check. It is a reproducible internal ranking signal, not
audited fund performance.

## Validation finding (seed run, equal-weight top 20, 5yr, ending 2026-05-15)
Engine numbers are internally consistent (price coverage ~99–100%) but differ
from WhaleWisdom's published 5yr figures by 7–17 points, and reorder the
ranking. The realistic filing-entry basis is not uniformly lower than the
period-end basis — the divergence is driven mostly by methodology and by the
extreme endpoint-sensitivity of any 5-year annualized figure, not by a single
correctable factor. This is the point of the build: a vendor's headline is not
reproducible without its exact construction, so the performance filter has to
be computed on a methodology you control. Millstreet is correctly rejected —
its 13F has no meaningful equity book (credit fund).

## Eligibility gate (`classify.py`)
A long-equity replica is only meaningful for long-biased, concentrated equity
managers. `run.py` scores each filer's latest 13F and excludes it if any hold:
position count > 500 (multi-strat/quant), option value share > 20% (13F reports
the option underlying's value, not premium or direction — a put reads as a
long), or top-20 long names < 25% of the long book (index-like). Verified
cases:
- Pershing Square — eligible (11 positions, 0% options, top-20 = 100%).
- Citadel Advisors (CIK 0001423053) — rejected on all three: 15,543 positions,
  78% option value, top-20 longs 15% of the book. The 13F is the gross long
  leg of a hedged multi-strategy book; a replica tracks beta, not Citadel.
- Scion / Burry (CIK 0001649339) — rejected: 95% of 13F value is option
  notional. A naive replica would invert and mis-scale his PLTR/NVDA put bets.

Options now carry a `put_call` tag and are excluded from the replica by
default (`exclude_options=True`); set it False only to inspect them.


```bash
pip install -r requirements.txt
export OPENFIGI_KEY=...        # free; required for exact CUSIP mapping at scale
export EDGAR_UA="YourName you@domain.com"

# CLI (uses data/universe.json)
python3 run.py
python3 run.py --top 10 --weight value
python3 run.py --ciks 0001336528,0001061165

# Streamlit UI
streamlit run app.py
```

Caches live in `cache/` and make reruns fast and resumable. Add or edit funds in
`data/universe.json` or via the Streamlit **Universe** tab.

### Streamlit tabs

| Tab | Purpose |
|-----|---------|
| Universe | Add/edit funds (SEC search or manual CIK) |
| Results | Replica return ranking |
| Holdings | Top holdings per fund + aggregate across universe (eligible-only toggle) |
| Moves | Quarter-over-quarter increases, decreases, new, and exited positions |
| Fund detail | Eligibility profile and quick single-fund preview |
