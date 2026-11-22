"""
Layer 1 runner — orchestrates data pull → valuation → storage.
Returns valued positions list for use by later layers.
"""
from __future__ import annotations
import datetime
import json
import logging
import os
from typing import Any

from .data_pull import fetch_positions_data
from .valuation import value_positions
from .storage import Storage

logger = logging.getLogger(__name__)


def run_layer1(
    positions: list[dict],
    cfg: dict,
    asof: str | None = None,
    storage: Storage | None = None,
) -> list[dict]:
    """
    Pull market data, value positions, save snapshots.
    Returns list of valued position dicts.
    """
    if asof is None:
        asof = datetime.date.today().isoformat()

    if storage is None:
        storage = Storage(
            db_path=cfg.get("db_path", "monitor.db"),
            snapshots_dir=cfg.get("snapshots_dir", "snapshots"),
        )

    # 1. Fetch market data
    logger.info("=== Layer 1: fetching market data (asof=%s) ===", asof)
    ticker_data = fetch_positions_data(positions, asof=asof)
    enriched    = ticker_data.pop("__positions__")

    # 2. Save chain snapshots (per underlying)
    for ticker, td in ticker_data.items():
        storage.save_chain_snapshot(ticker, asof, td["chain"])
        logger.info("Snapshot saved: %s @ %s", ticker, asof)

    # 3. Value positions
    valued = value_positions(
        enriched,
        risk_free_rate=cfg.get("risk_free_rate", 0.045),
        asof=asof,
    )

    # 4. Persist
    storage.save_positions_valued(asof, valued)
    logger.info("Valued %d positions, stored to DB.", len(valued))

    return valued
