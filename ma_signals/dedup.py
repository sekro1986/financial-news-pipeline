"""Dedup au niveau "histoire" (cross-source / cross-media).

Le dedup_key (source+id natif) empeche de re-stocker le MEME article. Mais un
meme deal republie par 8 medias (TradingView, Yahoo, PR Newswire, StreetInsider,
Investing.com...) produit 8 articles differents donc 8 dedup_key differents ->
flood. Ici on calcule une "story_key" stable qui regroupe ces variantes :

  - si on a extrait une societe : cle = société normalisee + type d'evenement
    ("whirlpool|tender_offer") -> toutes les variantes du meme deal fusionnent ;
  - sinon : empreinte de mots-cles significatifs du titre + type d'evenement,
    robuste aux reformulations ("Whirlpool Announces..." vs "whirlpool launches...").

La fusion s'applique dans une fenetre glissante (story_window_hours) : au-dela,
un meme couple societe/type est considere comme une nouvelle histoire.
"""
from __future__ import annotations

import re
import unicodedata

# Suffixe de source en fin de titre : " - Yahoo Finance", " | Reuters", " — Sky"
_SOURCE_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# Mots vides ignores dans l'empreinte de titre (EN + FR + bruit M&A generique).
_STOP = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "by", "with",
    "as", "at", "from", "is", "are", "be", "its", "it", "this", "that", "after",
    "over", "into", "amid", "says", "say", "report", "reports", "reported",
    "announces", "announce", "announced", "launches", "launch", "launched",
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "sur", "selon",
    "milliards", "millions", "billion", "million", "plc", "inc", "corp", "ltd",
    "sa", "nv", "co", "group",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_title(title: str) -> str:
    """Retire le suffixe de source, accents, ponctuation ; minuscule + espaces compactes."""
    if not title:
        return ""
    t = _SOURCE_SUFFIX.sub("", title)
    t = _strip_accents(t).lower()
    t = _NON_WORD.sub(" ", t)
    return _WS.sub(" ", t).strip()


def _title_fingerprint(title: str, n: int = 6) -> str:
    """Empreinte = n premiers mots significatifs, tries (ordre-insensible)."""
    toks = [w for w in normalize_title(title).split() if w not in _STOP and len(w) > 1]
    toks = sorted(set(toks))[:n]
    return " ".join(toks)


def story_key(company: str, event_type: str, title: str) -> str:
    """Cle de regroupement d'une meme histoire. Stable et deterministe.

    Regroupe par FAMILLE d'evenement, pas par type : une OPA racontee comme
    'possible_offer' par un media et 'tender_offer' par un autre est la MEME
    histoire (cas Intesa/Monte dei Paschi du 08/06 : 5 alertes).
    """
    from .classifier import family_of  # import local : evite tout cycle
    fam = family_of((event_type or "none").strip().lower())
    co = normalize_title(company) if company else ""
    if co:
        return f"co:{co}|{fam}"
    fp = _title_fingerprint(title)
    return f"t:{fp}|{fam}"


# Mots non-discriminants dans un NOM de societe : formes juridiques, articles
# multilingues, et descripteurs que l'extraction laisse parfois colles au nom
# ('Mpac Group Shares Crash', 'Partners Group Founder Calls').
_CORP_STOP = _STOP | {
    "holding", "holdings", "ag", "ab", "asa", "oyj", "gmbh", "spa", "srl",
    "bv", "se", "ad", "llc", "lp", "kk", "trust",
    "shares", "share", "stock", "stocks", "ceo", "founder", "founders",
    "calls", "crash", "crashes", "falls", "fall", "drops", "drop", "slides",
    "registers", "register", "cuts", "cut", "slashes", "soars", "tumbles",
    "jumps", "surges", "plunges",
    "en", "avec", "di", "dei", "della", "delle", "der", "die", "das", "el",
}


def company_tokens(name: str) -> frozenset[str]:
    """Tokens discriminants d'un nom de societe (vide si le nom est du bruit)."""
    return frozenset(
        w for w in normalize_title(name or "").split()
        if w not in _CORP_STOP and len(w) >= 2
    )


def same_story_company(a: str, b: str) -> bool:
    """Deux noms designent-ils la meme societe ? Rapprochement par tokens :
    vrai si l'ensemble le plus petit est inclus dans l'autre.

    'Monte dei Paschi' ~ 'Banca Monte dei Paschi di Siena' ;
    'BlackRock Latin American Inv Trust' ~ 'BlackRock Latin American Investment'.
    Un nom sans token discriminant ('Le', 'Les', 'Short-seller') ne matche RIEN :
    on prefere un doublon residuel a une fusion abusive."""
    ta, tb = company_tokens(a), company_tokens(b)
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Equivalence par prefixe (>= 3 chars) : 'inv' ~ 'investment'.
    def _covered(tok: str) -> bool:
        return any(
            tok == o or (min(len(tok), len(o)) >= 3 and (o.startswith(tok) or tok.startswith(o)))
            for o in big
        )
    n = sum(1 for t in small if _covered(t))
    # Inclusion complete ('Mpac' dans 'Mpac Group'), OU recouvrement large :
    # au moins 2 tokens communs couvrant >= 2/3 du nom le plus court
    # ('Banca Monte dei Paschi' ~ 'Monte dei Paschi di Siena' : 2/3).
    return n == len(small) or (n >= 2 and n * 3 >= len(small) * 2)


def split_repeats(pending: list) -> tuple[list, list]:
    """Filet anti-doublon a l'ENVOI (cooldown alert_cooldown_hours).

    Une alerte en attente est une REPETITION si une alerte pour la meme
    (societe ~tokens, famille) a deja ete ENVOYEE dans la fenetre, ou si une
    autre alerte du meme lot raconte deja la meme histoire (on garde alors la
    mieux scoree). Retourne (a_envoyer, a_mettre_en_sourdine).
    """
    import datetime as dt

    from .classifier import family_of
    from .config import settings

    if not pending or settings.alert_cooldown_hours <= 0:
        return list(pending), []

    from .db import SessionLocal
    from .models import Signal

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=settings.alert_cooldown_hours)
    with SessionLocal() as s:
        sent = s.query(Signal.company, Signal.event_type).filter(
            Signal.status == "envoye", Signal.sent_at.isnot(None),
            Signal.sent_at >= cutoff).all()
    sent_stories = [(family_of(et), co) for co, et in sent if co]

    to_send, repeats = [], []
    for sig in sorted(pending, key=lambda x: x.score or 0, reverse=True):
        fam = family_of(sig.event_type)
        already = any(f == fam and same_story_company(sig.company, co)
                      for f, co in sent_stories)
        in_batch = any(family_of(k.event_type) == fam
                       and same_story_company(sig.company, k.company)
                       for k in to_send)
        if sig.company and (already or in_batch):
            repeats.append(sig)
        else:
            to_send.append(sig)
    return to_send, repeats
