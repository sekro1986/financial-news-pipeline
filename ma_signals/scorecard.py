"""Scorecard d'apprentissage : fiabilité des signaux par type d'événement.

Agrège l'historique des verdicts d'impact (table signal_outcome) pour répondre,
chiffres à l'appui : QUELS types de signaux sont réellement fiables ? Pour chaque
event_type / famille : nombre observé, taux de confirmation (confirmés / notés),
variation moyenne du cours, taux de non-résolution. C'est la boucle d'amélioration
pilotée par les données : on voit où le bot prédit bien et où resserrer.

Usage : python -m ma_signals.scorecard [--days 90] [--send]
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections import defaultdict

from sqlalchemy import select

from .classifier import family_of
from .db import SessionLocal, init_db
from .models import SignalOutcome

log = logging.getLogger("ma_signals.scorecard")


def build_scorecard(days: int | None = 90) -> dict:
    with SessionLocal() as s:
        stmt = select(SignalOutcome)
        if days:
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
            stmt = stmt.where(SignalOutcome.run_at >= cutoff)
        rows = list(s.scalars(stmt).all())

    def _agg(items):
        graded = [r for r in items if r.verdict in ("confirmé", "infirmé")]
        conf = [r for r in graded if r.verdict == "confirmé"]
        unres = [r for r in items if r.verdict == "non_résolu"]
        avg = round(sum(r.pct_since for r in graded) / len(graded), 1) if graded else 0.0
        return {"n": len(items), "graded": len(graded), "confirmed": len(conf),
                "hit_rate": round(100 * len(conf) / len(graded)) if graded else None,
                "avg_pct": avg, "unresolved": len(unres)}

    by_event: dict[str, list] = defaultdict(list)
    by_family: dict[str, list] = defaultdict(list)
    for r in rows:
        by_event[r.event_type].append(r)
        by_family[r.family or family_of(r.event_type)].append(r)

    events = {k: _agg(v) for k, v in by_event.items()}
    families = {k: _agg(v) for k, v in by_family.items()}
    # Non-résolus récurrents : un nom qui revient souvent = probablement une vraie
    # société cotée à ajouter à la watchlist (vs one-shot = junk/privé à ignorer).
    unres: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.verdict == "non_résolu" and r.company:
            unres[r.company] += 1
    top_unres = sorted(unres.items(), key=lambda kv: kv[1], reverse=True)
    return {"days": days, "total": len(rows), "n_runs": len({r.signal_date for r in rows}),
            "by_event": events, "by_family": families,
            "top_unresolved": top_unres, "n_unresolved": sum(unres.values())}


def render_markdown(sc: dict) -> str:
    L = [f"# Scorecard de fiabilité — {sc['total']} verdicts sur {sc['n_runs']} jour(s)"
         f"{' (≤ %dj)' % sc['days'] if sc['days'] else ''}", ""]
    if not sc["total"]:
        return "\n".join(L + ["_Pas encore de verdicts historisés — l'analyse quotidienne en accumule chaque jour._"])

    def _section(title, d):
        out = [f"## {title}", "", "| clé | n | notés | confirmés | fiabilité | var. moy. | non résolus |",
               "|---|---|---|---|---|---|---|"]
        for k, a in sorted(d.items(), key=lambda kv: kv[1]["graded"], reverse=True):
            hr = f"{a['hit_rate']}%" if a["hit_rate"] is not None else "—"
            out.append(f"| {k} | {a['n']} | {a['graded']} | {a['confirmed']} | {hr} "
                       f"| {a['avg_pct']:+.1f}% | {a['unresolved']} |")
        return out

    L += _section("Par famille", sc["by_family"]) + [""]
    L += _section("Par type d'événement", sc["by_event"])
    recur = [(c, n) for c, n in sc.get("top_unresolved", []) if n >= 2]
    if recur:
        L += ["", "## Non résolus récurrents (candidats watchlist)",
              "_Reviennent souvent → probablement de vraies cotées à ajouter (ISIN) ; "
              "les noms à 1 occurrence sont surtout du junk/privé._", ""]
        L += [f"- {c} ({n}×)" for c, n in recur[:15]]
    return "\n".join(L)


def _telegram_summary(sc: dict) -> str:
    if not sc["total"]:
        return "📈 Scorecard : pas encore de verdicts historisés."
    head = f"📈 SCORECARD — {sc['total']} verdicts sur {sc['n_runs']} jour(s)\n"
    fam = sorted(sc["by_family"].items(), key=lambda kv: kv[1]["graded"], reverse=True)
    body = [f"{k}: {a['hit_rate']}% fiab. (n={a['graded']}, moy {a['avg_pct']:+.1f}%)"
            for k, a in fam if a["hit_rate"] is not None]
    recur = [c for c, n in sc.get("top_unresolved", []) if n >= 2][:5]
    if recur:
        body.append("À ajouter watchlist ? " + ", ".join(recur))
    return head + "\n".join(body)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Scorecard de fiabilité des signaux MA-Signals")
    ap.add_argument("--days", type=int, default=90, help="fenêtre d'historique (0 = tout)")
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()
    init_db()
    sc = build_scorecard(days=args.days or None)
    print(render_markdown(sc))
    if args.send:
        from .alerting import send_message
        send_message(_telegram_summary(sc))


if __name__ == "__main__":
    main()
