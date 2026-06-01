"""Envoi d'alertes via l'API Bot Telegram (gère le rate-limit 429)."""
from __future__ import annotations

import logging
import time

import httpx

from ..config import settings

log = logging.getLogger("ma_signals.alerting.telegram")

TELEGRAM_MAX_CHARS = 4096


def send_telegram(text: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return False

    text = text[:TELEGRAM_MAX_CHARS]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}

    for attempt in range(4):
        try:
            r = httpx.post(url, json=payload, timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.warning("echec reseau Telegram (tentative %d): %s", attempt + 1, exc)
            time.sleep(2)
            continue

        if r.status_code == 429:
            retry_after = 2
            try:
                retry_after = int(r.json().get("parameters", {}).get("retry_after", 2))
            except Exception:  # noqa: BLE001
                pass
            wait = retry_after + 0.5
            log.info("Telegram 429 : pause de %.1fs (retry_after).", wait)
            time.sleep(wait)
            continue

        try:
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("echec envoi Telegram: %s", exc)
            return False

    log.warning("Telegram : abandon apres plusieurs 429.")
    return False
