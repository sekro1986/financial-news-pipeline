"""Tests scorecard (agrégation des verdicts d'impact) + nuance de sens."""
import os, tempfile, datetime as dt
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/sc_test.db")

from ma_signals.db import init_db, get_session    # noqa: E402
from ma_signals.models import SignalOutcome        # noqa: E402
from ma_signals.scorecard import build_scorecard    # noqa: E402
from ma_signals.impact import refine_expected        # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def test_nuance_layer():
    # short-seller démenti -> pas de verdict
    assert refine_expected("short_seller", "Pirelli", "Pirelli shares recover after it denies short-seller report") == 0
    # profit warning "perte se réduit" -> positif
    assert refine_expected("profit_warning", "X", "X profit warning: annual loss expected to narrow significantly") == 1
    # augmentation de capital sursouscrite -> positif
    assert refine_expected("rights_issue", "Y", "Y announces oversubscribed rights issue") == 1
    # cas normaux conservés
    assert refine_expected("profit_warning", "Z", "Z issues profit warning and cuts guidance") == -1
    assert refine_expected("short_seller", "W", "W targeted by activist short seller report") == -1


def _seed():
    rows = [
        ("merger_agt", "mna", "confirmé", 8.0), ("merger_agt", "mna", "confirmé", 6.0),
        ("merger_agt", "mna", "infirmé", -3.0),
        ("profit_warning", "earnings", "confirmé", -7.0),
        ("short_seller", "governance", "non_résolu", 0.0),
        ("possible_offer", "mna", "neutre", 0.5),
    ]
    with get_session() as s:
        for et, fam, v, pct in rows:
            s.add(SignalOutcome(signal_id=0, signal_date="2026-06-05", company="C", symbol="C.X",
                                event_type=et, family=fam, verdict=v, pct_since=pct, run_at=NOW))


def test_scorecard_aggregates():
    init_db(); _seed()
    sc = build_scorecard(days=None)
    assert sc["total"] == 6
    mna = sc["by_family"]["mna"]
    assert mna["n"] == 4 and mna["graded"] == 3 and mna["confirmed"] == 2
    assert mna["hit_rate"] == 67           # 2/3
    me = sc["by_event"]["merger_agt"]
    assert me["graded"] == 3 and me["hit_rate"] == 67
    gov = sc["by_family"]["governance"]
    assert gov["hit_rate"] is None and gov["unresolved"] == 1   # que du non_résolu


def test_top_unresolved_recurrents():
    init_db()
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    with get_session() as s:
        # Wetherspoons non résolu 3x (récurrent = vrai candidat watchlist) ; Greensill 1x
        for i in range(3):
            s.add(SignalOutcome(signal_id=0, signal_date=f"2026-06-0{i+1}", company="Wetherspoons",
                                symbol="", event_type="profit_warning", family="earnings",
                                verdict="non_résolu", pct_since=0.0, run_at=now))
        s.add(SignalOutcome(signal_id=0, signal_date="2026-06-05", company="Lex Greensill",
                            symbol="", event_type="insolvency", family="distress",
                            verdict="non_résolu", pct_since=0.0, run_at=now))
    sc = build_scorecard(days=None)
    top = dict(sc["top_unresolved"])
    assert top["Wetherspoons"] == 3
    assert top["Lex Greensill"] == 1
    # Wetherspoons (récurrent) doit être devant
    assert sc["top_unresolved"][0][0] == "Wetherspoons"
