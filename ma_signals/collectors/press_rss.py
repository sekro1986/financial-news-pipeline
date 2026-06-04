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
DE = "https://news.google.com/rss/search?q={q}&hl=de&gl=CH&ceid=CH:de"  # Suisse alémanique + DE

# Requêtes EN supplémentaires (familles d'événements hors M&A) — toujours actives,
# en complément des requêtes M&A de la config (settings.press_queries).
EN_FAMILY_QUERIES = [
    '"profit warning" OR "cuts guidance" OR "lowers outlook" when:3d',
    '"suspends redemptions" OR "gates fund" OR "limits redemptions" OR "fund suspension" when:3d',
    '("files for bankruptcy" OR administration OR insolvency OR "going concern") listed company when:3d',
    '("cuts dividend" OR "suspends dividend") OR "capital increase" OR "rights issue" when:3d',
    '"short seller" OR "short report" OR "accounting irregularities" OR "CEO steps down" when:3d',
]

# Requêtes FR par défaut (marché européen / AMF)
FR_QUERIES = [
    '(OPA OR "offre publique" OR "offre de rachat") bourse when:3d',
    '("prise de participation" OR "montée au capital" OR rachat) cotée when:3d',
    '("avertissement sur résultats" OR "abaisse ses prévisions" OR "suspension des rachats") when:3d',
]

# Requêtes DE (Suisse alémanique + Allemagne) — couvre le cas Partners Group & co.
DE_QUERIES = [
    '(Übernahme OR Übernahmeangebot OR Fusion) börsennotiert when:3d',
    '("Gewinnwarnung" OR "senkt Prognose" OR "Rücknahmen" OR "Fonds" Rücknahme) when:3d',
]


class PressRssCollector(Collector):
    name = "press_rss"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()

        jobs = ([(EN, q) for q in settings.press_query_list]
                + [(EN, q) for q in EN_FAMILY_QUERIES]
                + [(FR, q) for q in FR_QUERIES]
                + [(DE, q) for q in DE_QUERIES])

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
