"""Collecteur SEC EDGAR (US).

Stratégie : interroge le flux "latest filings" (getcurrent, format Atom) pour les
types de formulaires liés au M&A. C'est gratuit, officiel, quasi temps réel.

Formulaires suivis :
  SC TO-T / SC TO-I : tender offers (offre publique d'achat)
  SC 13D            : prise de participation > 5 % avec intention d'influence
  DEFM14A           : proxy de fusion (vote des actionnaires)
  425               : communications en période de fusion
  8-K               : événements matériels (item 1.01 accord, item 8.01 autres)
"""
from __future__ import annotations

import datetime as dt
import re

import feedparser

from ..schema import RawItem
from .base import Collector

# form -> (event_hint, poids implicite via classifier sur le titre aussi)
FORMS = {
    "SC TO-T": "tender_offer",
    "SC TO-I": "tender_offer",
    "SC 13D": "stake_13d",
    "SC 13D/A": "stake_13d",
    "DEFM14A": "merger_agt",
    "425": "merger_agt",
}

_TITLE_RE = re.compile(r"^(?P<form>[\w/\s-]+?)\s*-\s*(?P<company>.+?)\s*\((?P<cik>\d+)\)")

BASE = "https://www.sec.gov/cgi-bin/browse-edgar"


class SecEdgarCollector(Collector):
    name = "sec_edgar"

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        for form, hint in FORMS.items():
            url = (
                f"{BASE}?action=getcurrent&type={form.replace(' ', '+')}"
                f"&output=atom&count=40"
            )
            try:
                resp = self._get(url)
            except Exception:
                continue
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                items.append(self._to_item(entry, hint))
        return items

    def _to_item(self, entry, hint: str) -> RawItem:
        title = entry.get("title", "")
        company = ""
        m = _TITLE_RE.match(title)
        if m:
            company = m.group("company").strip()

        published = None
        if getattr(entry, "updated_parsed", None):
            published = dt.datetime(*entry.updated_parsed[:6], tzinfo=dt.timezone.utc)

        return RawItem(
            source=self.name,
            native_id=entry.get("id", entry.get("link", title)),
            title=title,
            url=entry.get("link", ""),
            summary=entry.get("summary", ""),
            company=company,
            event_hint=hint,
            published_at=published,
        )
