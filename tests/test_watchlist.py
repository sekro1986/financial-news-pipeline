"""Tests watchlist (pivot) + symbologie (offline, sans reseau)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/wl_test.db")

from ma_signals.db import init_db                      # noqa: E402
from ma_signals import watchlist as wl                  # noqa: E402
from ma_signals.symbology import to_yf_symbol, openfigi_by_isin  # noqa: E402


def test_yf_symbol_mapping():
    assert to_yf_symbol("PGHN", "SW") == "PGHN.SW"
    assert to_yf_symbol("EQT", "SS") == "EQT.ST"
    assert to_yf_symbol("TSCO", "LN") == "TSCO.L"
    assert to_yf_symbol("BX", "US") == "BX"          # US : pas de suffixe
    assert to_yf_symbol("", "SW") == ""


def test_openfigi_empty_isin_noop():
    # ne doit pas appeler le reseau pour un ISIN vide
    assert openfigi_by_isin("") == {}


def _write_yaml(tmp_path):
    p = os.path.join(tmp_path, "wl.yaml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            "watchlist:\n"
            "  - name: Partners Group Holding AG\n"
            "    aliases: [Partners Group, PGHN]\n"
            "    isin: CH0024608827\n"
            "    ir_adhoc_url: https://example.com/adhoc\n"
            "  - name: Acme NoUrl AB\n"
            "    isin: SE0000000000\n"
        )
    return p


def test_import_and_match(tmp_path):
    init_db()
    yaml_path = _write_yaml(str(tmp_path))
    added, updated = wl.import_file(yaml_path)
    assert added >= 1
    # idempotence : re-import => 0 ajout
    a2, u2 = wl.import_file(yaml_path)
    assert a2 == 0 and u2 >= 1

    terms = wl.watchlist_terms()
    assert "partners group" in terms
    assert "ch0024608827" in terms
    assert wl.match_text("Why Partners Group is gating its fund", terms) == "partners group"
    assert wl.match_text("Totally unrelated headline", terms) is None


def test_adhoc_targets_only_with_url(tmp_path):
    init_db()
    wl.import_file(_write_yaml(str(tmp_path)))
    names = [n for n, _ in wl.adhoc_targets()]
    assert "Partners Group Holding AG" in names
    assert "Acme NoUrl AB" not in names  # pas d'ir_adhoc_url
