"""Tests screener anticipation (offline : pas d'appel Yahoo)."""
import os
import tempfile
import datetime as dt

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/scr_test.db")

from ma_signals.collectors.screener import target_score, TargetScreener  # noqa: E402
from ma_signals.config import settings                                   # noqa: E402
from ma_signals.db import init_db, get_session                           # noqa: E402
from ma_signals.models import Signal                                     # noqa: E402
from ma_signals.schema import RawItem                                    # noqa: E402
from ma_signals.pipeline import process_items                            # noqa: E402


def test_target_score_requires_both():
    assert target_score(True, 1)[0] >= family_thr()      # decote + accumulation -> alertable
    assert target_score(True, 0)[0] < family_thr()       # decote seule -> sous le seuil
    assert target_score(False, 1)[0] < family_thr()      # accumulation seule -> sous le seuil
    assert target_score(True, 1)[1] == "target_candidate"
    assert target_score(True, 0)[1] == "undervalued"
    assert target_score(False, 1)[1] == "accumulation"


def family_thr():
    from ma_signals.classifier import family_threshold
    return family_threshold("anticipation")


def test_accum_count_matches_watchlist_terms():
    init_db()
    with get_session() as s:
        s.add(Signal(dedup_key="a1", story_key="x", source="amf_france", event_type="stake",
                     company="Partners Group", title="X franchit un seuil au capital de Partners Group",
                     score=6, detected_at=dt.datetime.now(dt.timezone.utc)))
        s.add(Signal(dedup_key="a2", story_key="y", source="sec_edgar", event_type="stake_13d",
                     company="Other Co", title="SC 13D - OTHER CO",
                     score=7, detected_at=dt.datetime.now(dt.timezone.utc)))
    sc = TargetScreener()
    with get_session() as s:
        n = sc._accum_count(s, ["partners group", "pghn"])
    sc.close()
    assert n == 1   # seul le signal Partners Group matche


def test_pipeline_target_candidate_alerts():
    init_db()
    items = [
        RawItem(source="screener", native_id="PGHN.SW:2026-06-05:target_candidate",
                title="Partners Group Holding AG: proie potentielle", company="Partners Group Holding AG",
                event_hint="target_candidate", score_override=9),
        RawItem(source="screener", native_id="EQT.ST:2026-06-05:undervalued",
                title="EQT AB: decotee", company="EQT AB",
                event_hint="undervalued", score_override=4),
    ]
    alerts = process_items(items)
    assert any(a.event_type == "target_candidate" for a in alerts)   # 9 >= seuil(7)
    assert all(a.event_type != "undervalued" for a in alerts)        # 4 < 7 : stocke, pas alerte
