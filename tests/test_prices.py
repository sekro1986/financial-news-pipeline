"""Tests collecteur de prix (offline : pas d'appel reseau)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/px_test.db")

from ma_signals.collectors.prices import PriceCollector, score_for_move  # noqa: E402
from ma_signals.db import init_db, get_session                           # noqa: E402
from ma_signals.schema import RawItem                                    # noqa: E402
from ma_signals.pipeline import process_items                            # noqa: E402
from ma_signals.models import Signal                                     # noqa: E402


def test_score_for_move_scale():
    assert score_for_move(18) == 10
    assert score_for_move(-12) == 8     # signe ignore (drop ou spike)
    assert score_for_move(7) == 7
    assert score_for_move(5) == 6       # = seuil famille market
    assert score_for_move(2) == 0       # sous price_min_pct


def test_analyze_computes_pct():
    res = {
        "meta": {"chartPreviousClose": 100.0, "regularMarketPrice": 88.0, "currency": "CHF"},
        "timestamp": [],
        "indicators": {"quote": [{"close": [], "volume": []}]},
    }
    pct, last, prev, volr = PriceCollector._analyze(res)
    assert prev == 100.0 and last == 88.0
    assert round(pct, 1) == -12.0


def test_price_item_alerts_via_override():
    init_db()
    items = [
        RawItem(source="prices", native_id="PGHN.SW:2026-06-04:price_drop",
                title="Partners Group Holding AG -12.5% intraday (PGHN.SW)",
                company="Partners Group Holding AG", event_hint="price_drop", score_override=8),
        RawItem(source="prices", native_id="KKR:2026-06-04:price_drop",
                title="KKR & Co Inc -4.7% intraday (KKR)",
                company="KKR & Co Inc", event_hint="price_drop", score_override=4),
    ]
    alerts = process_items(items)
    titles = [a.title[:14] for a in alerts]
    assert any("Partners Group" in a.title for a in alerts)   # 8 >= seuil market(6)
    assert all("KKR" not in a.title for a in alerts)          # 4 < 6 : stocke, pas alerte
    with get_session() as s:
        kkr = s.query(Signal).filter(Signal.company == "KKR & Co Inc").first()
    assert kkr is not None and kkr.score == 4 and kkr.event_type == "price_drop"
