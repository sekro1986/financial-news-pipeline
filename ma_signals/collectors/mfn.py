"""Collecteur MFN.se (wire de communiqués nordiques/européens).

MFN expose un flux XML maison (PAS du RSS standard : feedparser le mal-parse) mais
TRES riche : chaque item porte le nom de l'émetteur, son/ses ISIN, LEI, ticker(s)
et la langue. On le parse donc directement (ElementTree). Le titre est dans
<content><title>.

Par défaut, mode 'watchlist-only' : on ne garde que les émetteurs dont l'ISIN est
dans la watchlist (haute précision, exploite la richesse ISIN/LEI, faible bruit).
Mettre mfn_watchlist_only=False pour ingérer tout le flux nordique (volumineux et
majoritairement en langues nordiques ; le score-gate filtre le routine).
"""
from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET

from ..config import settings
from ..schema import RawItem
from ..watchlist import active_entries
from .base import Collector

log = logging.getLogger("ma_signals.collectors")

_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"


def _txt(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_mfn(xml_bytes: bytes) -> list[dict]:
    """Parse le XML MFN -> liste de dicts normalisés. Robuste aux items partiels."""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        author = item.find("author")
        content = item.find("content")
        title = _txt(content.find("title")) if content is not None else ""
        if not title:
            continue
        name = _txt(author.find("name")) if author is not None else ""
        isin = lei = ticker = ""
        if author is not None:
            isins = author.find("isins")
            if isins is not None and isins.find("isin") is not None:
                isin = _txt(isins.find("isin"))
            leis = author.find("leis")
            if leis is not None and leis.find("lei") is not None:
                lei = _txt(leis.find("lei"))
            tickers = author.find("tickers")
            if tickers is not None and tickers.find("ticker") is not None:
                ticker = _txt(tickers.find("ticker"))   # ex: XSTO:FLERIE
        props = item.find("properties")
        lang = _txt(props.find("lang")) if props is not None else ""
        preamble = _txt(content.find("preamble")) if content is not None else ""
        published = None
        pd = _txt(item.find("publishDate"))
        if pd:
            try:
                published = dt.datetime.fromisoformat(pd.replace("Z", "+00:00"))
            except ValueError:
                published = None
        out.append({
            "news_id": _txt(item.find("newsId")), "url": _txt(item.find("url")),
            "title": title, "name": name, "isin": isin, "lei": lei, "ticker": ticker,
            "lang": lang, "preamble": preamble, "published": published,
        })
    return out


class MfnCollector(Collector):
    name = "mfn"

    def collect(self) -> list[RawItem]:
        try:
            resp = self._get(settings.mfn_feed_url, headers={"User-Agent": _UA})
        except Exception as exc:  # noqa: BLE001
            log.debug("mfn fetch: %s", exc)
            return []

        wl_isins: set[str] = set()
        if settings.mfn_watchlist_only:
            wl_isins = {e.isin.upper() for e in active_entries() if e.isin}
            if not wl_isins:
                return []   # mode watchlist mais aucun ISIN -> rien a faire

        items: list[RawItem] = []
        for rec in parse_mfn(resp.content):
            if wl_isins and rec["isin"].upper() not in wl_isins:
                continue
            if not rec["news_id"]:
                continue
            items.append(RawItem(
                source=self.name, native_id=rec["news_id"], title=rec["title"],
                url=rec["url"], summary=rec["preamble"][:1000], company=rec["name"],
                published_at=rec["published"],
                extra={"isin": rec["isin"], "lei": rec["lei"], "ticker": rec["ticker"], "lang": rec["lang"]},
            ))
        return items
