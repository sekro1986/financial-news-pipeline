"""Dédup des doublons multi-sources/multi-langues (extraction robuste + canonical)."""
import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/msd_test.db")

from ma_signals.db import init_db, get_session       # noqa: E402
from ma_signals.models import Signal, WatchlistEntry  # noqa: E402
from ma_signals.schema import RawItem                # noqa: E402
from ma_signals.pipeline import process_items        # noqa: E402
from ma_signals.extract import guess_company         # noqa: E402


def test_extraction_consistent_company():
    pir = ["Pirelli shares recover after it denies short-seller report - Reuters",
           "Pirelli Slides After Short Seller Warns on Exposure to Russia - Bloomberg",
           "Tire Giant Pirelli Threatens Legal Action As Short Seller Alleges Ties"]
    assert {guess_company(t) for t in pir} == {"Pirelli"}
    evoke = ["Evoke accepte l'offre de rachat de Bally's Intralot pour 243 millions de GBP",
             "PALMARÈS : Evoke accepte une offre de rachat ; essais pour Imaging Biometrics",
             "Repli des prix au Royaume-Uni ; Evoke accepte une offre de rachat"]
    assert {guess_company(t) for t in evoke} == {"Evoke"}
    # la logique cible (acquéreur->cible) reste intacte
    assert guess_company("Spectris agrees to acquire Micromeritics") == "Micromeritics"


def test_pirelli_cluster_collapses():
    init_db()
    items = [
        RawItem("press_rss", "p1", "Pirelli shares fall after short-seller report on Russian ties"),
        RawItem("press_rss", "p2", "Pirelli Slides After Short Seller Warns on Exposure to Russia"),
        RawItem("press_rss", "p3", "Tire Giant Pirelli Threatens Legal Action As Short Seller Alleges Ties"),
    ]
    process_items(items)
    with get_session() as s:
        n = s.query(Signal).filter(Signal.company == "Pirelli").count()
    assert n == 1, "les 3 articles Pirelli doivent fusionner en 1"


def test_watchlist_canonical_cross_language():
    init_db()
    with get_session() as s:
        s.add(WatchlistEntry(name="Worthington Steel", aliases="Klöckner", active=1))
    # EN + FR du meme deal -> meme nom canonique -> 1 signal
    items = [
        RawItem("press_rss", "w1", "Worthington Steel Unveils Delisting Tender Offer for Klöckner & Co."),
        RawItem("disclosures", "w2", "Worthington Steel dévoile une offre publique de retrait visant Klöckner & Co."),
    ]
    process_items(items)
    with get_session() as s:
        n = s.query(Signal).filter(Signal.company == "Worthington Steel").count()
    assert n == 1, "EN + FR du meme deal doivent fusionner via le nom canonique"
