"""Backfill / nettoyage retroactif des signaux deja en base.

Re-applique le scoring courant (filtre dette inclus), l'extraction de societe et
le nettoyage HTML sur toutes les lignes existantes :
  - met a jour score, company (si vide) et summary ;
  - supprime les lignes dont le score retombe a 0 (ex : anciennes 'tender offer'
    sur des obligations, devenues non pertinentes).

Usage :
  python -m ma_signals.backfill           # APERCU (n'ecrit rien)
  python -m ma_signals.backfill --apply   # ecrit les changements en base
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from .classifier import classify
from .config import settings
from .db import SessionLocal, init_db
from .extract import clean_html, guess_company
from .models import Signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ma_signals.backfill")


def _rescore(sig: Signal) -> int:
    text = " ".join(p for p in (sig.title, sig.summary, sig.company) if p)
    score = classify(text).score
    if sig.source == "rss_custom" and score > 0:
        score += settings.curated_score_bonus
    return score


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill/nettoyage des signaux MA-Signals")
    ap.add_argument("--apply", action="store_true", help="ecrire les changements (sinon apercu)")
    args = ap.parse_args()

    init_db()
    updated = deleted = 0
    with SessionLocal() as s:
        rows = s.scalars(select(Signal)).all()
        for sig in rows:
            new_score = _rescore(sig)
            if new_score <= 0:
                deleted += 1
                if args.apply:
                    s.delete(sig)
                continue
            new_company = (sig.company or guess_company(sig.title))[:256]
            new_summary = clean_html(sig.summary)[:4000]
            if (new_score != sig.score) or (new_company != sig.company) or (new_summary != sig.summary):
                updated += 1
                if args.apply:
                    sig.score = new_score
                    sig.company = new_company
                    sig.summary = new_summary
        if args.apply:
            s.commit()

    mode = "APPLIQUE" if args.apply else "APERCU (rien ecrit ; relance avec --apply)"
    log.info("Backfill %s : %d lignes a mettre a jour, %d a supprimer (score 0), sur %d.",
             mode, updated, deleted, len(rows))


if __name__ == "__main__":
    main()
