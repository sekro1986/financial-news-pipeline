"""Tests : digest groupé par famille + filtrage par qualité de source."""
import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/dq_test.db")

from ma_signals.config import settings              # noqa: E402
from ma_signals.db import init_db, get_session      # noqa: E402
from ma_signals.models import Signal                # noqa: E402
from ma_signals.schema import RawItem               # noqa: E402
from ma_signals.pipeline import process_items       # noqa: E402
from ma_signals.alerting import _build_messages     # noqa: E402


def test_digest_grouped_by_family():
    sigs = [
        Signal(event_type="merger_agt", score=12, company="A", title="A bid", source="press_rss"),
        Signal(event_type="profit_warning", score=8, company="B", title="B warns", source="press_rss"),
        Signal(event_type="redemption_gating", score=9, company="C", title="C gates", source="press_rss"),
    ]
    msg = "\n".join(_build_messages(sigs, 0))
    assert "signal(aux) détecté" in msg
    assert "M&A / contrôle" in msg
    assert "Résultats / guidance" in msg
    assert "Liquidité / fonds" in msg
    # plus d'entête trompeur "M&A" global
    assert "ALERTE" not in msg


def test_source_denylist_drops_clickbait():
    assert "mshale" in settings.source_deny_list
    init_db()
    items = [
        RawItem("press_rss", "j1", "Company agrees to acquire Target Corp - Mshale"),   # junk
        RawItem("press_rss", "r1", "Other agrees to acquire Rival Inc - Reuters"),       # ok
    ]
    process_items(items)
    with get_session() as s:
        titles = [r.title for r in s.query(Signal).all()]
    assert any("Reuters" in t for t in titles)
    assert all("Mshale" not in t for t in titles)
