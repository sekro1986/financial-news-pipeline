"""Couche LLM : parsing, garde-fous, et integration pipeline/impact.

Aucun test n'appelle l'API reelle : _call_api est monkeypatche."""
import datetime as dt
import json

from ma_signals import llm
from ma_signals.config import settings
from ma_signals.db import get_session
from ma_signals.models import Signal
from ma_signals.pipeline import process_items
from ma_signals.schema import RawItem


def _enable(monkeypatch, **over):
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    llm.reset_cycle()
    llm._cache.clear()


def _answer(monkeypatch, payload):
    monkeypatch.setattr(llm, "_call_api", lambda text: json.dumps(payload))


def _item(title, summary="", source="press_rss", uid=None):
    return RawItem(source=source, native_id=uid or title, title=title,
                   summary=summary, url="http://x", published_at=dt.datetime.now(dt.timezone.utc))


# ---------------------------- parsing & garde-fous ----------------------------

def test_enrich_parses_fields(monkeypatch):
    _enable(monkeypatch)
    _answer(monkeypatch, {"target": "MoneyGram", "acquirer": "Western Union",
                          "event_type": "possible_offer", "direction": "up", "confidence": 92})
    e = llm.enrich("Western Union Offers To Acquire MoneyGram")
    assert e.target == "MoneyGram" and e.acquirer == "Western Union"
    assert e.event_type == "possible_offer" and e.expected == 1 and e.confidence == 92


def test_enrich_rejects_unknown_event_type_and_clamps(monkeypatch):
    _enable(monkeypatch)
    _answer(monkeypatch, {"target": "X", "event_type": "alien_invasion",
                          "direction": "down", "confidence": 999})
    e = llm.enrich("titre")
    assert e.event_type == "" and e.expected == -1 and e.confidence == 100


def test_enrich_below_confidence_floor_returns_none(monkeypatch):
    _enable(monkeypatch)
    _answer(monkeypatch, {"target": "X", "event_type": "stake", "direction": "up", "confidence": 30})
    assert llm.enrich("titre") is None


def test_enrich_disabled_or_no_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_enabled", False)
    assert llm.enrich("titre") is None
    assert not llm.should_enrich(10)


def test_circuit_breaker_after_3_failures(monkeypatch):
    _enable(monkeypatch)
    def boom(text):
        raise RuntimeError("503")
    monkeypatch.setattr(llm, "_call_api", boom)
    for i in range(3):
        assert llm.enrich(f"t{i}") is None
    assert not llm.should_enrich(10)          # coupe-circuit
    llm.reset_cycle()
    assert llm.should_enrich(10)              # re-arme au cycle suivant


def test_budget_per_cycle(monkeypatch):
    _enable(monkeypatch, llm_max_per_cycle=1)
    _answer(monkeypatch, {"target": "A", "event_type": "stake", "direction": "up", "confidence": 90})
    assert llm.enrich("t1") is not None
    assert llm.enrich("t2") is None           # budget epuise
    assert llm.enrich("t1") is not None       # mais le cache reste servi


# ---------------------------- integration pipeline ----------------------------

def test_pipeline_uses_llm_entities_and_dedups_cross_language(monkeypatch):
    """Deux depeches EN/FR du meme deal -> meme cible LLM -> 1 seul signal,
    enrichi acquereur + sens attendu."""
    _enable(monkeypatch, llm_min_score=1)
    _answer(monkeypatch, {"target": "MoneyGram", "acquirer": "Western Union",
                          "event_type": "possible_offer", "direction": "up", "confidence": 95})
    items = [
        _item("Western Union Offers To Acquire MoneyGram", uid="en"),
        _item("Western Union fait une offre de rachat sur MoneyGram", uid="fr"),
    ]
    process_items(items)
    with get_session() as session:
        sigs = session.query(Signal).all()
        assert len(sigs) == 1
        s = sigs[0]
        assert s.company == "MoneyGram"
        assert s.acquirer == "Western Union"
        assert s.expected_move == 1 and s.llm_confidence == 95
        assert any("[llm]" in k for k in s.matched_keywords.split(","))


def test_pipeline_event_hint_beats_llm(monkeypatch):
    """Le type impose par un collecteur (ex prix) prime sur l'avis LLM."""
    _enable(monkeypatch, llm_min_score=1)
    _answer(monkeypatch, {"target": "Acme", "event_type": "merger_agt",
                          "direction": "up", "confidence": 90})
    it = _item("Acme agrees to acquire Foo")
    it.event_hint = "price_drop"
    it.score_override = 8
    process_items([it])
    with get_session() as session:
        s = session.query(Signal).one()
        assert s.event_type == "price_drop"


def test_pipeline_fallback_when_llm_unavailable(monkeypatch):
    """Sans LLM, comportement historique inchange (heuristiques regex)."""
    monkeypatch.setattr(settings, "llm_enabled", False)
    process_items([_item("Acme Corp agrees to acquire Foo Plc", uid="z")])
    with get_session() as session:
        s = session.query(Signal).one()
        assert s.expected_move is None and s.acquirer == ""


# ---------------------------- integration impact ----------------------------

def test_impact_prefers_stored_expected_move(monkeypatch):
    """Un signal avec expected_move LLM = -1 doit etre juge sur -1, meme si
    _EXPECTED dit +1 pour son event_type (ex: offre retiree)."""
    from ma_signals import impact

    day = impact.prev_business_day()
    ts = dt.datetime.combine(day, dt.time(10, 0), tzinfo=dt.timezone.utc)
    with get_session() as session:
        session.add(Signal(dedup_key="k1", story_key="sk1", source="press_rss",
                           event_type="possible_offer", company="Acme",
                           title="Bidder walks away from Acme", url="", summary="",
                           score=9, expected_move=-1, detected_at=ts))

    monkeypatch.setattr(impact, "yahoo_search_symbol", lambda c: "ACME")
    fake = {"pct_since": -8.0, "pct_day": -8.0, "last_date": str(day)}
    rep = impact.build_report(day=day, price_fn=lambda sym, d: dict(fake))
    row = [r for r in rep["rows"] if r["company"] == "Acme"][0]
    assert row["expected_dir"] == -1
    assert row["verdict"] == "confirmé"
