"""Courbe de réaction intraday par nature d'événement.

Agrège les price_marks capturées : pour chaque famille et type d'événement,
variation moyenne vs t0 à chaque horizon (+1h/+4h/+8h/+24h). Les marques
capturées marché fermé sont écartées (le cours n'a pas pu réagir).

Usage : python -m ma_signals.reaction [--days 30] [--send]
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict

from sqlalchemy import select

log = logging.getLogger("ma_signals.reaction")


def build_reaction(days: int = 30) -> dict:
    import datetime as dt

    from .classifier import family_of
    from .db import SessionLocal
    from .models import PriceMark, Signal

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(PriceMark.label, PriceMark.pct_vs_t0, Signal.event_type)
            .join(Signal, Signal.id == PriceMark.signal_id)
            .where(PriceMark.captured_at >= cutoff,
                   PriceMark.captured_at.isnot(None),
                   PriceMark.pct_vs_t0.isnot(None),
                   PriceMark.label != "t0",
                   PriceMark.market_state != "closed")
        ).all()

    by_event: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_family: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for label, pct, event_type in rows:
        by_event[event_type][label].append(pct)
        by_family[family_of(event_type)][label].append(pct)

    def _agg(d):
        return {k: {lbl: {"avg": round(sum(v) / len(v), 2), "n": len(v)}
                    for lbl, v in sorted(hs.items())}
                for k, hs in sorted(d.items())}
    return {"days": days, "by_family": _agg(by_family), "by_event": _agg(by_event)}


def _hkey(label: str) -> float:
    try:
        return float(label.rstrip("h"))
    except ValueError:
        return 999.0


def format_report(data: dict) -> str:
    lines = [f"⏱️ Courbe de réaction intraday ({data['days']} j, marché ouvert uniquement)"]
    if not data["by_family"]:
        return lines[0] + "\nPas encore de marques capturées — patience, ça s'accumule."
    for fam, horizons in data["by_family"].items():
        det = " ".join(f"+{lbl}: {st['avg']:+.1f}% (n={st['n']})"
                       for lbl, st in sorted(horizons.items(), key=lambda kv: _hkey(kv[0])))
        lines.append(f"\n{fam} : {det}")
    lines.append("\nPar type :")
    for et, horizons in data["by_event"].items():
        det = " ".join(f"+{lbl}: {st['avg']:+.1f}% (n={st['n']})"
                       for lbl, st in sorted(horizons.items(), key=lambda kv: _hkey(kv[0])))
        lines.append(f"  {et} : {det}")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Courbe de réaction intraday")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--send", action="store_true", help="envoie le rapport (Telegram/Slack)")
    args = parser.parse_args()

    from .db import init_db
    init_db()
    text = format_report(build_reaction(days=args.days))
    print(text)
    if args.send:
        from .alerting import send_message
        send_message(text)


if __name__ == "__main__":
    main()
