"""Service de polling : exécute un cycle de collecte à intervalle régulier.

Cycle = pour chaque collecteur activé -> collect() -> pipeline (classer, dédup,
stocker) -> dispatch des alertes. Robuste : l'échec d'une source n'arrête pas
les autres (safe_collect). Lançable en boucle (APScheduler) ou en one-shot.
"""
from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from .alerting import dispatch
from .collectors import build_enabled
from .config import settings
from .db import init_db
from .pipeline import process_items

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ma_signals.poller")


def run_cycle() -> int:
    """Exécute un cycle complet. Retourne le nombre de signaux alertés."""
    collectors = build_enabled(settings.sources_list)
    all_items = []
    for c in collectors:
        all_items.extend(c.safe_collect())
        c.close()

    log.info("cycle: %d items collectés au total", len(all_items))
    new_alerts = process_items(all_items)
    dispatch(new_alerts)
    log.info("cycle: %d nouvelles alertes", len(new_alerts))
    return len(new_alerts)


def main() -> None:
    parser = argparse.ArgumentParser(description="MA-Signals poller")
    parser.add_argument("--once", action="store_true", help="exécute un seul cycle puis quitte")
    args = parser.parse_args()

    init_db()
    log.info("DB initialisée. Sources actives: %s", settings.sources_list)

    # Premier cycle immédiat
    run_cycle()
    if args.once:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_cycle,
        "interval",
        seconds=settings.poll_interval_seconds,
        max_instances=1,
        coalesce=True,
        id="collect_cycle",
    )
    scheduler.start()
    log.info("Scheduler démarré (toutes les %ds). Ctrl+C pour arrêter.", settings.poll_interval_seconds)
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("Arrêt propre du poller.")


if __name__ == "__main__":
    main()
