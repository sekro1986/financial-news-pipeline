"""Tests du recap hebdo auto-evaluatif (offline : price_fn injecte)."""
import os, tempfile, datetime as dt

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/wr_test.db")

from ma_signals.db import init_db, get_session     # noqa: E402
from ma_signals.models import Signal, WatchlistEntry  # noqa: E402
from ma_signals.weekly_review import build_report, render_markdown  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def _seed():
    with get_session() as s:
        for name, sym in [("Big Mover AB", "BIG.ST"), ("Mid Co", "MID.L"), ("Quiet SA", "QUI.PA")]:
            s.add(WatchlistEntry(name=name, yf_symbol=sym, active=1))
        # Big Mover : alerté (capté) ; Mid Co : détecté sous le seuil ; Quiet : rien
        s.add(Signal(dedup_key="b1", story_key="x", source="press_rss", event_type="redemption_gating",
                     company="Big Mover AB", title="Big Mover AB limits fund redemptions",
                     score=8, alerted=1, detected_at=NOW - dt.timedelta(days=2)))
        s.add(Signal(dedup_key="m1", story_key="y", source="press_rss", event_type="generic",
                     company="Mid Co", title="Mid Co mentioned in passing",
                     score=4, alerted=0, detected_at=NOW - dt.timedelta(days=1)))


def _fake_price(sym, days):
    table = {"BIG.ST": -22.0, "MID.L": 11.0, "QUI.PA": -15.0}
    return {"pct": table[sym], "ref": 100, "last": 100 + table[sym],
            "swing": abs(table[sym]), "big_day": NOW - dt.timedelta(days=1)}


def test_ranking_and_capture_verdicts():
    init_db(); _seed()
    rep = build_report(days=7, top=10, price_fn=_fake_price)
    # tri par |pct| : BIG(-22) > QUI(-15) > MID(11)
    assert [m["symbol"] for m in rep["movers"]] == ["BIG.ST", "QUI.PA", "MID.L"]
    by = {m["symbol"]: m for m in rep["movers"]}
    assert by["BIG.ST"]["status"] == "capté"     # signal alerté
    assert by["QUI.PA"]["status"] == "manqué"     # aucun signal
    assert by["MID.L"]["status"] == "détecté"     # signal sous le seuil
    assert rep["n_captured"] == 1 and rep["n_missed"] == 1 and rep["n_detected"] == 1
    assert rep["capture_rate"] == 33               # 1/3


def test_markdown_lists_misses():
    init_db(); _seed()
    rep = build_report(days=7, top=10, price_fn=_fake_price)
    md = render_markdown(rep)
    assert "Recap hebdo" in md
    assert "À investiguer" in md and "Quiet SA" in md   # le manqué est signalé
