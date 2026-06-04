"""Tests collecteur ad-hoc par emetteur (offline)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/adhoc_test.db")

from ma_signals.collectors.adhoc import extract_links   # noqa: E402
from ma_signals.config import settings                  # noqa: E402
from ma_signals.db import init_db, get_session          # noqa: E402
from ma_signals.schema import RawItem                   # noqa: E402
from ma_signals.pipeline import process_items           # noqa: E402
from ma_signals.models import Signal                    # noqa: E402

_HTML = """
<html><body>
  <a href="javascript:;">Click here to open search box</a>
  <a href="/news/detail?id=1">Partners Group condemns defamatory publication by Grizzly Reports, a short-selling hedge fund</a>
  <a href="https://www.linkedin.com/company/x">Follow our updates on LinkedIn today please</a>
  <a href="/news/detail?id=2">Short</a>
  <a href="/news/detail?id=3">Acme issues profit warning and cuts its full-year guidance</a>
</body></html>
"""


def test_extract_links_filters_and_resolves():
    links = extract_links(_HTML, "https://ex.com/news/")
    urls = [u for _, u in links]
    texts = [t for t, _ in links]
    assert "https://ex.com/news/detail?id=1" in urls       # relatif resolu
    assert all("linkedin.com" not in u for u in urls)        # social exclu
    assert all("javascript" not in u for u in urls)          # JS exclu
    assert not any(t == "Short" for t in texts)              # trop court exclu
    assert len(links) == 2


def test_adhoc_is_curated_source():
    assert "adhoc_ir" in settings.curated_source_list


def test_adhoc_shortseller_alerts_with_bonus():
    init_db()
    items = [RawItem(source="adhoc_ir", native_id="ad1",
                     title="Partners Group condemns publication by a short-selling hedge fund",
                     company="Partners Group Holding AG")]
    alerts = process_items(items)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.event_type == "short_seller"
    assert a.score == 8 + settings.curated_score_bonus     # bonus source officielle
