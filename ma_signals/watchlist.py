"""Watchlist d'emetteurs surveilles (pivot) + outils de gestion.

CLI :
  python -m ma_signals.watchlist list
  python -m ma_signals.watchlist import [fichier.yaml]      # seed/upsert depuis un fichier
  python -m ma_signals.watchlist enrich [--apply]            # remplit ticker/yf/lei via OpenFIGI+GLEIF
  python -m ma_signals.watchlist add --name NOM [--isin ...] [--ir-url ...] [--alias a,b]

Le fichier d'import (YAML ou CSV) liste des emetteurs avec au minimum un `name`
et, idealement, un `isin` et une `ir_adhoc_url`. L'enrichissement complete le reste.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .models import WatchlistEntry

log = logging.getLogger("ma_signals.watchlist")


# ----------------------------- lecture / matching -----------------------------
def active_entries() -> list[WatchlistEntry]:
    with SessionLocal() as s:
        return list(s.scalars(select(WatchlistEntry).where(WatchlistEntry.active == 1)).all())


def yf_symbols() -> list[str]:
    """Symboles yfinance des emetteurs actifs (pour le moniteur de prix)."""
    return [e.yf_symbol for e in active_entries() if e.yf_symbol]


def adhoc_targets() -> list[tuple[str, str]]:
    """(nom, url ad-hoc) des emetteurs actifs ayant une page IR (pour le scraper CH)."""
    return [(e.name, e.ir_adhoc_url) for e in active_entries() if e.ir_adhoc_url]


def watchlist_terms() -> set[str]:
    """Tous les termes de reconnaissance (nom/alias/ticker/isin), en minuscule."""
    terms: set[str] = set()
    for e in active_entries():
        terms.update(e.match_terms)
    return terms


def match_text(text: str, terms: set[str] | None = None) -> str | None:
    """Renvoie le 1er terme de watchlist present dans le texte, sinon None."""
    if not text:
        return None
    hay = text.lower()
    for t in (terms if terms is not None else watchlist_terms()):
        if t and t in hay:
            return t
    return None


# ----------------------------- import / seed -----------------------------
def _load_file(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if path.endswith(".csv"):
        with open(path, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    # YAML par defaut (gere aussi .yaml.example)
    import yaml  # dependance optionnelle
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or []
    return doc.get("watchlist", doc) if isinstance(doc, dict) else doc


def upsert(session, rec: dict) -> tuple[WatchlistEntry, bool]:
    name = (rec.get("name") or "").strip()
    if not name:
        raise ValueError("entree sans 'name'")
    e = session.scalar(select(WatchlistEntry).where(WatchlistEntry.name == name))
    created = e is None
    if created:
        e = WatchlistEntry(name=name)
        session.add(e)
    for f in ("isin", "ticker", "exch_code", "yf_symbol", "lei", "figi", "country", "ir_adhoc_url", "notes"):
        if rec.get(f):
            setattr(e, f, str(rec[f]).strip())
    al = rec.get("aliases")
    if al:
        e.aliases = ",".join(al) if isinstance(al, list) else str(al)
    if "active" in rec:
        e.active = 1 if str(rec["active"]).lower() in ("1", "true", "yes", "oui") else 0
    return e, created


def import_file(path: str) -> tuple[int, int]:
    init_db()
    added = updated = 0
    with SessionLocal() as s:
        for rec in _load_file(path):
            _, created = upsert(s, rec)
            added += created
            updated += (not created)
        s.commit()
    return added, updated


# ----------------------------- enrichissement -----------------------------
def enrich(apply: bool = False) -> int:
    """Complete ticker/exch/yf_symbol/figi (OpenFIGI via ISIN) et lei/country (GLEIF)
    pour les entrees actives auxquelles il manque ces champs. Renvoie le nb modifie."""
    from .symbology import gleif_lookup, openfigi_by_isin

    init_db()
    changed = 0
    with SessionLocal() as s:
        for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.active == 1)).all():
            before = (e.ticker, e.yf_symbol, e.figi, e.lei, e.country)
            if e.isin and not e.yf_symbol:
                try:
                    m = openfigi_by_isin(e.isin)
                    if m:
                        e.ticker = e.ticker or m["ticker"]
                        e.exch_code = e.exch_code or m["exch_code"]
                        e.yf_symbol = e.yf_symbol or m["yf_symbol"]
                        e.figi = e.figi or m["figi"]
                except Exception as exc:  # noqa: BLE001
                    log.warning("OpenFIGI %s: %s", e.name, exc)
            if not e.lei:
                try:
                    g = gleif_lookup(name=e.name, isin=e.isin)
                    if g:
                        e.lei = e.lei or g["lei"]
                        e.country = e.country or g["country"]
                except Exception as exc:  # noqa: BLE001
                    log.warning("GLEIF %s: %s", e.name, exc)
            if (e.ticker, e.yf_symbol, e.figi, e.lei, e.country) != before:
                changed += 1
                log.info("enrichi: %s -> ticker=%s yf=%s lei=%s", e.name, e.ticker, e.yf_symbol, e.lei)
        if apply:
            s.commit()
        else:
            s.rollback()
    return changed


# ----------------------------- CLI -----------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Gestion de la watchlist MA-Signals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pi = sub.add_parser("import"); pi.add_argument("file", nargs="?", default=settings.watchlist_file)
    pe = sub.add_parser("enrich"); pe.add_argument("--apply", action="store_true")
    pa = sub.add_parser("add")
    pa.add_argument("--name", required=True); pa.add_argument("--isin", default="")
    pa.add_argument("--ir-url", default=""); pa.add_argument("--alias", default="")
    args = ap.parse_args()

    if args.cmd == "list":
        for e in active_entries():
            print(f"  [{e.id}] {e.name:30} isin={e.isin:14} yf={e.yf_symbol:10} lei={e.lei} adhoc={'oui' if e.ir_adhoc_url else 'non'}")
    elif args.cmd == "import":
        a, u = import_file(args.file)
        print(f"Import {args.file} : {a} ajoutes, {u} mis a jour.")
    elif args.cmd == "enrich":
        n = enrich(apply=args.apply)
        print(f"Enrichissement {'APPLIQUE' if args.apply else 'APERCU'} : {n} entrees modifiees.")
    elif args.cmd == "add":
        init_db()
        with SessionLocal() as s:
            rec = {"name": args.name, "isin": args.isin, "ir_adhoc_url": args.ir_url,
                   "aliases": args.alias.split(",") if args.alias else []}
            _, created = upsert(s, rec)
            s.commit()
        print(f"{'Ajoute' if created else 'Mis a jour'} : {args.name}")


if __name__ == "__main__":
    main()
