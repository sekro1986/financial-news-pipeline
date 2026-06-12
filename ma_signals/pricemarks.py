"""Price marks : cotation au moment du signal puis à horizons fixes.

Mesure la réaction INTRADAY d'un titre à une news, par nature d'événement :
à la détection d'un signal alertable dont l'émetteur est résoluble, on capture
le cours à t0, puis on programme des marques à +1h/+4h/+8h/+24h (configurable).
Le cycle du poller capture les marques arrivées à échéance — pas de service
supplémentaire.

Complément de impact.py (clôtures quotidiennes, verdict confirmé/infirmé) :
ici on voit la FORME de la réaction (immédiate ? progressive ? qui s'estompe ?).
Rapport : python -m ma_signals.reaction.

Limites assumées :
  - Yahoo est différé (~15 min) : t0 est en réalité t0-15min — suffisant pour
    des tendances, pas du tick-par-tick ;
  - news hors séance : les marques capturées marché fermé portent
    market_state='closed' (le cours ne bouge pas) — le rapport les écarte ;
  - résolution : watchlist d'abord (gratuite et sûre), sinon recherche Yahoo
    durcie, plafonnée par cycle (price_marks_max_resolve_per_cycle).
"""
from __future__ import annotations

import datetime as dt
import logging

import httpx

from .config import settings
from .dedup import same_story_company

log = logging.getLogger("ma_signals.pricemarks")

_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"
_CHART_NOW = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"


def fetch_quote(symbol: str, price_fn=None) -> dict | None:
    """Dernier cours connu. Retourne {price, market_state} ou None.

    market_state : 'open' si la dernière cote a moins de ~20 min, sinon 'closed'
    (heuristique robuste, le endpoint chart n'expose pas l'état directement).
    """
    if price_fn:
        return price_fn(symbol)
    try:
        r = httpx.get(_CHART_NOW.format(sym=symbol), headers={"User-Agent": _UA},
                      timeout=15, follow_redirects=True)
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        ts = meta.get("regularMarketTime")
        if not price:
            return None
        state = "unknown"
        if ts:
            age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            state = "open" if age <= dt.timedelta(minutes=20) else "closed"
        return {"price": float(price), "market_state": state}
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_quote %s: %s", symbol, exc)
        return None


def _watchlist_symbol(company: str) -> str:
    """Symbole via la watchlist (gratuit, fiable). '' si non trouvé."""
    from .watchlist import active_entries

    for e in active_entries():
        if e.yf_symbol and (company == e.canonical
                            or same_story_company(company, e.name)):
            return e.yf_symbol
    return ""


def schedule_marks(signals: list, price_fn=None, resolve_fn=None) -> int:
    """Pour chaque signal résoluble : capture t0 + programme les horizons.
    Retourne le nombre de signaux instrumentés."""
    from .db import SessionLocal
    from .models import PriceMark

    if not settings.price_marks_enabled or not signals:
        return 0
    if resolve_fn is None:
        from .impact import yahoo_search_symbol as resolve_fn

    horizons = [float(h) for h in settings.price_mark_horizon_list]
    now = dt.datetime.now(dt.timezone.utc)
    resolves_left = settings.price_marks_max_resolve_per_cycle
    done = 0
    with SessionLocal() as s:
        for sig in signals:
            if not sig.company:
                continue
            symbol = _watchlist_symbol(sig.company)
            if not symbol:
                if resolves_left <= 0:
                    continue
                resolves_left -= 1
                symbol = resolve_fn(sig.company)
            if not symbol:
                continue
            q = fetch_quote(symbol, price_fn=price_fn)
            if not q:
                continue
            s.add(PriceMark(signal_id=sig.id, symbol=symbol, label="t0",
                            due_at=now, captured_at=now, price=q["price"],
                            pct_vs_t0=0.0, market_state=q["market_state"]))
            for h in horizons:
                s.add(PriceMark(signal_id=sig.id, symbol=symbol, label=f"{h:g}h",
                                due_at=now + dt.timedelta(hours=h)))
            done += 1
        s.commit()
    log.info("price marks: %d signal(aux) instrumenté(s)", done)
    return done


def capture_due(now: dt.datetime | None = None, price_fn=None) -> int:
    """Capture toutes les marques arrivées à échéance. Retourne le nombre capté."""
    from .db import SessionLocal
    from .models import PriceMark

    now = now or dt.datetime.now(dt.timezone.utc)
    captured = 0
    with SessionLocal() as s:
        due = list(s.query(PriceMark).filter(
            PriceMark.captured_at.is_(None), PriceMark.due_at <= now).all())
        t0_cache: dict[int, float] = {}
        for m in due:
            q = fetch_quote(m.symbol, price_fn=price_fn)
            if not q:
                continue  # retentée au prochain cycle
            t0 = t0_cache.get(m.signal_id)
            if t0 is None:
                row = s.query(PriceMark).filter_by(signal_id=m.signal_id, label="t0").first()
                t0 = row.price if row and row.price else None
                t0_cache[m.signal_id] = t0
            m.captured_at = now
            m.price = q["price"]
            m.market_state = q["market_state"]
            m.pct_vs_t0 = round((q["price"] - t0) / t0 * 100.0, 3) if t0 else None
            captured += 1
        s.commit()
    if captured:
        log.info("price marks: %d marque(s) capturée(s)", captured)
    return captured
