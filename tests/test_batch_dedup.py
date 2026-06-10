"""Regression panne du 09/06/2026 : doublon de dedup_key DANS un meme lot.

EDGAR liste un meme filing cote 'Subject' et cote 'Filed by' (meme AccNo ->
meme dedup_key) avec des titres differents (-> story_keys differents). Avant le
correctif, le 2e INSERT violait uq_signals_dedup et le rollback annulait TOUT
le cycle : le poller restait 'actif' mais plus rien n'etait persiste."""
import datetime as dt

from ma_signals.db import get_session
from ma_signals.models import Signal
from ma_signals.pipeline import process_items
from ma_signals.schema import RawItem


def _edgar_pair():
    now = dt.datetime.now(dt.timezone.utc)
    common = dict(source="sec_edgar", native_id="0001104659-26-071859",
                  url="http://sec/x", published_at=now,
                  summary="Filed: 2026-06-09 AccNo: 0001104659-26-071859")
    return [
        RawItem(title="SC 13D/A - GENCO SHIPPING & TRADING LTD (0001326200) (Subject)", **common),
        RawItem(title="SC 13D/A - SOME ACTIVIST FUND LP (0009999999) (Filed by)", **common),
    ]


def test_same_dedup_key_in_one_batch_does_not_kill_cycle():
    a, b = _edgar_pair()
    assert a.dedup_key == b.dedup_key
    others = [RawItem(source="press_rss", native_id="other",
                      title="Acme Corp agrees to acquire Foo Plc", url="http://x",
                      published_at=dt.datetime.now(dt.timezone.utc))]
    process_items([a, b] + others)   # ne doit PAS lever
    with get_session() as session:
        # 1 seul signal pour le filing, et le reste du lot a bien ete persiste.
        assert session.query(Signal).filter_by(dedup_key=a.dedup_key).count() == 1
        assert session.query(Signal).filter_by(source="press_rss").count() == 1


def test_residual_collision_is_skipped_not_fatal():
    """Meme si une collision atteint l'INSERT (course inter-process simulee par
    un re-traitement avec story_dedup coupe), le cycle survit."""
    from ma_signals.config import settings
    a, _ = _edgar_pair()
    process_items([a])
    old = settings.story_dedup
    settings.story_dedup = False
    try:
        # une variante du meme article (titre modifie -> passe le check story,
        # meme dedup_key) + un item sain derriere
        a2 = RawItem(source="sec_edgar", native_id="0001104659-26-071859",
                     title="SC 13D/A - GENCO SHIPPING (amended) tender offer", url="http://sec/y",
                     published_at=dt.datetime.now(dt.timezone.utc))
        sane = RawItem(source="press_rss", native_id="sane",
                       title="Bar Plc agrees to acquire Baz Plc", url="http://x",
                       published_at=dt.datetime.now(dt.timezone.utc))
        process_items([a2, sane])  # collision DB sur a2 -> skip, sane persiste
    finally:
        settings.story_dedup = old
    with get_session() as session:
        assert session.query(Signal).filter_by(dedup_key=a.dedup_key).count() == 1
        assert session.query(Signal).filter(Signal.company.like("Baz%")).count() == 1


def test_watchdog_alerts_once_after_3_failures(monkeypatch):
    from ma_signals import poller

    sent = []
    monkeypatch.setattr(poller, "run_cycle", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(poller, "send_message", lambda t: sent.append(t) or True)
    monkeypatch.setattr(poller, "_fail_streak", 0)
    for _ in range(5):
        poller.safe_cycle()          # ne propage jamais
    assert len(sent) == 1            # une seule alerte par episode

    monkeypatch.setattr(poller, "run_cycle", lambda: 0)
    poller.safe_cycle()              # cycle OK -> re-arme
    assert poller._fail_streak == 0
