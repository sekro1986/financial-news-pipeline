"""Coeur d'orchestration : collecte -> classification -> dedup -> stockage -> alerte."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from .classifier import classify
from .config import settings
from .db import get_session
from .classifier import family_of, family_threshold
from .dedup import story_key
from .extract import clean_html, guess_company
from .watchlist import active_entries
from .models import Signal
from .schema import RawItem

log = logging.getLogger("ma_signals.pipeline")


def _passes_watchlist(item: RawItem) -> bool:
    wl = settings.watchlist_list
    if not wl:
        return True
    hay = item.text.lower()
    return any(name in hay for name in wl)


@dataclass
class _Candidate:
    item: RawItem
    score: int
    event_type: str
    company: str
    summary: str
    matched: list[str]
    story_key: str


def process_items(items: list[RawItem], seed: bool = False) -> list[Signal]:
    """Classe, deduplique (item + histoire) et persiste. Retourne les NOUVEAUX
    signaux a notifier.

    seed=True : persiste tout le backlog en le marquant deja notifie (alerted=1)
    et ne retourne rien -> pas d'inondation au premier demarrage.

    Deux niveaux de dedup :
      1. dedup_key (source+id natif) : empeche de re-stocker le MEME article ;
      2. story_key (societe/empreinte + type) : regroupe le MEME deal republie par
         plusieurs medias, dans une fenetre glissante (story_window_hours).

    Les sources curees (rss_custom) recoivent un bonus de score (curated_score_bonus).
    """
    to_alert: list[Signal] = []

    with get_session() as session:
        # Resolveur d'entite : (nom canonique, termes) des emetteurs surveilles.
        wl_resolver = [(e.canonical, e.match_terms) for e in active_entries()]

        # --- Etape 1 : classification + filtres item-level ---
        candidates: list[_Candidate] = []
        for item in items:
            if not _passes_watchlist(item):
                continue

            cls = classify(item.text)
            score = cls.score
            # Score impose par le collecteur (ex: collecteur de prix : pas de
            # mots-cles, le score vient de l'ampleur du mouvement).
            if item.score_override is not None and item.score_override > score:
                score = item.score_override
            if item.source in settings.curated_source_list:
                score += settings.curated_score_bonus
            if score <= 0:
                continue

            # Doublon exact (meme article deja en base) -> on ignore.
            if session.query(Signal).filter_by(dedup_key=item.dedup_key).first():
                continue

            event_type = item.event_hint or cls.event_type
            company = (item.company or guess_company(item.title))[:256]
            # Canonicalisation : si le texte matche un emetteur surveille, on force
            # son nom de reference -> clé de dedup stable (cross-source / cross-langue).
            if wl_resolver:
                low = item.text.lower()
                for canon, terms in wl_resolver:
                    if any(t and t in low for t in terms):
                        company = canon[:256]
                        break
            summary = clean_html(item.summary)[:4000]
            sk = story_key(company, event_type, item.title)
            candidates.append(
                _Candidate(item, score, event_type, company, summary, cls.matched, sk)
            )

        # --- Etape 2 : dedup "histoire" intra-cycle (garde le meilleur score) ---
        best_by_story: dict[str, _Candidate] = {}
        for c in candidates:
            kept = best_by_story.get(c.story_key)
            if kept is None or c.score > kept.score:
                best_by_story[c.story_key] = c

        # --- Etape 3 : dedup "histoire" inter-cycles (fenetre glissante) ---
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=settings.story_window_hours)
        for c in best_by_story.values():
            if settings.story_dedup:
                already = (
                    session.query(Signal)
                    .filter(Signal.story_key == c.story_key, Signal.detected_at >= cutoff)
                    .first()
                )
                if already:
                    continue  # meme histoire deja captee recemment (autre media) -> skip

            threshold = family_threshold(family_of(c.event_type))
            is_alertable = c.score >= threshold
            # Cycle de vie : amorce (seed) / en_attente (à envoyer) / sous_seuil (détecté).
            if seed:
                status, alerted_flag = "amorce", 1
            elif is_alertable:
                status, alerted_flag = "en_attente", 0
            else:
                status, alerted_flag = "sous_seuil", 1
            sig = Signal(
                dedup_key=c.item.dedup_key,
                story_key=c.story_key,
                source=c.item.source,
                event_type=c.event_type,
                company=c.company,
                title=c.item.title,
                url=c.item.url,
                summary=c.summary,
                score=c.score,
                matched_keywords=",".join(c.matched),
                published_at=c.item.published_at,
                alerted=alerted_flag,
                status=status,
            )
            session.add(sig)
            session.flush()
            if is_alertable and not seed:
                to_alert.append(sig)

        for s in to_alert:
            session.refresh(s)

    return to_alert
