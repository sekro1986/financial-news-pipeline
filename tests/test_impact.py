"""Tests analyse d'impact quotidienne (offline : resolve_fn + price_fn injectés)."""
import os, tempfile, datetime as dt
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/imp_test.db")

from ma_signals.db import init_db, get_session   # noqa: E402
from ma_signals.models import Signal             # noqa: E402
from ma_signals.impact import build_report, prev_business_day, _verdict, yahoo_search_symbol  # noqa: E402

DAY = dt.date(2026, 6, 4)   # un jeudi
DAY_TS = dt.datetime(2026, 6, 4, 10, tzinfo=dt.timezone.utc)


def test_prev_business_day_skips_weekend():
    assert prev_business_day(dt.date(2026, 6, 8)) == dt.date(2026, 6, 5)   # lundi -> vendredi
    assert prev_business_day(dt.date(2026, 6, 5)) == dt.date(2026, 6, 4)   # vendredi -> jeudi


def test_verdict_logic():
    assert _verdict(1, 5.0, 2.0) == "confirmé"     # hausse attendue, monté
    assert _verdict(1, -5.0, 2.0) == "infirmé"     # hausse attendue, baissé
    assert _verdict(-1, -5.0, 2.0) == "confirmé"   # baisse attendue, baissé
    assert _verdict(-1, 4.0, 2.0) == "infirmé"
    assert _verdict(1, 0.5, 2.0) == "neutre"       # sous le seuil
    assert _verdict(0, 9.0, 2.0) == "sans_attente"


def _seed():
    with get_session() as s:
        s.add(Signal(dedup_key="o1", story_key="x", source="press_rss", event_type="possible_offer",
                     company="GoodTarget", title="GoodTarget receives takeover approach",
                     score=9, status="envoye", detected_at=DAY_TS))      # hausse attendue
        s.add(Signal(dedup_key="o2", story_key="y", source="press_rss", event_type="profit_warning",
                     company="BadCo", title="BadCo issues profit warning",
                     score=8, status="envoye", detected_at=DAY_TS))       # baisse attendue
        s.add(Signal(dedup_key="o3", story_key="z", source="press_rss", event_type="merger_agt",
                     company="GhostCo", title="GhostCo agrees to acquire X",
                     score=10, status="envoye", detected_at=DAY_TS))      # ticker non résolu


def _resolve(company, text):
    return {"GoodTarget": ("GT", "watchlist"), "BadCo": ("BAD", "watchlist")}.get(company, ("", ""))


def _price(symbol, day):
    return {"GT": {"pct_since": 12.0, "pct_day": 8.0, "ref": 100, "last": 112, "last_date": "2026-06-08"},
            "BAD": {"pct_since": -9.0, "pct_day": -6.0, "ref": 100, "last": 91, "last_date": "2026-06-08"}}[symbol]


def test_build_report_verdicts_and_unresolved():
    init_db(); _seed()
    rep = build_report(day=DAY, resolve_fn=_resolve, price_fn=_price)
    by = {r["company"]: r for r in rep["rows"]}
    assert by["GoodTarget"]["verdict"] == "confirmé"
    assert by["BadCo"]["verdict"] == "confirmé"
    assert by["GhostCo"]["verdict"] == "non_résolu"
    assert rep["n_confirmed"] == 2
    assert rep["hit_rate"] == 100      # 2/2 notés confirmés
    assert rep["n_unresolved"] == 1


def test_refine_expected_collapse_and_acquirer():
    from ma_signals.impact import refine_expected
    # offre retiree -> baisse attendue (EN + FR)
    assert refine_expected("possible_offer", "Bodycote", "Bodycote shares fall as Apollo walks away from takeover") == -1
    assert refine_expected("possible_offer", "Bodycote", "L'action Bodycote chute après le retrait de l'offre d'Apollo") == -1
    # acquereur (sujet) -> ambigu, pas de verdict
    assert refine_expected("tender_offer", "UniCredit", "UniCredit augmente sa participation dans Commerzbank (OPA hostile)") == 0
    # cible classique -> hausse
    assert refine_expected("merger_agt", "Micromeritics", "Spectris agrees to acquire Micromeritics") == 1
