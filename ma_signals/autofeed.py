"""Auto-alimentation de la watchlist depuis les signaux captés.

La watchlist est le pivot de la veille active (prix, IR ad-hoc, screener,
corrélation, canonicalisation des noms) mais elle vivait à la main. Ici, on
boucle : les sociétés qui reviennent dans les signaux SANS être surveillées
deviennent candidates, et celles qui ne produisent plus rien sortent.

Entrée (ajout) — tous les critères, dans l'ordre :
  1. cluster de signaux récents (autofeed_window_days) regroupés par
     rapprochement de noms (dedup.same_story_company) ;
  2. >= autofeed_min_stories histoires DISTINCTES (story_key) — une société
     qui fait l'actualité une seule fois ne rentre pas ;
  3. pas déjà couverte par une entrée existante (active ou non) ;
  4. ticker résolu par la recherche Yahoo durcie d'impact (un nom non résolu
     = junk ou société privée -> REJET, jamais d'entrée sale) ;
  5. plafond autofeed_max_adds par run.

Sortie (prune) : les entrées origin='auto' sans AUCUN signal depuis
autofeed_prune_days sont désactivées (active=0, jamais supprimées). Les
entrées manuelles ne sont JAMAIS touchées.

Tout passe par un récap (stdout / --send Telegram) : l'humain garde la main.

Usage : python -m ma_signals.autofeed [--dry-run] [--send]
        (timer systemd : deploy/masignals-autofeed.{service,timer}, samedi 09:00)
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from .config import settings
from .dedup import company_tokens, same_story_company

log = logging.getLogger("ma_signals.autofeed")

# Un candidat dont le nom se reduit a UN token hyper-generique de la finance
# est un artefact d'extraction ('Capital' <- 'capital increase'), pas une
# societe identifiable : la recherche Yahoo renverrait n'importe qui
# ('Capital' -> Capital One). On exige au moins un token discriminant.
_GENERIC_LONERS = {
    "capital", "holdings", "energy", "financial", "finance", "bank", "banca",
    "national", "international", "first", "partners", "industries", "resources",
    "pharma", "media", "digital", "global", "investment", "investments",
    "properties", "ventures", "tech", "technologies", "solutions",
}


@dataclass
class Candidate:
    name: str                       # nom le plus fréquent du cluster
    names: Counter = field(default_factory=Counter)
    stories: set = field(default_factory=set)
    sources: set = field(default_factory=set)

    @property
    def best_name(self) -> str:
        top = self.names.most_common()
        if not top:
            return self.name
        best_count = top[0][1]
        # à fréquence égale, le nom le plus long (souvent le plus complet)
        return max((n for n, c in top if c == best_count), key=len)


def build_candidates(window_days: int, min_stories: int) -> list[Candidate]:
    """Clusters de sociétés récurrentes dans les signaux récents, hors watchlist."""
    from .db import SessionLocal
    from .models import Signal, WatchlistEntry

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    with SessionLocal() as s:
        rows = s.execute(
            select(Signal.company, Signal.story_key, Signal.source)
            .where(Signal.detected_at >= cutoff, Signal.company != "")
        ).all()
        existing = list(s.scalars(select(WatchlistEntry)).all())
        known: list[str] = []
        for e in existing:
            known.extend([e.name] + [a for a in (e.aliases or "").split(",") if a.strip()])

    clusters: list[Candidate] = []
    for company, sk, source in rows:
        if not company_tokens(company):
            continue  # nom-bruit ('Le', 'Short-seller') : jamais candidat
        cl = next((c for c in clusters
                   if any(same_story_company(n, company) for n in c.names)), None)
        if cl is None:
            cl = Candidate(name=company)
            clusters.append(cl)
        cl.names[company] += 1
        cl.stories.add(sk)
        cl.sources.add(source)

    out = []
    for cl in clusters:
        if len(cl.stories) < min_stories:
            continue
        toks = company_tokens(cl.best_name)
        if toks <= _GENERIC_LONERS:
            continue  # nom trop generique pour etre resolu sans ambiguite
        if any(same_story_company(cl.best_name, k) for k in known):
            continue  # déjà couvert par la watchlist
        out.append(cl)
    # les plus actives d'abord (histoires, puis diversité de sources)
    out.sort(key=lambda c: (len(c.stories), len(c.sources)), reverse=True)
    return out


def autofeed(dry_run: bool = False, resolve_fn=None) -> tuple[list[dict], list[str]]:
    """Ajoute les candidates résolues (plafonnées), désactive les entrées auto
    muettes. Retourne (ajouts, noms désactivés)."""
    from .db import SessionLocal
    from .models import Signal, WatchlistEntry

    if resolve_fn is None:
        from .impact import yahoo_search_symbol as resolve_fn

    added: list[dict] = []
    for cl in build_candidates(settings.autofeed_window_days, settings.autofeed_min_stories):
        if len(added) >= settings.autofeed_max_adds:
            break
        name = cl.best_name
        symbol = resolve_fn(name)
        if not symbol:
            log.info("candidat rejeté (ticker non résolu) : %r (%d histoires)",
                     name, len(cl.stories))
            continue
        rec = {"name": name, "yf_symbol": symbol, "ticker": symbol.split(".")[0],
               "stories": len(cl.stories), "sources": sorted(cl.sources)}
        added.append(rec)
        if not dry_run:
            with SessionLocal() as s:
                s.add(WatchlistEntry(
                    name=name, ticker=rec["ticker"], yf_symbol=symbol,
                    origin="auto", active=1,
                    notes=f"autofeed {dt.date.today().isoformat()} : "
                          f"{len(cl.stories)} histoires / {len(cl.sources)} sources"))
                s.commit()

    # --- prune : entrées AUTO sans signal depuis autofeed_prune_days ---
    pruned: list[str] = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=settings.autofeed_prune_days)
    with SessionLocal() as s:
        autos = list(s.scalars(select(WatchlistEntry).where(
            WatchlistEntry.origin == "auto", WatchlistEntry.active == 1)).all())
        if autos:
            recent_companies = {c for (c,) in s.execute(
                select(Signal.company).where(
                    Signal.detected_at >= cutoff, Signal.company != "").distinct()).all()}
            for e in autos:
                if any(n in {a["name"] for a in added} for n in (e.name,)):
                    continue  # ajoutée à l'instant
                if not any(same_story_company(e.name, c) for c in recent_companies):
                    pruned.append(e.name)
                    if not dry_run:
                        e.active = 0
                        e.notes = (e.notes or "") + f" | prune {dt.date.today().isoformat()}"
            if not dry_run:
                s.commit()
    return added, pruned


def format_recap(added: list[dict], pruned: list[str], dry_run: bool) -> str:
    mode = " (DRY-RUN, rien n'est écrit)" if dry_run else ""
    lines = [f"🧭 Watchlist autofeed{mode}"]
    if added:
        lines.append(f"\n➕ {len(added)} entrée(s) ajoutée(s) :")
        for a in added:
            lines.append(f"  • {a['name']} -> {a['yf_symbol']} "
                         f"({a['stories']} histoires, sources : {', '.join(a['sources'])})")
        lines.append("  Compléter ISIN/LEI/page IR : python -m ma_signals.watchlist enrich --apply")
    else:
        lines.append("\nAucune nouvelle candidate (récurrente, hors watchlist, ticker résolu).")
    if pruned:
        lines.append(f"\n➖ {len(pruned)} entrée(s) auto désactivée(s) (muettes depuis "
                     f"{settings.autofeed_prune_days} j) : " + ", ".join(pruned))
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Auto-alimentation de la watchlist")
    parser.add_argument("--dry-run", action="store_true", help="montre sans écrire")
    parser.add_argument("--send", action="store_true", help="envoie le récap (Telegram/Slack)")
    args = parser.parse_args()

    from .db import init_db
    init_db()
    added, pruned = autofeed(dry_run=args.dry_run)
    text = format_recap(added, pruned, args.dry_run)
    print(text)
    if args.send and (added or pruned):
        from .alerting import send_message
        send_message(text)


if __name__ == "__main__":
    main()
