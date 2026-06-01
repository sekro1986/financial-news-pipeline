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
    """Cle de regroupement d'une meme histoire. Stable et deterministe."""
    et = (event_type or "none").strip().lower()
    co = normalize_title(company) if company else ""
    if co:
        return f"co:{co}|{et}"
    fp = _title_fingerprint(title)
    return f"t:{fp}|{et}"
