"""Extraction heuristique du nom de societe + nettoyage HTML des resumes.

Pas de dependance lourde (pas de NER/ML) : regex + ancres M&A (EN + FR).
Imparfait par nature ; renvoie "" en cas de doute (le titre complet reste affiche).

Objectif cle pour la DEDUP : un meme emetteur doit donner le MEME nom quelle que
soit la formulation. Renforts vs v1 :
  - troncature aux "verbes de titre" (Slides/Warns/dévoile...) avales en Title-Case
    anglais ("Pirelli Slides After Short" -> "Pirelli") ;
  - nettoyage des prefixes/segments de presse FR (Zonebourse : "PALMARÈS : ...",
    "Repli des prix ... ; Evoke accepte ...") en isolant le segment de l'evenement ;
  - retrait de descripteurs en tete ("Tire Giant Pirelli ..." -> "Pirelli").
"""
from __future__ import annotations

import re

_STOP = {
    "the", "a", "an", "why", "how", "us", "uk", "eu", "new", "exclusive",
    "revealed", "breaking", "update", "analysis", "opinion", "comment", "could",
    "this", "that", "two", "three", "former", "sir", "report", "reports",
    "shares", "stock", "group", "plc", "inc", "corp", "ltd", "sa", "nv",
    "opa", "opr", "european", "american", "british", "french", "german",
    "asian", "global", "national", "international", "us-based", "uk-based",
}

# Mots d'action marquant la FIN d'un nom dans un titre Title-Case (EN) / accroche (FR)
_HEADLINE_VERBS = {
    "slides", "slide", "slips", "slumps", "sinks", "falls", "fall", "drops", "plunges",
    "plummets", "tumbles", "rises", "jumps", "surges", "soars", "gains", "rallies",
    "recovers", "rebounds", "warns", "warned", "says", "denies", "denied", "threatens",
    "unveils", "announces", "announced", "reports", "reported", "completes", "completed",
    "agrees", "agreed", "launches", "launched", "raises", "cuts", "issues", "issued",
    "faces", "facing", "explores", "weighs", "considers", "rejects", "accepts", "names",
    "appoints", "expects", "posts", "swings", "eyes", "mulls", "after", "amid", "as",
    "accepte", "lance", "dévoile", "devoile", "annonce", "abaisse", "relève", "releve",
    "chute", "bondit", "recule", "grimpe", "vise", "rejette", "confirme", "propose",
    "augmente", "envisage", "publie", "nomme", "cède", "cede", "rachète", "rachete",
}

_LEADING_DESCRIPTORS = {
    "tire", "giant", "drugmaker", "chipmaker", "carmaker", "automaker", "lender",
    "retailer", "miner", "insurer", "biotech", "fintech", "startup", "group",
    "tech", "oil", "energy", "luxury", "fashion", "pharma", "bank", "broker",
}

_EVENT_WORDS = re.compile(
    r"\b(offre|rachat|opa|opr|acqui|fusion|takeover|bid|merger|tender|buyout|stake|"
    r"profit\s+warning|avertissement|dividend|dividende|administration|insolven|"
    r"faillite|short[\s-]?sell|redempt|rachats|guidance|prévision|prevision)\w*",
    re.IGNORECASE,
)

_TOKEN = r"(?:[A-Z][A-Za-z0-9&.'’-]*|[a-z]+[A-Z][A-Za-z0-9&.'’-]*)"
_PHRASE = rf"{_TOKEN}(?:\s+{_TOKEN}){{0,4}}"

_SOURCE_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
_PREFIX = re.compile(
    r"^(?:exclusive|exclusif|revealed|breaking|update|rare alert|uncooked alert|"
    r"palmar[èe]s|flash|le point|[àa]\s+suivre|valeurs?\s+[àa]\s+suivre|"
    r"point\s+(?:march[ée]|bourse)|zoom)\s*:?\s*",
    re.I,
)

_PATTERNS = [
    # Sujet en tete qui ACCEPTE une offre -> c'est la cible (FR 'accepte', EN 'accepts')
    re.compile(rf"^({_PHRASE})\s+(?:accepte|a\s+accept[ée]e?|accepts)\b"),
    re.compile(rf"\b(?:bid|offer|approach|takeover|tender offer)\s+(?:for|of)\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:to\s+)?acquire\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:stake|interest)\s+in\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\bbidding\s+for\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:buys?|buyout of|takeover of)\s+(?:the\s+)?({_PHRASE})"),
    re.compile(rf"\bsur\s+(?:la\s+soci[ée]t[ée]\s+)?({_PHRASE})"),
    re.compile(rf"\b(?:rachat|acquisition|prise de participation)\s+(?:de|d'|d’|dans)\s+({_PHRASE})"),
]
_LEADING = re.compile(rf"^({_PHRASE})")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _pick_segment(t: str) -> str:
    parts = re.split(r"\s*[;]\s*", t)
    if len(parts) <= 1:
        return t
    for seg in parts:
        if _EVENT_WORDS.search(seg):
            return seg.strip()
    return parts[0].strip()


def _clean(s: str) -> str:
    s = s.strip(" .,:;–-—")
    toks = s.split()
    while len(toks) > 1 and toks[0].lower().strip(".,") in _LEADING_DESCRIPTORS:
        toks.pop(0)
    while toks and toks[0].lower() in _STOP:
        toks.pop(0)
    while toks and toks[-1].lower() in _STOP:
        toks.pop()
    if not toks:
        return ""
    cut = len(toks)
    for i in range(1, len(toks)):
        if toks[i].lower().strip(".,'’") in _HEADLINE_VERBS:
            cut = i
            break
    toks = toks[:cut][:4]
    while toks and toks[-1].lower() in _STOP:
        toks.pop()
    if not toks:
        return ""
    cand = " ".join(toks)
    if not any(ch.isupper() for ch in cand):
        return ""
    return cand


def guess_company(title: str) -> str:
    if not title:
        return ""
    t = _SOURCE_SUFFIX.sub("", title).strip()
    t = _PREFIX.sub("", t).strip()
    t = _pick_segment(t)
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
