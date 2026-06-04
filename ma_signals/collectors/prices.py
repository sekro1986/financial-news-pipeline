"""Collecteur de prix : detection d'anomalies de cours/volume sur la watchlist.

Source de donnees : endpoint public Yahoo Finance "chart" (le meme que yfinance
utilise en interne), interroge en direct via httpx -> pas de dependance lourde ni
fragile. Donnees differees (~15 min) mais couvrant l'international, y compris SIX
(ex: PGHN.SW). C'est le seul angle qui peut alerter AVANT la presse : le cours
bouge d'abord, les journalistes ecrivent ensuite.

Pour chaque symbole yfinance de la watchlist active :
  - variation intraday vs cloture precedente -> score selon l'ampleur ;
  - pic de volume (jour courant vs moyenne des jours precedents) -> booste le score.
Emet un RawItem avec event_hint price_drop/price_spike (famille "market") et un
score_override (le classifieur de texte ne s'applique pas a un mouvement de cours).
Un signal par symbole / jour / direction (dedup via native_id).
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict

from ..config import settings
from ..schema import RawItem
from ..watchlist import active_entries
from .base import Collector

log = logging.getLogger("ma_signals.collectors")

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=15m"
# UA navigateur : Yahoo rejette certains User-Agents applicatifs.
_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"


def score_for_move(pct: float) -> int:
    """Mappe |variation %| -> score. Aligne sur l'echelle du pipeline (>=8 fort)."""
    a = abs(pct)
    if a >= 15: return 10
    if a >= 10: return 8
    if a >= 7:  return 7
    if a >= 5:  return 6          # = seuil famille "market" -> alerte
    if a >= settings.price_min_pct: return 4   # stocke mais sous le seuil
    return 0


class PriceCollector(Collector):
    name = "prices"

    def _fetch(self, sym: str) -> dict | None:
        try:
            r = self._get(_CHART.format(sym=sym), headers={"User-Agent": _UA})
            res = r.json()["chart"]["result"][0]
            return res
        except Exception as exc:  # noqa: BLE001
            log.debug("prices %s: %s", sym, exc)
            return None

    @staticmethod
    def _analyze(res: dict) -> tuple[float, float, float, float]:
        """Renvoie (pct_change, last_price, prev_close, vol_ratio)."""
        meta = res.get("meta", {})
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0
        last = meta.get("regularMarketPrice")
        ts = res.get("timestamp", []) or []
        quote = (res.get("indicators", {}).get("quote", [{}]) or [{}])[0]
        closes = quote.get("close", []) or []
        vols = quote.get("volume", []) or []
        if last is None:
            # dernier close non nul de la serie
            for c in reversed(closes):
                if c:
                    last = c
                    break
        last = last or prev_close
        pct = ((last - prev_close) / prev_close * 100.0) if prev_close else 0.0

        # volume : somme du jour courant vs moyenne des jours precedents
        by_day: dict[dt.date, float] = defaultdict(float)
        for t, v in zip(ts, vols):
            if v:
                by_day[dt.datetime.fromtimestamp(t, dt.timezone.utc).date()] += v
        vol_ratio = 0.0
        if by_day:
            days = sorted(by_day)
            today_vol = by_day[days[-1]]
            prior = [by_day[d] for d in days[:-1]]
            if prior and sum(prior):
                avg = sum(prior) / len(prior)
                vol_ratio = today_vol / avg if avg else 0.0
        return pct, float(last or 0), float(prev_close or 0), vol_ratio

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        entries = [e for e in active_entries() if e.yf_symbol]
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        for e in entries:
            res = self._fetch(e.yf_symbol)
            if not res:
                continue
            pct, last, prev_close, vol_ratio = self._analyze(res)
            score = score_for_move(pct)
            vol_spike = vol_ratio >= settings.vol_spike_mult
            if score <= 0 and not vol_spike:
                continue
            if vol_spike:
                score = max(score, 6) + 1   # le volume confirme/booste
            direction = "price_drop" if pct < 0 else "price_spike"
            ccy = res.get("meta", {}).get("currency", "")
            vtxt = f", volume x{vol_ratio:.1f}" if vol_ratio else ""
            title = f"{e.name} {pct:+.1f}% intraday ({e.yf_symbol}{vtxt})"
            summary = (f"Mouvement de cours {e.yf_symbol}: {pct:+.1f}% "
                       f"(cloture prec. {prev_close:.2f} -> {last:.2f} {ccy}){vtxt}. "
                       f"Source: Yahoo Finance (differe ~15 min).")
            items.append(
                RawItem(
                    source=self.name,
                    native_id=f"{e.yf_symbol}:{today}:{direction}",
                    title=title,
                    url=f"https://finance.yahoo.com/quote/{e.yf_symbol}",
                    summary=summary,
                    company=e.name,
                    event_hint=direction,
                    score_override=score,
                    published_at=dt.datetime.now(dt.timezone.utc),
                )
            )
        return items
