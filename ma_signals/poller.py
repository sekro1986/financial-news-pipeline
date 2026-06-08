"""Service de polling : cycle de collecte a intervalle regulier.

Seeding silencieux au premier demarrage (base vide) pour ne pas envoyer tout le
backlog d'un coup ; ensuite seuls les evenements nouveaux declenchent une alerte.
"""
from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from .alerting import dispatch, get_pending_alerts, silence_pending
from .classifier import family_of
from .collectors import build_enabled
from .config import settings
from .db import SessionLocal, init_db
from .models import Signal
from .pipeline import process_items
from .correlate import mark_unexplained_moves

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ma_signals.poller")


def _db_is_empty() -> bool:
    with SessionLocal() as s:
        return (s.scalar(select(func.count()).select_from(Signal)) or 0) == 0


def run_cycle(seed: bool = False) -> int:
    """Execute un cycle complet. Retourne le nombre de signaux alertes."""
    collectors = build_enabled(settings.sources_list)
    all_items = []
    for c in collectors:
        all_items.extend(c.safe_collect())
        c.close()

    log.info("cycle: %d items collectes au total", len(all_items))
    process_items(all_items, seed=seed)
    if seed:
        # On annote quand meme les mouvements inexpliques en silence (sans alerter).
        mark_unexplained_moves(seed=True)
        log.info("SEED initial : backlog enregistre en silence (aucune alerte envoyee).")
        return 0
    # Correlation news<->prix : promeut d'eventuels mouvements inexpliques (-> en_attente).
    promoted = mark_unexplained_moves(seed=False)
    if promoted:
        log.info("cycle: %d mouvement(s) inexplique(s) promu(s)", len(promoted))
    # On envoie TOUS les signaux en attente (nouveaux + reliquats des cycles precedents :
    # report automatique, plus de perte au-dela du plafond).
    pending = get_pending_alerts()
    if not pending:
        return 0
    if not settings.alerts_enabled:
        # MODE OBSERVATION : rien n'est envoye en live, tout est capte en sourdine.
        silence_pending(pending)
        log.info("cycle: MODE OBSERVATION -> %d signaux captés en sourdine (aucune alerte live)",
                 len(pending))
        return 0
    allow = settings.alert_only_family_list
    if allow:   # reouverture selective : on n'alerte que certaines familles
        to_send = [s for s in pending if family_of(s.event_type) in allow]
        to_silence = [s for s in pending if family_of(s.event_type) not in allow]
    else:
        to_send, to_silence = pending, []
    dispatch(to_send)
    silence_pending(to_silence)
    log.info("cycle: %d alertes envoyées, %d en sourdine (plafond/cycle: %d)",
             len(to_send), len(to_silence), settings.max_alerts_per_cycle)
    return len(to_send)


def main() -> None:
    parser = argparse.ArgumentParser(description="MA-Signals poller")
    parser.add_argument("--once", action="store_true", help="execute un seul cycle puis quitte")
    parser.add_argument("--seed", action="store_true", help="force un seeding silencieux puis quitte")
    args = parser.parse_args()

    init_db()
    log.info("DB initialisee. Sources actives: %s", settings.sources_list)

    if args.seed:
        run_cycle(seed=True)
        return

    if _db_is_empty():
        log.info("Base vide detectee -> seeding silencieux du backlog initial.")
        run_cycle(seed=True)
    else:
        run_cycle()

    if args.once:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_cycle, "interval", seconds=settings.poll_interval_seconds,
        max_instances=1, coalesce=True, id="collect_cycle",
    )
    scheduler.start()
    log.info("Scheduler demarre (toutes les %ds). Ctrl+C pour arreter.", settings.poll_interval_seconds)
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("Arret propre du poller.")


if __name__ == "__main__":
    main()
