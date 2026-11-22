"""Layer 3 runner — macro gate + news, stored to SQLite."""
from __future__ import annotations
import datetime
import logging

from layer1.storage import Storage
from .macro_gate import compute_macro_score
from .news import run_news_analysis

logger = logging.getLogger(__name__)


def run_layer3(
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

    logger.info("=== Layer 3: macro gate + news (asof=%s) ===", asof)

    # Macro score
    macro = compute_macro_score(cfg.get("macro_gate", {}))
    storage.save_macro(asof, macro)
    logger.info("Macro score: %.1f", macro["macro_score"])

    # News
    news = run_news_analysis(valued_positions, cfg, asof, storage)
    logger.info("News analysis complete for %d tickers.", len(news))

    return {"macro": macro, "news": news}
