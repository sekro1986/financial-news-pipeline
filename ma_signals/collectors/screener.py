"""Screener 'anticipation' : reperer une proie potentielle AVANT l'annonce.

These : une cible d'OPA combine souvent deux signaux publics et gratuits :
  1) une VALORISATION DECOTEE  -> cours proche du plus-bas 52 semaines (Yahoo) ;
  2) une ACCUMULATION AU CAPITAL -> franchissements de seuils / prises de
     participation recents deja captes en base (13D/13G SEC, TR-1 UK, AMF).

Pour chaque emetteur de la watchlist (yf_symbol requis) :
  - position dans le range 52s = (cours - bas) / (haut - bas) ; 'decote' si <= seuil ;
  - nombre de signaux d'accumulation recents (stake / stake_13d / franchissement).
Score (famille 'anticipation') : decote + accumulation => 'target_candidate' alertable.
Decote seule ou accumulation seule => signal faible (stocke, sous le seuil) -> veille.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from ..config import settings
from ..db import get_session
from ..models import Signal
from ..schema import RawItem
from ..watchlist import active_entries
from .base import Collector

log = logging.getLogger("ma_signals.collectors")

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"
_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"
_ACCUM_TYPES = ("stake", "stake_13d")


def target_score(cheap: bool, n_accum: int) -> tuple[int, str]:
    """Renvoie (score, event_hint). Famille 'anticipation' (seuil par defaut 7)."""
    score = 0
    if cheap:
        score += settings.target_cheap_points
    if n_accum > 0:
        score += settings.target_accum_points
    if n_accum >= 2:
        score += 1
    if cheap and n_accum > 0:
        hint = "target_candidate"
    elif cheap:
        hint = "undervalued"
    else:
        hint = "accumulation"
    return score, hint


class TargetScreener(Collector):
    name = "screener"

    def _accum_count(self, session, terms: list[str]) -> int:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=settings.accumulation_window_days)
        rows = session.scalars(
            select(Signal).where(Signal.detected_at >= cutoff, Signal.event_type.in_(_ACCUM_TYPES))
        ).all()
        n = 0
        for sig in rows:
            hay = f"{sig.title} {sig.company}".lower()
            if any(t and t in hay for t in terms):
                n += 1
        return n

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        entries = [e for e in active_entries() if e.yf_symbol]
        if not entries:
            return items
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        with get_session() as session:
            for e in entries:
                try:
                    r = self._get(_CHART.format(sym=e.yf_symbol), headers={"User-Agent": _UA})
                    m = r.json()["chart"]["result"][0]["meta"]
                except Exception as exc:  # noqa: BLE001
                    log.debug("screener %s: %s", e.yf_symbol, exc)
                    continue
                price = m.get("regularMarketPrice")
                lo = m.get("fiftyTwoWeekLow")
                hi = m.get("fiftyTwoWeekHigh")
                if not (price and lo and hi) or hi <= lo:
                    continue
                pos = (price - lo) / (hi - lo)            # 0 = plus-bas, 1 = plus-haut
                cheap = pos <= settings.target_cheap_pct
                terms = e.match_terms
                n_accum = self._accum_count(session, terms)
                score, hint = target_score(cheap, n_accum)
                if score <= 0:
                    continue
                drawdown = (hi - price) / hi * 100
                title = (f"{e.name}: proie potentielle — {pos*100:.0f}% du range 52s "
                         f"(-{drawdown:.0f}% vs haut), {n_accum} accumulation(s) recente(s)")
                summary = (f"Valorisation {e.yf_symbol}: {price:.2f} (bas52 {lo:.2f}/haut52 {hi:.2f}). "
                           f"{'Decotee' if cheap else 'Non decotee'} ; "
                           f"{n_accum} franchissement(s)/prise(s) de participation sur "
                           f"{settings.accumulation_window_days} j.")
                items.append(RawItem(
                    source=self.name,
                    native_id=f"{e.yf_symbol}:{today}:{hint}",
                    title=title, url="", summary=summary, company=e.name,
                    event_hint=hint, score_override=score,
                    published_at=dt.datetime.now(dt.timezone.utc),
                ))
        return items
