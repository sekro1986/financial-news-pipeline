"""Healthcheck : heartbeat perime, sources muettes, anti-spam par episode."""
import datetime as dt
import tempfile
from pathlib import Path

from ma_signals import health
from ma_signals.config import settings
from ma_signals.db import SessionLocal
from ma_signals.models import Signal

NOW = dt.datetime(2026, 6, 11, 12, 0, tzinfo=dt.timezone.utc)


def _isolate(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(settings, "heartbeat_path", str(tmp / "hb.txt"))
    monkeypatch.setattr(settings, "health_state_path", str(tmp / "state.json"))
    return tmp


def _add_signal(source: str, age_hours: float):
    with SessionLocal() as s:
        s.add(Signal(source=source, title=f"t-{source}", url=f"u-{source}-{age_hours}",
                     dedup_key=f"k-{source}-{age_hours}", event_type="possible_offer",
                     score=5, detected_at=NOW - dt.timedelta(hours=age_hours)))
        s.commit()


def test_heartbeat_frais_et_perime(monkeypatch):
    _isolate(monkeypatch)
    assert health.check_heartbeat(NOW) is None        # jamais ecrit : silence
    health.write_heartbeat(NOW - dt.timedelta(minutes=10))
    assert health.check_heartbeat(NOW) is None        # frais
    health.write_heartbeat(NOW - dt.timedelta(minutes=45))
    assert "45 min" in health.check_heartbeat(NOW)    # perime (seuil 30)


def test_source_muette_detectee(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(settings, "monitored_sources", "sec_edgar,rns_uk")
    monkeypatch.setattr(settings, "enabled_sources", "sec_edgar,rns_uk")
    _add_signal("sec_edgar", age_hours=2)     # actif
    _add_signal("rns_uk", age_hours=30)       # muet (> 24 h)
    silent = health.silent_sources(NOW)
    assert [s for s, _ in silent] == ["rns_uk"]


def test_source_jamais_vue_fraiche_installation(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(settings, "monitored_sources", "mfn")
    monkeypatch.setattr(settings, "enabled_sources", "mfn,sec_edgar")
    _add_signal("sec_edgar", age_hours=1)     # base jeune -> pas d'alerte mfn
    assert health.silent_sources(NOW) == []
    with SessionLocal() as s:                  # base ancienne -> alerte mfn
        s.query(Signal).delete(); s.commit()
    _add_signal("sec_edgar", age_hours=50)
    assert [s for s, _ in health.silent_sources(NOW)] == ["mfn"]


def test_une_alerte_par_episode_puis_retablissement(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(settings, "monitored_sources", "rns_uk")
    monkeypatch.setattr(settings, "enabled_sources", "rns_uk")
    _add_signal("rns_uk", age_hours=30)
    health.write_heartbeat(NOW)                       # poller OK
    m1 = health.run_check(NOW)                        # 1er passage : alerte
    assert any("muette" in m for m in m1)
    m2 = health.run_check(NOW)                        # 2e passage : silence
    assert m2 == []
    _add_signal("rns_uk", age_hours=1)                # la source reparle
    m3 = health.run_check(NOW)
    assert any("de nouveau active" in m for m in m3)  # retablissement
