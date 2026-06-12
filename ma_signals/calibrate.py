"""Calibration automatique : quelles familles ont GAGNÉ le droit d'alerter en live ?

Le mode observation (alerts_enabled=false) capte tout sans rien envoyer, et
l'analyse d'impact accumule des verdicts. Ce module ferme la boucle : il décide,
preuves à l'appui, quelles familles réouvrir.

Règle (hystérésis pour éviter le clignotement) :
  - une famille S'OUVRE si hit_rate >= calibration_open_rate (65 %) avec au
    moins calibration_min_graded (30) verdicts gradés sur la fenêtre ;
  - une famille ouverte SE FERME si hit_rate < calibration_close_rate (50 %)
    (toujours avec l'échantillon minimal) ;
  - sans échantillon suffisant : statu quo (on ne juge pas sans données).

L'état vit dans calibration_state_path (JSON). Le poller le lit à chaque cycle
quand CALIBRATION_ENABLED=true : il n'alerte en live que les familles ouvertes.
Précédence : ALERT_ONLY_FAMILIES (manuel) > calibration > tout-ouvert.
La calibration n'agit que si alerts_enabled=true — en observation, rien ne part.

Usage : python -m ma_signals.calibrate [--days 30] [--send]
        (branché dans masignals-daily.service, après impact + scorecard)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from .config import settings

log = logging.getLogger("ma_signals.calibrate")


def load_state() -> dict:
    p = Path(settings.calibration_state_path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("état calibration illisible (%s), repart de zéro", p)
    return {"open": [], "updated_at": ""}


def save_state(state: dict) -> None:
    Path(settings.calibration_state_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def open_families() -> set[str]:
    """Familles actuellement ouvertes par la calibration (lu par le poller)."""
    return set(load_state().get("open", []))


def run_calibration(days: int = 30) -> tuple[set[str], list[str]]:
    """Met à jour l'état. Retourne (familles ouvertes, messages de changement)."""
    from .scorecard import build_scorecard

    card = build_scorecard(days=days)
    fams = card.get("families") or card.get("by_family") or {}
    state = load_state()
    currently_open = set(state.get("open", []))
    changes: list[str] = []

    for fam, st in sorted(fams.items()):
        hr, n = st.get("hit_rate"), st.get("graded", 0)
        if hr is None or n < settings.calibration_min_graded:
            continue  # échantillon insuffisant : statu quo
        if fam not in currently_open and hr >= settings.calibration_open_rate:
            currently_open.add(fam)
            changes.append(f"🟢 famille « {fam} » OUVERTE aux alertes live "
                           f"({hr} % de fiabilité sur {n} verdicts / {days} j)")
        elif fam in currently_open and hr < settings.calibration_close_rate:
            currently_open.discard(fam)
            changes.append(f"🔴 famille « {fam} » REFERMÉE "
                           f"({hr} % de fiabilité sur {n} verdicts / {days} j)")

    state["open"] = sorted(currently_open)
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_state(state)
    for c in changes:
        log.info("calibration: %s", c)
    return currently_open, changes


def format_status(open_fams: set[str], changes: list[str], days: int) -> str:
    lines = [f"🎚️ Calibration des alertes ({days} j)"]
    lines += changes or ["Aucun changement."]
    lines.append("Familles ouvertes : " + (", ".join(sorted(open_fams)) or "(aucune)"))
    if not settings.calibration_enabled:
        lines.append("NB : CALIBRATION_ENABLED=false — état calculé mais non appliqué.")
    if not settings.alerts_enabled:
        lines.append("NB : mode observation actif — aucune alerte live ne part de toute façon.")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Calibration auto des familles d'alerte")
    parser.add_argument("--days", type=int, default=30, help="fenêtre scorecard (défaut 30 j)")
    parser.add_argument("--send", action="store_true", help="envoie le statut si changement")
    args = parser.parse_args()

    from .db import init_db
    init_db()
    open_fams, changes = run_calibration(days=args.days)
    text = format_status(open_fams, changes, args.days)
    print(text)
    if args.send and changes:
        from .alerting import send_message
        send_message(text)


if __name__ == "__main__":
    main()
