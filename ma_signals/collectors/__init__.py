"""Registre des collecteurs. Chaque collecteur expose .collect() -> list[RawItem]."""
from __future__ import annotations

from .base import Collector
from .sec_edgar import SecEdgarCollector
from .rns_uk import RnsUkCollector
from .amf_france import AmfFranceCollector
from .press_rss import PressRssCollector
from .rss_custom import RssCustomCollector
from .disclosures import DisclosuresCollector
from .prices import PriceCollector
from .adhoc import AdhocCollector

REGISTRY: dict[str, type[Collector]] = {
    "sec_edgar": SecEdgarCollector,
    "rns_uk": RnsUkCollector,
    "amf_france": AmfFranceCollector,
    "press_rss": PressRssCollector,
    "rss_custom": RssCustomCollector,
    "disclosures": DisclosuresCollector,
    "prices": PriceCollector,
    "adhoc_ir": AdhocCollector,
}


def build_enabled(sources: list[str]) -> list[Collector]:
    out: list[Collector] = []
    for name in sources:
        cls = REGISTRY.get(name)
        if cls is None:
            continue
        out.append(cls())
    return out
