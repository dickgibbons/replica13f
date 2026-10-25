# replica13f — 13F replica-return engine

Computes a reproducible long-equity replica return per 13F filer, so a
"top performers" ranking is something you own and can defend, rather than a
number lifted from a vendor's opaque leaderboard.

## Layers
- `edgar.py` — lists 13F-HR/13F-HR/A filings per CIK, parses the information
  table, normalizes the value-unit change (thousands pre-2023-01-03, whole
  dollars after), and collapses amendments to the latest filing per period.
- `cusip_map.py` — CUSIP→ticker. OpenFIGI (exact; works keyless at 25
  requests/min, `OPENFIGI_KEY` for 10x throughput) with a SEC name-match
  fallback for CUSIPs OpenFIGI doesn't know. Persistent disk caches.
- `prices.py` — adjusted daily closes and split history (Yahoo chart
  endpoint), disk-cached per symbol and refreshed when stale, with as-of
  lookup so a portfolio is priced at the last trading day on/before a date.
- `replica.py` — portfolio construction, quarterly rebalance, geometric
  chaining, trailing/annualized returns.
- `runner.py` / `run.py` — orchestrate across a CIK universe and write
  `outputs/replica_ranking.csv`.
- `app.py` — Streamlit UI to manage funds, run rankings, and inspect results.
- `holdings.py` — latest holdings snapshots, cross-fund aggregate, quarter moves.
- `universe.py` / `data/universe.json` — persistent fund list (name + CIK).
- `backtest_universe.py` / `data/universe_published.json` — funds with the
  best published 5y/10y track records, the starting pool for the backtest.
- `simulator.py` — follow-the-filing copy-trading engine (see below).
- `metrics.py` — CAGR, volatility, Sharpe, Sortino, Calmar, max drawdown with
  dates, beta/alpha and up/down capture vs SPY, turnover, win rate.
- `study.py` — runs the 5y/10y study and writes `outputs/study_<date>/`.

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

# VPS deploy (hub tile + systemd on :8502)
# See DEPLOY.md
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
| Backtest | Study results (equity vs SPY, drawdown, metrics, trades) and single-fund runs |

## Copy-trading backtest (`simulator.py`, `study.py`)

Answers: "if I had copied fund X from the day each 13F became public, what
would my account have done?"

- **Entry**: close of the first trading day after EDGAR acceptance (Eastern
  time). Nothing trades on information before it is public.
- **Sells**: when a later 13F shows fewer shares, cut the position by the same
  fraction (`proportional`, default) or sell it all (`full_exit`). A name that
  drops out of the 13F is sold.
- **Buys**: adds and new top-N names are bought only with available cash;
  when cash is short, all buys are scaled down pro-rata (no forced sells).
  `cash_policy="rebalance"` instead resets to the new weights every filing.
- **Data hygiene**: 13F/A "new holdings" amendments are merged into the
  original report, share counts are split-adjusted between report dates, and
  positions with no price for 10 trading days are liquidated at the last print.
- **Costs**: 5 bps per trade by default; idle cash earns nothing unless
  `cash_yield` is set.

`study.py` runs every fund in `data/universe_published.json` over 5- and
10-year windows ending at the latest price date, in three variants (base,
full rebalance, and quarter-end entry — the unrealizable basis that shows
what the 45-day filing delay costs). It also builds equal-weight blends (all
funds; eligible long-biased funds only) and a point-in-time blend that each
February picks the top half of eligible funds by trailing 12-month copy
return.

```bash
python3 study.py                         # ~20 funds; first run is slow (EDGAR + OpenFIGI + prices)
python3 study.py --ciks 0001336528 --top-n 10 --sell-rule full_exit
python3 -m unittest discover -s tests    # simulator + metrics tests, no network
```

Outputs: `summary.csv` (one row per fund/window/variant plus blends),
`nav_*`, `drawdown_*`, `trades_*` CSVs, and `results.json` for the UI.

### First study (`outputs/study_2026-10-25`, top 20, value weights, 5 bps)

| Portfolio | 10y CAGR | 10y max DD | 5y CAGR | 5y max DD |
|-----------|---------:|-----------:|--------:|----------:|
| SPY | 15.3% | -33.7% | 12.3% | -24.5% |
| Equal blend, all 20 copies | 15.1% | -32.5% | 11.4% | -30.5% |
| Equal blend, eligible copies | 15.3% | -36.6% | 8.4% | -36.3% |
| Point-in-time top 6 of eligible | 16.4% | -34.5% | 12.9% | -24.3% |

- Copying the whole pool is roughly SPY with beta ~1: the edge is in fund
  selection, not in copying "top funds" broadly. 9 of 20 copies beat SPY over
  10y, 10 of 20 over 5y.
- Best 10y copies: Whale Rock 24.1% (but -56% drawdown), Duquesne 22.8%
  (-30%), Elliott 21.7% (-15%; long book only, 13F starts 2020), Point72
  17.9%, RenTech 16.9%, D.E. Shaw 16.6%.
- The 2021–22 growth crash dominates 5y results: Coatue, Tiger, Lone Pine and
  Third Point copies are flat to negative with 40–65% drawdowns.
- The 45-day filing delay costs about 1 point a year (median quarter-end
  entry minus realistic entry). Full rebalancing beats proportional trims by
  about 1–1.4 points but with deeper drawdowns.
- Published fund returns and copy returns are only loosely related; several
  multi-strat funds the eligibility gate rejects copy well on their long book.

Caveats that must accompany any number shown to a client: funds were chosen
by today's published returns (hindsight; the point-in-time blend only
re-selects within that pool), prices come from Yahoo and cover only listed
tickers (survivorship; not licensed for commercial use), and 13Fs omit
shorts, cash, options direction and non-US holdings.
