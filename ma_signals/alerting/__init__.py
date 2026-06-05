"""Couche d'alerting : regroupe les signaux en messages DIGEST (anti-429)."""
from __future__ import annotations

import logging
import time

from ..config import settings
from ..models import Signal
from .telegram import send_telegram
from .slack import send_slack

log = logging.getLogger("ma_signals.alerting")

_MSG_CHAR_BUDGET = 3800


def _format_block(sig: Signal) -> str:
    company = sig.company or "-"
    line = f"[{sig.score}] {sig.event_type} - {company} ({sig.source})\n{sig.title}"
    if sig.url:
        line += f"\n{sig.url}"
    return line


def _build_messages(signals: list[Signal], truncated: int) -> list[str]:
    header = f"ALERTE {len(signals)} signal(aux) M&A (score >= {settings.alert_min_score})"
    blocks = [_format_block(s) for s in signals]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = len(header)
    for block in blocks:
        too_many = len(current) >= settings.alert_batch_size
        too_long = current_len + len(block) + 2 > _MSG_CHAR_BUDGET
        if current and (too_many or too_long):
            chunks.append(current)
            current = []
            current_len = len(header)
        current.append(block)
        current_len += len(block) + 2
    if current:
        chunks.append(current)

    total = len(chunks)
    out: list[str] = []
    for i, chunk in enumerate(chunks):
        pre = header if total == 1 else f"{header} - partie {i + 1}/{total}"
        msg = pre + "\n\n" + "\n\n".join(chunk)
        if i == total - 1 and truncated > 0:
            msg += f"\n\n... (+{truncated} autres signaux non affiches ce cycle)"
        out.append(msg)
    return out


def _send_all(message: str) -> bool:
    ok = False
    if settings.telegram_bot_token and settings.telegram_chat_id:
        ok = send_telegram(message) or ok
    if settings.slack_webhook_url:
        ok = send_slack(message) or ok
    if not (settings.telegram_bot_token or settings.slack_webhook_url):
        log.info("ALERTE (aucun canal configure) :\n%s", message)
        ok = True
    return ok


def send_message(text: str) -> bool:
    """Envoie un message libre sur les canaux configures (recap hebdo, etc.)."""
    return _send_all(text)


def dispatch(signals: list[Signal]) -> None:
    if not signals:
        return

    from ..db import get_session
    from ..models import Signal as S

    signals = sorted(signals, key=lambda s: s.score, reverse=True)
    cap = settings.max_alerts_per_cycle
    truncated = 0
    if len(signals) > cap:
        truncated = len(signals) - cap
        signals = signals[:cap]

    messages = _build_messages(signals, truncated)

    all_sent = True
    for i, msg in enumerate(messages):
        if not _send_all(msg):
            all_sent = False
        if i < len(messages) - 1:
            time.sleep(settings.telegram_send_delay)

    if all_sent:
        ids = [s.id for s in signals]
        with get_session() as session:
            for sid in ids:
                obj = session.get(S, sid)
                if obj:
                    obj.alerted = 1
        log.info("digest envoye : %d signaux en %d message(s).", len(signals), len(messages))
