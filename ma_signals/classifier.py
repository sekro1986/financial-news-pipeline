"""Moteur de scoring multi-evenements.

Approche : regles ponderees (regex insensibles a la casse) regroupees par TYPE
d'evenement, eux-memes regroupes en FAMILLES (mna, liquidity, earnings, distress,
capital, governance, regulatory, generic). Chaque match ajoute des points ; le
type retenu est celui de la regle au plus fort poids declenchee, et la famille en
decoule.

Le seuil d'alerte est defini PAR FAMILLE (family_thresholds) : un "profit warning"
n'a pas a franchir la meme barre qu'une OPA. Volontairement transparent et
auditable (vs. boite noire ML), extensible vers un LLM via la meme interface.

Echelle de score indicative :
  >= 8  : signal fort       5-7 : interessant       1-4 : faible/bruit       0 : rien
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import settings

# (poids, type_evenement, motif_regex, libelle)
RULES: list[tuple[int, str, str, str]] = [
    # ============================ M&A / CONTROLE ============================
    (10, "firm_offer",      r"\brule\s*2\.7\b",                                   "Rule 2.7 (offre ferme)"),
    (9,  "possible_offer",  r"\brule\s*2\.4\b",                                   "Rule 2.4 (offre possible)"),
    (8,  "firm_offer",      r"\b(recommended|firm)\s+(cash\s+)?(and\s+share\s+)?offer\b", "offre ferme/recommandee"),
    (8,  "possible_offer",  r"\bpossible\s+(cash\s+)?offer\b",                    "possible offer"),
    (7,  "scheme",          r"\bscheme\s+of\s+arrangement\b",                     "scheme of arrangement"),
    (6,  "possible_offer",  r"\b(takeover|bid)\s+approach\b",                     "takeover/bid approach"),
    (6,  "possible_offer",  r"\bin\s+receipt\s+of\s+(an?\s+)?approach\b",         "receipt of approach"),
    (9,  "tender_offer",    r"\bsc\s*to-t\b|\bschedule\s*to\b",                   "Schedule TO (tender offer)"),
    (8,  "tender_offer",    r"\btender\s+offer\b",                                "tender offer"),
    (8,  "merger_agt",      r"\b(definitive\s+)?merger\s+agreement\b",            "merger agreement"),
    (7,  "merger_agt",      r"\bagree(?:s|d|ing)?\s+to\s+(?:be\s+)?acquire[ds]?\b", "agree(s) to acquire"),
    (6,  "merger_agt",      r"\bto\s+acquire\b",                                  "to acquire"),
    (7,  "merger_agt",      r"\bdefinitive\s+agreement\b",                        "definitive agreement"),
    (7,  "stake_13d",       r"\bsc\s*13d\b|\bschedule\s*13d\b",                   "SC 13D (prise de participation)"),
    (5,  "stake",           r"\b(building|acquired|raised|increased)\s+(a\s+)?stake\b", "stake building"),
    (5,  "stake",           r"\bactivist\s+(investor|stake|campaign)\b",          "activist"),
    (6,  "take_private",    r"\btake[\s-]?private\b|\bgo[\s-]?private\b",          "take private"),
    (6,  "buyout",          r"\b(leveraged\s+)?buyout\b|\blbo\b",                 "buyout/LBO"),
    (5,  "strategic_review",r"\bstrategic\s+review\b",                            "strategic review"),
    (5,  "strategic_review",r"\bexploring\s+strategic\s+(alternatives|options)\b","exploring strategic alternatives"),
    (4,  "interest",        r"\b(takeover|acquisition)\s+interest\b",             "takeover interest"),
    (4,  "interest",        r"\b(considering|mulls?|weighs?|explores?)\s+(a\s+)?(possible\s+)?(bid|offer|acquisition)\b", "considering a bid"),
    # FR
    (9,  "tender_offer",    r"\boffre\s+publique\s+d['’]?achat\b|\bopa\b",        "OPA"),
    (8,  "tender_offer",    r"\boffre\s+publique\s+de\s+retrait\b|\bopr\b",       "OPR"),
    (8,  "possible_offer",  r"\boffre\s+(de\s+)?rachat\b",                        "offre de rachat"),
    (7,  "possible_offer",  r"\boffre\s+publique\b",                              "offre publique"),
    (6,  "stake",           r"\bfranchissement\s+de\s+seuil\b",                   "franchissement de seuil"),
    (6,  "stake",           r"\b(prise\s+de\s+participation|mont[ée]e?\s+au\s+capital)\b", "prise de participation"),
    (5,  "interest",        r"\b(lorgne|convoite|s['’]int[ée]resse\s+à)\b",       "interet declare (FR)"),

    # ============================ LIQUIDITE / FONDS ============================
    (8,  "redemption_gating", r"\b(gate[sd]?|gating|limit(?:s|ed|ing)?|cap(?:s|ped|ping)?|restrict(?:s|ed|ing)?|suspend(?:s|ed|ing)?)\s+(?:fund\s+)?redemptions?\b", "limite/suspension de rachats"),
    (8,  "redemption_gating", r"\bredemptions?\s+(?:are\s+|to\s+be\s+)?(?:gated|suspended|limited|capped|restricted|halted)\b", "rachats gated/suspendus"),
    (7,  "fund_suspension",   r"\b(suspend(?:s|ed|ing)?|halt(?:s|ed|ing)?|freez(?:e|es|ing)|froze[n]?)\s+(?:the\s+)?fund\b", "suspension de fonds"),
    (6,  "fund_suspension",   r"\bwithdrawal\s+(?:requests?|limits?|restrictions?)\b", "demandes/limites de retrait"),
    (6,  "nav_cut",           r"\bnav\b.{0,20}\b(cut|reduc\w+|declin\w+|writedown|write[\s-]?down)\b", "baisse de NAV"),
    # FR
    (8,  "redemption_gating", r"\b(limit\w+|suspen\w+|gel\w+|plafonn\w+)\s+(?:les\s+|des\s+)?rachats\b", "limite/suspension de rachats (FR)"),

    # ============================ RESULTATS / GUIDANCE ============================
    (8,  "profit_warning",  r"\bprofit\s+warning\b",                              "profit warning"),
    (7,  "guidance_cut",    r"\b(cuts?|lowers?|slashe?s?|reduces?|trims?|downgrades?)\s+(?:its\s+|full[\s-]?year\s+|fy\s+|annual\s+)?(?:guidance|outlook|forecasts?|profit\s+(?:forecast|outlook)|targets?)\b", "abaissement de guidance"),
    (5,  "guidance_raise",  r"\b(raises?|lifts?|upgrades?|hikes?|boosts?)\s+(?:its\s+|full[\s-]?year\s+)?(?:guidance|outlook|forecasts?)\b", "relevement de guidance"),
    (6,  "earnings_miss",   r"\b(miss(?:es|ed)?|fall[s]?\s+short\s+of|below)\s+(?:analyst[s']*\s+)?(?:estimates?|expectations?|forecasts?|consensus)\b", "resultats sous attentes"),
    # FR
    (8,  "profit_warning",  r"\bavertissement\s+sur\s+r[ée]sultats?\b",           "avertissement sur resultats (FR)"),
    (7,  "guidance_cut",    r"\b(abaisse|r[ée]vise\s+[àa]\s+la\s+baisse|r[ée]duit)\s+(?:ses\s+)?(?:pr[ée]visions|objectifs|guidance|perspectives)\b", "abaissement objectifs (FR)"),

    # ============================ DETRESSE ============================
    (8,  "insolvency",      r"\b(chapter\s*1[15]|chapter\s*7|administration|insolven\w+|bankruptc\w+|liquidation|receivership)\b", "insolvabilite/faillite"),
    (7,  "default_event",   r"\b(defaults?\s+on|payment\s+default|misses?\s+(?:a\s+)?(?:coupon|interest|debt)\s+payment)\b", "defaut de paiement"),
    (7,  "covenant_breach", r"\bcovenant\s+(?:breach|waiver|default)\b|\bbreach(?:es|ed)?\s+(?:its\s+)?covenants?\b", "breach de covenant"),
    (7,  "going_concern",   r"\bgoing\s+concern\b",                               "going concern"),
    (6,  "restructuring",   r"\b(debt\s+restructuring|restructur\w+\s+(?:its\s+)?debt)\b", "restructuration de dette"),
    # FR
    (8,  "insolvency",      r"\b(faillite|redressement\s+judiciaire|liquidation\s+judiciaire|cessation\s+de\s+paiements?)\b", "faillite/redressement (FR)"),

    # ============================ CAPITAL ============================
    (7,  "equity_raise",    r"\b(capital\s+(?:increase|raise|hike)|equity\s+(?:raise|offering|placing|placement)|share\s+placing|placing\s+of\s+(?:new\s+)?shares|secondary\s+offering|rights\s+offering)\b", "augmentation de capital"),
    (6,  "rights_issue",    r"\brights\s+issue\b",                                "rights issue"),
    (7,  "dividend_cut",    r"\b(cuts?|suspend(?:s|ed|ing)?|cancels?|scraps?|omits?|slashe?s?|halves?)\s+(?:its\s+)?dividend\b|\bdividend\s+(?:cut|suspension|cancell?ation|omission)\b", "coupe/suspension de dividende"),
    (4,  "buyback",         r"\b(share\s+buybacks?|share\s+repurchases?|buy[\s-]?back\s+programme?)\b", "buyback"),
    # FR
    (7,  "equity_raise",    r"\baugmentation\s+de\s+capital\b",                   "augmentation de capital (FR)"),
    (7,  "dividend_cut",    r"\b(suppression|r[ée]duction|coupe|suspension)\s+d[eu]\s*(?:la\s+)?dividende?s?\b", "coupe de dividende (FR)"),

    # ============================ GOUVERNANCE / INTEGRITE ============================
    (7,  "exec_departure",  r"\b(ceo|cfo|chief\s+executive|chief\s+financial\s+officer|chairman)\b.{0,40}\b(steps?\s+down|resign\w+|departs?|to\s+leave|ousted|fired|sacked|exits?)\b", "depart dirigeant"),
    (8,  "accounting_irregularity", r"\b(accounting\s+(?:irregularit\w+|scandal|fraud|errors?)|restate(?:s|d|ment)?\s+(?:its\s+)?(?:accounts|earnings|financials|results)|material\s+weakness)\b", "irregularite comptable"),
    (7,  "auditor_resignation", r"\bauditor\s+(?:resign\w+|quits?|steps?\s+down|departs?)\b", "demission de l'auditeur"),
    (8,  "short_seller",    r"\bshort[\s-]?sell(?:er|ers|ing)\b|\bshort\s+report\b|\b(hindenburg|muddy\s+waters|viceroy\s+research|grizzly\s+(?:research|reports))\b", "rapport short-seller"),

    # ============================ REGLEMENTAIRE / LEGAL ============================
    (6,  "investigation",   r"\b(under\s+investigation|regulatory\s+(?:probe|investigation)|probe[sd]?\s+(?:by|into)|investigat(?:es|ed|ion)\s+by)\b", "enquete/probe"),
    (6,  "sanction",        r"\b(fined|fine\s+of|penalty\s+of|sanction(?:s|ed)?\b)", "sanction/amende"),

    # ============================ GENERIQUES (confirment le contexte) ============================
    (4,  "generic",         r"\brapprochement\b",                                 "rapprochement (FR)"),
    (3,  "generic",         r"\brachat\b",                                        "rachat (FR)"),
    (3,  "generic",         r"\bfusion\b",                                        "fusion (FR)"),
    (3,  "generic",         r"\btakeover\b",                                      "takeover (generique)"),
    (3,  "generic",         r"\bacquisition\b",                                   "acquisition (generique)"),
    (3,  "generic",         r"\bacquires?\b",                                     "acquire (generique)"),
    (2,  "generic",         r"\bmerger\b",                                        "merger (generique)"),
    (2,  "generic",         r"\bbid\b",                                           "bid (generique)"),
]

# event_type -> famille. Les types absents tombent dans "generic".
FAMILY_OF: dict[str, str] = {
    # mna
    "firm_offer": "mna", "possible_offer": "mna", "scheme": "mna", "tender_offer": "mna",
    "merger_agt": "mna", "stake_13d": "mna", "stake": "mna", "take_private": "mna",
    "buyout": "mna", "strategic_review": "mna", "interest": "mna",
    # liquidity
    "redemption_gating": "liquidity", "fund_suspension": "liquidity", "nav_cut": "liquidity",
    # earnings
    "profit_warning": "earnings", "guidance_cut": "earnings", "guidance_raise": "earnings",
    "earnings_miss": "earnings",
    # distress
    "insolvency": "distress", "default_event": "distress", "covenant_breach": "distress",
    "going_concern": "distress", "restructuring": "distress",
    # capital
    "equity_raise": "capital", "rights_issue": "capital", "dividend_cut": "capital", "buyback": "capital",
    # governance
    "exec_departure": "governance", "accounting_irregularity": "governance",
    "auditor_resignation": "governance", "short_seller": "governance",
    # regulatory
    "investigation": "regulatory", "sanction": "regulatory",
    # marche (collecteur de prix)
    "price_drop": "market", "price_spike": "market", "volume_spike": "market",
    # anticipation (screener de proies)
    "target_candidate": "anticipation", "undervalued": "anticipation", "accumulation": "anticipation",
}

_COMPILED = [(w, et, re.compile(pat, re.IGNORECASE), label) for (w, et, pat, label) in RULES]

# Filtre "dette / titres obligataires" : une "tender offer" sur des NOTES/BONDS
# est un refinancement, PAS une acquisition.
_DEBT_NOUN = re.compile(r"\b(notes?|bonds?|debentures?|consent solicitation)\b", re.IGNORECASE)
_DEBT_CTX = re.compile(r"\b(tender offer|exchange offer|consent solicitation|offering|refinanc)\w*", re.IGNORECASE)
_DEBT_PENALTY = 8


def family_of(event_type: str) -> str:
    return FAMILY_OF.get(event_type, "generic")


def family_threshold(family: str) -> int:
    """Seuil d'alerte de la famille (fallback : alert_min_score)."""
    return settings.family_thresholds.get(family, settings.alert_min_score)


@dataclass
class Classification:
    score: int
    event_type: str
    matched: list[str]
    family: str = "generic"


def classify(text: str) -> Classification:
    """Score un texte. Retourne score cumule, type dominant, famille et regles."""
    if not text:
        return Classification(0, "none", [], "generic")

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

    # Resserrage du bucket "generic" : si AUCUNE regle specifique n'a fire,
    # on plafonne le score (un empilement de synonymes generiques n'alerte jamais).
    if best_type == "generic" and total > settings.generic_score_cap:
        total = settings.generic_score_cap
        matched.append("[~] generic plafonne (pas d'ancre de deal)")

    # Penalite contexte dette (notes/obligations) -> evite les faux positifs tender offer
    if total > 0 and _DEBT_NOUN.search(text) and _DEBT_CTX.search(text):
        total = max(0, total - _DEBT_PENALTY)
        matched.append("[-] contexte dette (notes/obligations)")
        if total == 0:
            best_type = "none"

    return Classification(score=total, event_type=best_type, matched=matched,
                          family=family_of(best_type))
