"""Autofeed watchlist : candidates, garde-fous, plafond, prune."""
import datetime as dt

from ma_signals import autofeed
from ma_signals.config import settings
from ma_signals.db import SessionLocal, init_db
from ma_signals.models import Signal, WatchlistEntry

NOW = dt.datetime.now(dt.timezone.utc)


def _sig(company, story, age_days=1, source="press_rss", n=[0]):
    n[0] += 1
    return Signal(source=source, title=f"t{n[0]}", url=f"u{n[0]}", dedup_key=f"k{n[0]}",
                  company=company, event_type="possible_offer", score=8,
                  story_key=f"co:{story}|mna",
                  detected_at=NOW - dt.timedelta(days=age_days))


def _seed(*sigs, entries=()):
    init_db()
    with SessionLocal() as s:
        for x in sigs:
            s.add(x)
        for e in entries:
            s.add(e)
        s.commit()


def test_candidate_recurrente_ajoutee_avec_ticker_resolu():
    _seed(_sig("Wetherspoon", "wls-1"), _sig("J D Wetherspoon", "wls-2"),
          _sig("Wetherspoon", "wls-3"))
    added, pruned = autofeed.autofeed(resolve_fn=lambda name: "JDW.L")
    assert len(added) == 1 and added[0]["yf_symbol"] == "JDW.L"
    assert added[0]["name"] == "Wetherspoon"  # nom le plus FRÉQUENT du cluster (2x vs 1x)
    with SessionLocal() as s:
        e = s.query(WatchlistEntry).one()
        assert e.origin == "auto" and e.active == 1 and e.ticker == "JDW"


def test_ticker_non_resolu_jamais_ajoute():
    _seed(_sig("Mystere Prive", "m1"), _sig("Mystere Prive", "m2"),
          _sig("Mystere Prive", "m3"))
    added, _ = autofeed.autofeed(resolve_fn=lambda name: "")
    assert added == []
    with SessionLocal() as s:
        assert s.query(WatchlistEntry).count() == 0


def test_pas_assez_d_histoires_distinctes():
    _seed(_sig("Acme", "a1"), _sig("Acme", "a1"), _sig("Acme", "a2"))  # 2 distinctes < 3
    added, _ = autofeed.autofeed(resolve_fn=lambda name: "ACME")
    assert added == []


def test_deja_en_watchlist_exclue():
    _seed(_sig("Partners Group", "p1"), _sig("Partners Group", "p2"),
          _sig("Partners Group Holding", "p3"),
          entries=(WatchlistEntry(name="Partners Group Holding AG", yf_symbol="PGHN.SW"),))
    added, _ = autofeed.autofeed(resolve_fn=lambda name: "PGHN.SW")
    assert added == []


def test_plafond_d_ajouts(monkeypatch):
    monkeypatch.setattr(settings, "autofeed_max_adds", 1)
    _seed(_sig("Alpha One", "a1"), _sig("Alpha One", "a2"), _sig("Alpha One", "a3"),
          _sig("Beta Two", "b1"), _sig("Beta Two", "b2"), _sig("Beta Two", "b3"))
    added, _ = autofeed.autofeed(resolve_fn=lambda name: "XX")
    assert len(added) == 1


def test_prune_entree_auto_muette_mais_jamais_les_manuelles():
    _seed(_sig("Gamma Corp", "g1", age_days=1),
          entries=(WatchlistEntry(name="Gamma Corp", origin="auto"),
                   WatchlistEntry(name="Vieille Auto", origin="auto"),
                   WatchlistEntry(name="Vieille Manuelle", origin="")))
    added, pruned = autofeed.autofeed(resolve_fn=lambda name: "")
    assert pruned == ["Vieille Auto"]   # muette -> desactivee
    with SessionLocal() as s:
        by_name = {e.name: e for e in s.query(WatchlistEntry).all()}
        assert by_name["Vieille Auto"].active == 0
        assert by_name["Gamma Corp"].active == 1        # a des signaux recents
        assert by_name["Vieille Manuelle"].active == 1  # manuelle : intouchable


def test_dry_run_n_ecrit_rien():
    _seed(_sig("Delta Co", "d1"), _sig("Delta Co", "d2"), _sig("Delta Co", "d3"),
          entries=(WatchlistEntry(name="Morte Auto", origin="auto"),))
    added, pruned = autofeed.autofeed(dry_run=True, resolve_fn=lambda name: "DD")
    assert len(added) == 1 and pruned == ["Morte Auto"]
    with SessionLocal() as s:
        assert s.query(WatchlistEntry).count() == 1          # pas d'ajout
        assert s.query(WatchlistEntry).one().active == 1     # pas de prune
