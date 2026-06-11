"""Service agent (Claude Agent SDK) : compile en quasi temps réel les news qui bougent les marchés.

À côté du poller (qui collecte/classifie/alerte signal par signal), ce service :
  1. lit les NOUVEAUX signaux persistés (curseur sur signals.id), y compris les
     sous-seuil au-dessus de agent_min_score (l'agent peut repêcher ce que les
     regex sous-notent) ;
  2. toutes les agent_macro_interval_minutes, demande en plus une veille macro
     par recherche web (banques centrales, stats clés, géopolitique, énergie) ;
  3. confie le tout à un agent Claude (SDK) qui juge, regroupe les histoires,
     pondère sa conviction avec le scorecard historique, vérifie au besoin
     (outil WebSearch), et rédige UN message Telegram seulement si ça en vaut
     la peine ;
  4. exige un JSON strict en sortie ; tout parse douteux => rien n'est envoyé.

Garde-fous : désactivé par défaut (AGENT_ENABLED=false), budget de cycles/jour,
max_turns, timeout, circuit-breaker, dédup macro persistée (agent_state.json).

Dépendances (en plus de requirements.txt) — voir requirements-agent.txt :
  pip install claude-agent-sdk   # + Node >= 18 : npm i -g @anthropic-ai/claude-code
  ANTHROPIC_API_KEY dans .env

Usage : python -m ma_signals.agent_digest [--once]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import time
from pathlib import Path

from sqlalchemy import select

from .config import settings

log = logging.getLogger("ma_signals.agent_digest")

_MACRO_SENT_CAP = 300       # clés macro mémorisées pour la dédup
_SIGNALS_PER_CYCLE = 200    # plafond de signaux injectés dans un prompt

_SYSTEM = (
    "Tu es l'analyste de garde d'un desk marchés. On te donne, à intervalle "
    "régulier, les nouveaux signaux d'un pipeline de news financières (événements "
    "corporate scorés par règles) et, périodiquement, mission de veille macro via "
    "recherche web. Ton travail : décider ce qui INFLUENCE réellement les marchés, "
    "regrouper les doublons d'une même histoire, pondérer ta conviction avec le "
    "scorecard de fiabilité fourni, et rédiger au plus UN message Telegram concis "
    "en français. Si rien ne mérite une notification, tu n'envoies rien.\n\n"
    "Tu réponds UNIQUEMENT par un objet JSON, sans texte autour :\n"
    '{"send": true|false, "message": "texte ou null", '
    '"used_signal_ids": [ints], "macro_keys": ["slug-stable", ...]}\n\n'
    "Règles du message : texte brut (pas de markdown), <= 3500 caractères ; "
    "section '📡 SIGNAUX' (corporate) puis '🌍 MACRO' si pertinent ; pour chaque "
    "item : société/sujet, fait, sens attendu, conviction (haute/moyenne/basse) "
    "et pourquoi en quelques mots ; URLs des sources clés. "
    "macro_keys : un slug stable et descriptif par item macro inclus (ex. "
    "'fed-hold-2026-06') pour la déduplication — ne JAMAIS réinclure un slug "
    "déjà envoyé. Sois avare : un flash toutes les heures qui répète, c'est du "
    "bruit ; mieux vaut send=false que du remplissage."
)


# ----------------------------------------------------------------- état local
def load_state(path: str | Path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("état agent illisible (%s), repart de zéro", p)
    return {"last_id": 0, "last_macro_at": "", "macro_sent": [],
            "date": "", "cycles_today": 0, "fail_streak": 0}


def save_state(path: str | Path, state: dict) -> None:
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def roll_day(state: dict, now: dt.datetime) -> None:
    today = now.date().isoformat()
    if state.get("date") != today:
        state["date"] = today
        state["cycles_today"] = 0


def macro_due(state: dict, now: dt.datetime) -> bool:
    last = state.get("last_macro_at") or ""
    if not last:
        return True
    try:
        prev = dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - prev) >= dt.timedelta(minutes=settings.agent_macro_interval_minutes)


# ------------------------------------------------------------------- données
def select_new_signals(last_id: int) -> tuple[list, int]:
    """Nouveaux signaux depuis le curseur. Retourne (rows, nouveau curseur)."""
    from .db import SessionLocal
    from .models import Signal

    with SessionLocal() as s:
        rows = list(s.scalars(
            select(Signal).where(Signal.id > last_id)
            .order_by(Signal.id).limit(_SIGNALS_PER_CYCLE)
        ).all())
    max_id = max((r.id for r in rows), default=last_id)
    kept = [r for r in rows if (r.score or 0) >= settings.agent_min_score]
    return kept, max_id


def scorecard_brief(days: int = 30) -> str:
    """Résumé compact du scorecard par famille, injecté comme contexte."""
    try:
        from .scorecard import build_scorecard
        card = build_scorecard(days=days)
    except Exception:  # noqa: BLE001
        return "(scorecard indisponible)"
    fams = card.get("families") or card.get("by_family") or {}
    if not fams:
        return "(scorecard vide — pondère prudemment)"
    parts = []
    for fam, st in sorted(fams.items()):
        hr, n = st.get("hit_rate"), st.get("graded", 0)
        parts.append(f"{fam}: {hr}% fiab. (n={n}, moy {st.get('avg_pct', 0):+.1f}%)"
                     if hr is not None else f"{fam}: n/a (n={n})")
    return " · ".join(parts)


def fmt_signal(r) -> str:
    exp = {1: "up", -1: "down", 0: "~"}.get(r.expected_move, "?")
    return (f"#{r.id} [{r.source}] {r.event_type} score={r.score} dir={exp} "
            f"société={r.company or '?'} — {r.title!r} {r.url or ''}".strip())


def build_cycle_prompt(signals: list, scorecard: str, include_macro: bool,
                       sent_macro_keys: list[str], now: dt.datetime) -> str:
    lines = [f"Cycle du {now.isoformat(timespec='minutes')} (UTC).",
             f"Scorecard fiabilité par famille (30 j) : {scorecard}", ""]
    if signals:
        lines.append(f"NOUVEAUX SIGNAUX DU PIPELINE ({len(signals)}) :")
        lines += [fmt_signal(r) for r in signals]
    else:
        lines.append("Aucun nouveau signal du pipeline sur ce cycle.")
    lines.append("")
    if include_macro:
        lines.append(
            "VEILLE MACRO DUE : fais 1 à 3 recherches web ciblées (décisions de "
            "banques centrales, inflation/emploi majeurs, géopolitique de marché, "
            "énergie/matières premières) des dernières heures. N'inclus que ce qui "
            "bouge réellement les marchés et qui est NOUVEAU.")
        recent = sent_macro_keys[-60:]
        lines.append("Slugs macro déjà envoyés (ne pas répéter) : "
                     + (", ".join(recent) if recent else "(aucun)"))
    else:
        lines.append("Pas de veille macro sur ce cycle : ne fais PAS de recherche "
                     "web sauf pour vérifier un signal corporate douteux.")
    lines.append("\nRéponds par le JSON seul.")
    return "\n".join(lines)


def parse_agent_json(text: str) -> dict | None:
    """Extrait le premier objet JSON valide du texte. None si introuvable/invalide."""
    if not text:
        return None
    dec = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = dec.raw_decode(text, idx)
            if isinstance(obj, dict) and "send" in obj:
                return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    return None


# --------------------------------------------------------------------- agent
async def _run_agent(prompt: str) -> tuple[str, float | None]:
    """Un run agentique. Import paresseux : le module reste importable sans SDK."""
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                  ResultMessage, TextBlock, query)

    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM,
        model=settings.agent_model,
        allowed_tools=["WebSearch"],
        max_turns=settings.agent_max_turns,
        max_budget_usd=settings.agent_max_budget_usd,
    )
    final_text, cost = "", None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    final_text = block.text
        elif isinstance(msg, ResultMessage):
            cost = getattr(msg, "total_cost_usd", None)
            if getattr(msg, "result", None):
                final_text = msg.result
    return final_text, cost


def run_cycle(state: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    roll_day(state, now)

    if state["cycles_today"] >= settings.agent_max_cycles_per_day:
        log.warning("budget de cycles/jour atteint (%d) — cycle sauté",
                    settings.agent_max_cycles_per_day)
        return

    signals, new_last_id = select_new_signals(state.get("last_id", 0))
    do_macro = macro_due(state, now)
    if not signals and not do_macro:
        state["last_id"] = new_last_id
        return  # rien à juger : cycle gratuit

    prompt = build_cycle_prompt(signals, scorecard_brief(), do_macro,
                                state.get("macro_sent", []), now)
    state["cycles_today"] += 1
    try:
        text, cost = asyncio.run(asyncio.wait_for(
            _run_agent(prompt), timeout=settings.agent_timeout))
    except Exception as exc:  # noqa: BLE001
        state["fail_streak"] = state.get("fail_streak", 0) + 1
        log.error("run agent en échec (streak=%d): %s", state["fail_streak"], exc)
        return  # curseur non avancé : les signaux seront revus au prochain cycle

    out = parse_agent_json(text)
    if out is None:
        state["fail_streak"] = state.get("fail_streak", 0) + 1
        log.error("sortie agent non-JSON (streak=%d): %.300s", state["fail_streak"], text)
        state["last_id"] = new_last_id  # éviter une boucle de retry payante
        return

    state["fail_streak"] = 0
    state["last_id"] = new_last_id
    if do_macro:
        state["last_macro_at"] = now.isoformat()
        keys = [k for k in out.get("macro_keys") or [] if isinstance(k, str)]
        state["macro_sent"] = (state.get("macro_sent", []) + keys)[-_MACRO_SENT_CAP:]

    if out.get("send") and out.get("message"):
        from .alerting.telegram import send_telegram
        ok = send_telegram(str(out["message"]))
        log.info("flash agent envoyé=%s coût=%s ids=%s", ok, cost,
                 out.get("used_signal_ids"))
    else:
        log.info("cycle silencieux (send=false) coût=%s", cost)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="un seul cycle puis sortie")
    args = ap.parse_args()

    if not settings.agent_enabled and not args.once:
        raise SystemExit("AGENT_ENABLED=false — active-le dans .env (voir DEPLOY.md).")

    state_path = settings.agent_state_path
    while True:
        state = load_state(state_path)
        try:
            run_cycle(state)
        finally:
            save_state(state_path, state)
        if args.once:
            break
        backoff = min(state.get("fail_streak", 0), 4)
        time.sleep(settings.agent_interval_seconds * (2 ** backoff if backoff >= 3 else 1))


if __name__ == "__main__":
    main()
