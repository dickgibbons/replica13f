"""Streamlit UI for replica13f."""
from __future__ import annotations
import json
import os
from io import StringIO

import pandas as pd
import streamlit as st

import activist
import classify
import edgar
import feed13d
import holdings as holdings_mod
import leaderboard
import runner
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

    tab_top, tab_univ, tab_results, tab_holdings, tab_moves, tab_13d, tab_detail = st.tabs(
        ["Top funds", "Universe", "Results", "Holdings", "Moves", "13D filings", "Fund detail"]
    )

    with tab_top:
        st.subheader("Top hedge funds by annualized return")
        st.caption(
            f"Approximate net returns compiled from public reporting, as of {leaderboard.AS_OF}. "
            "Hedge funds do not publish audited public returns, so treat these as directional — "
            "edit `data/top_funds.json` to update numbers or change the list. "
            "**Select rows (checkboxes on the left) to add funds to your universe.**"
        )

        board = leaderboard.load()

        # Selecting a row reruns the script; process the selection BEFORE
        # drawing the table so the "In universe" checkmark is current.
        state = st.session_state.get("top_funds_table")
        selected_rows = state.get("selection", {}).get("rows", []) if state else []
        universe_ciks = {f["cik"] for f in universe.load()}
        added = []
        for i in selected_rows:
            row = board[i]
            if row["cik"] not in universe_ciks:
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
        st.subheader("Daily 13D feed — all filers")
        st.caption(
            "Every 13D filed with the SEC, market-wide, pulled from EDGAR's daily "
            "index. A 13D is filed within days of anyone taking an activist stake "
            "above 5% of a company — much fresher than the quarterly 13F. "
            "The VPS refreshes this feed automatically every day."
        )

        feed = feed13d.load_feed()
        c_refresh, c_lookback = st.columns([1, 1])
        with c_refresh:
            refresh_feed = st.button("Refresh feed now")
        with c_lookback:
            lookback = st.selectbox(
                "Lookback (days)", [7, 14, 30, 60, 90], index=2, key="feed_lookback"
            )

        if refresh_feed:
            fstatus = st.empty()
            with st.spinner("Updating feed from EDGAR…"):
                feed = feed13d.update_feed(
                    days_back=7,
                    on_progress=lambda d, m: fstatus.caption(f"{d}: {m}"),
                )
            fstatus.caption(f"Done — {feed.get('new_count', 0)} new filings")

        if feed.get("rows"):
            rows = feed13d.recent_rows(feed, days=int(lookback))
            st.caption(
                f"{len(rows)} filings in the last {lookback} days · "
                f"feed updated {feed.get('updated', '—')}"
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
