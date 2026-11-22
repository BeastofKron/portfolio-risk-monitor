"""
Layer 4 – Notifier (optional, off by default).

Sends alerts via:
  - Telegram: bot token + chat_id from environment variables
  - iMessage (macOS): osascript

Usage:
    notifier = Notifier(cfg)
    notifier.maybe_send(alerts, asof)

Set  config.notifier.enabled = true  to activate.
"""
from __future__ import annotations
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _format_alert_text(alerts: list[dict], asof: str) -> str:
    if not alerts:
        return f"[{asof}] No alerts today."
    lines = [f"Portfolio alerts — {asof}", ""]
    for a in alerts:
        sev_icon = {"high": "🔴", "warn": "🟡", "info": "🔵"}.get(a.get("severity", "info"), "•")
        lines.append(f"{sev_icon} {a.get('message', '')}")
    return "\n".join(lines)


class Notifier:
    def __init__(self, cfg: dict):
        notifier_cfg  = cfg.get("notifier", {})
        self.enabled  = notifier_cfg.get("enabled", False)
        tg            = notifier_cfg.get("telegram", {})
        self.tg_token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        self.tg_chat  = os.environ.get(tg.get("chat_id_env",   "TELEGRAM_CHAT_ID"), "")
        im            = notifier_cfg.get("imessage", {})
        self.im_to    = im.get("recipient", "")

    def maybe_send(self, alerts: list[dict], asof: str) -> None:
        if not self.enabled:
            return
        text = _format_alert_text(alerts, asof)
        if self.tg_token and self.tg_chat:
            self._send_telegram(text)
        if self.im_to:
            self._send_imessage(text)

    def _send_telegram(self, text: str) -> None:
        try:
            import urllib.request, urllib.parse
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.tg_chat,
                "text":    text,
            }).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
            logger.info("Telegram sent, status=%s", status)
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    def _send_imessage(self, text: str) -> None:
        """Send via osascript on macOS."""
        script = (
            f'tell application "Messages"\n'
            f'  send "{text}" to buddy "{self.im_to}"\n'
            f'end tell'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                logger.error("iMessage error: %s", result.stderr.strip())
            else:
                logger.info("iMessage sent to %s", self.im_to)
        except FileNotFoundError:
            logger.warning("osascript not found — iMessage only works on macOS.")
        except Exception as e:
            logger.error("iMessage send failed: %s", e)
