"""Coeur d'orchestration : collecte -> classification -> dedup -> stockage -> alerte."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from .classifier import classify
from .config import settings
from .db import get_session
from .classifier import family_of, family_threshold
from .dedup import same_story_company, story_key
from .extract import clean_html, guess_company, publisher_name, strip_source_suffix
from .watchlist import active_entries
from . import llm
from .models import Signal
from .schema import RawItem
from sqlalchemy.exc import IntegrityError

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
    acquirer: str = ""
    expected: int | None = None     # sens attendu LLM (-1/0/+1) ; None = heuristiques
    llm_conf: int | None = None


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

        llm.reset_cycle()  # recharge le budget d'appels LLM pour ce lot

        # --- Etape 1 : classification + filtres item-level ---
        candidates: list[_Candidate] = []
        for item in items:
            if not _passes_watchlist(item):
                continue

            # Filtrage par qualite de source (clickbait / content-farms)
            deny = settings.source_deny_list
            if deny:
                pub = publisher_name(item.title, item.url)
                if pub and any(d in pub for d in deny):
                    continue

            # Classifie sur le titre nettoye de l'editeur (evite 'Profit Warning Alert'
            # & co. d'injecter de faux mots-cles), + resume + societe.
            clf_text = " ".join(p for p in (strip_source_suffix(item.title), item.summary, item.company) if p)
            cls = classify(clf_text)
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

            # --- Enrichissement LLM (optionnel) : cible/acquereur/type/sens attendu.
            # Appele apres le dedup exact (on ne paie jamais 2x le meme article) ;
            # None -> on retombe integralement sur les heuristiques historiques.
            enr = llm.enrich(strip_source_suffix(item.title), item.summary) \
                if llm.should_enrich(score) else None

            # Priorites type : collecteur (event_hint, ex prix) > LLM > regex.
            event_type = item.event_hint or (enr.event_type if enr and enr.event_type else cls.event_type)
            # Priorites nom : collecteur (ex MFN/EDGAR, fiable) > LLM > heuristique.
            company = (item.company or (enr.target if enr else "") or guess_company(item.title))[:256]
            # Canonicalisation : si le texte matche un emetteur surveille, on force
            # son nom de reference -> clé de dedup stable (cross-source / cross-langue).
            if wl_resolver:
                low = item.text.lower()
                for canon, terms in wl_resolver:
                    if any(t and t in low for t in terms):
                        company = canon[:256]
                        break
            matched = list(cls.matched)
            if enr:
                matched.append(enr.label)
            summary = clean_html(item.summary)[:4000]
            sk = story_key(company, event_type, item.title)
            candidates.append(
                _Candidate(item, score, event_type, company, summary, matched, sk,
                           acquirer=enr.acquirer if enr else "",
                           expected=enr.expected if enr else None,
                           llm_conf=enr.confidence if enr else None)
            )

        # --- Etape 2a : dedup par dedup_key INTRA-cycle ---
        # Le MEME article peut apparaitre 2x dans un lot avec des titres differents
        # (ex EDGAR : un SC 13D/A est liste cote 'Subject' ET cote 'Filed by' avec
        # le meme AccNo -> meme dedup_key mais story_keys differents). Sans ce
        # regroupement, le 2e INSERT violait uq_signals_dedup et le rollback
        # annulait TOUT le cycle (panne silencieuse du 09/06/2026 : le poller
        # tournait mais plus aucun signal n'etait persiste).
        best_by_key: dict[str, _Candidate] = {}
        for c in candidates:
            kept = best_by_key.get(c.item.dedup_key)
            if kept is None or c.score > kept.score:
                best_by_key[c.item.dedup_key] = c

        # --- Etape 2b : dedup "histoire" intra-cycle (garde le meilleur score) ---
        best_by_story: dict[str, _Candidate] = {}
        for c in best_by_key.values():
            kept = best_by_story.get(c.story_key)
            if kept is None or c.score > kept.score:
                best_by_story[c.story_key] = c

        # --- Etape 2c : fusion FLOUE intra-cycle ---
        # Meme famille + noms de societe qui se recouvrent (tokens) = meme
        # histoire, meme si l'extraction a donne des variantes ('Monte dei
        # Paschi' / 'Banca Monte dei Paschi'). On garde le meilleur score.
        merged: list[_Candidate] = []
        for c in sorted(best_by_story.values(), key=lambda x: x.score, reverse=True):
            dup = next((k for k in merged
                        if family_of(k.event_type) == family_of(c.event_type)
                        and same_story_company(k.company, c.company)), None)
            if dup is None:
                merged.append(c)

        # --- Etape 3 : dedup "histoire" inter-cycles (fenetre glissante) ---
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=settings.story_window_hours)
        for c in merged:
            if settings.story_dedup:
                already = (
                    session.query(Signal)
                    .filter(Signal.story_key == c.story_key, Signal.detected_at >= cutoff)
                    .first()
                )
                if already:
                    continue  # meme histoire deja captee recemment (autre media) -> skip
                # Verif FLOUE inter-cycles : meme famille, societe qui se recouvre.
                fam = family_of(c.event_type)
                if c.company:
                    recent = (
                        session.query(Signal.company)
                        .filter(Signal.story_key.like(f"co:%|{fam}"),
                                Signal.detected_at >= cutoff)
                        .limit(500).all()
                    )
                    if any(same_story_company(c.company, r[0]) for r in recent):
                        continue  # variante du nom d'une histoire deja captee

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
                acquirer=c.acquirer,
                expected_move=c.expected,
                llm_confidence=c.llm_conf,
                matched_keywords=",".join(c.matched),
                published_at=c.item.published_at,
                alerted=alerted_flag,
                status=status,
            )
            # Filet de securite : une collision residuelle (course inter-process,
            # contrainte inattendue) ne doit JAMAIS faire perdre le cycle entier.
            # SAVEPOINT par insertion -> on ignore l'item fautif et on continue.
            try:
                with session.begin_nested():
                    session.add(sig)
                    session.flush()
            except IntegrityError:
                log.warning("insertion ignoree (dedup_key deja present) : %s | %s",
                            c.item.dedup_key, c.item.title[:100])
                continue
            if is_alertable and not seed:
                to_alert.append(sig)

        for s in to_alert:
            session.refresh(s)

    return to_alert
