"""Extraction de la CIBLE (et non du fonds attaquant) en contexte short-seller."""
from ma_signals.extract import guess_company


def test_target_not_attacker():
    assert guess_company("Pirelli shares recover after it denies short-seller report") == "Pirelli"
    assert guess_company("Pirelli Slides After Short Seller Warns on Exposure to Russia") == "Pirelli"
    assert guess_company("Tire Giant Pirelli Threatens Legal Action As Short Seller Alleges Ties") == "Pirelli"
    assert guess_company("Sportradar Group AG Facing Activist Short Seller Accusations").startswith("Sportradar")
    # titre mené par l'attaquant -> on récupère la cible après l'ancre
    assert guess_company("Hindenburg Research targets Acme Corp in new short report") == "Acme"
    # un fonds seul (pas de cible fiable) -> vide plutôt qu'un faux nom
    assert guess_company("Muddy Waters releases short report") == ""


def test_non_short_unaffected():
    assert guess_company("Spectris agrees to acquire Micromeritics") == "Micromeritics"
