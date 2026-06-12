"""Replay des règles : basculements d'alerte, hint conservé, croisement verdicts."""
import datetime as dt

from ma_signals import replay
from ma_signals.classifier import classify, family_of, family_threshold
from ma_signals.db import SessionLocal
from ma_signals.models import Signal, SignalOutcome

NOW = dt.datetime.now(dt.timezone.utc)
# Titre qui matche fortement les règles M&A actuelles :
HOT = "Possible offer: Bidco weighs takeover approach for Acme Corp"
assert classify(HOT).score >= family_threshold(family_of(classify(HOT).event_type)), \
    "préconditions du test : ce titre doit être alertable avec les règles courantes"


def _add(title, source="press_rss", event_type="generic", score=0, sig_id=None):
    with SessionLocal() as s:
        sig = Signal(id=sig_id, source=source, title=title, url=f"u-{title[:30]}",
                     dedup_key=f"k-{title[:40]}", event_type=event_type,
                     score=score, detected_at=NOW)
        s.add(sig); s.commit()
        return sig.id


def test_alerte_gagnee():
    _add(HOT, event_type="generic", score=1)          # sous-noté à l'époque
    rep = replay.run_replay(days=7)
    assert len(rep.gained) == 1 and rep.lost == []
    assert rep.gained[0].new_score >= 8


def test_alerte_perdue_et_verdict():
    sid = _add("Quarterly weather outlook for gardeners",   # plus rien ne matche
               event_type="possible_offer", score=11)
    with SessionLocal() as s:
        s.add(SignalOutcome(signal_id=sid, verdict="confirmé")); s.commit()
    rep = replay.run_replay(days=7)
    assert len(rep.lost) == 1 and rep.gained == []
    assert rep.lost[0].verdict == "confirmé"
    assert "régression probable" in replay.format_report(rep)


def test_hint_source_conserve_le_type():
    _add("SC 14D9 filing", source="sec_edgar", event_type="tender_offer", score=9)
    rep = replay.run_replay(days=7)
    diffs = rep.gained + rep.lost
    # le type collecteur est conservé (pas de re-typage regex sur EDGAR)
    assert all(d.new_type == "tender_offer" for d in diffs) or rep.retyped == 0


def test_sources_collecteur_exclues():
    _add("Price drop -12%", source="prices", event_type="price_drop", score=8)
    _add("Undervalued + accumulation", source="screener", event_type="target_candidate", score=9)
    rep = replay.run_replay(days=7)
    assert rep.total == 0


def test_signal_inchange_est_neutre():
    c = classify(HOT)
    _add(HOT, event_type=c.event_type, score=c.score)
    rep = replay.run_replay(days=7)
    assert rep.unchanged == 1 and rep.gained == [] and rep.lost == []
    assert "neutre" in replay.format_report(rep)
