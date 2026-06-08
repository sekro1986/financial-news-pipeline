"""Recap hebdomadaire AUTO-EVALUATIF (boucle d'amelioration continue).

Chaque vendredi : on classe les plus gros mouvements de la semaine sur les valeurs
de la WATCHLIST (les noms qu'on prétend surveiller), puis pour chacun on verifie si
le pipeline a produit un signal — et si oui, AVANT ou APRES le mouvement.

  ✅ capté/alerté   : un signal a franchi le seuil d'alerte dans la fenetre ;
  🟡 détecté        : un signal existe mais sous le seuil (stocké, non alerté) ;
  ❌ manqué         : aucun signal -> trou de couverture a analyser.

Un mouvement manqué = une piste d'amelioration (valeur hors watchlist, type
d'evenement non couvert, ou mouvement reellement inexplique). Le taux de capture
est historise (table weekly_audit) pour suivre les progres dans le temps.

Usage :
  python -m ma_signals.weekly_review            # apercu (markdown sur stdout)
  python -m ma_signals.weekly_review --send      # + envoi Telegram/Slack
  python -m ma_signals.weekly_review --days 7 --top 10 --save rapport.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, get_session, init_db
from .models import Signal, WeeklyAudit
from .watchlist import active_entries

log = logging.getLogger("ma_signals.weekly_review")

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"


def weekly_move(symbol: str, days: int = 7) -> dict | None:
    """Mouvement sur la periode via Yahoo : (pct, ref_close, last_close, swing, low_day).
    Renvoie None si indisponible."""
    import httpx
    try:
        r = httpx.get(_CHART.format(sym=symbol), headers={"User-Agent": _UA},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
    except Exception as exc:  # noqa: BLE001
        log.debug("weekly_move %s: %s", symbol, exc)
        return None
    ts = res.get("timestamp", []) or []
    closes = (res.get("indicators", {}).get("quote", [{}]) or [{}])[0].get("close", []) or []
    pairs = [(t, c) for t, c in zip(ts, closes) if c]
    if len(pairs) < 2:
        return None
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - days * 86400
    window = [(t, c) for t, c in pairs if t >= cutoff] or pairs[-2:]
    ref_close = window[0][1]
    last_close = pairs[-1][1]
    pct = (last_close - ref_close) / ref_close * 100 if ref_close else 0.0
    # jour du mouvement le plus marqué (plus grosse variation absolue intra-semaine)
    low = min(window, key=lambda x: x[1])
    high = max(window, key=lambda x: x[1])
    swing = (high[1] - low[1]) / ref_close * 100 if ref_close else 0.0
    big_day_ts = low[0] if abs(low[1] - ref_close) > abs(high[1] - ref_close) else high[0]
    return {"pct": pct, "ref": ref_close, "last": last_close, "swing": swing,
            "big_day": dt.datetime.fromtimestamp(big_day_ts, dt.timezone.utc)}


def _signals_for(session, terms: list[str], cutoff: dt.datetime) -> list[Signal]:
    rows = session.scalars(select(Signal).where(Signal.detected_at >= cutoff)).all()
    out = []
    for sig in rows:
        hay = f"{sig.title} {sig.company}".lower()
        if any(t and t in hay for t in terms):
            out.append(sig)
    return out


def build_report(days: int = 7, top: int = 10, price_fn=None) -> dict:
    """Construit le recap. price_fn(symbol, days)->dict|None injectable (tests)."""
    price_fn = price_fn or weekly_move
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    entries = [e for e in active_entries() if e.yf_symbol]

    movers = []
    with get_session() as session:
        for e in entries:
            mv = price_fn(e.yf_symbol, days)
            if not mv:
                continue
            sigs = _signals_for(session, e.match_terms, cutoff)
            sent = [s for s in sigs if s.status == "envoye"]
            if sent:
                status, icon = "capté", "✅"
            elif sigs:
                status, icon = "détecté", "🟡"
            else:
                status, icon = "manqué", "❌"
            # antériorité : 1er signal vs jour du gros mouvement
            lead = ""
            if sigs:
                first = min(sigs, key=lambda s: s.detected_at or cutoff)
                fd = first.detected_at
                if fd is not None and fd.tzinfo is None:
                    fd = fd.replace(tzinfo=dt.timezone.utc)   # SQLite renvoie du naive
                if fd and mv["big_day"]:
                    delta_h = (mv["big_day"] - fd).total_seconds() / 3600
                    lead = "anticipé" if delta_h > 6 else ("concomitant" if delta_h > -18 else "après")
            movers.append({
                "name": e.name, "symbol": e.yf_symbol, "pct": mv["pct"], "swing": mv["swing"],
                "status": status, "icon": icon, "lead": lead,
                "n_signals": len(sigs), "sources": sorted({s.source for s in sigs}),
                "event_types": sorted({s.event_type for s in sigs}),
            })

    movers.sort(key=lambda m: abs(m["pct"]), reverse=True)
    movers = movers[:top]
    n = len(movers)
    n_capt = sum(1 for m in movers if m["status"] == "capté")
    n_det = sum(1 for m in movers if m["status"] == "détecté")
    n_miss = sum(1 for m in movers if m["status"] == "manqué")
    rate = round(100 * n_capt / n) if n else 0
    return {"days": days, "movers": movers, "n": n, "n_captured": n_capt,
            "n_detected": n_det, "n_missed": n_miss, "capture_rate": rate}


def render_markdown(rep: dict) -> str:
    d = dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y")
    lines = [f"# Recap hebdo MA-Signals — {d}",
             f"Top {rep['n']} mouvements (watchlist, {rep['days']}j) — "
             f"capté {rep['n_captured']} · détecté {rep['n_detected']} · manqué {rep['n_missed']} "
             f"→ taux de capture **{rep['capture_rate']}%**", ""]
    for i, m in enumerate(rep["movers"], 1):
        det = ""
        if m["n_signals"]:
            det = f" — {m['n_signals']} signal(s) [{', '.join(m['event_types'])}] via {', '.join(m['sources'])}"
            if m["lead"]:
                det += f" ({m['lead']})"
        lines.append(f"{i}. {m['icon']} **{m['name']}** {m['pct']:+.1f}% "
                     f"(amplitude {m['swing']:.0f}%) — {m['status']}{det}")
    misses = [m for m in rep["movers"] if m["status"] == "manqué"]
    if misses:
        lines += ["", "## À investiguer (mouvements manqués)"]
        for m in misses:
            lines.append(f"- **{m['name']}** {m['pct']:+.1f}% : aucun signal. "
                         f"Mouvement inexpliqué, type d'événement non couvert, ou source manquante ?")
    return "\n".join(lines)


def _telegram_summary(rep: dict) -> str:
    d = dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y")
    head = (f"📊 RECAP HEBDO {d} — top {rep['n']} mouvements\n"
            f"capté {rep['n_captured']} · détecté {rep['n_detected']} · manqué {rep['n_missed']} "
            f"(capture {rep['capture_rate']}%)\n")
    body = []
    for i, m in enumerate(rep["movers"], 1):
        extra = f" [{','.join(m['sources'])}]" if m["sources"] else ""
        body.append(f"{i}. {m['icon']} {m['name']} {m['pct']:+.1f}%{extra}")
    return head + "\n".join(body)


def _persist(rep: dict) -> None:
    with SessionLocal() as s:
        s.add(WeeklyAudit(
            period_days=rep["days"], n_movers=rep["n"], n_captured=rep["n_captured"],
            n_detected=rep["n_detected"], n_missed=rep["n_missed"], capture_rate=rep["capture_rate"],
            details=json.dumps(rep["movers"], ensure_ascii=False, default=str)[:8000],
        ))
        s.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Recap hebdomadaire auto-évaluatif MA-Signals")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--send", action="store_true", help="envoyer le résumé sur Telegram/Slack")
    ap.add_argument("--save", default="", help="chemin d'un fichier .md à écrire")
    args = ap.parse_args()

    init_db()
    rep = build_report(days=args.days, top=args.top)
    md = render_markdown(rep)
    print(md)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(md)
    _persist(rep)
    if args.send:
        from .alerting import send_message
        send_message(_telegram_summary(rep))


if __name__ == "__main__":
    main()
