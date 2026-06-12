"""Replay/backtest : rejoue les règles ACTUELLES du classifier sur l'historique.

Répond à « si je déploie ce changement de règles, qu'est-ce que ça change ? » :
on re-score chaque signal stocké (titre nettoyé + résumé) avec les règles
courantes et on compare à ce qui s'était passé à l'époque :
  - alertes GAGNÉES  : sous le seuil hier, alertables avec les nouvelles règles ;
  - alertes PERDUES  : alertables hier, sous le seuil aujourd'hui ;
  - re-typages et dérive de score par famille.
Quand un verdict d'impact existe (signal_outcome), il qualifie le diff :
perdre un signal « confirmé » est une régression ; perdre un « infirmé » est
un gain de précision.

Workflow type : modifier les règles dans classifier.py -> `python -m
ma_signals.replay --days 30` -> lire le diff -> ajuster -> déployer.

Limites assumées :
  - prices/screener sont exclus (score imposé par le collecteur, pas de règles) ;
  - pour les sources à event_hint (sec_edgar), le type stocké est conservé
    (même priorité que le pipeline : hint > règles) ;
  - si l'enrichissement LLM était actif à l'époque, le type historique peut
    venir du LLM : un « re-typage » peut refléter ce delta, pas un changement
    de règles.

Usage : python -m ma_signals.replay [--days 30] [--examples 10] [--send]
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from .classifier import classify, family_of, family_threshold
from .config import settings
from .extract import strip_source_suffix

log = logging.getLogger("ma_signals.replay")

# Sources dont le score ne vient pas des règles texte : rien à rejouer.
_EXCLUDED_SOURCES = {"prices", "screener"}
# Sources dont le type vient du collecteur (event_hint prioritaire sur les règles).
_HINT_SOURCES = {"sec_edgar"}


@dataclass
class Diff:
    signal_id: int
    title: str
    source: str
    old_type: str
    new_type: str
    old_score: int
    new_score: int
    verdict: str = ""   # verdict d'impact historique, si mesuré

    @property
    def old_alert(self) -> bool:
        return self.old_score >= family_threshold(family_of(self.old_type))

    @property
    def new_alert(self) -> bool:
        return self.new_score >= family_threshold(family_of(self.new_type))


@dataclass
class ReplayReport:
    days: int
    total: int = 0
    unchanged: int = 0
    retyped: int = 0
    rescored: int = 0
    gained: list[Diff] = field(default_factory=list)
    lost: list[Diff] = field(default_factory=list)
    by_family_delta: Counter = field(default_factory=Counter)


def replay_signal(sig, verdicts: dict[int, str]) -> Diff:
    """Re-score un signal stocké avec les règles courantes (même chemin que le
    pipeline : titre sans suffixe éditeur + résumé, bonus source curée)."""
    text = " ".join(p for p in (strip_source_suffix(sig.title or ""), sig.summary or "") if p)
    cls = classify(text)
    new_score = cls.score
    if sig.source in settings.curated_source_list:
        new_score += settings.curated_score_bonus
    # Priorité du pipeline conservée : pour les sources à hint, le type stocké
    # vient du collecteur et resterait identique au re-traitement.
    new_type = sig.event_type if sig.source in _HINT_SOURCES else cls.event_type
    return Diff(sig.id, sig.title or "", sig.source, sig.event_type or "none",
                new_type, sig.score or 0, new_score,
                verdicts.get(sig.id, ""))


def run_replay(days: int = 30) -> ReplayReport:
    from .db import SessionLocal
    from .models import Signal, SignalOutcome

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    rep = ReplayReport(days=days)

    with SessionLocal() as s:
        verdicts = {sid: v for sid, v in s.execute(
            select(SignalOutcome.signal_id, SignalOutcome.verdict)
            .where(SignalOutcome.verdict != "")).all()}
        rows = s.scalars(
            select(Signal).where(Signal.detected_at >= cutoff)
            .where(Signal.source.notin_(_EXCLUDED_SOURCES))
            .order_by(Signal.id)).all()

        for sig in rows:
            d = replay_signal(sig, verdicts)
            rep.total += 1
            if d.new_type != d.old_type:
                rep.retyped += 1
            if d.new_score != d.old_score:
                rep.rescored += 1
                rep.by_family_delta[family_of(d.new_type)] += d.new_score - d.old_score
            if d.new_type == d.old_type and d.new_score == d.old_score:
                rep.unchanged += 1
            if d.new_alert and not d.old_alert:
                rep.gained.append(d)
            elif d.old_alert and not d.new_alert:
                rep.lost.append(d)
    return rep


def _verdict_mix(diffs: list[Diff]) -> str:
    c = Counter(d.verdict for d in diffs if d.verdict)
    if not c:
        return "aucun verdict d'impact disponible"
    return ", ".join(f"{k}: {n}" for k, n in c.most_common())


def format_report(rep: ReplayReport, examples: int = 10) -> str:
    lines = [
        f"🔁 REPLAY des règles — {rep.days} derniers jours",
        f"{rep.total} signaux rejoués · {rep.unchanged} inchangés · "
        f"{rep.retyped} re-typés · {rep.rescored} re-scorés",
        "",
        f"➕ Alertes GAGNÉES : {len(rep.gained)} ({_verdict_mix(rep.gained)})",
    ]
    for d in rep.gained[:examples]:
        lines.append(f"  #{d.signal_id} [{d.source}] {d.old_type}:{d.old_score} -> "
                     f"{d.new_type}:{d.new_score}"
                     + (f" [{d.verdict}]" if d.verdict else "")
                     + f" — {d.title[:90]}")
    lines.append(f"\n➖ Alertes PERDUES : {len(rep.lost)} ({_verdict_mix(rep.lost)})")
    regrets = [d for d in rep.lost if d.verdict == "confirmé"]
    if regrets:
        lines.append(f"  ⚠️ dont {len(regrets)} CONFIRMÉS par le marché — régression probable !")
    for d in rep.lost[:examples]:
        lines.append(f"  #{d.signal_id} [{d.source}] {d.old_type}:{d.old_score} -> "
                     f"{d.new_type}:{d.new_score}"
                     + (f" [{d.verdict}]" if d.verdict else "")
                     + f" — {d.title[:90]}")
    if rep.by_family_delta:
        det = " · ".join(f"{fam}: {delta:+d}" for fam, delta in
                         sorted(rep.by_family_delta.items(), key=lambda kv: kv[1]))
        lines.append(f"\nDérive de score cumulée par famille : {det}")
    if not rep.gained and not rep.lost:
        lines.append("\nAucun basculement d'alerte : le changement de règles est neutre "
                     "sur cette fenêtre.")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Replay des règles sur l'historique")
    parser.add_argument("--days", type=int, default=30, help="fenêtre (défaut 30 j)")
    parser.add_argument("--examples", type=int, default=10, help="exemples listés par section")
    parser.add_argument("--send", action="store_true", help="envoie le rapport (Telegram/Slack)")
    args = parser.parse_args()

    from .db import init_db
    init_db()
    rep = run_replay(days=args.days)
    text = format_report(rep, examples=args.examples)
    print(text)
    if args.send:
        from .alerting import send_message
        send_message(text)


if __name__ == "__main__":
    main()
