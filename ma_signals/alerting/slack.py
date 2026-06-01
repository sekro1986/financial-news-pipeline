"""Envoi d'alertes via un webhook entrant Slack."""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("ma_signals.alerting.slack")


def send_slack(text: str) -> bool:
    webhook = settings.slack_webhook_url
    if not webhook:
        return False
    try:
        r = httpx.post(webhook, json={"text": text}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("échec envoi Slack: %s", exc)
        return False
