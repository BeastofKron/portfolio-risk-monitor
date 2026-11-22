"""
Layer 1 – Data pull via yfinance.

For each underlying ticker in the position book:
  - Fetch spot price
  - Fetch the full option chain (all expiries)
  - Match each held option contract: bid, ask, last, IV, volume, open interest
  - For shares: mark = spot

Returns a dict keyed by ticker with structure:
  {
    "spot": float,
    "chain": { expiry: { strike: {bid,ask,last,iv,volume,oi} } },
    "held_contracts": [ { ...position fields..., mark, bid, ask, last, iv, volume, oi } ]
  }
"""
from __future__ import annotations
import datetime
import logging
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)


def _mid_or_last(bid: float, ask: float, last: float) -> float:
    """Return mid when both sides exist, otherwise last."""
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return last if last > 0 else 0.0


def fetch_chain_for_ticker(ticker: str) -> dict[str, Any]:
    """
    Fetch spot + full option chain for one ticker.
    Returns {"spot": float, "chain": {expiry_str: {strike: {...}}}}
    """
    tk = yf.Ticker(ticker)

    # Spot
    try:
        info = tk.fast_info
        spot = float(info.last_price or 0.0)
    except Exception:
        spot = 0.0

    if spot == 0.0:
        try:
            hist = tk.history(period="1d")
            if not hist.empty:
                spot = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    # Option chain
    chain: dict[str, dict] = {}
    try:
        expiries = tk.options  # tuple of expiry strings YYYY-MM-DD
    except Exception:
        expiries = []

    for exp in expiries:
        try:
            opt = tk.option_chain(exp)
            exp_data: dict[float, dict] = {}
            for row in opt.calls.itertuples(index=False):
                exp_data[float(row.strike)] = {
                    "type": "call",
                    "bid": float(row.bid or 0),
                    "ask": float(row.ask or 0),
                    "last": float(row.lastPrice or 0),
                    "iv": float(row.impliedVolatility or 0),
                    "volume": int(row.volume or 0),
                    "oi": int(row.openInterest or 0),
                }
            for row in opt.puts.itertuples(index=False):
                key = -float(row.strike)   # negative to distinguish puts at same strike
                exp_data[key] = {
                    "type": "put",
                    "strike": float(row.strike),
                    "bid": float(row.bid or 0),
                    "ask": float(row.ask or 0),
                    "last": float(row.lastPrice or 0),
                    "iv": float(row.impliedVolatility or 0),
                    "volume": int(row.volume or 0),
                    "oi": int(row.openInterest or 0),
                }
            chain[exp] = exp_data
        except Exception as e:
            logger.warning("Could not fetch chain for %s %s: %s", ticker, exp, e)

    return {"spot": spot, "chain": chain}


def match_option_contract(
    position: dict,
    chain_data: dict[str, Any],
) -> dict:
    """
    Given a position (option), find its contract in the chain and return market data.
    """
    expiry = position["expiry"]          # YYYY-MM-DD
    strike = float(position["strike"])
    opt_type = position["option_type"]   # "call" or "put"

    result = dict(bid=0.0, ask=0.0, last=0.0, iv=0.0, volume=0, oi=0, mark=0.0)

    exp_data = chain_data.get("chain", {}).get(expiry, {})
    if not exp_data:
        logger.warning("No chain data for %s expiry %s", position.get("ticker"), expiry)
        return result

    # Calls stored under positive strike, puts under negative
    lookup_key = strike if opt_type == "call" else -strike
    contract = exp_data.get(lookup_key)

    if contract is None:
        # Fallback: nearest strike
        candidates = {k: v for k, v in exp_data.items()
                      if v.get("type") == opt_type}
        if candidates:
            lookup_key = min(candidates, key=lambda k: abs(abs(k) - strike))
            contract = candidates[lookup_key]
            logger.info("Used nearest strike %.2f for %s %s %s",
                        abs(lookup_key), position.get("ticker"), opt_type, expiry)

    if contract:
        result.update(
            bid=contract["bid"],
            ask=contract["ask"],
            last=contract["last"],
            iv=contract["iv"],
            volume=contract["volume"],
            oi=contract["oi"],
        )
        result["mark"] = _mid_or_last(contract["bid"], contract["ask"], contract["last"])

    return result


def fetch_positions_data(
    positions: list[dict],
    asof: str | None = None,
) -> dict[str, Any]:
    """
    Master data-pull entry point.

    Returns:
        {
          ticker: {
            "spot": float,
            "chain": { expiry: { key: {...} } },
          },
          "__positions__": [ enriched position dicts ]
        }
    """
    tickers = list({p["ticker"] for p in positions})
    ticker_data: dict[str, Any] = {}

    for ticker in tickers:
        logger.info("Fetching data for %s …", ticker)
        ticker_data[ticker] = fetch_chain_for_ticker(ticker)

    enriched = []
    for pos in positions:
        p = dict(pos)
        ticker = p["ticker"]
        td = ticker_data.get(ticker, {})
        spot = td.get("spot", 0.0)

        if p["asset_type"] == "option":
            market = match_option_contract(p, td)
            p.update(market)
        else:
            # shares
            p["mark"]   = spot
            p["bid"]    = 0.0
            p["ask"]    = 0.0
            p["last"]   = spot
            p["iv"]     = 0.0
            p["volume"] = 0
            p["oi"]     = 0

        p["spot"] = spot
        enriched.append(p)

    ticker_data["__positions__"] = enriched
    return ticker_data
