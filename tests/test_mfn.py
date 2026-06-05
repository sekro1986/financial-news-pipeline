"""Tests collecteur MFN (parsing du XML maison, offline)."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/mfn_test.db")

from ma_signals.collectors.mfn import parse_mfn       # noqa: E402
from ma_signals.config import settings                # noqa: E402
from ma_signals.db import init_db, get_session        # noqa: E402
from ma_signals.schema import RawItem                 # noqa: E402
from ma_signals.pipeline import process_items         # noqa: E402
from ma_signals.models import Signal                  # noqa: E402

_XML = b"""<feed><items>
<item><newsId>n1</newsId><url>https://mfn.se/a/eqt</url>
 <author><name>EQT AB</name>
   <isins><isin>SE0012853455</isin></isins>
   <leis><lei>213800U7P9GOIRKCTB34</lei></leis>
   <tickers><ticker>XSTO:EQT</ticker></tickers></author>
 <properties><lang>en</lang><type>ir</type></properties>
 <content><title>EQT agrees to acquire TargetCo for EUR 2 billion</title><preamble>Cash deal.</preamble></content>
 <publishDate>2026-06-05T08:00:00Z</publishDate>
</item>
<item><newsId>n2</newsId><url>https://mfn.se/a/other</url>
 <author><name>Other AB</name><isins><isin>SE9999999999</isin></isins></author>
 <properties><lang>sv</lang></properties>
 <content><title>Other AB bokslutskommunike for 2025</title></content>
</item>
</items></feed>"""


def test_parse_extracts_rich_fields():
    recs = parse_mfn(_XML)
    assert len(recs) == 2
    r = recs[0]
    assert r["title"].startswith("EQT agrees to acquire")
    assert r["isin"] == "SE0012853455"
    assert r["lei"] == "213800U7P9GOIRKCTB34"
    assert r["ticker"] == "XSTO:EQT"
    assert r["lang"] == "en"
    assert r["published"] is not None


def test_parse_handles_garbage():
    assert parse_mfn(b"not xml") == []


def test_mfn_is_curated_and_enabled():
    assert "mfn" in settings.curated_source_list
    assert "mfn" in settings.sources_list


def test_mfn_item_through_pipeline_gets_bonus():
    from ma_signals.classifier import classify
    init_db()
    title = "EQT agrees to acquire TargetCo for EUR 2 billion"
    base = classify(title).score                      # merger_agt
    items = [RawItem(source="mfn", native_id="n1", title=title, company="EQT AB")]
    alerts = process_items(items)
    assert len(alerts) == 1
    assert alerts[0].event_type == "merger_agt"
    assert alerts[0].score == base + settings.curated_score_bonus   # bonus source curee
