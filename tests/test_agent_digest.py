"""Tests structurels du service agent (sans réseau, sans SDK installé)."""
import datetime as dt
import json

from ma_signals import agent_digest as ad
from ma_signals.config import settings

NOW = dt.datetime(2026, 6, 11, 9, 0, tzinfo=dt.timezone.utc)


class FakeSig:
    def __init__(self, id=1, source="press_rss", event_type="possible_offer",
                 score=9, company="Acme", title="Acme receives takeover approach",
                 url="https://x", expected_move=1):
        self.__dict__.update(locals())


# ---------------------------------------------------------------- parse JSON
def test_parse_json_clean():
    out = ad.parse_agent_json('{"send": true, "message": "m", "macro_keys": []}')
    assert out and out["send"] is True


def test_parse_json_with_prose_around():
    txt = 'Voici ma réponse:\n```json\n{"send": false, "message": null}\n``` merci'
    out = ad.parse_agent_json(txt)
    assert out == {"send": False, "message": None}


def test_parse_json_invalid_or_foreign():
    assert ad.parse_agent_json("pas de json ici") is None
    assert ad.parse_agent_json('{"autre": 1}') is None  # pas de clé "send"
    assert ad.parse_agent_json("") is None


# ------------------------------------------------------------------- prompt
def test_prompt_contains_signals_and_scorecard():
    p = ad.build_cycle_prompt([FakeSig()], "mna: 60% fiab. (n=15)", False, [], NOW)
    assert "Acme" in p and "#1" in p and "60% fiab." in p
    assert "ne fais PAS de recherche web" in p


def test_prompt_macro_includes_sent_keys():
    p = ad.build_cycle_prompt([], "(vide)", True, ["fed-hold-2026-06"], NOW)
    assert "VEILLE MACRO DUE" in p and "fed-hold-2026-06" in p


# -------------------------------------------------------------------- état
def test_state_roundtrip(tmp_path):
    f = tmp_path / "state.json"
    st = ad.load_state(f)
    assert st["last_id"] == 0
    st["last_id"] = 42
    st["macro_sent"] = ["a"]
    ad.save_state(f, st)
    assert ad.load_state(f)["last_id"] == 42


def test_roll_day_resets_budget():
    st = {"date": "2026-06-10", "cycles_today": 99}
    ad.roll_day(st, NOW)
    assert st["cycles_today"] == 0 and st["date"] == "2026-06-11"


def test_macro_due_logic():
    st = {"last_macro_at": ""}
    assert ad.macro_due(st, NOW)
    st["last_macro_at"] = (NOW - dt.timedelta(minutes=5)).isoformat()
    assert not ad.macro_due(st, NOW)
    st["last_macro_at"] = (
        NOW - dt.timedelta(minutes=settings.agent_macro_interval_minutes + 1)
    ).isoformat()
    assert ad.macro_due(st, NOW)


def test_fmt_signal():
    line = ad.fmt_signal(FakeSig(expected_move=-1, score=7))
    assert "dir=down" in line and "score=7" in line
