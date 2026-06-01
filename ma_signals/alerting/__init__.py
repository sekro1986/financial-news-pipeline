"""Couche d'alerting : envoie les signaux forts vers Telegram et/ou Slack."""
from __future__ import annotations

import logging

from ..config import settings
from ..models import Signal
from .telegram import send_telegram
from .slack import send_slack

log = logging.getLogger("ma_signals.alerting")


def _format(sig: Signal) -> str:
    kw = sig.matched_keywords.replace(",", ", ")
    company = sig.company or "—"
    return (
        f"🚨 SIGNAL M&A [{sig.score}] {sig.event_type}\n"
        f"Société : {company}\n"
        f"Source : {sig.source}\n"
        f"{sig.title}\n"
        f"Mots-clés : {kw}\n"
        f"{sig.url}"
    )


def dispatch(signals: list[Signal]) -> None:
    """Notifie chaque signal sur les canaux configurés, puis marque alerted=1."""
    if not signals:
        return
    from ..db import get_session
    from ..models import Signal as S

    for sig in signals:
        msg = _format(sig)
        ok = False
        if settings.telegram_bot_token and settings.telegram_chat_id:
            ok = send_telegram(msg) or ok
        if settings.slack_webhook_url:
            ok = send_slack(msg) or ok
        if not (settings.telegram_bot_token or settings.slack_webhook_url):
            log.info("ALERTE (aucun canal configuré) :\n%s", msg)
            ok = True  # on considère "vu" pour ne pas reboucler

        if ok:
            with get_session() as session:
                obj = session.get(S, sig.id)
                if obj:
                    obj.alerted = 1
