"""Streamlit UI for replica13f."""
from __future__ import annotations
import json
import os
from io import StringIO

import pandas as pd
import streamlit as st

import activist
import backtest_universe
import classify
import edgar
import feed13d
import form4
import holdings as holdings_mod
import insider_score
import leaderboard
import metrics
import runner
import simulator
import study
import ticker_lookup
import universe

ROOT = os.path.dirname(__file__)
CACHE = os.path.join(ROOT, "cache")
LAST_RUN_PATH = os.path.join(CACHE, "last_run.json")
HOLDINGS_SNAPSHOT_PATH = os.path.join(CACHE, "holdings_snapshot.json")
MOVES_SNAPSHOT_PATH = os.path.join(CACHE, "moves_snapshot.json")
ACTIVIST_SNAPSHOT_PATH = os.path.join(CACHE, "activist_snapshot.json")


def _fmt_usd(val) -> str:
    if val is None or val == "":
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _fmt_shares(val) -> str:
    if val is None or val == "":
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}"


def _env_ok() -> tuple[bool, bool]:
    ua = os.environ.get("EDGAR_UA", "")
    default_ua = "Replica13F research contact@example.com"
    edgar_ok = bool(ua) and ua != default_ua
    figi_ok = bool(os.environ.get("OPENFIGI_KEY"))
    return edgar_ok, figi_ok


def _load_json(path: str) -> dict | None:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_json(path: str, data: dict) -> None:
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_last_run() -> dict | None:
    return _load_json(LAST_RUN_PATH)


def _save_last_run(meta: dict, rows: list[dict]) -> None:
    _save_json(LAST_RUN_PATH, {"meta": meta, "rows": rows})


def _load_holdings_snapshot() -> dict | None:
    if "holdings_snapshot" in st.session_state:
        return st.session_state["holdings_snapshot"]
    snap = _load_json(HOLDINGS_SNAPSHOT_PATH)
    if snap:
        st.session_state["holdings_snapshot"] = snap
    return snap


def _save_holdings_snapshot(snap: dict) -> None:
    st.session_state["holdings_snapshot"] = snap
    _save_json(HOLDINGS_SNAPSHOT_PATH, snap)


def _load_moves_snapshot() -> dict | None:
    if "moves_snapshot" in st.session_state:
        return st.session_state["moves_snapshot"]
    snap = _load_json(MOVES_SNAPSHOT_PATH)
    if snap:
        st.session_state["moves_snapshot"] = snap
    return snap


def _save_moves_snapshot(snap: dict) -> None:
    st.session_state["moves_snapshot"] = snap
    _save_json(MOVES_SNAPSHOT_PATH, snap)


def _load_activist_snapshot() -> dict | None:
    if "activist_snapshot" in st.session_state:
        return st.session_state["activist_snapshot"]
    snap = _load_json(ACTIVIST_SNAPSHOT_PATH)
    if snap:
        st.session_state["activist_snapshot"] = snap
    return snap


def _save_activist_snapshot(snap: dict) -> None:
    st.session_state["activist_snapshot"] = snap
    _save_json(ACTIVIST_SNAPSHOT_PATH, snap)


def _activist_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Filed": r["filed"],
            "Fund": r["fund"],
            "Form": r["form"],
            "Target company": r["subject"],
            "Filing": r["url"],
        }
        for r in rows
    ])


def _feed_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Filed": r["filed"],
            "Company": r["company"],
            "Ticker": r["ticker"] or "—",
            "Form": r["form"],
            "Filed by": ", ".join(r["filers"]) or "—",
            "Filing": r["url"],
        }
        for r in rows
    ])


def _rows_to_df(rows: list[dict], years: int) -> pd.DataFrame:
    key = f"ann_{years}yr_pct"
    data = []
    for r in rows:
        data.append({
            "Rank": r.get("rank"),
            "Name": r.get("name"),
            "CIK": r.get("cik"),
            "Status": r.get("status", ""),
            f"{years}yr Ann %": r.get(key),
            "Full Ann %": r.get("ann_full_pct"),
            "Coverage": r.get("avg_coverage"),
            "Windows": r.get("windows"),
            "WW Ref 5yr": r.get("ww_ref_5yr"),
        })
    return pd.DataFrame(data)


def _fund_holdings_df(fund: dict, top_n: int) -> pd.DataFrame:
    rows = fund.get("top_holdings") or fund.get("holdings", [])[:top_n]
    return pd.DataFrame([
        {
            "Rank": i + 1,
            "Ticker": h["ticker"],
            "Issuer": h["issuer"],
            "Value": _fmt_usd(h["value_usd"]),
            "% of Book": f"{h['pct_of_book'] * 100:.1f}%",
            "Shares": _fmt_shares(h.get("shares")),
            "CUSIP": h["cusip"],
        }
        for i, h in enumerate(rows)
    ])


def _aggregate_df(rows: list[dict], top_n: int) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Ticker": r["ticker"],
            "Issuer": r["issuer"],
            "Total Value": _fmt_usd(r["total_value_usd"]),
            "% of Aggregate": f"{r['pct_of_aggregate'] * 100:.1f}%",
            "# Funds": r["fund_count"],
            "Funds": r["funds"],
            "CUSIP": r["cusip"],
        }
        for r in rows[:top_n]
    ])


def _moves_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Fund": m["fund"],
            "Ticker": m["ticker"],
            "Issuer": m["issuer"],
            "Prev Value": _fmt_usd(m["prev_value_usd"]),
            "Curr Value": _fmt_usd(m["curr_value_usd"]),
            "Δ Value": _fmt_usd(m["delta_usd"]),
            "Δ %": f"{m['delta_pct'] * 100:.1f}%" if m.get("delta_pct") is not None else "",
            "Prev Shares": _fmt_shares(m.get("prev_shares")),
            "Curr Shares": _fmt_shares(m.get("curr_shares")),
            "Status": m["status"],
            "Period": f"{m['period_prev']} → {m['period_curr']}",
        }
        for m in rows
    ])


def _csv_download(label: str, df: pd.DataFrame, filename: str, key: str) -> None:
    buf = StringIO()
    df.to_csv(buf, index=False)
    st.download_button(label, buf.getvalue(), file_name=filename, mime="text/csv", key=key)


def _pct(val, digits: int = 1) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val * 100:.{digits}f}%"


def _num(val, digits: int = 2) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val:.{digits}f}"


