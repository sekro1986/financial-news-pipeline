"""Collecteur AMF France (régulateur français).

L'AMF expose des flux RSS officiels. On suit le flux global des actualités et
publications (qui inclut les offres publiques / OPA et décisions sur les
franchissements de seuils). Le classifier filtre sur les termes M&A FR/EN.

Flux RSS AMF (https://www.amf-france.org/fr/abonnements-flux-rss) :
  display/21 : Toutes les actualités et publications  (le plus large)
  display/30 : Actualités
"""
from __future__ import annotations

import datetime as dt

import feedparser

from ..schema import RawItem
from .base import Collector

FEEDS = [
    "https://www.amf-france.org/fr/flux-rss/display/21",
    "https://www.amf-france.org/fr/flux-rss/display/30",
]


class AmfFranceCollector(Collector):
    name = "amf_france"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for url in FEEDS:
            try:
                resp = self._get(url)
            except Exception:
                continue
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                guid = entry.get("id", entry.get("link", entry.get("title", "")))
                if guid in seen:
                    continue
                seen.add(guid)

                published = None
                if getattr(entry, "published_parsed", None):
                    published = dt.datetime(*entry.published_parsed[:6], tzinfo=dt.timezone.utc)

                items.append(
                    RawItem(
                        source=self.name,
                        native_id=guid,
                        title=entry.get("title", ""),
                        url=entry.get("link", ""),
                        summary=entry.get("summary", ""),
                        company="",
                        published_at=published,
                    )
                )
        return items
