"""
Layer 4 – Alerts engine.

Two alert sources:
 A) Chain diff: new strikes / new expiries vs prior snapshot.
 B) Condition alerts (surface facts, never buy/sell):
    - target_hit   (high)  : mark crossed target_price
    - stop_hit     (high)  : mark crossed stop_price
    - target_near  (info)  : within N% of target (default 80%)
    - iv_change    (warn)  : IV moved ≥ N% vs prior snapshot
    - new_strikes  (info)  : from chain diff
    - new_expiry   (info)  : from chain diff

All alerts are plain-language facts. No buy/sell language.
"""
from __future__ import annotations
import datetime
import logging
from typing import Any

from layer1.storage import Storage

logger = logging.getLogger(__name__)

# ── Chain diff helpers ────────────────────────────────────────────────────────

def _diff_chains(current_chain: dict, prior_chain: dict,
                 held_strikes: set[float], band_pct: float) -> dict:
    """
    Compare current vs prior full option chain for one ticker.
    Returns {"new_expiries": [...], "new_strikes": {expiry: [...]}}
    """
    new_expiries = []
    new_strikes: dict[str, list[float]] = {}

    current_expiries = set(current_chain.keys())
    prior_expiries   = set(prior_chain.keys()) if prior_chain else set()

    for exp in current_expiries - prior_expiries:
        new_expiries.append(exp)

    for exp in current_expiries & prior_expiries:
        current_strikes = {abs(float(k)) for k in current_chain[exp]}
        prior_strikes   = {abs(float(k)) for k in prior_chain.get(exp, {})}
        added = current_strikes - prior_strikes
        # filter to band around any held strike
        nearby = set()
        for s in added:
            for hs in held_strikes:
                if hs > 0 and abs(s - hs) / hs * 100 <= band_pct:
                    nearby.add(s)
        if nearby:
            new_strikes[exp] = sorted(nearby)

    return {"new_expiries": sorted(new_expiries), "new_strikes": new_strikes}


def run_chain_diff(
    positions: list[dict],
    cfg: dict,
    asof: str,
    storage: Storage,
) -> list[dict]:
    """Produce new_strike / new_expiry alerts from chain diff."""
    band_pct = cfg.get("alerts", {}).get("strike_band_pct", 30)
    alerts   = []

    tickers = list({p["ticker"] for p in positions if p["asset_type"] == "option"})

    for ticker in tickers:
        # held strikes for this ticker
        held_strikes = {
            float(p["strike"]) for p in positions
            if p["ticker"] == ticker and p["asset_type"] == "option"
        }

        # current snapshot
        current_snap = storage.load_chain_snapshot(ticker, asof)
        if not current_snap:
            continue

        # prior snapshot
        prior = storage.load_latest_chain_snapshot(ticker, asof)
        if not prior:
            continue
        _, prior_chain = prior

        diff = _diff_chains(current_snap, prior_chain, held_strikes, band_pct)

        for exp in diff["new_expiries"]:
            alerts.append({
                "severity": "info",
                "type":     "new_expiry",
                "ticker":   ticker,
                "message":  f"{ticker}: new expiry listed — {exp}",
                "expiry":   exp,
            })

        for exp, strikes in diff["new_strikes"].items():
            for s in strikes:
                alerts.append({
                    "severity": "info",
                    "type":     "new_strikes",
                    "ticker":   ticker,
                    "message":  f"{ticker}: new strike {s} listed for {exp} "
                                f"(within {band_pct}% of a held strike)",
                    "expiry":   exp,
                    "strike":   s,
                })

    return alerts


# ── Condition alerts ──────────────────────────────────────────────────────────

