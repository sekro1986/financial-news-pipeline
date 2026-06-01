"""Cœur d'orchestration : collecte -> classification -> dédup -> stockage -> alerte."""
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


def process_items(items: list[RawItem]) -> list[Signal]:
    """Classe, déduplique et persiste une liste d'items. Retourne les NOUVEAUX signaux
    dont le score >= seuil d'alerte (ceux à notifier)."""
    to_alert: list[Signal] = []

    with get_session() as session:
        for item in items:
            if not _passes_watchlist(item):
                continue

            cls = classify(item.text)
            if cls.score <= 0:
                continue  # aucun signal M&A : on ignore

            # Déduplication : déjà en base ?
            existing = session.query(Signal).filter_by(dedup_key=item.dedup_key).first()
            if existing:
                continue

            # event_type : priorité à l'indice du collecteur s'il est fort (ex: form EDGAR)
            event_type = item.event_hint or cls.event_type

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
                alerted=0,
            )
            session.add(sig)
            session.flush()  # pour obtenir l'id

            if cls.score >= settings.alert_min_score:
                to_alert.append(sig)

        # détacher les objets à notifier (les valeurs sont déjà chargées)
        for s in to_alert:
            session.refresh(s)

    return to_alert
