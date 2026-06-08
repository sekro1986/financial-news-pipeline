"""Tests du cycle de vie des alertes : statut + sent_at + report (anti-perte)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/life_test.db")

from ma_signals.config import settings                         # noqa: E402
from ma_signals.db import init_db, get_session                 # noqa: E402
from ma_signals.models import Signal                           # noqa: E402
from ma_signals.schema import RawItem                          # noqa: E402
from ma_signals.pipeline import process_items                  # noqa: E402
from ma_signals.alerting import get_pending_alerts, dispatch   # noqa: E402


def _n(status):
    with get_session() as s:
        return s.query(Signal).filter(Signal.status == status).count()


def test_status_assigned_on_creation():
    init_db()
    process_items([
        RawItem(source="press_rss", native_id="a", title="Alpha agrees to acquire Beta", company="Alpha"),   # alertable
        RawItem(source="press_rss", native_id="b", title="Gamma announces share buyback programme", company="Gamma"),  # sous seuil
    ])
    assert _n("en_attente") == 1
    assert _n("sous_seuil") == 1


def test_pending_carryover_no_loss(monkeypatch):
    init_db()
    items = [RawItem(source="press_rss", native_id=f"n{i}",
                     title=f"Comp{i} agrees to acquire Targ{i}", company=f"Comp{i}") for i in range(3)]
    process_items(items)
    monkeypatch.setattr(settings, "max_alerts_per_cycle", 2)   # plafond bas

    pending = get_pending_alerts()
    assert len(pending) == 3
    dispatch(pending)                       # envoie 2, le reste reste en_attente
    assert _n("envoye") == 2
    assert _n("en_attente") == 1

    dispatch(get_pending_alerts())          # cycle suivant : le reliquat part
    assert _n("envoye") == 3
    assert _n("en_attente") == 0
    with get_session() as s:
        assert all(x.sent_at is not None for x in s.query(Signal).filter_by(status="envoye"))
