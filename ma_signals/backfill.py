"""Backfill / nettoyage retroactif des signaux deja en base.

Re-applique le traitement courant sur toutes les lignes existantes :
  - re-scoring (filtre dette + plafond "generic" inclus) ;
  - extraction de societe (si vide) et nettoyage HTML du resume ;
  - calcul de la story_key (regroupement cross-media) ;
  - suppression des lignes dont le score retombe a 0 ;
  - collapse des doublons "histoire" : pour une meme story_key, on ne garde que
    la meilleure ligne (score le plus haut, puis la plus recente) et on supprime
    les republications par d'autres medias.

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
from .dedup import story_key
from .extract import clean_html, guess_company
from .models import Signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ma_signals.backfill")


def _rescore(sig: Signal) -> int:
    # Les signaux de marche (collecteur de prix) n'ont pas de mots-cles : leur
    # score vient de l'ampleur du mouvement. On le preserve tel quel.
    if sig.source in ("prices", "screener") or (sig.event_type or "").startswith(("price_", "target_", "undervalued", "accumulation")):
        return sig.score
    text = " ".join(p for p in (sig.title, sig.summary, sig.company) if p)
    score = classify(text).score
    if sig.source in settings.curated_source_list and score > 0:
        score += settings.curated_score_bonus
    return score


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill/nettoyage des signaux MA-Signals")
    ap.add_argument("--apply", action="store_true", help="ecrire les changements (sinon apercu)")
    ap.add_argument("--no-collapse", action="store_true",
                    help="ne pas fusionner les doublons cross-media (story_key)")
    args = ap.parse_args()

    init_db()
    updated = deleted = collapsed = 0
    with SessionLocal() as s:
        rows = s.scalars(select(Signal)).all()

        # --- Passe 1 : re-scoring + enrichissement + story_key ---
        survivors: list[Signal] = []
        for sig in rows:
            new_score = _rescore(sig)
            if new_score <= 0:
                deleted += 1
                if args.apply:
                    s.delete(sig)
                continue
            # Sources presse : on RÉ-EXTRAIT depuis le titre avec l'extracteur courant
            # (nettoie les vieux noms sales) ; sources à société fournie (sec/mfn/prix/
            # screener) : on conserve la valeur du collecteur.
            if sig.source in ("press_rss", "disclosures", "rss_custom", "adhoc_ir"):
                new_company = (guess_company(sig.title) or sig.company)[:256]
            else:
                new_company = (sig.company or guess_company(sig.title))[:256]
            new_summary = clean_html(sig.summary)[:4000]
            new_sk = story_key(new_company, sig.event_type, sig.title)
            if (new_score != sig.score) or (new_company != sig.company) \
                    or (new_summary != sig.summary) or (new_sk != (sig.story_key or "")):
                updated += 1
                if args.apply:
                    sig.score = new_score
                    sig.company = new_company
                    sig.summary = new_summary
                    sig.story_key = new_sk
            # valeurs courantes pour la passe collapse (que --apply soit actif ou non)
            sig._eff_score = new_score          # type: ignore[attr-defined]
            sig._eff_sk = new_sk                # type: ignore[attr-defined]
            survivors.append(sig)

        # --- Passe 2 : collapse des doublons cross-media (meme story_key) ---
        if not args.no_collapse:
            groups: dict[str, list[Signal]] = {}
            for sig in survivors:
                groups.setdefault(sig._eff_sk, []).append(sig)  # type: ignore[attr-defined]
            for sk, grp in groups.items():
                if len(grp) <= 1:
                    continue
                # garde : meilleur score, puis le plus recent (detected_at), puis id le plus bas
                grp.sort(key=lambda x: (x._eff_score,  # type: ignore[attr-defined]
                                        x.detected_at or x.id, -x.id), reverse=True)
                for dup in grp[1:]:
                    collapsed += 1
                    if args.apply:
                        s.delete(dup)

        if args.apply:
            s.commit()

    mode = "APPLIQUE" if args.apply else "APERCU (rien ecrit ; relance avec --apply)"
    log.info(
        "Backfill %s : %d a mettre a jour, %d a supprimer (score 0), "
        "%d doublons cross-media fusionnes, sur %d lignes.",
        mode, updated, deleted, collapsed, len(rows),
    )


if __name__ == "__main__":
    main()
