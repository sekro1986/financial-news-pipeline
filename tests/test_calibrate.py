"""Calibration auto : ouverture/fermeture par scorecard, hysteresis, application poller."""
import datetime as dt
import tempfile
from pathlib import Path

from ma_signals import calibrate
from ma_signals.config import settings
from ma_signals.db import SessionLocal, init_db
from ma_signals.models import SignalOutcome

NOW = dt.datetime.now(dt.timezone.utc)


def _isolate(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(settings, "calibration_state_path", str(tmp / "cal.json"))


def _outcomes(family, confirmed, infirmed):
    init_db()
    with SessionLocal() as s:
        for i in range(confirmed):
            s.add(SignalOutcome(family=family, event_type="x", verdict="confirmé",
                                pct_since=2.0, run_at=NOW))
        for i in range(infirmed):
            s.add(SignalOutcome(family=family, event_type="x", verdict="infirmé",
                                pct_since=-1.0, run_at=NOW))
        s.commit()


def test_famille_fiable_s_ouvre(monkeypatch):
    _isolate(monkeypatch)
    _outcomes("mna", confirmed=28, infirmed=7)       # 80 % sur 35 verdicts
    open_fams, changes = calibrate.run_calibration(days=30)
    assert "mna" in open_fams
    assert any("OUVERTE" in c for c in changes)


def test_echantillon_insuffisant_statu_quo(monkeypatch):
    _isolate(monkeypatch)
    _outcomes("mna", confirmed=10, infirmed=1)       # 91 % mais n=11 < 30
    open_fams, changes = calibrate.run_calibration(days=30)
    assert open_fams == set() and changes == []


def test_hysteresis_fermeture_seulement_sous_close_rate(monkeypatch):
    _isolate(monkeypatch)
    calibrate.save_state({"open": ["mna"], "updated_at": ""})
    _outcomes("mna", confirmed=18, infirmed=14)      # 56 % : entre 50 et 65 -> reste ouverte
    open_fams, changes = calibrate.run_calibration(days=30)
    assert "mna" in open_fams and changes == []


def test_famille_decrochee_se_referme(monkeypatch):
    _isolate(monkeypatch)
    calibrate.save_state({"open": ["mna"], "updated_at": ""})
    _outcomes("mna", confirmed=10, infirmed=25)      # 29 % < 50 -> fermee
    open_fams, changes = calibrate.run_calibration(days=30)
    assert "mna" not in open_fams
    assert any("REFERMÉE" in c for c in changes)


def test_poller_applique_la_calibration(monkeypatch):
    _isolate(monkeypatch)
    calibrate.save_state({"open": ["mna"], "updated_at": ""})
    monkeypatch.setattr(settings, "calibration_enabled", True)
    monkeypatch.setattr(settings, "alerts_enabled", True)
    init_db()
    from ma_signals import poller
    from ma_signals.models import Signal
    with SessionLocal() as s:
        s.add(Signal(source="press_rss", title="a", url="ua", dedup_key="ka",
                     company="Acme", event_type="possible_offer", score=11,
                     status="en_attente", detected_at=NOW))     # mna : doit partir
        s.add(Signal(source="press_rss", title="b", url="ub", dedup_key="kb",
                     company="Bcme", event_type="profit_warning", score=11,
                     status="en_attente", detected_at=NOW))     # earnings : sourdine
        s.commit()
    sent = []
    monkeypatch.setattr(poller, "dispatch", lambda sigs: sent.extend(sigs))
    monkeypatch.setattr(poller, "process_items", lambda items, seed=False: [])
    monkeypatch.setattr(poller, "mark_unexplained_moves", lambda seed=False: [])
    monkeypatch.setattr(poller, "build_enabled", lambda srcs: [])
    poller.run_cycle()
    assert [s.event_type for s in sent] == ["possible_offer"]
