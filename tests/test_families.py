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


def test_no_false_insolvency_on_trump_administration():
    for t in ["Trump administration to ask US AI firms to submit models",
              "Officials in the Trump administration may benefit from the listing",
              "New York sues over the Trump administration's deal",
              "How India's Insolvency Framework Has Evolved over 10 Years"]:
        c = classify(t)
        assert c.event_type != "insolvency", (t, c.score)


def test_real_administration_still_fires():
    assert classify("European cargo airline in administration").event_type == "insolvency"
    assert classify("UK retailer collapsing into liquidation").event_type == "insolvency"


def test_debt_tender_filtered():
    for t in ["Aptiv completes $1.37 billion debt tender offer",
              "Zambia improves tender offer on its $1.36 billion Eurobond maturing in 2053"]:
        assert classify(t).score == 0, t


def test_shortseller_legal_saga_filtered_but_attack_kept():
    assert classify("Federal Jury Convicts Short Seller Andrew Left of Securities Fraud").score == 0
    assert classify("Andrew Left found guilty of fraud, short-seller community rattled").score == 0
    # une vraie attaque short sur une societe reste
    assert classify("Pirelli shares fall after short-seller report on Russian ties").event_type == "short_seller"
