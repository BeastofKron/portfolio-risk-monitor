#!/usr/bin/env python3
"""
Portfolio Monitor — main runner.

Orchestrates Layers 1–4 in sequence:
  Layer 1 : Data pull + valuation + snapshots
  Layer 2 : Portfolio analytics
  Layer 3 : Macro gate + Claude news
  Layer 4 : Alerts + notifier

Usage:
    python main.py                        # run for today
    python main.py --asof 2025-12-19      # backfill / replay date
    python main.py --skip-news            # skip Claude API call
    python main.py --no-notify            # suppress notifier even if enabled

Cron (weekdays after close, 5 PM ET):
    0 17 * * 1-5 cd /path/to/portfolio_monitor && python main.py >> logs/monitor.log 2>&1
"""
from __future__ import annotations
import argparse
import datetime
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")

# Make sure sibling packages resolve correctly when run from any cwd.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from layer1.runner  import run_layer1
from layer1.storage import Storage
from layer2.analytics import run_layer2
from layer3.runner  import run_layer3
from layer4.alerts  import run_alerts
from layer4.notifier import Notifier


def _load_cfg(path: str | None = None) -> dict:
    path = path or os.path.join(_ROOT, "config.json")
    with open(path) as f:
        return json.load(f)


def _load_positions(path: str | None = None) -> list[dict]:
    path = path or os.path.join(_ROOT, "positions.json") if os.path.exists(os.path.join(_ROOT, "positions.json")) else os.path.join(_ROOT, "positions.example.json")
    with open(path) as f:
        data = json.load(f)
    # Auto-generate id if missing
    for i, p in enumerate(data):
        if "id" not in p:
            if p.get("asset_type") == "option":
                p["id"] = f"{p['ticker']}_{p['option_type']}_{p['strike']}_{p['expiry']}"
            else:
                p["id"] = f"{p['ticker']}_shares"
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Portfolio Monitor")
    parser.add_argument("--asof",     default=None,
                        help="Run date YYYY-MM-DD (default: today)")
    parser.add_argument("--config",   default=None,
                        help="Path to config.json")
    parser.add_argument("--positions", default=None,
                        help="Path to positions.json")
    parser.add_argument("--skip-news", action="store_true",
                        help="Skip Claude news API call")
    parser.add_argument("--no-notify", action="store_true",
                        help="Disable notifier even if enabled in config")
    return parser.parse_args()


def main():
    args = parse_args()
    asof = args.asof or datetime.date.today().isoformat()

    cfg       = _load_cfg(args.config)
    positions = _load_positions(args.positions)

    # Patch config if flags are set
    if args.skip_news:
        cfg.setdefault("news", {})["window_days"] = 0   # triggers no-headline path
    if args.no_notify:
        cfg.setdefault("notifier", {})["enabled"] = False

    storage = Storage(
        db_path=cfg.get("db_path", "monitor.db"),
        snapshots_dir=cfg.get("snapshots_dir", "snapshots"),
    )

    logger.info("══════════════════════════════════════════")
    logger.info("  Portfolio Monitor  asof=%s", asof)
    logger.info("══════════════════════════════════════════")

    # ── Layer 1 ──────────────────────────────────────────────────────────────
    valued = run_layer1(positions, cfg, asof=asof, storage=storage)

    # ── Layer 2 ──────────────────────────────────────────────────────────────
    analytics = run_layer2(valued, cfg, asof=asof, storage=storage)

    # ── Layer 3 ──────────────────────────────────────────────────────────────
    layer3_out = run_layer3(valued, cfg, asof=asof, storage=storage)
    news       = layer3_out.get("news", {})
    macro      = layer3_out.get("macro", {})

    # ── Layer 4 : alerts ─────────────────────────────────────────────────────
    alerts = run_alerts(valued, cfg, asof=asof, storage=storage, news=news)

    # ── Layer 4 : notifier ───────────────────────────────────────────────────
    notifier = Notifier(cfg)
    notifier.maybe_send(alerts, asof)

    # ── Summary ──────────────────────────────────────────────────────────────
    alloc  = analytics.get("allocation", {})
    agg_g  = analytics.get("aggregate_greeks", {})
    nav    = alloc.get("total_value", 0)
    pnl    = sum(p.get("unrealized_pnl", 0) for p in valued)

    logger.info("─── Summary ───────────────────────────────")
    logger.info("  NAV:          $%,.2f", nav)
    logger.info("  Unrealized:   $%+,.2f", pnl)
    logger.info("  Net delta:    %+.2f share-equiv", agg_g.get("net_delta_share_equiv", 0))
    logger.info("  Theta/day:    $%+.2f", agg_g.get("total_theta_per_day_usd", 0))
    logger.info("  Macro score:  %.1f / 100", macro.get("macro_score", 0))
    logger.info("  Alerts:       %d", len(alerts))
    for a in alerts:
        logger.info("    [%s] %s", a.get("severity","?").upper(), a.get("message",""))

    dte_flags = analytics.get("dte_flags", [])
    if dte_flags:
        logger.info("  DTE warnings:")
        for f in dte_flags:
            logger.info("    %s", f["message"])

    conc_flags = alloc.get("flags", [])
    if conc_flags:
        logger.info("  Concentration warnings:")
        for f in conc_flags:
            logger.info("    %s", f)

    logger.info("  Run complete. Launch dashboard:")
    logger.info("    streamlit run layer4/dashboard.py -- --asof %s", asof)
    logger.info("══════════════════════════════════════════")

    storage.close()


if __name__ == "__main__":
    main()
