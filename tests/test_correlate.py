"""Tests correlation news<->prix (mouvement inexplique)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/corr_test.db")

from ma_signals.config import settings                 # noqa: E402
from ma_signals.db import init_db, get_session         # noqa: E402
from ma_signals.models import Signal, WatchlistEntry   # noqa: E402
from ma_signals.correlate import mark_unexplained_moves, _FLAG  # noqa: E402


def _reset_and_seed_entry():
    init_db()
    with get_session() as s:
        s.query(Signal).delete()
        if not s.query(WatchlistEntry).filter_by(name="Partners Group Holding AG").first():
            s.add(WatchlistEntry(name="Partners Group Holding AG",
                                 aliases="Partners Group,PGHN", yf_symbol="PGHN.SW", active=1))


def _add_price(score=4, alerted=0):
    with get_session() as s:
        s.add(Signal(dedup_key="px1", story_key="co:partners group holding ag|price_drop",
                     source="prices", event_type="price_drop",
                     company="Partners Group Holding AG",
                     title="Partners Group Holding AG -4.7% intraday (PGHN.SW)",
                     score=score, alerted=alerted))


def test_unexplained_move_promoted():
    _reset_and_seed_entry()
    _add_price(score=4, alerted=0)            # sous le seuil market(6), pas de news
    promoted = mark_unexplained_moves(seed=False)
    assert len(promoted) == 1                  # +bonus(2) -> 6 >= seuil -> alertable
    with get_session() as s:
        m = s.query(Signal).filter_by(dedup_key="px1").first()
    assert _FLAG in m.matched_keywords
    assert m.score == 4 + settings.unexplained_bonus
    assert m.status == "en_attente"   # promu -> sera envoye au prochain dispatch
    assert m.alerted == 0


def test_explained_move_not_flagged():
    _reset_and_seed_entry()
    _add_price(score=4, alerted=0)
    with get_session() as s:                   # une news existe pour le meme emetteur
        s.add(Signal(dedup_key="nw1", story_key="x", source="press_rss", event_type="redemption_gating",
                     company="Partners Group", title="Partners Group limits fund redemptions", score=8, alerted=1))
    promoted = mark_unexplained_moves(seed=False)
    assert promoted == []                       # explique -> pas de bonus, pas de promotion
    with get_session() as s:
        m = s.query(Signal).filter_by(dedup_key="px1").first()
    assert _FLAG not in (m.matched_keywords or "")
    assert m.score == 4


def test_idempotent_no_double_bonus():
    _reset_and_seed_entry()
    _add_price(score=4, alerted=0)
    mark_unexplained_moves(seed=False)
    again = mark_unexplained_moves(seed=False)  # 2e passage : deja marque
    assert again == []
    with get_session() as s:
        m = s.query(Signal).filter_by(dedup_key="px1").first()
    assert m.score == 4 + settings.unexplained_bonus   # bonus applique une seule fois
