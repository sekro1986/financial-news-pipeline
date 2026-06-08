"""Correlation news <-> prix : detection des 'mouvements inexpliques'.

Un signal de marche (collecteur de prix) est dit INEXPLIQUE si, dans une fenetre
glissante, AUCUNE news/communique (source non-marche) ne concerne le meme emetteur.
C'est le signal le plus precoce : le cours decroche sans explication publique encore
disponible -> quelque chose se prepare, ou asymetrie d'information.

Un mouvement inexplique recoit un bonus de score (unexplained_bonus) et un marqueur ;
s'il franchit alors le seuil de la famille 'market' sans avoir ete deja notifie, il
devient alertable. A l'inverse, un mouvement deja couvert par une news est 'explique'
(on a deja le pourquoi via la news).
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from .classifier import family_threshold
from .config import settings
from .db import get_session
from .models import Signal
from .watchlist import active_entries

log = logging.getLogger("ma_signals.correlate")

MARKET_SOURCE = "prices"
_FLAG = "[mouvement inexpliqué]"


def mark_unexplained_moves(seed: bool = False) -> list[Signal]:
    """Annote les mouvements de prix inexpliques recents. Retourne ceux qui
    DEVIENNENT alertables grace au bonus (et n'avaient pas encore ete notifies)."""
    if not settings.correlation_enabled:
        return []

    window = dt.timedelta(hours=settings.unexplained_window_hours)
    cutoff = dt.datetime.now(dt.timezone.utc) - window
    threshold = family_threshold("market")
    terms_by_name = {e.name: e.match_terms for e in active_entries()}

    newly_alertable: list[Signal] = []
    with get_session() as s:
        recent = s.scalars(select(Signal).where(Signal.detected_at >= cutoff)).all()
        news = [sig for sig in recent if sig.source != MARKET_SOURCE]
        news_hay = [f"{sig.title} {sig.company}".lower() for sig in news]
        moves = [sig for sig in recent if sig.source == MARKET_SOURCE]

        for m in moves:
            if _FLAG in (m.matched_keywords or ""):
                continue  # deja traite
            terms = terms_by_name.get(m.company) or [m.company.lower()] if m.company else []
            explained = any(any(t and t in hay for t in terms) for hay in news_hay)
            if explained:
                continue
            # inexplique -> marqueur + bonus
            m.matched_keywords = ((m.matched_keywords + ",") if m.matched_keywords else "") + _FLAG
            m.score += settings.unexplained_bonus
            if not seed and m.score >= threshold and m.status not in ("envoye", "en_attente"):
                m.status = "en_attente"   # -> sera envoye par le prochain dispatch
                m.alerted = 0
                newly_alertable.append(m)
        # NB: SessionLocal a expire_on_commit=False -> les objets restent lisibles
        # apres la fermeture de session (pas d'expunge, sinon les modifs ne sont
        # pas persistees).
    return newly_alertable
