"""Collecteur RNS UK via Investegate (gratuit).

Investegate publie le flux RNS (Regulatory News Service) du LSE. La page d'accueil
liste les dernières annonces sous forme d'URLs très parlantes :

  /announcement/rns/{company-slug}--{ticker}/{headline-slug}/{id}

On extrait company, ticker, headline et id directement depuis l'URL. Le headline
("recommended-cash-offer", "possible-offer", "rule-2-4-announcement", ...) suffit
au classifier pour détecter un événement M&A. C'est exactement le canal où est
tombée l'annonce easyJet.
"""
from __future__ import annotations

import re

from ..schema import RawItem
from .base import Collector

HOME = "https://www.investegate.co.uk/"
ARCHIVE = "https://www.investegate.co.uk/announcement-archive"

# /announcement/{provider}/{company}--{ticker}/{headline}/{id}
_LINK_RE = re.compile(
    r'/announcement/(?P<provider>[\w-]+)/(?P<company>[^/]+?)--(?P<ticker>[^/]+?)/(?P<headline>[^/]+?)/(?P<id>\d+)'
)


def _deslug(s: str) -> str:
    return re.sub(r"[-_]+", " ", s).strip().title()


class RnsUkCollector(Collector):
    name = "rns_uk"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for url in (HOME, ARCHIVE):
            try:
                html = self._get(url).text
            except Exception:
                continue
            for m in _LINK_RE.finditer(html):
                ann_id = m.group("id")
                if ann_id in seen:
                    continue
                seen.add(ann_id)

                company = _deslug(m.group("company"))
                ticker = m.group("ticker").upper()
                headline = _deslug(m.group("headline"))
                link = "https://www.investegate.co.uk" + m.group(0)

                items.append(
                    RawItem(
                        source=self.name,
                        native_id=f"investegate:{ann_id}",
                        title=f"{headline} — {company} ({ticker})",
                        url=link,
                        summary=headline,
                        company=company,
                        published_at=None,  # non disponible sans fetch détaillé
                    )
                )
        return items
