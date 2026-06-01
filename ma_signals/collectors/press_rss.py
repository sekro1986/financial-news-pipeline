"""Collecteur presse via Google News RSS (EN + FR).

Capte les rumeurs précoces et la couverture média (souvent en amont ou en
parallèle des communiqués réglementaires). Les requêtes sont configurables
(settings.press_queries). On interroge en anglais (marché UK/US) et en français
(marché européen / AMF) pour une couverture large.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse

import feedparser

from ..config import settings
from ..schema import RawItem
from .base import Collector

EN = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
FR = "https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"

# Requêtes FR par défaut (en complément des requêtes EN de la config)
FR_QUERIES = [
    '(OPA OR "offre publique" OR "offre de rachat") bourse when:3d',
    '("prise de participation" OR "montée au capital" OR rachat) cotée when:3d',
]


class PressRssCollector(Collector):
    name = "press_rss"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()

        jobs = [(EN, q) for q in settings.press_query_list] + [(FR, q) for q in FR_QUERIES]

        for tmpl, query in jobs:
            url = tmpl.format(q=urllib.parse.quote(query))
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

                # Google News met "Titre - Source" ; on isole la source comme "company hint" faible.
                title = entry.get("title", "")
                source_name = entry.get("source", {}).get("title", "") if entry.get("source") else ""

                items.append(
                    RawItem(
                        source=self.name,
                        native_id=guid,
                        title=title,
                        url=entry.get("link", ""),
                        summary=source_name,
                        company="",
                        published_at=published,
                    )
                )
        return items
