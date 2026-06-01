"""Envoi d'alertes via l'API Bot Telegram (gratuit, simple, fiable)."""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("ma_signals.alerting.telegram")


def send_telegram(text: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("échec envoi Telegram: %s", exc)
        return False
