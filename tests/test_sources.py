"""Tests des nouvelles sources : registre disclosures + bonus curated generalise."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/sources_test.db")

from ma_signals.config import settings          # noqa: E402
from ma_signals.collectors import REGISTRY       # noqa: E402
from ma_signals.db import init_db, get_session   # noqa: E402
from ma_signals.schema import RawItem            # noqa: E402
from ma_signals.pipeline import process_items     # noqa: E402
from ma_signals.models import Signal             # noqa: E402


def test_disclosures_registered():
    assert "disclosures" in REGISTRY


def test_curated_sources_config():
    assert "rss_custom" in settings.curated_source_list
    assert "disclosures" in settings.curated_source_list


def test_curated_bonus_applies_to_disclosures():
    init_db()
    bonus = settings.curated_score_bonus
    # "strategic review" -> score de base 5 (famille mna)
    items = [
        RawItem(source="press_rss",   native_id="srp", title="Alpha announces strategic review"),
        RawItem(source="disclosures", native_id="srd", title="Beta announces strategic review"),
    ]
    process_items(items)
    with get_session() as s:
        base = s.query(Signal).filter_by(dedup_key=items[0].dedup_key).first()
        cur  = s.query(Signal).filter_by(dedup_key=items[1].dedup_key).first()
    assert base is not None and cur is not None
    assert base.score == 5
    assert cur.score == 5 + bonus, (cur.score, bonus)
