"""Tests du classifier sur des cas réels (dont le cas easyJet / Castlelake)."""
from ma_signals.classifier import classify


def test_easyjet_castlelake_possible_offer():
    # Le titre type de l'événement easyJet
    c = classify("easyJet possible offer — Castlelake confirms takeover approach, Rule 2.4")
    assert c.score >= 8
    assert c.event_type in ("possible_offer", "firm_offer")


def test_rule_2_7_is_strongest():
    c = classify("Recommended cash offer Rule 2.7 announcement for Target plc")
    assert c.event_type == "firm_offer"
    assert c.score >= 10


def test_sec_tender_offer():
    c = classify("SC TO-T - ACME CORP (0001234567) (Subject) tender offer")
    assert c.event_type == "tender_offer"
    assert c.score >= 8


def test_sec_13d_stake():
    c = classify("SC 13D - WIDGETS INC (0007654321) (Subject)")
    assert c.event_type == "stake_13d"
    assert c.score >= 5


def test_french_opa():
    c = classify("Castlelake lance une offre de rachat — OPA possible sur la compagnie")
    assert c.score >= 8
    assert c.event_type in ("tender_offer", "possible_offer")


def test_french_prise_de_participation():
    c = classify("Franchissement de seuil : prise de participation au capital de la société cotée")
    assert c.score >= 6


def test_noise_returns_zero():
    c = classify("Total voting rights and dividend declaration")
    assert c.score == 0
    assert c.event_type == "none"


def test_strategic_review_medium():
    c = classify("Company announces strategic review exploring strategic alternatives")
    assert c.score >= 5
    assert c.event_type == "strategic_review"


def test_agrees_to_acquire_variants():
    for t in ["Spectris agrees to acquire Micromeritics",
              "Aviva agreed to acquire Direct Line",
              "Company plans to acquire rival"]:
        c = classify(t)
        assert c.score >= 6, (t, c.score)


def test_generic_stack_is_capped_below_alert():
    # Empilement de synonymes generiques, sans aucune ancre de deal precise :
    # doit etre plafonne sous le seuil d'alerte (pas un vrai deal).
    c = classify("Takeover and merger buzz: acquisition and bid chatter rises across the market")
    assert c.event_type == "generic"
    assert c.score <= 5, (c.score, c.matched)


def test_specific_deal_not_capped():
    # Une vraie ancre ('to acquire') ne doit PAS etre plafonnee par le cap generic.
    c = classify("Spectris agrees to acquire Micromeritics in cash deal")
    assert c.event_type == "merger_agt"
    assert c.score >= 8
