"""Couche d'enrichissement LLM (Claude Haiku) derriere le pre-filtre regex.

Principe : le classifieur a regles reste le filtre gratuit qui ecarte 95 % du
bruit. Pour les items qui le franchissent (pre-score >= llm_min_score), UN appel
LLM extrait d'un coup ce que les regex ne savent pas faire :
  - la societe CIBLE (celle dont le cours doit reagir) et l'eventuel acquereur ;
  - le type d'evenement (parmi la taxonomie existante du classifieur) ;
  - le sens attendu de la reaction du cours (up/down/ambiguous) -> remplace les
    heuristiques empilees de impact.refine_expected pour les nouveaux signaux ;
  - une confiance 0-100 (sous llm_confidence_floor, l'enrichissement est ignore).

Garde-fous : desactive par defaut (LLM_ENABLED=false), budget d'appels par cycle,
circuit-breaker apres 3 echecs consecutifs, cache memoire, timeout court. En cas
d'absence de cle/erreur/JSON invalide -> None et le pipeline retombe sur les
heuristiques existantes (zero regression possible).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

import httpx

from .classifier import FAMILY_OF
from .config import settings

log = logging.getLogger("ma_signals.llm")

_API_URL = "https://api.anthropic.com/v1/messages"

# Types attribuables par le LLM = taxonomie du classifieur, MOINS les types
# reserves aux collecteurs quantitatifs (prix/screener) qui ne viennent pas du texte.
_COLLECTOR_ONLY = {"price_drop", "price_spike", "volume_spike",
                   "target_candidate", "undervalued", "accumulation"}
ALLOWED_TYPES = sorted(set(FAMILY_OF) - _COLLECTOR_ONLY)

_SYSTEM = (
    "You analyze financial news headlines (English, French, German, Swedish) for an "
    "event-detection pipeline. Respond with a single JSON object, nothing else.\n"
    "Fields:\n"
    '  "target": the listed company the event is ABOUT (whose stock price would react). '
    "Short market name without legal suffixes (AG/PLC/SA...). null if none.\n"
    '  "acquirer": the acquiring/bidding company if the event is an M&A approach, '
    "offer or stake build. null otherwise.\n"
    '  "event_type": one of ' + json.dumps(ALLOWED_TYPES) + ' or "none" if the text '
    "does not describe such a corporate event (macro news, politics, sport, opinion).\n"
    '  "direction": expected reaction of the TARGET stock: "up", "down" or "ambiguous". '
    "Careful: a withdrawn/failed offer is down for the target; a short-seller attack "
    "that is denied/refuted is ambiguous; news about the ACQUIRER side is ambiguous.\n"
    '  "confidence": integer 0-100, your overall confidence in these fields.\n'
    "A 'tender offer' on notes/bonds is debt refinancing: event_type none."
)

_DIR = {"up": 1, "down": -1, "ambiguous": 0}


@dataclass
class Enrichment:
    target: str = ""
    acquirer: str = ""
    event_type: str = ""        # "" si hors taxonomie / "none"
    expected: int | None = 0    # -1/0/1 ; None jamais retourne ici
    confidence: int = 0
    raw: str = ""

    @property
    def label(self) -> str:
        d = {1: "up", -1: "down", 0: "~"}[self.expected or 0]
        return f"[llm] {self.event_type or 'n/a'} dir={d} conf={self.confidence}"


# --- Etat par cycle (budget + circuit-breaker) ---
_calls_left = 0
_fail_streak = 0
_cache: dict[str, Enrichment | None] = {}


def reset_cycle() -> None:
    """A appeler en debut de cycle de collecte : recharge le budget d'appels."""
    global _calls_left, _fail_streak
    _calls_left = settings.llm_max_per_cycle
    _fail_streak = 0
    if len(_cache) > 2048:
        _cache.clear()


def available() -> bool:
    return bool(settings.llm_enabled and settings.anthropic_api_key)


def should_enrich(pre_score: int) -> bool:
    """Un appel ne se justifie que si le pre-filtre regex y voit deja quelque chose."""
    return (available() and pre_score >= settings.llm_min_score
            and _calls_left > 0 and _fail_streak < 3)


def _call_api(user_text: str) -> str:
    r = httpx.post(
        _API_URL,
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": settings.llm_model,
            "max_tokens": 250,
            "temperature": 0,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": user_text}],
        },
        timeout=settings.llm_timeout,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def _parse(text: str) -> Enrichment | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    et = (d.get("event_type") or "").strip()
    if et not in ALLOWED_TYPES:
        et = ""
    return Enrichment(
        target=(d.get("target") or "").strip()[:256],
        acquirer=(d.get("acquirer") or "").strip()[:256],
        event_type=et,
        expected=_DIR.get((d.get("direction") or "").strip().lower(), 0),
        confidence=max(0, min(100, int(d.get("confidence") or 0))),
        raw=text,
    )


def enrich(title: str, summary: str = "") -> Enrichment | None:
    """Enrichit un item. None = indisponible/echec/confiance insuffisante ->
    l'appelant retombe sur les heuristiques regex (comportement historique)."""
    global _calls_left, _fail_streak
    if not available():
        return None
    text = title.strip()
    if summary:
        text += "\n\n" + summary.strip()[:1500]
    key = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
    if key in _cache:
        return _cache[key]
    if _calls_left <= 0 or _fail_streak >= 3:
        return None
    _calls_left -= 1
    try:
        enr = _parse(_call_api(text))
        _fail_streak = 0
    except Exception as exc:  # noqa: BLE001 - reseau/quota/5xx : on degrade, on ne casse pas le cycle
        _fail_streak += 1
        log.warning("llm.enrich KO (%d/3): %s", _fail_streak, exc)
        _cache[key] = None
        return None
    if enr is not None and enr.confidence < settings.llm_confidence_floor:
        log.debug("llm.enrich confiance %s < plancher -> ignore", enr.confidence)
        enr = None
    _cache[key] = enr
    return enr
