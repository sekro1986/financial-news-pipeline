"""Coeur d'orchestration : collecte -> classification -> dedup -> stockage -> alerte."""
from __future__ import annotations

import logging

from .classifier import classify
from .config import settings
from .db import get_session
from .models import Signal
from .schema import RawItem

log = logging.getLogger("ma_signals.pipeline")


def _passes_watchlist(item: RawItem) -> bool:
    wl = settings.watchlist_list
    if not wl:
        return True
    hay = item.text.lower()
    return any(name in hay for name in wl)


def process_items(items: list[RawItem], seed: bool = False) -> list[Signal]:
    """Classe, deduplique et persiste. Retourne les NOUVEAUX signaux a notifier.

    seed=True : persiste tout le backlog en le marquant deja notifie (alerted=1)
    et ne retourne rien -> pas d'inondation au premier demarrage.
    """
    to_alert: list[Signal] = []

    with get_session() as session:
        for item in items:
            if not _passes_watchlist(item):
                continue

            cls = classify(item.text)
            if cls.score <= 0:
                continue

            existing = session.query(Signal).filter_by(dedup_key=item.dedup_key).first()
            if existing:
                continue

            event_type = item.event_hint or cls.event_type
            is_alertable = cls.score >= settings.alert_min_score
            alerted_flag = 1 if (seed or not is_alertable) else 0

            sig = Signal(
                dedup_key=item.dedup_key,
                source=item.source,
                event_type=event_type,
                company=item.company[:256],
                title=item.title,
                url=item.url,
                summary=item.summary[:4000],
                score=cls.score,
                matched_keywords=",".join(cls.matched),
                published_at=item.published_at,
                alerted=alerted_flag,
            )
            session.add(sig)
            session.flush()

            if is_alertable and not seed:
                to_alert.append(sig)

        for s in to_alert:
            session.refresh(s)

    return to_alert
