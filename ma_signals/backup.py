"""Sauvegarde de la base SQLite + rotation.

Utilise l'API de backup native de sqlite3 (cohérente même si le poller écrit
en parallèle — pas un simple cp). Conserve les `backup_keep` sauvegardes les
plus récentes dans `backup_dir`.

Usage : python -m ma_signals.backup            # une sauvegarde + rotation
        (timer systemd : deploy/masignals-backup.{service,timer}, 03:00)

Pour restaurer : arrêter le poller, copier la sauvegarde sur ma_signals.db,
redémarrer. Penser à copier régulièrement backup_dir HORS de la VM (rsync/rclone).
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path

from .config import settings

log = logging.getLogger("ma_signals.backup")

_PREFIX = "ma_signals-"


def _sqlite_path() -> Path | None:
    """Chemin du fichier SQLite, ou None si la base n'est pas SQLite."""
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    return Path(url.split("///", 1)[-1])


def run_backup(now: dt.datetime | None = None) -> Path | None:
    """Sauvegarde la base et applique la rotation. Retourne le fichier créé."""
    src_path = _sqlite_path()
    if src_path is None:
        log.info("base non-SQLite (%s) : backup délégué à l'infra (pg_dump).",
                 settings.database_url.split(":", 1)[0])
        return None
    if not src_path.exists():
        log.warning("base introuvable (%s) : rien à sauvegarder.", src_path)
        return None

    now = now or dt.datetime.now(dt.timezone.utc)
    dest_dir = Path(settings.backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.db"

    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)  # snapshot cohérent, même base en cours d'écriture
    finally:
        dst.close()
        src.close()
    log.info("sauvegarde OK : %s (%.1f Mo)", dest, dest.stat().st_size / 1e6)

    rotate(dest_dir)
    return dest


def rotate(dest_dir: Path) -> list[Path]:
    """Supprime les sauvegardes au-delà des `backup_keep` plus récentes."""
    backups = sorted(dest_dir.glob(f"{_PREFIX}*.db"))
    excess = backups[: max(0, len(backups) - settings.backup_keep)]
    for p in excess:
        p.unlink(missing_ok=True)
        log.info("rotation : %s supprimé", p.name)
    return excess


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dest = run_backup()
    if dest:
        print(dest)


if __name__ == "__main__":
    main()
