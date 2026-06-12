"""Garde-fous autofeed : prefixe-mot Yahoo + rejet des noms generiques."""
import datetime as dt

from ma_signals import autofeed, impact
from ma_signals.db import SessionLocal, init_db
from ma_signals.models import Signal

NOW = dt.datetime.now(dt.timezone.utc)


def _fake_yahoo(monkeypatch, quotes):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"quotes": quotes}
    monkeypatch.setattr(impact.httpx, "get", lambda *a, **k: R())


def test_orange_ne_matche_pas_orangekloud(monkeypatch):
    _fake_yahoo(monkeypatch, [
        {"quoteType": "EQUITY", "symbol": "ORKT",
         "shortname": "Orangekloud Technology", "longname": "Orangekloud Technology Inc."},
        {"quoteType": "EQUITY", "symbol": "ORA.PA",
         "shortname": "Orange", "longname": "Orange S.A."},
    ])
    assert impact.yahoo_search_symbol("Orange") == "ORA.PA"


def test_prefixe_exige_la_frontiere_de_mot(monkeypatch):
    _fake_yahoo(monkeypatch, [
        {"quoteType": "EQUITY", "symbol": "ORKT",
         "shortname": "Orangekloud Technology", "longname": "Orangekloud Technology Inc."},
    ])
    assert impact.yahoo_search_symbol("Orange") == ""


def _sig(company, story, n=[0]):
    n[0] += 1
    return Signal(source="press_rss", title=f"t{n[0]}", url=f"u{n[0]}", dedup_key=f"k{n[0]}",
                  company=company, event_type="equity_raise", score=8,
                  story_key=f"co:{story}|capital", detected_at=NOW)


def test_nom_generique_seul_jamais_candidat():
    init_db()
    with SessionLocal() as s:
        for x in (_sig("Capital", "c1"), _sig("Capital", "c2"), _sig("Capital", "c3")):
            s.add(x)
        s.commit()
    added, _ = autofeed.autofeed(resolve_fn=lambda name: "COF")
    assert added == []  # 'Capital' seul = artefact d'extraction, pas une societe
