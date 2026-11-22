"""
Layer 3 – Claude News Analysis.

Per held ticker:
  1. Pull recent headlines via yfinance .news (configurable window_days).
  2. Send to Claude API → returns: summary, sentiment, key_drivers, position_flag.
  3. Cache result in SQLite (keyed by ticker + asof) so re-runs don't re-bill.

This is the ONLY part of the system that calls the Anthropic API.
"""
from __future__ import annotations
import datetime
import json
import logging
import os
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)

# Anthropic client is imported lazily so the rest of the system works
# even if the package isn't installed.
def _get_anthropic_client():
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "News analysis disabled."
            )
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        )


_SYSTEM_PROMPT = (
    "You are a concise equity analyst assistant. "
    "You receive recent news headlines for a stock ticker that a user holds "
    "as part of an options or equity portfolio. "
    "Respond ONLY with valid JSON — no markdown fences, no preamble."
)

_USER_TEMPLATE = """
Ticker: {ticker}
Position: {position_summary}
Recent headlines ({window_days} days):

{headlines}

Return a JSON object with these exact keys:
- "summary": string (2-3 sentences on what actually happened)
- "sentiment": one of "positive", "neutral", "negative"
- "key_drivers": list of up to 3 short strings naming the main drivers
- "position_flag": string or null — if any headline directly affects the
  held position (e.g. earnings beat for a call, macro headwind for a put),
  describe the impact in one sentence; else null
"""


def _position_summary(positions: list[dict], ticker: str) -> str:
    parts = []
    for p in positions:
        if p["ticker"] != ticker:
            continue
        if p["asset_type"] == "option":
            parts.append(
                f"{p['contracts']} {p['option_type']} contract(s) "
                f"strike={p['strike']} expiry={p['expiry']}"
            )
        else:
            parts.append(f"{p['contracts']} shares")
    return "; ".join(parts) or "unspecified"


def _fetch_headlines(ticker: str, window_days: int) -> list[str]:
    """Pull yfinance news headlines within the window."""
    try:
        tk    = yf.Ticker(ticker)
        news  = tk.news or []
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=window_days)
        headlines = []
        for item in news:
            ts = item.get("providerPublishTime") or item.get("content", {}).get("pubDate")
            if isinstance(ts, int):
                pub = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            elif isinstance(ts, str):
                try:
                    pub = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    pub = cutoff   # assume in window if can't parse
            else:
                pub = cutoff
            if pub >= cutoff:
                title = (item.get("title")
                         or item.get("content", {}).get("title", ""))
                if title:
                    headlines.append(title)
        return headlines[:20]   # cap to keep prompt short
    except Exception as e:
        logger.warning("Could not fetch headlines for %s: %s", ticker, e)
        return []


def analyze_news_for_ticker(
    ticker: str,
    positions: list[dict],
    model: str,
    window_days: int,
    client=None,
) -> dict:
    """Call Claude API and return structured news dict."""
    headlines = _fetch_headlines(ticker, window_days)
    if not headlines:
        return {
            "ticker":        ticker,
            "summary":       "No recent headlines found.",
            "sentiment":     "neutral",
            "key_drivers":   [],
            "position_flag": None,
            "headlines_used": 0,
        }

    pos_summary = _position_summary(positions, ticker)
    user_msg    = _USER_TEMPLATE.format(
        ticker=ticker,
        position_summary=pos_summary,
        window_days=window_days,
        headlines="\n".join(f"- {h}" for h in headlines),
    )

    if client is None:
        client = _get_anthropic_client()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "summary":       raw[:300],
            "sentiment":     "neutral",
            "key_drivers":   [],
            "position_flag": None,
        }
    except Exception as e:
        logger.error("Claude API error for %s: %s", ticker, e)
        parsed = {
            "summary":       f"API error: {e}",
            "sentiment":     "neutral",
            "key_drivers":   [],
            "position_flag": None,
        }

    parsed["ticker"]         = ticker
    parsed["headlines_used"] = len(headlines)
    return parsed


def run_news_analysis(
    positions: list[dict],
    cfg: dict,
    asof: str,
    storage,
) -> dict[str, dict]:
    """
    Run news analysis for all unique tickers in positions.
    Uses daily cache — won't re-call the API if already cached for asof.
    Returns {ticker: news_dict}.
    """
    news_cfg    = cfg.get("news", {})
    model       = news_cfg.get("anthropic_model", "claude-haiku-4-5-20251001")
    window_days = news_cfg.get("window_days", 3)

    tickers = list({p["ticker"] for p in positions})
    results: dict[str, dict] = {}
    client  = None   # lazy-init once

    for ticker in tickers:
        cached = storage.load_news(ticker, asof)
        if cached:
            logger.info("News cache hit: %s @ %s", ticker, asof)
            results[ticker] = cached
            continue

        logger.info("Calling Claude API for news: %s", ticker)
        try:
            if client is None:
                client = _get_anthropic_client()
            result = analyze_news_for_ticker(
                ticker, positions, model, window_days, client
            )
        except RuntimeError as e:
            logger.error("News disabled: %s", e)
            result = {
                "ticker":        ticker,
                "summary":       str(e),
                "sentiment":     "neutral",
                "key_drivers":   [],
                "position_flag": None,
                "headlines_used": 0,
            }

        storage.save_news(ticker, asof, result)
        results[ticker] = result

    return results
