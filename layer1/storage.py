"""
Layer 1 – Storage.

- SQLite database (single file, all layers write here)
- JSON snapshots: snapshots/TICKER_YYYY-MM-DD.json  (full chain per run)

Schema
------
chain_snapshots  : ticker, asof, data_json          (full chain)
positions_valued : asof, data_json                  (valued positions list)
analytics        : asof, data_json                  (layer 2 output)
macro_scores     : asof, data_json                  (layer 3 macro)
news_cache       : ticker, asof, data_json          (layer 3 news)
alerts           : asof, severity, alert_type, ticker, message, data_json
"""
from __future__ import annotations
import datetime
import json
import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS chain_snapshots (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    asof    TEXT NOT NULL,
    data    TEXT NOT NULL,
    UNIQUE(ticker, asof)
);

CREATE TABLE IF NOT EXISTS positions_valued (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asof    TEXT NOT NULL UNIQUE,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asof    TEXT NOT NULL UNIQUE,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macro_scores (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asof    TEXT NOT NULL UNIQUE,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_cache (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    asof    TEXT NOT NULL,
    data    TEXT NOT NULL,
    UNIQUE(ticker, asof)
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asof       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    ticker     TEXT,
    message    TEXT NOT NULL,
    data       TEXT
);
"""


class Storage:
    def __init__(self, db_path: str = "monitor.db", snapshots_dir: str = "snapshots"):
        self.db_path      = db_path
        self.snapshots_dir = snapshots_dir
        os.makedirs(snapshots_dir, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ── Chain snapshots ──────────────────────────────────────────────────────

    def save_chain_snapshot(self, ticker: str, asof: str, chain: dict) -> None:
        """Save full option chain for ticker on asof date."""
        data = json.dumps(chain, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO chain_snapshots (ticker, asof, data) VALUES (?,?,?)",
            (ticker, asof, data),
        )
        self._conn.commit()

        # Also write JSON file
        fname = os.path.join(self.snapshots_dir, f"{ticker}_{asof}.json")
        with open(fname, "w") as f:
            f.write(data)
        logger.debug("Saved snapshot: %s", fname)

    def load_chain_snapshot(self, ticker: str, asof: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM chain_snapshots WHERE ticker=? AND asof=?",
            (ticker, asof),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def load_latest_chain_snapshot(self, ticker: str, before_asof: str) -> tuple[str, dict] | None:
        """Return (asof, chain) of the most recent snapshot strictly before before_asof."""
        row = self._conn.execute(
            "SELECT asof, data FROM chain_snapshots WHERE ticker=? AND asof<? ORDER BY asof DESC LIMIT 1",
            (ticker, before_asof),
        ).fetchone()
        if row:
            return row["asof"], json.loads(row["data"])
        return None

    def load_iv_history(self, ticker: str, expiry: str, strike: float,
                        opt_type: str, lookback_days: int = 252) -> list[float]:
        """
        Fetch daily IV values for a specific contract from stored chain snapshots.
        Used for IV rank computation.
        """
        cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()
        rows = self._conn.execute(
            "SELECT asof, data FROM chain_snapshots WHERE ticker=? AND asof>=? ORDER BY asof",
            (ticker, cutoff),
        ).fetchall()
        ivs = []
        for row in rows:
            try:
                chain = json.loads(row["data"])
                exp_data = chain.get(expiry, {})
                key = strike if opt_type == "call" else -strike
                contract = exp_data.get(str(key)) or exp_data.get(key)
                if contract and contract.get("iv", 0) > 0:
                    ivs.append(float(contract["iv"]))
            except Exception:
                pass
        return ivs

    # ── Valued positions ─────────────────────────────────────────────────────

    def save_positions_valued(self, asof: str, positions: list[dict]) -> None:
        data = json.dumps(positions, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO positions_valued (asof, data) VALUES (?,?)",
            (asof, data),
        )
        self._conn.commit()

    def load_positions_valued(self, asof: str) -> list[dict] | None:
        row = self._conn.execute(
            "SELECT data FROM positions_valued WHERE asof=?", (asof,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # ── Analytics ────────────────────────────────────────────────────────────

    def save_analytics(self, asof: str, analytics: dict) -> None:
        data = json.dumps(analytics, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO analytics (asof, data) VALUES (?,?)",
            (asof, data),
        )
        self._conn.commit()

    def load_analytics(self, asof: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM analytics WHERE asof=?", (asof,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # ── Macro ────────────────────────────────────────────────────────────────

    def save_macro(self, asof: str, macro: dict) -> None:
        data = json.dumps(macro, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO macro_scores (asof, data) VALUES (?,?)",
            (asof, data),
        )
        self._conn.commit()

    def load_macro(self, asof: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM macro_scores WHERE asof=?", (asof,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # ── News cache ───────────────────────────────────────────────────────────

    def save_news(self, ticker: str, asof: str, news: dict) -> None:
        data = json.dumps(news, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO news_cache (ticker, asof, data) VALUES (?,?,?)",
            (ticker, asof, data),
        )
        self._conn.commit()

    def load_news(self, ticker: str, asof: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM news_cache WHERE ticker=? AND asof=?",
            (ticker, asof),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # ── Alerts ───────────────────────────────────────────────────────────────

    def save_alerts(self, asof: str, alerts: list[dict]) -> None:
        for a in alerts:
            self._conn.execute(
                """INSERT INTO alerts (asof, severity, alert_type, ticker, message, data)
                   VALUES (?,?,?,?,?,?)""",
                (
                    asof,
                    a.get("severity", "info"),
                    a.get("type", ""),
                    a.get("ticker"),
                    a.get("message", ""),
                    json.dumps(a, default=str),
                ),
            )
        self._conn.commit()

    def load_alerts(self, asof: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM alerts WHERE asof=? ORDER BY id", (asof,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]
