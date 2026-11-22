"""
Layer 1 – Valuation.

Takes enriched positions (from data_pull) and computes:
  - current_value   : mark * contracts * multiplier
  - cost_basis      : entry_price * contracts * multiplier
  - unrealized_pnl  : current_value - cost_basis
  - unrealized_pnl_pct
  - dte             : days to expiry (options only)
  - progress_to_target_pct
  - progress_to_stop_pct
  - Greeks (options only, via Black-Scholes)
"""
from __future__ import annotations
import datetime
from typing import Any

from .greeks import black_scholes_greeks

OPTION_MULTIPLIER = 100   # 1 contract = 100 shares


def _dte(expiry_str: str, asof: str | None = None) -> float:
    """Calendar days until expiry from asof (or today)."""
    base = (datetime.date.fromisoformat(asof)
            if asof else datetime.date.today())
    exp  = datetime.date.fromisoformat(expiry_str)
    return max((exp - base).days, 0)


def _progress(entry: float, current: float, target: float) -> float:
    """
    How far current has moved from entry toward target, as a percent.
    Returns None if target not set.
    """
    if target is None or entry is None:
        return None
    rng = target - entry
    if rng == 0:
        return None
    return round((current - entry) / rng * 100.0, 1)


def value_positions(
    enriched_positions: list[dict],
    risk_free_rate: float = 0.045,
    asof: str | None = None,
) -> list[dict]:
    """
    Attach valuation fields to each position.
    Returns the list with added keys (in-place copy).
    """
    results = []
    for pos in enriched_positions:
        p = dict(pos)
        mark        = p.get("mark", 0.0)
        entry_price = float(p.get("entry_price", 0.0))
        contracts   = int(p.get("contracts", 0))
        target      = p.get("target_price")
        stop        = p.get("stop_price")
        target      = float(target) if target is not None else None
        stop        = float(stop)   if stop   is not None else None

        if p["asset_type"] == "option":
            mult           = OPTION_MULTIPLIER
            dte            = _dte(p["expiry"], asof)
            p["dte"]       = dte
            iv             = p.get("iv", 0.0)
            spot           = p.get("spot", 0.0)
            strike         = float(p["strike"])
            opt_type       = p["option_type"]
            greeks         = black_scholes_greeks(
                opt_type, spot, strike, dte, iv, risk_free_rate
            )
            p["greeks"]    = greeks
            # Use BS price if market mark is 0 (e.g., expired chain)
            if mark == 0.0 and greeks["price"] > 0:
                mark = greeks["price"]
                p["mark"] = mark
        else:
            mult      = 1
            p["dte"]  = None
            p["greeks"] = dict(price=mark, delta=1.0, gamma=0.0, theta=0.0, vega=0.0)

        p["multiplier"]         = mult
        p["current_value"]      = round(mark * contracts * mult, 2)
        p["cost_basis"]         = round(entry_price * contracts * mult, 2)
        p["unrealized_pnl"]     = round(p["current_value"] - p["cost_basis"], 2)
        pnl_pct = (
            (p["unrealized_pnl"] / p["cost_basis"] * 100.0)
            if p["cost_basis"] != 0 else 0.0
        )
        p["unrealized_pnl_pct"] = round(pnl_pct, 2)

        p["progress_to_target_pct"] = _progress(entry_price, mark, target)
        p["progress_to_stop_pct"]   = _progress(entry_price, mark, stop)

        results.append(p)

    return results
