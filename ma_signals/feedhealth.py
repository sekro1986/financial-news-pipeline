"""Suivi de sante par FLUX individuel. Alimente par les collecteurs multi-flux
(rss_custom, disclosures), lu par ma_signals.health (timer 15 min).

record() ne doit JAMAIS faire echouer une collecte : tout est best-effort.
"""
from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger("ma_signals.feedhealth")


def record(url: str, source: str, status: int, ok: bool,
           newest_item: dt.datetime | None = None) -> None:
    """Upsert l'etat d'un flux apres une tentative de collecte."""
    try:
        from .db import SessionLocal
        from .models import FeedHealth

        now = dt.datetime.now(dt.timezone.utc)
        with SessionLocal() as s:
            row = s.query(FeedHealth).filter_by(url=url).first()
            if row is None:
                row = FeedHealth(url=url, source=source)
                s.add(row)
            row.source = source
            row.last_status = status
            row.updated_at = now
            if ok:
                row.fail_streak = 0
                row.last_ok_at = now
                if newest_item is not None:
                    prev = row.last_item_at
                    if prev is not None and prev.tzinfo is None:
                        prev = prev.replace(tzinfo=dt.timezone.utc)
                    if prev is None or newest_item > prev:
                        row.last_item_at = newest_item
            else:
                row.fail_streak = (row.fail_streak or 0) + 1
            s.commit()
    except Exception:  # noqa: BLE001
        log.debug("feedhealth.record best-effort: echec ignore (%s)", url)
