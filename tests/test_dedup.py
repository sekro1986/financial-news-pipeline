"""Tests de la story_key et de la dedup cross-media au niveau pipeline."""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/dedup_test.db"

from ma_signals.dedup import story_key, normalize_title  # noqa: E402
from ma_signals.db import init_db, get_session            # noqa: E402
from ma_signals.schema import RawItem                      # noqa: E402
from ma_signals.pipeline import process_items              # noqa: E402
from ma_signals.models import Signal                       # noqa: E402


def test_source_suffix_stripped():
    assert normalize_title("Whirlpool Tender Offer - Yahoo Finance") == "whirlpool tender offer"


def test_same_company_event_same_key():
    a = story_key("Micromeritics", "merger_agt", "Spectris agrees to acquire Micromeritics - Yahoo")
    b = story_key("Micromeritics", "merger_agt", "Spectris to acquire Micromeritics, confirmed - Reuters")
    assert a == b == "co:micromeritics|mna"


def test_same_family_same_key():
    # possible_offer / tender_offer / merger_agt = la MEME saga M&A (cas MPS du 08/06)
    a = story_key("Monte dei Paschi", "possible_offer", "x")
    b = story_key("Monte dei Paschi", "tender_offer", "y")
    assert a == b


def test_different_family_different_key():
    a = story_key("Vodafone", "merger_agt", "x")        # famille mna
    b = story_key("Vodafone", "profit_warning", "x")    # famille earnings
    assert a != b


def test_pipeline_collapses_cross_source_duplicates():
    init_db()
    items = [
        RawItem(source="press_rss", native_id="d1",
                title="Spectris agrees to acquire Micromeritics - Yahoo Finance"),
        RawItem(source="press_rss", native_id="d2",
                title="Spectris agrees to acquire Micromeritics - Reuters"),
        RawItem(source="press_rss", native_id="d3",
                title="Spectris to acquire Micromeritics, deal confirmed - PR Newswire"),
        RawItem(source="press_rss", native_id="d4",
                title="Aviva agreed to acquire Direct Line - Sky News"),
    ]
    process_items(items)
    with get_session() as s:
        micro = s.query(Signal).filter(Signal.company == "Micromeritics").count()
        aviva = s.query(Signal).filter(Signal.company == "Direct Line").count()
    assert micro == 1, "les 3 republications du meme deal doivent fusionner en 1"
    assert aviva == 1, "un deal distinct reste conserve"
