"""Price marks : t0 + horizons, capture a echeance, rapport reaction."""
import datetime as dt

from ma_signals import pricemarks, reaction
from ma_signals.config import settings
from ma_signals.db import SessionLocal, init_db
from ma_signals.models import PriceMark, Signal, WatchlistEntry

NOW = dt.datetime.now(dt.timezone.utc)


def _signal(company="Partners Group Holding AG", event_type="possible_offer", n=[0]):
    n[0] += 1
    init_db()
    with SessionLocal() as s:
        sig = Signal(source="press_rss", title=f"t{n[0]}", url=f"u{n[0]}",
                     dedup_key=f"k{n[0]}", company=company, event_type=event_type,
                     score=10, status="en_attente", detected_at=NOW)
        s.add(sig)
        s.add(WatchlistEntry(name="Partners Group Holding AG", yf_symbol="PGHN.SW"))
        s.commit()
        s.refresh(sig)
        return sig


def test_schedule_cree_t0_et_les_horizons():
    sig = _signal()
    done = pricemarks.schedule_marks(
        [sig], price_fn=lambda sym: {"price": 100.0, "market_state": "open"})
    assert done == 1
    with SessionLocal() as s:
        marks = s.query(PriceMark).filter_by(signal_id=sig.id).all()
        labels = {m.label for m in marks}
        assert labels == {"t0", "1h", "4h", "8h", "24h"}
        t0 = next(m for m in marks if m.label == "t0")
        assert t0.price == 100.0 and t0.captured_at is not None
        assert all(m.captured_at is None for m in marks if m.label != "t0")


def test_symbole_via_watchlist_sans_recherche_yahoo():
    sig = _signal(company="Partners Group")   # variante du nom -> rapprochement flou
    calls = []
    pricemarks.schedule_marks([sig], price_fn=lambda sym: {"price": 50.0, "market_state": "open"},
                              resolve_fn=lambda name: calls.append(name) or "XX")
    assert calls == []   # la watchlist a suffi
    with SessionLocal() as s:
        assert s.query(PriceMark).filter_by(label="t0").one().symbol == "PGHN.SW"


def test_signal_non_resoluble_ignore():
    sig = _signal(company="Inconnue Privee SARL")
    done = pricemarks.schedule_marks(
        [sig], price_fn=lambda sym: {"price": 1.0, "market_state": "open"},
        resolve_fn=lambda name: "")
    assert done == 0


def test_capture_a_echeance_calcule_le_pct():
    sig = _signal()
    pricemarks.schedule_marks([sig], price_fn=lambda sym: {"price": 100.0, "market_state": "open"})
    # avant echeance : rien
    assert pricemarks.capture_due(NOW + dt.timedelta(minutes=30),
                                  price_fn=lambda sym: {"price": 103.0, "market_state": "open"}) == 0
    # apres +1h : la marque 1h est capturee a 103 -> +3 %
    got = pricemarks.capture_due(NOW + dt.timedelta(hours=1, minutes=5),
                                 price_fn=lambda sym: {"price": 103.0, "market_state": "open"})
    assert got == 1
    with SessionLocal() as s:
        m = s.query(PriceMark).filter_by(label="1h").one()
        assert abs(m.pct_vs_t0 - 3.0) < 0.01


def test_rapport_reaction_ecarte_le_marche_ferme():
    sig = _signal(event_type="profit_warning")
    with SessionLocal() as s:
        s.add(PriceMark(signal_id=sig.id, symbol="PGHN.SW", label="t0", due_at=NOW,
                        captured_at=NOW, price=100.0, pct_vs_t0=0.0, market_state="open"))
        s.add(PriceMark(signal_id=sig.id, symbol="PGHN.SW", label="1h", due_at=NOW,
                        captured_at=NOW, price=97.0, pct_vs_t0=-3.0, market_state="open"))
        s.add(PriceMark(signal_id=sig.id, symbol="PGHN.SW", label="4h", due_at=NOW,
                        captured_at=NOW, price=97.0, pct_vs_t0=-3.0, market_state="closed"))
        s.commit()
    data = reaction.build_reaction(days=7)
    assert data["by_event"]["profit_warning"]["1h"]["avg"] == -3.0
    assert "4h" not in data["by_event"]["profit_warning"]   # marche ferme ecarte
    assert "earnings" in data["by_family"]
