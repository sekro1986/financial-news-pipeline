"""Moteur de scoring M&A.

Approche : règles pondérées (regex insensibles à la casse) regroupées par TYPE
d'événement. Chaque match ajoute des points ; le type d'événement du signal est
celui de la règle au plus fort poids déclenchée. Volontairement transparent et
auditable (vs. boîte noire ML) — on peut l'étendre vers un LLM plus tard via la
même interface `classify()`.

Échelle de score indicative :
  >= 8  : signal fort (offre ferme/possible, tender offer, schéma d'arrangement)
  5-7   : signal intéressant (prise de participation activiste, strategic review)
  1-4   : bruit / mention faible
  0     : aucun mot-clé M&A
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (poids, type_evenement, motif_regex, libellé)
# Ordonné par poids décroissant pour que le type retenu soit le plus significatif.
RULES: list[tuple[int, str, str, str]] = [
    # --- Offres formelles UK (Takeover Code) ---
    (10, "firm_offer",      r"\brule\s*2\.7\b",                                   "Rule 2.7 (offre ferme)"),
    (9,  "possible_offer",  r"\brule\s*2\.4\b",                                   "Rule 2.4 (offre possible)"),
    (8,  "firm_offer",      r"\b(recommended|firm)\s+(cash\s+)?(and\s+share\s+)?offer\b", "offre ferme/recommandée"),
    (8,  "possible_offer",  r"\bpossible\s+(cash\s+)?offer\b",                    "possible offer"),
    (7,  "scheme",          r"\bscheme\s+of\s+arrangement\b",                     "scheme of arrangement"),
    (6,  "possible_offer",  r"\b(takeover|bid)\s+approach\b",                     "takeover/bid approach"),
    (6,  "possible_offer",  r"\bin\s+receipt\s+of\s+(an?\s+)?approach\b",         "receipt of approach"),

    # --- US tender offers / M&A formels ---
    (9,  "tender_offer",    r"\bsc\s*to-t\b|\bschedule\s*to\b",                   "Schedule TO (tender offer)"),
    (8,  "tender_offer",    r"\btender\s+offer\b",                                "tender offer"),
    (8,  "merger_agt",      r"\b(definitive\s+)?merger\s+agreement\b",            "merger agreement"),
    (7,  "merger_agt",      r"\bagree(?:s|d|ing)?\s+to\s+(?:be\s+)?acquire[ds]?\b", "agree(s) to acquire"),
    (6,  "merger_agt",      r"\bto\s+acquire\b",                                "to acquire"),
    (7,  "merger_agt",      r"\bdefinitive\s+agreement\b",                        "definitive agreement"),

    # --- Prises de participation / activisme ---
    (7,  "stake_13d",       r"\bsc\s*13d\b|\bschedule\s*13d\b",                   "SC 13D (prise de participation)"),
    (5,  "stake",           r"\b(building|acquired|raised|increased)\s+(a\s+)?stake\b", "stake building"),
    (5,  "stake",           r"\bactivist\s+(investor|stake|campaign)\b",          "activist"),

    # --- Intentions / signaux amont ---
    (6,  "take_private",    r"\btake[\s-]?private\b|\bgo[\s-]?private\b",          "take private"),
    (6,  "buyout",          r"\b(leveraged\s+)?buyout\b|\blbo\b",                 "buyout/LBO"),
    (5,  "strategic_review",r"\bstrategic\s+review\b",                            "strategic review"),
    (5,  "strategic_review",r"\bexploring\s+strategic\s+(alternatives|options)\b","exploring strategic alternatives"),
    (4,  "interest",        r"\b(takeover|acquisition)\s+interest\b",             "takeover interest"),
    (4,  "interest",        r"\b(considering|mulls?|weighs?|explores?)\s+(a\s+)?(possible\s+)?(bid|offer|acquisition)\b", "considering a bid"),

    # --- Français (AMF + presse FR) ---
    (9,  "tender_offer",    r"\boffre\s+publique\s+d['’]?achat\b|\bopa\b",        "OPA (offre publique d'achat)"),
    (8,  "tender_offer",    r"\boffre\s+publique\s+de\s+retrait\b|\bopr\b",       "OPR (offre publique de retrait)"),
    (8,  "possible_offer",  r"\boffre\s+(de\s+)?rachat\b",                        "offre de rachat"),
    (7,  "possible_offer",  r"\boffre\s+publique\b",                              "offre publique"),
    (6,  "stake",           r"\bfranchissement\s+de\s+seuil\b",                   "franchissement de seuil"),
    (6,  "stake",           r"\b(prise\s+de\s+participation|mont[ée]e?\s+au\s+capital)\b", "prise de participation"),
    (5,  "interest",        r"\b(lorgne|convoite|s['’]int[ée]resse\s+à)\b",       "intérêt déclaré (FR)"),
    (4,  "generic",         r"\brapprochement\b",                                 "rapprochement (générique FR)"),
    (3,  "generic",         r"\brachat\b",                                        "rachat (générique FR)"),
    (3,  "generic",         r"\bfusion\b",                                        "fusion (générique FR)"),

    # --- Termes génériques EN (faibles, confirment le contexte) ---
    (3,  "generic",         r"\btakeover\b",                                      "takeover (générique)"),
    (3,  "generic",         r"\bacquisition\b",                                   "acquisition (générique)"),
    (3,  "generic",         r"\bacquires?\b",                                     "acquire (générique)"),
    (2,  "generic",         r"\bmerger\b",                                        "merger (générique)"),
    (2,  "generic",         r"\bbid\b",                                           "bid (générique)"),
]

_COMPILED = [(w, et, re.compile(pat, re.IGNORECASE), label) for (w, et, pat, label) in RULES]


@dataclass
class Classification:
    score: int
    event_type: str
    matched: list[str]   # libellés des règles déclenchées


def classify(text: str) -> Classification:
    """Score un texte. Retourne score cumulé, type dominant et règles déclenchées."""
    if not text:
        return Classification(0, "none", [])

    total = 0
    matched: list[str] = []
    best_weight = -1
    best_type = "none"

    for weight, event_type, regex, label in _COMPILED:
        if regex.search(text):
            total += weight
            matched.append(label)
            if weight > best_weight:
                best_weight = weight
                best_type = event_type

    return Classification(score=total, event_type=best_type, matched=matched)
