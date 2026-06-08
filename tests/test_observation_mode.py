"""Tests du mode observation (alerts_enabled) + réouverture sélective par famille."""
import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/obs_test.db")

from ma_signals.config import settings              # noqa: E402
from ma_signals.db import init_db, get_session      # noqa: E402
from ma_signals.models import Signal                # noqa: E402
from ma_signals.schema import RawItem               # noqa: E402
from ma_signals.pipeline import process_items       # noqa: E402
from ma_signals.alerting import get_pending_alerts, silence_pending, dispatch  # noqa: E402
from ma_signals.classifier import family_of         # noqa: E402


def _count(status):
    with get_session() as s:
        return s.query(Signal).filter(Signal.status == status).count()


def _seed_two_families():
    process_items([
        RawItem("press_rss", "m1", "Alpha agrees to acquire Beta", company="Alpha"),       # mna
        RawItem("press_rss", "e1", "Gamma issues profit warning, cuts guidance", company="Gamma"),  # earnings
    ])


def test_observation_mode_silences_everything(monkeypatch):
    init_db(); _seed_two_families()
    monkeypatch.setattr(settings, "alerts_enabled", False)
    pending = get_pending_alerts()
    assert len(pending) == 2
    # simulate run_cycle's observation branch
    silence_pending(pending)
    assert _count("silencieux") == 2
    assert _count("envoye") == 0
    assert len(get_pending_alerts()) == 0   # file vidée -> plus de spam ni d'accumulation


def test_selective_reopen_by_family(monkeypatch):
    init_db(); _seed_two_families()
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_only_families", "mna")
    pending = get_pending_alerts()
    allow = settings.alert_only_family_list
    to_send = [s for s in pending if family_of(s.event_type) in allow]
    to_silence = [s for s in pending if family_of(s.event_type) not in allow]
    dispatch(to_send); silence_pending(to_silence)
    assert _count("envoye") == 1        # la famille mna part
    assert _count("silencieux") == 1    # earnings reste en sourdine