@st.cache_data(show_spinner=False)
def _load_study(path: str, mtime: float) -> dict:
    return study.load_results(path)


def _study_dirs() -> list[str]:
    root = study.OUT_ROOT
    if not os.path.isdir(root):
        return []
    return sorted(
        (d for d in os.listdir(root)
         if d.startswith("study_") and os.path.exists(os.path.join(root, d, "results.json"))),
        reverse=True,
    )


def _study_summary_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Portfolio": r["name"],
            "Eligible": {True: "yes", False: "no"}.get(r.get("eligible"), "—")
            if r["scope"] == "fund" else "blend",
            "Published 5y": _pct(r.get("published_5y") / 100) if r.get("published_5y") is not None else "—",
            "Published 10y": _pct(r.get("published_10y") / 100) if r.get("published_10y") is not None else "—",
            "Copy CAGR": _pct(r.get("cagr")),
            "SPY CAGR": _pct(r.get("spy_cagr")),
            "Excess": _pct(r.get("excess_cagr")),
            "Max DD": _pct(r.get("max_drawdown")),
            "Sharpe": _num(r.get("sharpe")),
            "Sortino": _num(r.get("sortino")),
            "Calmar": _num(r.get("calmar")),
            "Beta": _num(r.get("beta")),
            "Coverage": _pct(r.get("coverage"), 0),
            "Late start": "yes" if r.get("late_start") else "",
        }
        for r in rows
    ])


def _metrics_df(s: dict) -> pd.DataFrame:
    items = [
        ("Start", s.get("start")), ("End", s.get("end")),
        ("Ending value", _fmt_usd(s.get("end_value"))),
        ("Total return", _pct(s.get("total_return"))),
        ("CAGR", _pct(s.get("cagr"))),
        ("SPY CAGR", _pct(s.get("spy_cagr"))),
        ("Volatility", _pct(s.get("volatility"))),
        ("Sharpe", _num(s.get("sharpe"))),
        ("Sortino", _num(s.get("sortino"))),
        ("Calmar", _num(s.get("calmar"))),
        ("Max drawdown", _pct(s.get("max_drawdown"))),
        ("Drawdown peak / trough", f"{s.get('peak') or '—'} / {s.get('trough') or '—'}"),
        ("Recovered", s.get("recovery") or "not yet"),
        ("Beta vs SPY", _num(s.get("beta"))),
        ("Alpha vs SPY (ann.)", _pct(s.get("alpha"))),
        ("Up / down capture", f"{_num(s.get('up_capture'))} / {_num(s.get('down_capture'))}"),
        ("Best / worst year", f"{_pct(s.get('best_year'))} / {_pct(s.get('worst_year'))}"),
        ("Turnover (ann.)", _pct(s.get("turnover"), 0)),
        ("Win rate (closed)", _pct(s.get("win_rate"), 0)),
        ("Avg holding (days)", _num(s.get("avg_holding_days"), 0)),
    ]
    return pd.DataFrame(items, columns=["Metric", "Value"])


def _render_backtest_series(nav: list, spy: list, summary: dict, trades: list,
                            key: str) -> None:
    if not nav:
        st.info("No NAV series.")
        return
    df = pd.DataFrame(nav, columns=["date", "Copy portfolio"]).set_index("date")
    if spy:
        df = df.join(pd.DataFrame(spy, columns=["date", "SPY"]).set_index("date"), how="left")
    df.index = pd.to_datetime(df.index)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Growth of the account vs SPY**")
        st.line_chart(df)
        st.markdown("**Drawdown (underwater)**")
        dd = pd.DataFrame(metrics.drawdown_series(nav), columns=["date", "Copy portfolio"])
        if spy:
            sdd = pd.DataFrame(metrics.drawdown_series(spy), columns=["date", "SPY"])
            dd = dd.merge(sdd, on="date", how="left")
        dd["date"] = pd.to_datetime(dd["date"])
        st.area_chart(dd.set_index("date"))
    with c2:
        st.dataframe(_metrics_df(summary), use_container_width=True, hide_index=True)
    nav_out = df.reset_index().rename(columns={"index": "date"})
    _csv_download("Download NAV CSV", nav_out, f"nav_{key}.csv", key=f"nav_dl_{key}")
    if trades:
        tdf = pd.DataFrame([
            {
                "Date": t["date"], "Filing period": t.get("period", ""),
                "Ticker": t["ticker"], "Side": t["side"], "Reason": t["reason"],
                "Shares": _fmt_shares(t["units"]), "Price": f"${t['price']:,.2f}",
                "Notional": _fmt_usd(t["notional"]),
            }
            for t in trades
        ])
        with st.expander(f"Trade ledger ({len(trades)} trades)"):
            st.dataframe(tdf, use_container_width=True, hide_index=True)
        _csv_download("Download trade ledger CSV", pd.DataFrame(trades),
                      f"trades_{key}.csv", key=f"trades_dl_{key}")


def _run_single_backtest(cik: str, years: int, rules: "simulator.Rules",
                         capital: float) -> dict:
    import datetime as dt
    from prices import PriceBook
    pb = PriceBook(study.PRICE_START, dt.date.today())
    spy = pb.series(study.BENCH)
    calendar = [d for d, _ in spy]
    end = calendar[-1]
    start = study.window_start(calendar, end, years)
    hp, tmap = study.load_fund(cik, rules.top_n)
    events = simulator.build_events(hp, tmap, calendar, rules, pb.splits, pb.series)
    res = simulator.simulate(events, calendar, pb.series, rules, start=start,
                             end=end, capital=capital)
    days = calendar[calendar.index(start):]
    spy_nav = study.bench_nav(spy, days, capital)
    summary = metrics.summarize(res.nav, spy_nav, res.trades, res.closed)
    s_spy = metrics.summarize(spy_nav)
    summary["spy_cagr"] = s_spy.get("cagr")
    return {"result": res, "spy": spy_nav, "summary": summary,
            "coverage": study.avg_coverage(res)}


