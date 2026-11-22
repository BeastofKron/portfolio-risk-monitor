"""
Layer 2 - Portfolio Analytics.

Inputs:  valued positions from Layer 1 + Storage for IV history.
Outputs: analytics dict saved to SQLite and returned.
"""
from __future__ import annotations
import datetime
import logging
from typing import Any

import yfinance as yf

from layer1.storage import Storage

logger = logging.getLogger(__name__)

OPTION_MULTIPLIER = 100


def _get_sector(ticker: str, sector_map: dict) -> str:
    if ticker in sector_map:
        return sector_map[ticker]
    try:
        info = yf.Ticker(ticker).info
        return info.get("sector", "Unknown") or "Unknown"
    except Exception:
        return "Unknown"


def compute_allocation(
    positions: list[dict],
    sector_map: dict,
    ticker_cap: float,
    sector_cap: float,
) -> dict:
    total_value = sum(p.get("current_value", 0) for p in positions)
    if total_value == 0:
        return {"by_ticker": {}, "by_sector": {}, "flags": [], "total_value": 0}

    by_ticker: dict = {}
    by_sector: dict = {}

    for p in positions:
        ticker = p["ticker"]
        val    = p.get("current_value", 0)
        sector = _get_sector(ticker, sector_map)
        by_ticker.setdefault(ticker, {"value": 0.0, "sector": sector})
        by_ticker[ticker]["value"] += val
        by_sector[sector] = by_sector.get(sector, 0) + val

    flags = []
    for ticker, td in by_ticker.items():
        td["pct"] = round(td["value"] / total_value * 100, 1)
        if td["pct"] > ticker_cap:
            flags.append(
                f"CONCENTRATION: {ticker} is {td['pct']:.1f}% of book (cap {ticker_cap}%)"
            )

    sector_out: dict = {}
    for sector, val in by_sector.items():
        pct = round(val / total_value * 100, 1)
        sector_out[sector] = {"value": round(val, 2), "pct": pct}
        if pct > sector_cap:
            flags.append(
                f"CONCENTRATION: sector '{sector}' is {pct:.1f}% of book (cap {sector_cap}%)"
            )

    return {
        "by_ticker":   {t: {**d, "value": round(d["value"], 2)} for t, d in by_ticker.items()},
        "by_sector":   sector_out,
        "flags":       flags,
        "total_value": round(total_value, 2),
    }


def compute_aggregate_greeks(positions: list[dict]) -> dict:
    net_delta   = 0.0
    total_theta = 0.0
    net_vega    = 0.0

    for p in positions:
        g         = p.get("greeks", {})
        contracts = int(p.get("contracts", 0))
        mult      = int(p.get("multiplier", 1))
        net_delta   += g.get("delta", 0) * contracts * mult
        total_theta += g.get("theta", 0) * contracts * mult
        net_vega    += g.get("vega",  0) * contracts * mult

    return {
        "net_delta_share_equiv":   round(net_delta,   4),
        "total_theta_per_day_usd": round(total_theta, 2),
        "net_vega_per_iv_point":   round(net_vega,    2),
    }


def compute_iv_environment(
    positions: list[dict],
    storage: Storage,
    lookback_days: int,
    min_days: int,
    rich_threshold: float,
    cheap_threshold: float,
) -> list[dict]:
    results = []
    for p in positions:
        if p["asset_type"] != "option":
            continue
        ticker   = p["ticker"]
        expiry   = p["expiry"]
        strike   = float(p["strike"])
        opt_type = p["option_type"]
        iv       = p.get("iv", 0.0)
        if iv == 0:
            continue

        history = storage.load_iv_history(ticker, expiry, strike, opt_type, lookback_days)

        if len(history) < min_days:
            label   = "building history"
            iv_rank = None
            iv_pct  = None
        else:
            lo, hi  = min(history), max(history)
            iv_rank = round((iv - lo) / (hi - lo) * 100, 1) if hi != lo else 50.0
            below   = sum(1 for x in history if x < iv)
            iv_pct  = round(below / len(history) * 100, 1)
            if iv_rank > rich_threshold:
                label = "rich"
            elif iv_rank < cheap_threshold:
                label = "cheap"
            else:
                label = "fair"

        results.append({
            "ticker":   ticker,
            "expiry":   expiry,
            "strike":   strike,
            "opt_type": opt_type,
            "iv":       round(iv * 100, 1),
            "iv_rank":  iv_rank,
            "iv_pct":   iv_pct,
            "label":    label,
        })
    return results


def compute_dte_flags(positions: list[dict], warning_days: int) -> list[dict]:
    flags = []
    for p in positions:
        if p["asset_type"] != "option":
            continue
        dte = p.get("dte")
        if dte is None:
            continue
        if dte <= warning_days:
            flags.append({
                "ticker":   p["ticker"],
                "expiry":   p["expiry"],
                "strike":   p["strike"],
                "opt_type": p["option_type"],
                "dte":      dte,
                "message":  (
                    f"{p['ticker']} {p['option_type']} {p['strike']} "
                    f"expires in {dte} days ({p['expiry']})"
                ),
            })
    return flags


def run_layer2(
    valued_positions: list[dict],
    cfg: dict,
    asof: str | None = None,
    storage: Storage | None = None,
) -> dict:
    if asof is None:
        asof = datetime.date.today().isoformat()
    if storage is None:
        storage = Storage(
            db_path=cfg.get("db_path", "monitor.db"),
            snapshots_dir=cfg.get("snapshots_dir", "snapshots"),
        )

    logger.info("=== Layer 2: portfolio analytics (asof=%s) ===", asof)

    sector_map = cfg.get("sector_map", {})
    conc       = cfg.get("concentration", {})
    iv_cfg     = cfg.get("iv_rank", {})
    dte_warn   = cfg.get("dte_warning_days", 45)

    allocation = compute_allocation(
        valued_positions, sector_map,
        ticker_cap=conc.get("ticker_cap_pct", 40),
        sector_cap=conc.get("sector_cap_pct", 60),
    )
    agg_greeks = compute_aggregate_greeks(valued_positions)
    iv_env     = compute_iv_environment(
        valued_positions, storage,
        lookback_days=iv_cfg.get("lookback_days", 252),
        min_days=iv_cfg.get("min_days", 20),
        rich_threshold=iv_cfg.get("rich_threshold", 70),
        cheap_threshold=iv_cfg.get("cheap_threshold", 30),
    )
    dte_flags  = compute_dte_flags(valued_positions, dte_warn)

    analytics = {
        "asof":             asof,
        "allocation":       allocation,
        "aggregate_greeks": agg_greeks,
        "iv_environment":   iv_env,
        "dte_flags":        dte_flags,
    }

    storage.save_analytics(asof, analytics)
    logger.info(
        "Analytics: total_value=$%.2f  net_delta=%.2f  theta/day=$%.2f  dte_flags=%d",
        allocation["total_value"],
        agg_greeks["net_delta_share_equiv"],
        agg_greeks["total_theta_per_day_usd"],
        len(dte_flags),
    )

    return analytics
