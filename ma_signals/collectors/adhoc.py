"""Collecteur ad-hoc par emetteur : scrape la page IR (ir_adhoc_url) de chaque
emetteur de la watchlist.

C'est le signal "avant la presse" cote communique officiel : l'emetteur publie son
ad-hoc sur son propre site avant/au moment de la diffusion aux wires, donc avant la
presse secondaire (cas Partners Group).

Strategie robuste, par URL :
  1) si l'URL est un flux RSS/Atom -> feedparser (le plus fiable) ;
  2) sinon HTML -> extraction heuristique des liens-titres (BeautifulSoup),
     resolution des liens relatifs, dedup par URL.
Les pages 100% rendues en JS renvoient peu de chose : pointer alors ir_adhoc_url
vers un flux RSS de l'emetteur, ou vers une page de liste server-rendered (ex.
Partners Group: .../press-releases/corporate-news).

Source consideree "curee" (officielle) -> bonus de score dans le pipeline.
Le classifieur + score-gate ne gardent que les communiques materiels.
"""
from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from ..schema import RawItem
from ..watchlist import adhoc_targets
from .base import Collector

log = logging.getLogger("ma_signals.collectors")

_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"
_SKIP_HOSTS = ("linkedin.com", "twitter.com", "x.com", "facebook.com", "youtube.com",
               "instagram.com", "google.com")


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Liens-titres candidats d'une page de liste de communiques.
    Garde les <a> au texte substantiel (titre), hors nav/social/JS. Dedup par URL."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = (a.get("href") or "").strip()
        if not href or len(text) < 20 or len(text) > 250:
            continue
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        host = urlparse(url).netloc.lower()
        if any(host == d or host.endswith('.' + d) for d in _SKIP_HOSTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((text, url))
    return out


class AdhocCollector(Collector):
    name = "adhoc_ir"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in adhoc_targets():
            try:
                resp = self._get(url, headers={"User-Agent": _UA})
            except Exception as exc:  # noqa: BLE001
                log.debug("adhoc %s: %s", name, exc)
                continue

            # 1) tentative flux RSS/Atom
            feed = feedparser.parse(resp.content)
            if getattr(feed, "entries", None) and any(e.get("title") for e in feed.entries):
                for e in feed.entries[:40]:
                    guid = e.get("id", e.get("link", e.get("title", "")))
                    if not guid:
                        continue
                    published = None
                    if getattr(e, "published_parsed", None):
                        published = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
                    items.append(RawItem(
                        source=self.name, native_id=guid, title=e.get("title", ""),
                        url=e.get("link", ""), summary=(e.get("summary", "") or "")[:1000],
                        company=name, published_at=published,
                    ))
                continue

            # 2) repli HTML : extraction heuristique
            try:
                html = resp.text
            except Exception:
                continue
            for title, link in extract_links(html, url)[:40]:
                items.append(RawItem(
                    source=self.name, native_id=link, title=title,
                    url=link, summary="", company=name,
                ))
        return items
