"""Collecteur de disclosures regulatoires / communiques multi-marches.

Lit des flux RSS/Atom STANDARD de wires de communiques d'entreprises (defauts
integres : GlobeNewswire, categories M&A et resultats), plus tout flux ajoute par
l'utilisateur dans disclosure_feeds.txt ou la variable DISCLOSURE_FEEDS.

But : etendre la couverture au-dela des regulateurs US/UK/FR deja branches, vers
une couverture mondiale (emetteurs cotes de nombreux marches publient via ces
wires). Source consideree "curee" -> bonus de score applique dans le pipeline.

NB : les wires a schema non standard (ex. MFN.se, qui expose ISIN/LEI/ticker mais
un format maison) ne sont PAS geres ici ; ils feront l'objet d'un parseur dedie.
"""
from __future__ import annotations

import datetime as dt

import feedparser

from ..config import settings
from ..schema import RawItem
from .base import Collector

# Flux par defaut (RSS standard, verifies, anglais, multi-emetteurs).
# GlobeNewswire expose des feeds par "subjectcode" ; on cible les plus a signal.
# Aucun defaut hardcode : les feeds candidats teste se sont averes bruyants
# (GlobeNewswire "M&A" = notifications de transactions de dirigeants multilingues)
# ou a schema non standard (MFN). On alimente donc via disclosure_feeds.txt, ou
# chaque ligne ajoutee est documentee. Le score-gate du pipeline ignore de toute
# facon le routine (items a score 0 non stockes).
DEFAULT_FEEDS: list[str] = []


class DisclosuresCollector(Collector):
    name = "disclosures"

    def feeds(self) -> list[str]:
        # defauts + extras utilisateur, dedupliques en conservant l'ordre
        seen: set[str] = set()
        out: list[str] = []
        for u in DEFAULT_FEEDS + settings.disclosure_feed_list:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for url in self.feeds():
            try:
                resp = self._get(url)
            except Exception:
                continue
            feed = feedparser.parse(resp.content)
            for e in feed.entries:
                guid = e.get("id", e.get("link", e.get("title", "")))
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                published = None
                if getattr(e, "published_parsed", None):
                    published = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
                # nom d'emetteur souvent dispo via author/dc:creator sur ces wires
                company = (e.get("author", "") or "")[:256]
                items.append(
                    RawItem(
                        source=self.name,
                        native_id=guid,
                        title=e.get("title", ""),
                        url=e.get("link", ""),
                        summary=(e.get("summary", "") or "")[:1000],
                        company=company,
                        published_at=published,
                    )
                )
        return items
