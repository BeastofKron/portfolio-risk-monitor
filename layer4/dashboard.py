"""
Layer 4 – Bloomberg-style Streamlit dashboard.

Run with:
    streamlit run layer4/dashboard.py -- --asof YYYY-MM-DD

Panels:
  1. Status strip   : NAV, P&L, positions, alert count, macro score,
                      net delta, daily theta, as-of date
  2. Alerts panel   : color-coded by severity
  3. Positions grid : dense monospace table with progress bars
  4. Theta decay    : aggregate $ projection + BS extrinsic decay curve
  5. Strike ladder  : +/-6 strikes per held option, NEW badge for new listings
  6. Allocation     : stacked bars by ticker and sector
  7. News           : per-ticker analyst summary from Claude
"""
from __future__ import annotations
import argparse
import datetime
import json
import math
import os
import sys

import streamlit as st

# ── path setup so we can import sibling packages ──────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from layer1.storage import Storage
from layer1.greeks  import black_scholes_greeks

# ── Config / CLI ──────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    cfg_path = os.path.join(_ROOT, "config.json")
    with open(cfg_path) as f:
        return json.load(f)

def _parse_asof() -> str:
    """
    Streamlit passes extra args after '--'.
    Accept --asof YYYY-MM-DD or default to today.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--asof", default=None)
    args, _ = parser.parse_known_args()
    return args.asof or datetime.date.today().isoformat()

# ── Colour helpers ────────────────────────────────────────────────────────────

_SEV_COLOR = {"high": "#FF4B4B", "warn": "#FFA500", "info": "#4B9FFF"}
_SENTIMENT_COLOR = {"positive": "#00C853", "neutral": "#AAAAAA", "negative": "#FF4B4B"}

def _pnl_color(v: float) -> str:
    return "#00C853" if v >= 0 else "#FF4B4B"

# ── Progress bar HTML ─────────────────────────────────────────────────────────

def _progress_bar(pct: float | None, color: str = "#4B9FFF") -> str:
    if pct is None:
        return "—"
    clamped = max(0.0, min(100.0, pct))
    return (
        f'<div style="background:#1E1E2E;border-radius:4px;height:8px;width:80px;display:inline-block">'
        f'<div style="background:{color};width:{clamped:.0f}%;height:100%;border-radius:4px"></div>'
        f'</div> {pct:.0f}%'
    )

# ── DTE pill ─────────────────────────────────────────────────────────────────

def _dte_pill(dte: int | None) -> str:
    if dte is None:
        return "—"
    color = "#FF4B4B" if dte <= 14 else ("#FFA500" if dte <= 45 else "#4B9FFF")
    return (
        f'<span style="background:{color};color:#fff;border-radius:4px;'
        f'padding:1px 6px;font-size:0.75em">{dte}d</span>'
    )

# ── BS extrinsic decay curve ──────────────────────────────────────────────────

def _extrinsic_decay_curve(position: dict, rfr: float, n_points: int = 60):
    """
    Compute extrinsic value at constant spot & IV from today to expiry.
    Returns (dte_values, extrinsic_values).
    """
    dte_max = position.get("dte", 0)
    if dte_max <= 0:
        return [], []
    spot     = position.get("spot", 0)
    strike   = float(position["strike"])
    iv       = position.get("iv", 0.3)
    opt_type = position["option_type"]
    contracts = int(position.get("contracts", 1))

    dtes, extrinsics = [], []
    for i in range(n_points + 1):
        dte_i = dte_max * (1 - i / n_points)
        g = black_scholes_greeks(opt_type, spot, strike, dte_i, iv, rfr)
        intrinsic = max(0.0,
            (spot - strike) if opt_type == "call" else (strike - spot))
        ext = max(0.0, g["price"] - intrinsic) * contracts * 100
        dtes.append(round(dte_i, 1))
        extrinsics.append(round(ext, 2))
    return dtes, extrinsics

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(
        page_title="Portfolio Monitor",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Dark theme via CSS injection
    st.markdown("""
    <style>
    html, body, [data-testid="stApp"] {
        background-color: #0D1117; color: #C9D1D9;
        font-family: 'Courier New', monospace;
    }
    .status-card {
        background: #161B22; border: 1px solid #30363D;
        border-radius: 6px; padding: 12px 16px; text-align: center;
    }
    .status-label { font-size: 0.7em; color: #8B949E; text-transform: uppercase; }
    .status-value { font-size: 1.4em; font-weight: bold; margin-top: 2px; }
    .alert-row {
        border-left: 4px solid; padding: 6px 12px; margin: 4px 0;
        background: #161B22; border-radius: 0 4px 4px 0;
        font-size: 0.85em;
    }
    .pos-table { font-size: 0.78em; border-collapse: collapse; width: 100%; }
    .pos-table th { background: #161B22; padding: 4px 8px;
                    border-bottom: 1px solid #30363D; text-align: left; }
    .pos-table td { padding: 3px 8px; border-bottom: 1px solid #21262D; }
    .new-badge {
        background: #FFA500; color: #000; border-radius: 3px;
        padding: 0 4px; font-size: 0.7em; margin-left: 4px;
    }
    h1, h2, h3 { color: #E6EDF3 !important; }
    </style>
    """, unsafe_allow_html=True)

    cfg     = _load_cfg()
    asof    = _parse_asof()
    storage = Storage(
        db_path=cfg.get("db_path", "monitor.db"),
        snapshots_dir=cfg.get("snapshots_dir", "snapshots"),
    )
    rfr = cfg.get("risk_free_rate", 0.045)

    # Load data
    positions = storage.load_positions_valued(asof) or []
    analytics = storage.load_analytics(asof) or {}
    macro     = storage.load_macro(asof) or {}
    alerts    = storage.load_alerts(asof) or []

    # News: load per ticker
    tickers   = list({p["ticker"] for p in positions})
    news_by_ticker = {t: (storage.load_news(t, asof) or {}) for t in tickers}

    if not positions:
        st.warning(f"No valued positions found for {asof}. Run main.py first.")
        st.stop()

    # ── 1. STATUS STRIP ──────────────────────────────────────────────────────
    st.markdown(f"## 📊 Portfolio Monitor — {asof}")

    alloc    = analytics.get("allocation", {})
    agg_g    = analytics.get("aggregate_greeks", {})
    nav      = alloc.get("total_value", 0)
    total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
    macro_score = macro.get("macro_score", "—")

    cols = st.columns(8)
    metrics = [
        ("NAV", f"${nav:,.0f}"),
        ("P&L", f"${total_pnl:+,.0f}"),
        ("Positions", str(len(positions))),
        ("Alerts", str(len(alerts))),
        ("Macro", f"{macro_score}/100" if isinstance(macro_score, (int, float)) else "—"),
        ("Net Δ", f"{agg_g.get('net_delta_share_equiv', 0):+.1f}"),
        ("Θ/day", f"${agg_g.get('total_theta_per_day_usd', 0):+.2f}"),
        ("As-of", asof),
    ]
    for col, (label, value) in zip(cols, metrics):
        with col:
            st.markdown(
                f'<div class="status-card">'
                f'<div class="status-label">{label}</div>'
                f'<div class="status-value">{value}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── 2. ALERTS ────────────────────────────────────────────────────────────
    st.markdown("### 🔔 Active Alerts")
    if not alerts:
        st.success("No alerts today.")
    else:
        for a in alerts:
            sev   = a.get("severity", "info")
            color = _SEV_COLOR.get(sev, "#4B9FFF")
            st.markdown(
                f'<div class="alert-row" style="border-color:{color}">'
                f'<b style="color:{color}">[{sev.upper()}]</b> {a.get("message","")}'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── 3. POSITIONS GRID ─────────────────────────────────────────────────────
    st.markdown("### 📋 Positions")
    rows_html = []
    rows_html.append(
        "<table class='pos-table'><thead><tr>"
        "<th>Ticker</th><th>Type</th><th>Strike</th><th>Expiry</th><th>DTE</th>"
        "<th>Entry</th><th>Mark</th><th>Value</th>"
        "<th>P&L $</th><th>P&L %</th>"
        "<th>Δ</th><th>Θ/d</th><th>Vega</th><th>IV%</th>"
        "<th>→ Target</th><th>→ Stop</th>"
        "</tr></thead><tbody>"
    )
    for p in positions:
        g         = p.get("greeks", {})
        pnl_col   = _pnl_color(p.get("unrealized_pnl", 0))
        dte_html  = _dte_pill(p.get("dte"))
        tgt_html  = _progress_bar(p.get("progress_to_target_pct"), "#00C853")
        stp_html  = _progress_bar(p.get("progress_to_stop_pct"),   "#FF4B4B")
        iv_pct    = f"{p.get('iv', 0)*100:.1f}" if p.get("iv") else "—"

        rows_html.append(
            f"<tr>"
            f"<td><b>{p['ticker']}</b></td>"
            f"<td>{'OPT' if p['asset_type']=='option' else 'SHR'} "
            f"{'C' if p.get('option_type')=='call' else ('P' if p.get('option_type')=='put' else '')}</td>"
            f"<td>{p.get('strike','—')}</td>"
            f"<td>{p.get('expiry','—')}</td>"
            f"<td>{dte_html}</td>"
            f"<td>${p.get('entry_price',0):.2f}</td>"
            f"<td>${p.get('mark',0):.2f}</td>"
            f"<td>${p.get('current_value',0):,.0f}</td>"
            f"<td style='color:{pnl_col}'>${p.get('unrealized_pnl',0):+,.0f}</td>"
            f"<td style='color:{pnl_col}'>{p.get('unrealized_pnl_pct',0):+.1f}%</td>"
            f"<td>{g.get('delta',0):+.3f}</td>"
            f"<td>${g.get('theta',0)*p.get('contracts',1)*p.get('multiplier',1):+.2f}</td>"
            f"<td>{g.get('vega',0)*p.get('contracts',1)*p.get('multiplier',1):.2f}</td>"
            f"<td>{iv_pct}</td>"
            f"<td>{tgt_html}</td>"
            f"<td>{stp_html}</td>"
            f"</tr>"
        )
    rows_html.append("</tbody></table>")
    st.markdown("".join(rows_html), unsafe_allow_html=True)

    st.markdown("---")

    # ── 4. THETA DECAY ───────────────────────────────────────────────────────
    st.markdown("### ⏳ Theta Decay")
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        option_positions = [p for p in positions if p["asset_type"] == "option"]

        # Aggregate theta projection stacked by position (cumulative theta bleed)
        for p in option_positions:
            dte_max   = p.get("dte", 0)
            if dte_max <= 0:
                continue
            theta_day = p.get("greeks", {}).get("theta", 0) * p.get("contracts", 1) * 100
            dtes_proj = list(range(dte_max, -1, -1))
            # simple linear theta projection (illustrative)
            vals      = [theta_day * (dte_max - d) for d in dtes_proj]
            fig.add_trace(go.Scatter(
                x=dtes_proj, y=vals,
                mode="lines", name=f"{p['ticker']} {p['option_type']} {p['strike']}",
                fill="tozeroy", stackgroup="one",
            ))

        # Extrinsic decay curve for each option (BS at constant spot+IV)
        for p in option_positions:
            dtes, exts = _extrinsic_decay_curve(p, rfr)
            if dtes:
                fig.add_trace(go.Scatter(
                    x=dtes, y=exts, mode="lines",
                    name=f"{p['ticker']} extrinsic (BS)",
                    line=dict(dash="dash"),
                ))

        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#0D1117", plot_bgcolor="#0D1117",
            xaxis_title="DTE", yaxis_title="$ Value",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=350, margin=dict(l=40, r=20, t=20, b=40),
        )
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.info("Install plotly for charts: pip install plotly")

    st.markdown("---")

    # ── 5. STRIKE LADDER ─────────────────────────────────────────────────────
    st.markdown("### 🪜 Strike Ladder")
    option_positions = [p for p in positions if p["asset_type"] == "option"]

    if not option_positions:
        st.info("No option positions.")
    else:
        # Detect new strikes from alerts
        new_strike_set: set[tuple] = set()
        for a in alerts:
            if a.get("type") in ("new_strikes",):
                new_strike_set.add((a.get("ticker"), a.get("expiry"), a.get("strike")))

        for p in option_positions:
            ticker   = p["ticker"]
            expiry   = p["expiry"]
            strike   = float(p["strike"])
            opt_type = p["option_type"]

            st.markdown(f"**{ticker} {opt_type.upper()} {strike} {expiry}**")

            snap = storage.load_chain_snapshot(ticker, asof)
            if not snap:
                st.caption("No snapshot available.")
                continue

            exp_data = snap.get(expiry, {})
            # collect all strikes for this expiry and type
            all_strikes_data = {}
            for k, v in exp_data.items():
                s = abs(float(k))
                t = v.get("type", "call")
                if t == opt_type:
                    all_strikes_data[s] = v

            if not all_strikes_data:
                st.caption("No chain data for this expiry.")
                continue

            sorted_strikes = sorted(all_strikes_data.keys())
            # find nearest held strike in list
            try:
                idx = min(range(len(sorted_strikes)),
                          key=lambda i: abs(sorted_strikes[i] - strike))
            except ValueError:
                continue

            lo = max(0, idx - 6)
            hi = min(len(sorted_strikes), idx + 7)
            window = sorted_strikes[lo:hi]

            rows = []
            rows.append(
                "<table class='pos-table'><thead><tr>"
                "<th>Strike</th><th>Bid</th><th>Ask</th><th>Last</th>"
                "<th>IV%</th><th>Vol</th><th>OI</th>"
                "</tr></thead><tbody>"
            )
            for s in window:
                d         = all_strikes_data[s]
                is_held   = abs(s - strike) < 0.01
                is_new    = (ticker, expiry, s) in new_strike_set
                style     = ' style="background:#1F3A5F"' if is_held else ""
                badge     = '<span class="new-badge">NEW</span>' if is_new else ""
                rows.append(
                    f"<tr{style}>"
                    f"<td><b>{s:.1f}</b>{badge}</td>"
                    f"<td>{d.get('bid',0):.2f}</td>"
                    f"<td>{d.get('ask',0):.2f}</td>"
                    f"<td>{d.get('last',0):.2f}</td>"
                    f"<td>{d.get('iv',0)*100:.1f}</td>"
                    f"<td>{d.get('volume',0):,}</td>"
                    f"<td>{d.get('oi',0):,}</td>"
                    f"</tr>"
                )
            rows.append("</tbody></table>")
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.markdown("")

    st.markdown("---")

    # ── 6. ALLOCATION ─────────────────────────────────────────────────────────
    st.markdown("### 🥧 Allocation")
    col_a, col_b = st.columns(2)

    try:
        import plotly.graph_objects as go

        by_ticker = alloc.get("by_ticker", {})
        by_sector = alloc.get("by_sector", {})
        flags     = alloc.get("flags", [])

        with col_a:
            st.markdown("**By Ticker**")
            if by_ticker:
                fig_t = go.Figure(go.Bar(
                    x=list(by_ticker.keys()),
                    y=[v["pct"] for v in by_ticker.values()],
                    marker_color=[
                        "#FF4B4B" if v["pct"] > cfg.get("concentration", {}).get("ticker_cap_pct", 40)
                        else "#4B9FFF"
                        for v in by_ticker.values()
                    ],
                    text=[f"{v['pct']:.1f}%" for v in by_ticker.values()],
                    textposition="outside",
                ))
                fig_t.update_layout(
                    template="plotly_dark", paper_bgcolor="#0D1117",
                    plot_bgcolor="#0D1117", yaxis_title="%",
                    height=280, margin=dict(l=20, r=20, t=10, b=40),
                    showlegend=False,
                )
                st.plotly_chart(fig_t, use_container_width=True)

        with col_b:
            st.markdown("**By Sector**")
            if by_sector:
                fig_s = go.Figure(go.Bar(
                    x=list(by_sector.keys()),
                    y=[v["pct"] for v in by_sector.values()],
                    marker_color=[
                        "#FF4B4B" if v["pct"] > cfg.get("concentration", {}).get("sector_cap_pct", 60)
                        else "#00C853"
                        for v in by_sector.values()
                    ],
                    text=[f"{v['pct']:.1f}%" for v in by_sector.values()],
                    textposition="outside",
                ))
                fig_s.update_layout(
                    template="plotly_dark", paper_bgcolor="#0D1117",
                    plot_bgcolor="#0D1117", yaxis_title="%",
                    height=280, margin=dict(l=20, r=20, t=10, b=40),
                    showlegend=False,
                )
                st.plotly_chart(fig_s, use_container_width=True)

        if flags:
            for f in flags:
                st.warning(f)

    except ImportError:
        st.info("Install plotly for charts: pip install plotly")

    st.markdown("---")

    # ── 7. NEWS ───────────────────────────────────────────────────────────────
    st.markdown("### 📰 Claude News Analysis")
    for ticker, nd in news_by_ticker.items():
        if not nd:
            continue
        sentiment  = nd.get("sentiment", "neutral")
        sent_color = _SENTIMENT_COLOR.get(sentiment, "#AAAAAA")
        with st.expander(f"{ticker}  — sentiment: **{sentiment}**", expanded=False):
            st.markdown(f"**Summary:** {nd.get('summary','')}")
            drivers = nd.get("key_drivers", [])
            if drivers:
                st.markdown("**Key drivers:** " + " · ".join(drivers))
            flag = nd.get("position_flag")
            if flag:
                st.markdown(
                    f'<div style="border-left:3px solid {sent_color};'
                    f'padding:4px 10px;background:#161B22">'
                    f'<b>Position flag:</b> {flag}</div>',
                    unsafe_allow_html=True,
                )


if __name__ == "__main__":
    main()