def _render_backtest_tab() -> None:
    st.subheader("Copy-trading backtest")
    st.caption(
        "Hypothetical results. Each copy buys at the close of the first trading day after "
        "the 13F is public, trims by the same fraction the fund cut its shares, and adds or "
        "buys new names only with available cash. Funds were chosen by today's published "
        "track records (hindsight), prices cover only currently listed tickers "
        "(survivorship), and 13Fs omit shorts, cash and non-US holdings."
    )

    dirs = _study_dirs()
    if not dirs:
        st.info("No study results yet. Run `python study.py` to generate them, "
                "or use the single-fund backtest below.")
    else:
        sel_dir = st.selectbox("Study run", dirs, key="bt_dir")
        path = os.path.join(study.OUT_ROOT, sel_dir)
        res = _load_study(path, os.path.getmtime(os.path.join(path, "results.json")))
        rules = res.get("rules", {})
        st.caption(
            f"As of {res.get('as_of')} · top {rules.get('top_n')} · {rules.get('weighting')} "
            f"weights · {rules.get('sell_rule')} sells · {rules.get('cost_bps')} bps costs · "
            f"start {_fmt_usd(res.get('capital'))}"
        )
        c1, c2 = st.columns(2)
        window = c1.radio("Window", [10, 5], horizontal=True, key="bt_window",
                          format_func=lambda y: f"{y} years")
        variant = c2.radio("Variant", ["base", "rebalance", "period_end"],
                           horizontal=True, key="bt_variant",
                           format_func={"base": "Follow filings (base)",
                                        "rebalance": "Full rebalance",
                                        "period_end": "Quarter-end (unrealizable)"}.get)
        summary = res.get("summary", [])
        rows = [r for r in summary if r.get("window") == window
                and (r.get("variant") == variant or r.get("scope") == "combined")]
        rows.sort(key=lambda r: (r["scope"] != "combined", -(r.get("cagr") or -9)))
        st.dataframe(_study_summary_df(rows), use_container_width=True, hide_index=True)
        _csv_download("Download summary CSV", pd.DataFrame(summary),
                      f"{sel_dir}_summary.csv", key="bt_summary_dl")

        fund_rows = [r for r in summary if r.get("window") == window and r["scope"] == "fund"]
        if fund_rows:
            with st.expander("Rule and timing comparison (CAGR by variant)"):
                piv = pd.DataFrame(fund_rows).pivot_table(
                    index="name", columns="variant", values="cagr", aggfunc="first")
                st.dataframe(piv.map(_pct) if hasattr(piv, "map") else piv.applymap(_pct),
                             use_container_width=True)
                st.caption("Quarter-end entry is not achievable (13Fs are filed up to 45 days "
                           "later); the gap to the base variant is the cost of the delay.")

        skipped = [f for f in res.get("funds", []) if f.get("error")]
        if skipped:
            st.caption("Not simulated: " + "; ".join(f"{f['name']} ({f['error']})" for f in skipped))

        series = [s for s in res.get("series", []) if s.get("window") == window]
        if series:
            series.sort(key=lambda s: (s["cik"] not in ("combined_eligible", "combined_all",
                                                        "point_in_time"), s["name"]))
            pick = st.selectbox("Portfolio detail", range(len(series)),
                                format_func=lambda i: series[i]["name"], key="bt_series")
            s = series[pick]
            _render_backtest_series(s["nav"], s.get("spy", []), s.get("summary", {}),
                                    s.get("trades", []), key=f"{s['cik']}_{window}y")
            if s.get("picks"):
                with st.expander("Point-in-time selections"):
                    names = {f["cik"]: f["name"] for f in res.get("funds", [])}
                    st.dataframe(pd.DataFrame([
                        {"Date": p["date"],
                         "Chosen": ", ".join(names.get(c, c) for c in p["chosen"])}
                        for p in s["picks"]
                    ]), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Run a single fund")
    pool = {f["cik"]: f["name"] for f in backtest_universe.load()}
    for f in universe.load():
        pool.setdefault(f["cik"], f["name"])
    if not pool:
        st.info("No funds available.")
        return
    ciks = list(pool)
    c1, c2, c3 = st.columns(3)
    cik = c1.selectbox("Fund", ciks, format_func=lambda c: f"{pool[c]} ({c})", key="bt_cik")
    years = c2.selectbox("Years", [10, 5, 3], key="bt_years")
    capital = c3.number_input("Starting capital", min_value=1_000, value=100_000,
                              step=10_000, key="bt_capital")
    c4, c5, c6, c7 = st.columns(4)
    top = c4.number_input("Top N", min_value=1, max_value=50, value=20, key="bt_topn")
    wt = c5.selectbox("Weighting", ["value", "equal"], key="bt_wt")
    sell = c6.selectbox("Sell rule", ["proportional", "full_exit"], key="bt_sell")
    cost = c7.number_input("Cost (bps)", min_value=0.0, value=5.0, step=1.0, key="bt_cost")
    if st.button("Run backtest", type="primary", key="bt_run"):
        rules = simulator.Rules(top_n=int(top), weighting=wt, sell_rule=sell, cost_bps=cost)
        with st.spinner("Fetching filings and prices (first run for a fund can take minutes)…"):
            try:
                st.session_state["bt_single"] = {
                    "cik": cik, "years": years,
                    **_run_single_backtest(cik, int(years), rules, float(capital)),
                }
            except Exception as e:
                st.error(f"Backtest failed: {e}")
    out = st.session_state.get("bt_single")
    if out and out["cik"] == cik:
        r = out["result"]
        if r.late_start:
            st.warning(f"First usable filing executes {r.first_event}; "
                       "the account holds cash until then.")
        st.caption(f"Average tradable coverage of the fund's top-N value: {_pct(out['coverage'], 0)}")
        _render_backtest_series(r.nav, out["spy"], out["summary"], r.trades,
                                key=f"single_{cik}_{out['years']}y")


def _run_with_progress(funds, top_n, weighting, years):
    progress = st.progress(0.0, text="Starting…")
    status = st.empty()
    messages: list[str] = []
    total = max(len(funds), 1)

    def on_progress(name: str, msg: str):
        messages.append(f"{name}: {msg}")
        status.caption(messages[-1] if messages else "")
        done = sum(
            1 for m in messages
            if any(x in m for x in ("done", "skipped", "error", "insufficient"))
        )
        progress.progress(min(done / total, 0.99), text=messages[-1])

    rows = runner.run_ranking(
        funds,
        top_n=top_n,
        weighting=weighting,
        years=years,
        on_progress=on_progress,
    )
    progress.progress(1.0, text="Done")
    return rows


def _load_with_progress(funds, loader, label: str):
    progress = st.progress(0.0, text=f"{label}…")
    status = st.empty()
    messages: list[str] = []
    total = max(len(funds), 1)

    def on_progress(name: str, msg: str):
        messages.append(f"{name}: {msg}")
        status.caption(messages[-1] if messages else "")
        done = sum(
            1 for m in messages
            if any(x in m for x in ("done", "error", "no data", "insufficient"))
        )
        progress.progress(min(done / total, 0.99), text=messages[-1])

    result = loader(funds, on_progress=on_progress)
    progress.progress(1.0, text="Done")
    return result


def main():
    st.set_page_config(page_title="replica13f", layout="wide")
    st.title("replica13f")
    st.caption("Reproducible 13F long-equity replica returns")

    edgar_ok, figi_ok = _env_ok()
    with st.sidebar:
        st.header("Methodology")
        top_n = st.number_input("Top N holdings", min_value=1, max_value=50, value=20)
        weighting = st.selectbox("Weighting", ["equal", "value"])
        years = st.number_input("Trailing years", min_value=1, max_value=20, value=5)
        moves_limit = st.number_input("Moves table rows", min_value=5, max_value=100, value=25)

        st.divider()
        st.header("Environment")
        if edgar_ok:
            st.success("EDGAR_UA set")
        else:
            st.warning("Set EDGAR_UA (Name email@domain.com)")
        if figi_ok:
            st.success("OPENFIGI_KEY set")
        else:
            st.info("OPENFIGI_KEY optional for seed; recommended at scale")

        run_clicked = st.button("Run ranking", type="primary", use_container_width=True)

    (tab_top, tab_univ, tab_results, tab_holdings, tab_moves, tab_13d,
     tab_insider, tab_ticker, tab_detail, tab_backtest) = st.tabs(
        ["Top funds", "Universe", "Results", "Holdings", "Moves", "13D filings",
         "Insider buys", "Ticker lookup", "Fund detail", "Backtest"]
    )

    with tab_backtest:
        _render_backtest_tab()

    with tab_top:
        st.subheader("Top hedge funds by annualized return")
        st.caption(
            f"Approximate net returns compiled from public reporting, as of {leaderboard.AS_OF}. "
            "Hedge funds do not publish audited public returns, so treat these as directional — "
            "edit `data/top_funds.json` to update numbers or change the list. "
            "**Select rows (checkboxes on the left) to add funds to your universe.**"
        )

        board = leaderboard.load()

        st.markdown("**Long-term track records (1, 5 and 10 years)**")
        long_term = sorted(
            (r for r in board if r.get("ret_5yr") is not None or r.get("ret_10yr") is not None),
            key=lambda r: (-(r.get("ret_10yr") if r.get("ret_10yr") is not None else -1e9),
                           -(r.get("ret_5yr") if r.get("ret_5yr") is not None else -1e9)),
        )
        maverick = [r for r in board if "Maverick" in r["name"] and r not in long_term]
        st.dataframe(
            pd.DataFrame([
                {"Fund": r["name"], "1Y (2025)": r.get("ret_1yr"),
                 "5Y ann.": r.get("ret_5yr"), "10Y ann.": r.get("ret_10yr")}
                for r in long_term + maverick
            ]),
            use_container_width=True,
            hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(format="%.1f%%")
                for c in ("1Y (2025)", "5Y ann.", "10Y ann.")
            },
        )
        st.caption("Blank = no credible published figure. 5Y/10Y are approximate; "
                   "Haidar files no 13F, so it can't be copied.")

        st.markdown("**All tracked funds**")
        # Selecting a row reruns the script; process the selection BEFORE
        # drawing the table so the "In universe" checkmark is current.
        state = st.session_state.get("top_funds_table")
        selected_rows = state.get("selection", {}).get("rows", []) if state else []
        universe_ciks = {f["cik"] for f in universe.load()}
        added = []
        for i in selected_rows:
            row = board[i]
            if row["cik"] and row["cik"] not in universe_ciks:
                universe.add({
                    "name": row["name"],
                    "cik": row["cik"],
                    "ww_ref_5yr": row.get("ret_5yr"),
                })
                universe_ciks.add(row["cik"])
                added.append(row["name"])

        df_top = pd.DataFrame([
            {
                "Fund": r["name"],
                "1yr Ann %": r.get("ret_1yr"),
                "3yr Ann %": r.get("ret_3yr"),
                "5yr Ann %": r.get("ret_5yr"),
                "10yr Ann %": r.get("ret_10yr"),
                "In universe": "✓" if r["cik"] in universe_ciks else "",
                "CIK": r["cik"],
            }
            for r in board
        ])
        st.dataframe(
            df_top,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="top_funds_table",
            column_config={
                "1yr Ann %": st.column_config.NumberColumn(format="%.1f%%"),
                "3yr Ann %": st.column_config.NumberColumn(format="%.1f%%"),
                "5yr Ann %": st.column_config.NumberColumn(format="%.1f%%"),
                "10yr Ann %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

        if added:
            st.success("Added to universe: " + ", ".join(added))
        elif selected_rows:
            st.caption("All selected funds are already in the universe.")

    funds = universe.load()

    with tab_univ:
        st.subheader("Saved funds")
        if funds:
            df_univ = pd.DataFrame([
                {
                    "Name": f["name"],
                    "CIK": f["cik"],
                    "WW Ref 5yr": f.get("ww_ref_5yr", ""),
                }
                for f in funds
            ])
            st.dataframe(df_univ, use_container_width=True, hide_index=True)
        else:
            st.info("No funds in universe.")

        st.divider()
        st.subheader("Add fund")

        add_mode = st.radio("Entry mode", ["Search SEC", "Manual CIK"], horizontal=True)

        if add_mode == "Search SEC":
            q = st.text_input("Search by manager / company name")
            hits = edgar.search_entities(q, limit=10) if q.strip() else []
            if hits:
                labels = [f"{h['name']}  (CIK {h['cik']}, {h['ticker']})" for h in hits]
                pick = st.selectbox("Select match", range(len(labels)), format_func=lambda i: labels[i])
                sel = hits[pick]
                add_name = st.text_input("Display name", value=sel["name"])
                add_cik = st.text_input("CIK", value=sel["cik"])
            else:
                if q.strip():
                    st.caption("No matches — try manual CIK or a shorter query.")
                add_name = st.text_input("Display name")
                add_cik = st.text_input("CIK")
        else:
            add_name = st.text_input("Display name")
            add_cik = st.text_input("CIK (10 digits)")

        add_ref = st.text_input("WW reference 5yr % (optional)", value="")
        if st.button("Add to universe"):
            try:
                fund = {"name": add_name, "cik": add_cik}
                if add_ref.strip():
                    fund["ww_ref_5yr"] = float(add_ref)
                universe.add(fund)
                st.success(f"Added {add_name}")
                st.rerun()
            except (ValueError, KeyError) as e:
                st.error(str(e))

        st.divider()
        st.subheader("Edit / remove")
        if funds:
            labels = [f"{f['name']} ({f['cik']})" for f in funds]
            idx = st.selectbox("Select fund", range(len(labels)), format_func=lambda i: labels[i])
            sel = funds[idx]
            edit_name = st.text_input("Name", value=sel["name"], key="edit_name")
            edit_cik = st.text_input("CIK", value=sel["cik"], key="edit_cik", disabled=True)
            edit_ref = st.text_input(
                "WW ref 5yr %",
                value=str(sel.get("ww_ref_5yr", "") or ""),
                key="edit_ref",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Save changes"):
                    try:
                        fund = {"name": edit_name, "cik": edit_cik}
                        if edit_ref.strip():
                            fund["ww_ref_5yr"] = float(edit_ref)
                        universe.update(sel["cik"], fund)
                        st.success("Updated")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
            with c2:
                if st.button("Remove fund"):
                    universe.remove(sel["cik"])
                    st.success("Removed")
                    st.rerun()

        if st.button("Reset to seed universe"):
            universe.bootstrap_from_seed()
            st.success("Universe reset to seed list")
            st.rerun()

    with tab_results:
        last = _load_last_run()
        if last:
            meta = last.get("meta", {})
            st.caption(
                f"Last run: top {meta.get('top_n')} · {meta.get('weighting')} · "
                f"{meta.get('years')}yr trailing"
            )

        if run_clicked:
            funds = universe.load()
            if not funds:
                st.warning("Add funds in the Universe tab first.")
            else:
                with st.spinner("Running replica engine…"):
                    rows = _run_with_progress(funds, top_n, weighting, years)
                meta = {"top_n": top_n, "weighting": weighting, "years": years}
                _save_last_run(meta, rows)
                last = {"meta": meta, "rows": rows}

        if last and last.get("rows"):
            rows = last["rows"]
            yr = last.get("meta", {}).get("years", years)
            df = _rows_to_df(rows, yr)
            st.dataframe(df, use_container_width=True, hide_index=True)
            _csv_download("Download CSV", df, "replica_ranking.csv", "rank_csv")
        elif not run_clicked:
            st.info("Configure methodology in the sidebar, then click **Run ranking**.")

    with tab_holdings:
        st.subheader("Top holdings")
        st.caption(
            "Latest 13F long-equity positions per fund. "
            "Aggregate sums disclosed values across managers (overlap is intentional)."
        )

        col_load, col_filter = st.columns([1, 1])
        with col_load:
            load_holdings = st.button("Load holdings", type="primary")
        with col_filter:
            agg_filter = st.radio(
                "Aggregate includes",
                ["All funds", "Eligible only"],
                horizontal=True,
                key="holdings_agg_filter",
            )
        fund_filter = "eligible" if agg_filter == "Eligible only" else "all"

        if load_holdings:
            funds = universe.load()
            if not funds:
                st.warning("Add funds in the Universe tab first.")
            else:
                snap = _load_with_progress(
                    funds,
                    lambda f, on_progress: holdings_mod.latest_snapshot(
                        f, top_n=top_n, on_progress=on_progress
                    ),
                    "Loading holdings",
                )
                _save_holdings_snapshot(snap)

        snap = _load_holdings_snapshot()
        if snap and snap.get("funds"):
            fund_entries = [
                (cik, f) for cik, f in snap["funds"].items() if not f.get("error")
            ]
            if not fund_entries:
                st.warning("No holdings loaded successfully.")
            else:
                st.subheader("By fund")
                labels = [f"{f['name']} ({cik})" for cik, f in fund_entries]
                idx = st.selectbox(
                    "Select fund",
                    range(len(labels)),
                    format_func=lambda i: labels[i],
                    key="holdings_fund_pick",
                )
                cik, fund = fund_entries[idx]
                st.caption(f"Period {fund.get('period')} · filed {fund.get('filed')}")
                df_fund = _fund_holdings_df(fund, top_n)
                st.dataframe(df_fund, use_container_width=True, hide_index=True)
                _csv_download(
                    "Download fund CSV",
                    df_fund,
                    f"holdings_{cik}.csv",
                    f"hold_csv_{cik}",
                )

                st.divider()
                st.subheader("Aggregate")
                agg_rows = holdings_mod.aggregate_holdings(snap, fund_filter=fund_filter)
                if agg_rows:
                    st.caption(
                        f"{len(agg_rows)} names · showing top {top_n} · "
                        f"filter: {agg_filter.lower()}"
                    )
                    df_agg = _aggregate_df(agg_rows, top_n)
                    st.dataframe(df_agg, use_container_width=True, hide_index=True)
                    _csv_download(
                        "Download aggregate CSV",
                        _aggregate_df(agg_rows, len(agg_rows)),
                        "holdings_aggregate.csv",
                        "hold_agg_csv",
                    )
                else:
                    st.info("No aggregate rows for this filter.")
        else:
            st.info("Click **Load holdings** to fetch latest 13F positions for the universe.")

    with tab_moves:
        st.subheader("Quarter-over-quarter moves")
        st.caption("Value changes between the last two 13F periods (Δ USD).")

        col_load, col_fund, col_elig = st.columns([1, 1, 1])
        with col_load:
            load_moves = st.button("Load moves", type="primary")
        with col_fund:
            fund_names = ["All funds"] + [f["name"] for f in universe.load()]
            moves_fund_pick = st.selectbox("Fund filter", fund_names, key="moves_fund_pick")
        with col_elig:
            moves_agg_filter = st.radio(
                "Include",
                ["All funds", "Eligible only"],
                horizontal=True,
                key="moves_elig_filter",
            )
        moves_fund_filter = "eligible" if moves_agg_filter == "Eligible only" else "all"

        if load_moves:
            funds = universe.load()
            if not funds:
                st.warning("Add funds in the Universe tab first.")
            else:
                snap = _load_with_progress(
                    funds,
                    lambda f, on_progress: holdings_mod.quarter_changes(
                        f, on_progress=on_progress
                    ),
                    "Loading moves",
                )
                _save_moves_snapshot(snap)

        msnap = _load_moves_snapshot()
        if msnap and msnap.get("all_moves"):
            filtered = holdings_mod.filter_moves(
                msnap["all_moves"],
                fund=moves_fund_pick,
                fund_filter=moves_fund_filter,
            )
            lim = int(moves_limit)

            t1, t2 = st.columns(2)
            with t1:
                st.markdown("**Biggest increases**")
                inc = holdings_mod.top_increases(filtered, limit=lim)
                df_inc = _moves_df(inc)
                st.dataframe(df_inc, use_container_width=True, hide_index=True)
                if not df_inc.empty:
                    _csv_download("Download increases", df_inc, "moves_increases.csv", "mov_inc")

            with t2:
                st.markdown("**Biggest decreases**")
                dec = holdings_mod.top_decreases(filtered, limit=lim)
                df_dec = _moves_df(dec)
                st.dataframe(df_dec, use_container_width=True, hide_index=True)
                if not df_dec.empty:
                    _csv_download("Download decreases", df_dec, "moves_decreases.csv", "mov_dec")

            t3, t4 = st.columns(2)
            with t3:
                st.markdown("**New positions**")
                new = holdings_mod.new_positions(filtered, limit=lim)
                df_new = _moves_df(new)
                st.dataframe(df_new, use_container_width=True, hide_index=True)
                if not df_new.empty:
                    _csv_download("Download new", df_new, "moves_new.csv", "mov_new")

            with t4:
                st.markdown("**Exited positions**")
                ex = holdings_mod.exited_positions(filtered, limit=lim)
                df_ex = _moves_df(ex)
                st.dataframe(df_ex, use_container_width=True, hide_index=True)
                if not df_ex.empty:
                    _csv_download("Download exited", df_ex, "moves_exited.csv", "mov_ex")

            skipped = [
                f"{f['name']}: {f.get('error')}"
                for f in msnap.get("funds", {}).values()
                if f.get("error")
            ]
            if skipped:
                with st.expander("Funds skipped"):
                    for s in skipped:
                        st.caption(s)
        else:
            st.info("Click **Load moves** to compare the last two 13F filings per fund.")

    with tab_13d:
        st.subheader("Daily 13D/13G feed — all filers")
        st.caption(
            "Every 13D and 13G filed with the SEC, market-wide, pulled from "
            "EDGAR's daily index. Both are >5%-ownership disclosures filed within "
            "days — much fresher than the quarterly 13F. 13D = activist intent; "
            "13G = passive (short form). The VPS refreshes this feed every day."
        )

        feed = feed13d.load_feed()
        c_refresh, c_family, c_lookback = st.columns([1, 1, 1])
        with c_refresh:
            refresh_feed = st.button("Refresh feed now")
        with c_family:
            family_pick = st.selectbox(
                "Form family",
                ["13D only", "13G only", "13D + 13G"],
                index=0,
                key="feed_family",
            )
        with c_lookback:
            lookback = st.selectbox(
                "Lookback (days)", [7, 14, 30, 60, 90], index=2, key="feed_lookback"
            )
        family = {"13D only": "13D", "13G only": "13G", "13D + 13G": "both"}[family_pick]

        if refresh_feed:
            fstatus = st.empty()
            with st.spinner("Updating feed from EDGAR…"):
                feed = feed13d.update_feed(
                    days_back=7,
                    on_progress=lambda d, m: fstatus.caption(f"{d}: {m}"),
                )
            fstatus.caption(f"Done — {feed.get('new_count', 0)} new filings")

        if feed.get("rows"):
            all_rows = feed13d.recent_rows(feed, days=int(lookback))
            rows = feed13d.filter_family(all_rows, family)
            st.caption(
                f"{len(rows)} {family_pick.replace(' only', '')} filings in the "
                f"last {lookback} days · feed updated {feed.get('updated', '—')}"
            )
            df_feed = _feed_df(rows)
            st.dataframe(
                df_feed,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Filing": st.column_config.LinkColumn(
                        "Filing", display_text="open on EDGAR"
                    ),
                },
            )
            _csv_download("Download feed CSV", df_feed, "feed_13d.csv", "csv_feed13d")

            st.markdown("**Filed by your universe funds**")
            st.caption("Always shows both 13D and 13G, regardless of the filter above.")
            urows = feed13d.universe_rows(all_rows, universe.load())
            if urows:
                df_ufeed = pd.DataFrame([
                    {
                        "Filed": r["filed"],
                        "Your fund": r["fund"],
                        "Company": r["company"],
                        "Ticker": r["ticker"] or "—",
                        "Form": r["form"],
                        "Filing": r["url"],
                    }
                    for r in urows
                ])
                st.dataframe(
                    df_ufeed,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Filing": st.column_config.LinkColumn(
                            "Filing", display_text="open on EDGAR"
                        ),
                    },
                )
            else:
                st.caption(
                    f"No 13Ds filed by your universe funds in the last "
                    f"{lookback} days."
                )

            a1, a2 = st.columns(2)
            with a1:
                st.markdown("**Most 13D'd companies**")
                st.caption("Several filers circling one stock is a strong signal.")
                df_tgt = pd.DataFrame([
                    {
                        "Company": t["company"],
                        "Ticker": t["ticker"] or "—",
                        "Filings": t["filings"],
                        "New 13Ds": t["new_13d"],
                        "Amendments": t["amendments"],
                        "# Filers": t["filers"],
                        "Last filed": t["last_filed"],
                    }
                    for t in feed13d.most_targeted(rows)
                ])
                st.dataframe(df_tgt, use_container_width=True, hide_index=True)
            with a2:
                st.markdown("**Most active filers**")
                df_flr = pd.DataFrame([
                    {
                        "Filer": t["filer"],
                        "Filings": t["filings"],
                        "Companies": t["companies"],
                        "Last filed": t["last_filed"],
                    }
                    for t in feed13d.most_active_filers(rows)
                ])
                st.dataframe(df_flr, use_container_width=True, hide_index=True)
        else:
            st.info(
                "No feed data yet — click **Refresh feed now** "
                "(the first pull takes a few minutes)."
            )

        st.divider()
        st.subheader("Your funds' 13D filings")
        st.caption(
            "13D history for the funds in your universe. "
            "Amendments (13D/A) signal the position is changing. "
            "13G is the passive equivalent."
        )

        c_load, c_13g, c_limit = st.columns([1, 1, 1])
        with c_load:
            load_13d = st.button("Load 13D filings", type="primary")
        with c_13g:
            include_13g = st.checkbox("Include 13G (passive stakes)", value=False)
        with c_limit:
            per_fund_limit = st.number_input(
                "Max filings per fund", min_value=1, max_value=50, value=10
            )

        if load_13d:
            funds = universe.load()
            if not funds:
                st.warning("Add funds in the Universe tab first.")
            else:
                snap = _load_with_progress(
                    funds,
                    lambda f, on_progress: activist.snapshot(
                        f,
                        include_13g=include_13g,
                        per_fund_limit=int(per_fund_limit),
                        on_progress=on_progress,
                    ),
                    "Loading 13D filings",
                )
                _save_activist_snapshot(snap)

        asnap = _load_activist_snapshot()
        if asnap and asnap.get("rows"):
            fund_names = ["All funds"] + sorted({r["fund"] for r in asnap["rows"]})
            pick = st.selectbox("Fund filter", fund_names, key="activist_fund_pick")
            rows = asnap["rows"]
            if pick != "All funds":
                rows = [r for r in rows if r["fund"] == pick]

            meta = asnap.get("meta", {})
            st.caption(
                f"{len(rows)} filings · newest first · "
                f"{'13D + 13G' if meta.get('include_13g') else '13D only'} · "
                f"up to {meta.get('per_fund_limit')} per fund"
            )
            df_13d = _activist_df(rows)
            st.dataframe(
                df_13d,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Filing": st.column_config.LinkColumn(
                        "Filing", display_text="open on EDGAR"
                    ),
                },
            )
            _csv_download("Download CSV", df_13d, "filings_13d.csv", "csv_13d")

            skipped = [
                f"{f['name']}: {f.get('error')}"
                for f in asnap.get("funds", {}).values()
                if f.get("error")
            ]
            if skipped:
                with st.expander("Funds skipped"):
                    for s in skipped:
                        st.caption(s)
        elif asnap:
            st.info("No 13D filings found for the current universe.")
        else:
            st.info("Click **Load 13D filings** to check every fund in the universe.")

    with tab_insider:
        st.subheader("Insider open-market buys")
        st.caption(
            "Every SEC Form 4 is screened daily; only open-market purchases "
            "(transaction code P) are kept — insiders spending their own money, "
            "the same screen as openinsider.com. Grants, option exercises, and "
            "sales are excluded. The VPS refreshes this feed every day."
        )

        ifeed = form4.load_feed()
        ci_refresh, ci_look, ci_min, ci_sort = st.columns([1, 1, 1, 1])
        with ci_refresh:
            refresh_insiders = st.button("Refresh feed now", key="ins_refresh")
        with ci_look:
            ins_lookback = st.selectbox(
                "Lookback (days)", [7, 14, 30, 60, 90], index=2, key="ins_lookback"
            )
        with ci_min:
            min_label = st.selectbox(
                "Min purchase value",
                ["$0", "$25k", "$100k", "$250k", "$1M"],
                index=1,
                key="ins_minval",
            )
        with ci_sort:
            ins_sort = st.selectbox(
                "Sort by", ["Conviction score", "Newest"], index=0, key="ins_sort"
            )
        min_value = {"$0": 0, "$25k": 25_000, "$100k": 100_000,
                     "$250k": 250_000, "$1M": 1_000_000}[min_label]

        if refresh_insiders:
            istatus = st.empty()
            with st.spinner("Screening Form 4s from EDGAR (a few minutes)…"):
                ifeed = form4.update_feed(
                    days_back=2,
                    on_progress=lambda d, m: istatus.caption(f"{d}: {m}"),
                )
                insider_score.enrich_feed(
                    ifeed,
                    on_progress=lambda t, m: istatus.caption(f"{t}: {m}"),
                )
            istatus.caption(f"Done — {ifeed.get('new_count', 0)} new purchases")

        if ifeed.get("rows"):
            irows = form4.recent_rows(ifeed, days=int(ins_lookback), min_value=min_value)
            if ins_sort == "Conviction score":
                irows = sorted(irows, key=lambda r: -(r.get("score") or 0))
            st.caption(
                f"{len(irows)} purchases ≥ {min_label} in the last {ins_lookback} "
                f"days · feed updated {ifeed.get('updated', '—')} · "
                "Score = size, stake increase, role, rarity, dip-buying, cluster, "
                "record; pre-scheduled (10b5-1) buys are heavily discounted. "
                "Record = buyer's prior buys that beat the S&P over 3 months."
            )

            def _record(r):
                if r.get("rec_n"):
                    return (f"{r['rec_hits']}/{r['rec_n']} · "
                            f"{r['rec_avg'] * 100:+.0f}%")
                return "—"

            df_ins = pd.DataFrame([
                {
                    "Score": r.get("score", "—"),
                    "Filed": r["filed"],
                    "Ticker": r["ticker"] or "—",
                    "Company": r["company"],
                    "Insider": r["insider"],
                    "Title": r["title"],
                    "Value": _fmt_usd(r["value"]),
                    "Record (3mo vs S&P)": _record(r),
                    "Plan": "10b5-1" if r.get("plan") else "",
                    "Why": r.get("why", ""),
                    "Shares": _fmt_shares(r["shares"]),
                    "Price": f"${r['price']:,.2f}" if r.get("price") else "—",
                    "Filing": r["url"],
                }
                for r in irows
            ])
            st.dataframe(
                df_ins,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Filing": st.column_config.LinkColumn(
                        "Filing", display_text="open on EDGAR"
                    ),
                },
            )
            _csv_download("Download insider CSV", df_ins, "insider_buys.csv", "csv_ins")

            st.markdown("**Cluster buys — 2+ insiders buying the same stock**")
            st.caption("Multiple insiders buying together is the strongest insider signal.")
            clusters = form4.cluster_buys(irows)
            if clusters:
                df_cl = pd.DataFrame([
                    {
                        "Ticker": c["ticker"] or "—",
                        "Company": c["company"],
                        "# Insiders": c["insiders"],
                        "Buys": c["buys"],
                        "Total value": _fmt_usd(c["total_value"]),
                        "Last filed": c["last_filed"],
                    }
                    for c in clusters
                ])
                st.dataframe(df_cl, use_container_width=True, hide_index=True)
            else:
                st.caption("No cluster buys in this window/filter.")
        else:
            st.info(
                "No insider feed yet — click **Refresh feed now**, or wait for "
                "the daily pull on the VPS."
            )

    with tab_ticker:
        st.subheader("Ticker lookup")
        st.caption(
            "One stock, everyone involved: insider buys & sells (Form 4), "
            "13D/13G holders, and which of your followed funds own it per "
            "their latest 13F."
        )

        c_in, c_go = st.columns([2, 1])
        with c_in:
            tl_ticker = st.text_input("Ticker", key="tl_ticker",
                                      placeholder="e.g. HHH")
        with c_go:
            st.write("")
            st.write("")
            tl_go = st.button("Look up", type="primary", key="tl_go")

        t_in = tl_ticker.strip().upper()
        # run on button click OR whenever a new ticker is entered (Enter key)
        if t_in and (tl_go or st.session_state.get("tl_last_ticker") != t_in):
            st.session_state["tl_last_ticker"] = t_in
            tstatus = st.empty()
            with st.spinner(f"Pulling EDGAR filings for {t_in}…"):
                res = ticker_lookup.lookup(
                    t_in, on_progress=lambda m: tstatus.caption(m)
                )
            tstatus.empty()
            if res is None:
                st.error(
                    f"Ticker '{t_in}' not found in the SEC's "
                    "company list — check the symbol."
                )
                st.session_state.pop("tl_result", None)
            else:
                st.session_state["tl_result"] = res

        tl_res = st.session_state.get("tl_result")
        if tl_res and not tl_res.get("error"):
            st.markdown(f"### {tl_res['name']}  ·  {tl_res['ticker']}")
            st.caption(f"CIK {tl_res['cik']}")

            st.markdown("**Followed funds holding it (latest 13F)**")
            hsnap = _load_holdings_snapshot()
            if hsnap:
                pos = ticker_lookup.fund_positions(tl_res["ticker"], hsnap)
                if pos:
                    st.dataframe(pd.DataFrame([
                        {
                            "Fund": p["fund"],
                            "Value": _fmt_usd(p["value_usd"]),
                            "% of book": f"{p['pct_of_book'] * 100:.1f}%",
                            "Shares": _fmt_shares(p.get("shares")),
                            "13F period": p["period"],
                        }
                        for p in pos
                    ]), use_container_width=True, hide_index=True)
                else:
                    st.caption("None of your universe funds hold it "
                               "(per the loaded holdings snapshot).")
            else:
                st.info("Load holdings in the **Holdings** tab first to see "
                        "which of your funds own it.")

            st.markdown("**13D / 13G holders**")
            if tl_res["holders"]:
                st.dataframe(pd.DataFrame([
                    {
                        "Filed": h["filed"],
                        "Form": h["form"],
                        "Holder(s)": h["holders"],
                        "Filing": h["url"],
                    }
                    for h in tl_res["holders"]
                ]), use_container_width=True, hide_index=True,
                    column_config={
                        "Filing": st.column_config.LinkColumn(
                            "Filing", display_text="open on EDGAR"),
                    })
            else:
                st.caption("No 13D/13G filings on record.")

            st.markdown("**Insider activity (open-market buys & sells, "
                        f"last {ticker_lookup.MAX_FORM4} Form 4s)**")
            if tl_res["trades"]:
                st.dataframe(pd.DataFrame([
                    {
                        "Filed": t["filed"],
                        "Trade": t["trade"],
                        "Action": t["action"],
                        "Insider": t["insider"],
                        "Title": t["title"],
                        "Shares": _fmt_shares(t["shares"]),
                        "Price": f"${t['price']:,.2f}" if t.get("price") else "—",
                        "Value": _fmt_usd(t["value"]),
                        "Plan": "10b5-1" if t.get("plan") else "",
                        "Filing": t["url"],
                    }
                    for t in tl_res["trades"]
                ]), use_container_width=True, hide_index=True,
                    column_config={
                        "Filing": st.column_config.LinkColumn(
                            "Filing", display_text="open on EDGAR"),
                    })
            else:
                st.caption("No open-market insider trades in the recent "
                           "Form 4s (grants and option exercises excluded).")
        elif tl_res and tl_res.get("error"):
            st.error(tl_res["error"])

    with tab_detail:
        funds = universe.load()
        if not funds:
            st.info("No funds in universe.")
        else:
            labels = [f"{f['name']} ({f['cik']})" for f in funds]
            idx = st.selectbox("Fund", range(len(labels)), format_func=lambda i: labels[i])
            sel = funds[idx]
            cik = sel["cik"]

            if st.button("Load profile & holdings", key="detail_load"):
                with st.spinner("Fetching from EDGAR…"):
                    verdict = classify.classify(cik)
                    st.subheader("Eligibility")
                    if verdict.get("eligible"):
                        st.success(verdict.get("reason", "eligible"))
                    else:
                        st.error(verdict.get("reason", "ineligible"))
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Positions", verdict.get("positions", "—"))
                    m2.metric("Option value share", verdict.get("option_value_share", "—"))
                    m3.metric("Top-20 concentration", verdict.get("top20_long_concentration", "—"))

                    hp = edgar.holdings_by_period(cik, max_periods=1)
                    if hp:
                        last_p = sorted(hp)[-1]
                        hs = sorted(hp[last_p]["holdings"], key=lambda x: -x["value_usd"])[:20]
                        st.subheader(f"Top holdings — period {last_p}")
                        st.caption(f"Filed {hp[last_p]['filing_date']}")
                        st.dataframe(
                            pd.DataFrame([
                                {
                                    "Issuer": h["issuer"],
                                    "CUSIP": h["cusip"],
                                    "Value": _fmt_usd(h["value_usd"]),
                                    "Shares": _fmt_shares(h.get("shares")),
                                }
                                for h in hs
                            ]),
                            use_container_width=True,
                            hide_index=True,
                        )

            last = _load_last_run()
            if last and last.get("rows"):
                match = next((r for r in last["rows"] if r.get("cik") == cik), None)
                if match:
                    st.subheader("Last ranking run")
                    yr = last.get("meta", {}).get("years", 5)
                    key = f"ann_{yr}yr_pct"
                    c1, c2, c3 = st.columns(3)
                    val = match.get(key)
                    c1.metric(f"{yr}yr ann %", f"{val:.1f}" if val == val else "n/a")
                    c2.metric("Full ann %", match.get("ann_full_pct", "n/a"))
                    c3.metric("Coverage", match.get("avg_coverage", "n/a"))
                    st.json({k: v for k, v in match.items() if k not in ("name",)})


if __name__ == "__main__":
    main()
