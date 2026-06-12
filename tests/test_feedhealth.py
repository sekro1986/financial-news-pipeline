"""Sante par flux : enregistrement collecteur, detection 402/silence, anti-spam."""
import datetime as dt
import tempfile
from pathlib import Path

from ma_signals import feedhealth, health
from ma_signals.config import settings
from ma_signals.db import SessionLocal, init_db
from ma_signals.models import FeedHealth

NOW = dt.datetime.now(dt.timezone.utc)
URL = "https://rss.app/feeds/dead.xml"


def _isolate(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(settings, "heartbeat_path", str(tmp / "hb.txt"))
    monkeypatch.setattr(settings, "health_state_path", str(tmp / "state.json"))
    monkeypatch.setattr(settings, "monitored_sources", "")  # focus flux


def test_record_echec_incremente_le_streak():
    init_db()
    for _ in range(3):
        feedhealth.record(URL, "rss_custom", 402, ok=False)
    with SessionLocal() as s:
        row = s.query(FeedHealth).filter_by(url=URL).one()
        assert row.fail_streak == 3 and row.last_status == 402


def test_record_succes_remet_a_zero_et_trace_le_dernier_item():
    init_db()
    feedhealth.record(URL, "rss_custom", 402, ok=False)
    feedhealth.record(URL, "rss_custom", 200, ok=True,
                      newest_item=NOW - dt.timedelta(hours=2))
    with SessionLocal() as s:
        row = s.query(FeedHealth).filter_by(url=URL).one()
        assert row.fail_streak == 0 and row.last_item_at is not None


def test_flux_402_detecte_apres_3_echecs(monkeypatch):
    _isolate(monkeypatch)
    init_db()
    for _ in range(3):
        feedhealth.record(URL, "rss_custom", 402, ok=False)
    sick = health.sick_feeds(NOW)
    assert sick and sick[0][0] == URL and "402" in sick[0][1]


def test_flux_vivant_mais_sans_item_frais(monkeypatch):
    _isolate(monkeypatch)
    init_db()
    feedhealth.record(URL, "rss_custom", 200, ok=True,
                      newest_item=NOW - dt.timedelta(hours=100))   # > 72 h
    sick = health.sick_feeds(NOW)
    assert sick and "aucun item frais" in sick[0][1]


def test_une_alerte_par_episode_puis_retablissement(monkeypatch):
    _isolate(monkeypatch)
    init_db()
    health.write_heartbeat(NOW)
    for _ in range(3):
        feedhealth.record(URL, "rss_custom", 402, ok=False)
    m1 = health.run_check(NOW)
    assert any("flux RSS malade" in m for m in m1)
    assert health.run_check(NOW) == []                 # pas de spam
    feedhealth.record(URL, "rss_custom", 200, ok=True, newest_item=NOW)
    m3 = health.run_check(NOW)
    assert any("rétabli" in m for m in m3)
