"""Extraction heuristique du nom de societe + nettoyage HTML des resumes.

Pas de dependance lourde (pas de NER/ML) : regex + ancres M&A (EN + FR).
Imparfait par nature sur les titres libres ; renvoie "" en cas de doute
(le titre complet reste affiche dans l'alerte).
"""
from __future__ import annotations

import re

# Mots a ne jamais retenir en tete/fin d'un nom de societe
_STOP = {
    "the", "a", "an", "why", "how", "us", "uk", "eu", "new", "exclusive",
    "revealed", "breaking", "update", "analysis", "opinion", "comment", "could",
    "this", "that", "two", "three", "former", "sir", "report", "reports",
    "shares", "stock", "group", "plc", "inc", "corp", "ltd", "sa", "nv",
    # acronymes d'operations / adjectifs frequents (faux positifs)
    "opa", "opr", "european", "american", "british", "french", "german",
    "asian", "global", "national", "international", "us-based", "uk-based",
}

# Jeton : mot capitalise OU marque camelCase (easyJet, iliad -> non, mais easyJet oui)
_TOKEN = r"(?:[A-Z][A-Za-z0-9&.'’-]*|[a-z]+[A-Z][A-Za-z0-9&.'’-]*)"
_PHRASE = rf"{_TOKEN}(?:\s+{_TOKEN}){{0,4}}"

_SOURCE_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
_PREFIX = re.compile(r"^(?:exclusive|revealed|breaking|update|rare alert|uncooked alert)\s*:?\s*", re.I)

# Ancres ciblant la SOCIETE CIBLE (priorite decroissante) — EN puis FR
_PATTERNS = [
    re.compile(rf"\b(?:bid|offer|approach|takeover|tender offer)\s+(?:for|of)\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:to\s+)?acquire\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:stake|interest)\s+in\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\bbidding\s+for\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:buys?|buyout of|takeover of)\s+(?:the\s+)?({_PHRASE})"),
    # Francais : "... sur (la societe) X", "rachat de X"
    re.compile(rf"\bsur\s+(?:la\s+soci[ée]t[ée]\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:rachat|acquisition|prise de participation)\s+(?:de|d'|d’|dans)\s+({_PHRASE})"),
]
_LEADING = re.compile(rf"^({_PHRASE})")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    s = s.strip(" .,:;–-—")
    toks = s.split()
    while toks and toks[0].lower() in _STOP:
        toks.pop(0)
    while toks and toks[-1].lower() in _STOP:
        toks.pop()
    if not toks:
        return ""
    toks = toks[:4]
    cand = " ".join(toks)
    if not any(ch.isupper() for ch in cand):  # camelCase inclus (easyJet -> J)
        return ""
    return cand


def guess_company(title: str) -> str:
    if not title:
        return ""
    t = _SOURCE_SUFFIX.sub("", title).strip()
    t = _PREFIX.sub("", t).strip()
    for pat in _PATTERNS:
        m = pat.search(t)
        if m:
            c = _clean(m.group(1))
            if c:
                return c
    m = _LEADING.match(t)
    if m:
        c = _clean(m.group(1))
        if c:
            return c
    return ""


def clean_html(text: str) -> str:
    if not text:
        return ""
    return _WS.sub(" ", _TAG.sub(" ", text)).strip()
