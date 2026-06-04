"""Tests de l'elargissement multi-familles + seuils par famille."""
from ma_signals.classifier import classify, family_of, family_threshold


def _alerts(text: str) -> bool:
    c = classify(text)
    return c.score >= family_threshold(c.family)


def test_redemption_gating_liquidity():
    c = classify("Partners Group limits fund redemptions to 5% of NAV")
    assert c.family == "liquidity"
    assert c.event_type == "redemption_gating"
    assert _alerts("Partners Group limits fund redemptions to 5% of NAV")


def test_profit_warning_earnings():
    c = classify("Acme issues profit warning and cuts full-year guidance")
    assert c.family == "earnings"
    assert c.event_type == "profit_warning"
    assert _alerts("Acme issues profit warning and cuts full-year guidance")


def test_insolvency_distress():
    c = classify("XYZ Corp files for Chapter 11 bankruptcy protection")
    assert c.family == "distress"
    assert _alerts("XYZ Corp files for Chapter 11 bankruptcy protection")


def test_short_seller_governance():
    c = classify("Hindenburg short report targets ABC Inc")
    assert c.family == "governance"
    assert c.event_type == "short_seller"
    assert _alerts("Hindenburg short report targets ABC Inc")


def test_dividend_cut_capital():
    c = classify("Company suspends its dividend amid cash crunch")
    assert c.family == "capital"
    assert c.event_type == "dividend_cut"


def test_french_avertissement_resultats():
    assert classify("La société publie un avertissement sur résultats").family == "earnings"


def test_buyback_does_not_alert_alone():
    # buyback (poids 4) sous le seuil capital (7) -> pas d'alerte seul
    assert not _alerts("Company announces share buyback programme")


def test_mna_threshold_unchanged():
    assert family_threshold("mna") == 8
    assert family_of("merger_agt") == "mna"


def test_generic_still_capped_and_silent():
    c = classify("Takeover and merger buzz: acquisition and bid chatter rises")
    assert c.family == "generic"
    assert c.score <= 5
    assert not _alerts("Takeover and merger buzz: acquisition and bid chatter rises")