def run_condition_alerts(
    valued_positions: list[dict],
    cfg: dict,
    asof: str,
    storage: Storage,
) -> list[dict]:
    alert_cfg  = cfg.get("alerts", {})
    near_pct   = alert_cfg.get("target_near_pct", 80)
    iv_chg_pct = alert_cfg.get("iv_change_pct", 20)
    alerts     = []

    for p in valued_positions:
        ticker   = p["ticker"]
        mark     = p.get("mark", 0.0)
        entry    = float(p.get("entry_price", 0.0))
        target   = p.get("target_price")
        stop     = p.get("stop_price")
        target   = float(target) if target is not None else None
        stop     = float(stop)   if stop   is not None else None

        label = (f"{ticker} {p['option_type']} {p['strike']} {p['expiry']}"
                 if p["asset_type"] == "option"
                 else f"{ticker} shares")

        # target_hit
        if target is not None and mark >= target:
            alerts.append({
                "severity": "high",
                "type":     "target_hit",
                "ticker":   ticker,
                "message":  f"{label} hit your target level (mark={mark:.2f} ≥ target={target:.2f})",
                "mark":     mark,
                "target":   target,
            })

        # stop_hit
        elif stop is not None and mark <= stop:
            alerts.append({
                "severity": "high",
                "type":     "stop_hit",
                "ticker":   ticker,
                "message":  f"{label} hit your stop level (mark={mark:.2f} ≤ stop={stop:.2f})",
                "mark":     mark,
                "stop":     stop,
            })

        # target_near (only if not already hit)
        elif target is not None and target != entry:
            progress = (mark - entry) / (target - entry) * 100 if (target - entry) != 0 else 0
            if progress >= near_pct:
                alerts.append({
                    "severity": "info",
                    "type":     "target_near",
                    "ticker":   ticker,
                    "message":  f"{label} is {progress:.0f}% of the way to your target "
                                f"(mark={mark:.2f}, target={target:.2f})",
                    "mark":     mark,
                    "target":   target,
                    "progress": round(progress, 1),
                })

        # iv_change (options only)
        if p["asset_type"] == "option":
            current_iv = p.get("iv", 0.0)
            if current_iv > 0:
                prior = storage.load_latest_chain_snapshot(ticker, asof)
                if prior:
                    _, prior_chain = prior
                    expiry   = p["expiry"]
                    strike   = float(p["strike"])
                    opt_type = p["option_type"]
                    key      = strike if opt_type == "call" else -strike
                    exp_data = prior_chain.get(expiry, {})
                    contract = exp_data.get(str(key)) or exp_data.get(key)
                    if contract and contract.get("iv", 0) > 0:
                        prior_iv    = float(contract["iv"])
                        chg_pct     = abs(current_iv - prior_iv) / prior_iv * 100
                        if chg_pct >= iv_chg_pct:
                            direction = "up" if current_iv > prior_iv else "down"
                            alerts.append({
                                "severity": "warn",
                                "type":     "iv_change",
                                "ticker":   ticker,
                                "message":  f"{label} IV moved {direction} "
                                            f"{chg_pct:.0f}% "
                                            f"({prior_iv*100:.1f}% → {current_iv*100:.1f}%)",
                                "iv_current": round(current_iv * 100, 1),
                                "iv_prior":   round(prior_iv * 100, 1),
                                "iv_chg_pct": round(chg_pct, 1),
                            })

    return alerts


# ── Main runner ───────────────────────────────────────────────────────────────

def run_alerts(
    valued_positions: list[dict],
    cfg: dict,
    asof: str | None = None,
    storage: Storage | None = None,
    news: dict | None = None,
) -> list[dict]:
    if asof is None:
        asof = datetime.date.today().isoformat()
    if storage is None:
        storage = Storage(
            db_path=cfg.get("db_path", "monitor.db"),
            snapshots_dir=cfg.get("snapshots_dir", "snapshots"),
        )

    logger.info("=== Layer 4: generating alerts (asof=%s) ===", asof)

    alerts: list[dict] = []

    # A) Chain diff
    alerts += run_chain_diff(valued_positions, cfg, asof, storage)

    # B) Condition alerts
    alerts += run_condition_alerts(valued_positions, cfg, asof, storage)

    # C) Surface news position_flags as alerts
    if news:
        for ticker, nd in news.items():
            flag = nd.get("position_flag")
            if flag:
                sentiment = nd.get("sentiment", "neutral")
                severity  = "warn" if sentiment == "negative" else "info"
                alerts.append({
                    "severity": severity,
                    "type":     "news_flag",
                    "ticker":   ticker,
                    "message":  f"{ticker} news flag: {flag}",
                    "sentiment": sentiment,
                })

    # Sort: high first
    priority = {"high": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: priority.get(a.get("severity", "info"), 2))

    storage.save_alerts(asof, alerts)
    logger.info("Generated %d alerts.", len(alerts))
    return alerts
