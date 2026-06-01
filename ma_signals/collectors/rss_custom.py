"""Collecteur RSS generique : ingere des flux RSS/Atom arbitraires definis dans
.env (RSS_CUSTOM_FEEDS), separes par des virgules ou des retours a la ligne.

Concu pour rss.app : convertis n'importe quel compte X/Twitter public, blog
(Betaville), ou newsletter (FT Due Diligence, Axios Pro Rata) en flux RSS, puis
colle l'URL ici. Tout passe ensuite par le meme scoring / dedup / digest que les
autres sources. Ces sources etant curees, elles recoivent un bonus de score
(settings.curated_score_bonus) applique dans le pipeline.
"""
from __future__ import annotations

import datetime as dt

import feedparser

from ..config import settings
from ..schema import RawItem
from .base import Collector


class RssCustomCollector(Collector):
    name = "rss_custom"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for url in settings.rss_custom_feed_list:
            try:
                resp = self._get(url)
            except Exception:
                continue
            feed = feedparser.parse(resp.content)
            feed_title = ""
            if getattr(feed, "feed", None):
                feed_title = (feed.feed.get("title", "") or "")[:80]
            for e in feed.entries:
                guid = e.get("id", e.get("link", e.get("title", "")))
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                published = None
                if getattr(e, "published_parsed", None):
                    published = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
                summary = (e.get("summary", "") or feed_title)[:1000]
                items.append(
                    RawItem(
                        source=self.name,
                        native_id=guid,
                        title=e.get("title", ""),
                        url=e.get("link", ""),
                        summary=summary,
                        company="",
                        published_at=published,
                    )
                )
        return items
