"""Fusion floue des histoires (variantes de nom) + cooldown anti-doublon a l'envoi."""
import datetime as dt

from ma_signals.dedup import company_tokens, same_story_company, split_repeats, story_key
from ma_signals.config import settings
from ma_signals.db import SessionLocal, get_session, init_db
from ma_signals.models import Signal
from ma_signals.pipeline import process_items
from ma_signals.schema import RawItem

NOW = dt.datetime.now(dt.timezone.utc)
TITLE_A = "Intesa Sanpaolo launches tender offer for Banca Monte dei Paschi - Reuters"
TITLE_B = "Possible offer: Intesa weighs tender offer for Monte dei Paschi di Siena - MSN"


def test_comparateur_variantes_reelles_du_0806():
    assert same_story_company("Monte dei Paschi", "Banca Monte dei Paschi di Siena")
    assert same_story_company("BlackRock Latin American Inv Trust",
                              "BlackRock Latin American Investment")
    assert same_story_company("Mpac Group Shares Crash", "Mpac")
    assert same_story_company("Partners", "Partners Group Founder Calls")
    # le bruit ne matche rien (pas de fusion abusive)
    assert not same_story_company("Les", "Mpac")
    assert not same_story_company("Short-seller", "Partners")
    assert not same_story_company("Delta", "United")
    assert company_tokens("Le") == frozenset()


def test_pipeline_fusionne_les_variantes_intra_cycle():
    init_db()
    items = [
        RawItem(source="press_rss", native_id="m1", title=TITLE_A,
                company="Banca Monte dei Paschi"),
        RawItem(source="press_rss", native_id="m2", title=TITLE_B,
                company="Monte dei Paschi di Siena"),
    ]
    process_items(items)
    with get_session() as s:
        assert s.query(Signal).count() == 1  # une seule histoire persistee


def test_pipeline_fusionne_les_variantes_inter_cycles():
    init_db()
    process_items([RawItem(source="press_rss", native_id="c1", title=TITLE_A,
                           company="Banca Monte dei Paschi")])
    process_items([RawItem(source="press_rss", native_id="c2", title=TITLE_B,
                           company="Monte dei Paschi di Siena")])
    with get_session() as s:
        assert s.query(Signal).count() == 1  # le 2e cycle reconnait la variante


def _sig(company, event_type, score, status="en_attente", sent_at=None, n=[0]):
    n[0] += 1
    return Signal(source="press_rss", title=f"t{n[0]}", url=f"u{n[0]}",
                  dedup_key=f"k{n[0]}", company=company, event_type=event_type,
                  score=score, status=status, sent_at=sent_at, detected_at=NOW)


def test_cooldown_supprime_la_repetition_deja_envoyee():
    init_db()
    with SessionLocal() as s:
        s.add(_sig("Banca Monte dei Paschi", "tender_offer", 11,
                   status="envoye", sent_at=NOW - dt.timedelta(hours=3)))
        s.commit()
    pending = [_sig("Monte dei Paschi di Siena", "possible_offer", 9)]
    to_send, repeats = split_repeats(pending)
    assert to_send == [] and len(repeats) == 1


def test_cooldown_expire_apres_la_fenetre():
    init_db()
    with SessionLocal() as s:
        s.add(_sig("Mpac", "profit_warning", 9, status="envoye",
                   sent_at=NOW - dt.timedelta(hours=30)))   # > 24 h
        s.commit()
    pending = [_sig("Mpac Group", "profit_warning", 8)]
    to_send, repeats = split_repeats(pending)
    assert len(to_send) == 1 and repeats == []


def test_cooldown_intra_lot_garde_le_meilleur_score():
    init_db()
    a = _sig("Partners Group", "short_seller", 10)
    b = _sig("Partners Group Founder Calls", "short_seller", 8)
    to_send, repeats = split_repeats([b, a])
    assert to_send == [a] and repeats == [b]


def test_cooldown_ignore_les_societes_vides_et_familles_differentes():
    init_db()
    a = _sig("", "insolvency", 9)                       # pas de nom -> jamais filtre
    b = _sig("Mpac", "profit_warning", 9)               # earnings
    c = _sig("Mpac", "merger_agt", 9)                   # mna : autre famille, passe
    to_send, repeats = split_repeats([a, b, c])
    assert len(to_send) == 3 and repeats == []
