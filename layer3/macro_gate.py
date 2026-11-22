"""
Layer 3 – Macro Gate (deterministic, 0–100).

Score components (weights sum to 1.0, configurable):
  vix_level     : VIX vs its 1-year percentile  (low VIX → bullish → higher score)
  vix_term      : VIX vs VIX3M  (contango → bullish → higher score)
  breadth       : % SPY constituents above 200-day MA  (proxy via yfinance)
  credit_spread : HYG/TLT spread  (tight spread → bullish → higher score)

All components are normalised 0–100 independently, then blended.
Same inputs → same output.
"""
from __future__ import annotations
import logging
from typing import Any

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _vix_score(lookback_days: int = 252) -> dict:
    """
    VIX level score:
      - Fetch VIX history for lookback period.
      - Current VIX percentile among that history.
      - LOW vix percentile → low fear → high score.
      - Score = 100 - percentile_rank(VIX current in history).
    """
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period=f"{lookback_days + 10}d")["Close"].dropna()
        if hist.empty:
            return {"score": 50.0, "vix": None, "vix_pct_rank": None, "note": "no data"}
        current = float(hist.iloc[-1])
        rank = float((hist < current).sum() / len(hist) * 100)
        score = _clamp(100.0 - rank)
        return {"score": score, "vix": round(current, 2), "vix_pct_rank": round(rank, 1)}
    except Exception as e:
        logger.warning("VIX level fetch failed: %s", e)
        return {"score": 50.0, "vix": None, "vix_pct_rank": None, "note": str(e)}


def _vix_term_score() -> dict:
    """
    VIX vs VIX3M (VVIX proxy).
    VIX < VIX3M → contango → healthy → score closer to 100.
    Ratio = VIX / VIX3M. Score = 100 when ratio < 0.8, 0 when > 1.2, linear between.
    """
    try:
        vix  = float(yf.Ticker("^VIX").fast_info.last_price or 0)
        vx3m = float(yf.Ticker("^VIX3M").fast_info.last_price or 0)
        if vix == 0 or vx3m == 0:
            return {"score": 50.0, "vix": vix, "vix3m": vx3m, "note": "zero price"}
        ratio = vix / vx3m
        # linear: ratio=0.8 → score=100, ratio=1.2 → score=0
        score = _clamp((1.2 - ratio) / 0.4 * 100)
        return {"score": score, "vix": round(vix, 2), "vix3m": round(vx3m, 2),
                "ratio": round(ratio, 4)}
    except Exception as e:
        logger.warning("VIX term fetch failed: %s", e)
        return {"score": 50.0, "note": str(e)}


def _breadth_score(proxy_ticker: str = "SPY") -> dict:
    """
    Breadth proxy: % of SPY price above its own 200-day MA.
    (Full constituent scan is impractical; use SPY itself as a proxy:
     score 100 if SPY > 200dMA, 0 if not, scaled by how far it is.)
    For more accuracy the user can swap in a breadth ETF like $MMOH.
    """
    try:
        tk   = yf.Ticker(proxy_ticker)
        hist = tk.history(period="220d")["Close"].dropna()
        if len(hist) < 200:
            return {"score": 50.0, "note": "insufficient history"}
        ma200   = float(hist.iloc[-200:].mean())
        current = float(hist.iloc[-1])
        # pct distance from 200MA
        distance_pct = (current - ma200) / ma200 * 100
        # score: +10% above → 100, -10% below → 0, linear
        score = _clamp((distance_pct + 10) / 20 * 100)
        return {
            "score":        score,
            "proxy":        proxy_ticker,
            "spot":         round(current, 2),
            "ma200":        round(ma200, 2),
            "distance_pct": round(distance_pct, 2),
        }
    except Exception as e:
        logger.warning("Breadth fetch failed: %s", e)
        return {"score": 50.0, "note": str(e)}


def _credit_score() -> dict:
    """
    Credit spread: HYG (high-yield) vs TLT (long treasury).
    Ratio HYG/TLT rising (credit tightening relative to govts) → bullish.
    Use rolling 252-day to normalise.
    Score = percentile rank of ratio (high ratio → tight spread → high score).
    """
    try:
        hyg  = yf.Ticker("HYG").history(period="270d")["Close"].dropna()
        tlt  = yf.Ticker("TLT").history(period="270d")["Close"].dropna()
        combined = pd.concat([hyg.rename("hyg"), tlt.rename("tlt")], axis=1).dropna()
        if len(combined) < 20:
            return {"score": 50.0, "note": "insufficient history"}
        ratio   = combined["hyg"] / combined["tlt"]
        current = float(ratio.iloc[-1])
        rank    = float((ratio < current).sum() / len(ratio) * 100)
        score   = _clamp(rank)   # high ratio → bullish
        return {
            "score":     score,
            "hyg_tlt_ratio": round(current, 4),
            "pct_rank":  round(rank, 1),
        }
    except Exception as e:
        logger.warning("Credit fetch failed: %s", e)
        return {"score": 50.0, "note": str(e)}


def compute_macro_score(cfg: dict) -> dict:
    """
    Compute blended macro score 0-100.
    Returns full breakdown dict.
    """
    weights = cfg.get("weights", {
        "vix_level":     0.30,
        "vix_term":      0.25,
        "breadth":       0.25,
        "credit_spread": 0.20,
    })
    lookback = cfg.get("vix_lookback_days", 252)
    proxy    = cfg.get("breadth_proxy_ticker", "SPY")

    components = {
        "vix_level":     _vix_score(lookback),
        "vix_term":      _vix_term_score(),
        "breadth":       _breadth_score(proxy),
        "credit_spread": _credit_score(),
    }

    blended = 0.0
    for name, comp in components.items():
        w = weights.get(name, 0.0)
        blended += comp["score"] * w

    return {
        "macro_score": round(blended, 1),
        "weights":     weights,
        "components":  components,
    }
