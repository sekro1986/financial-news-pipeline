"""Healthcheck externe : poller vivant + sources muettes.

Complète le watchdog interne du poller (qui n'attrape que les cycles en ÉCHEC) :
ici on détecte aussi un process bloqué/arrêté (heartbeat périmé) et une source
qui ne produit plus rien (flux cassé silencieusement — ex. rss.app expiré,
endpoint Yahoo modifié).

Deux contrôles :
  1. heartbeat : le poller touche `heartbeat_path` à chaque cycle réussi ;
     si le fichier date de plus de `heartbeat_stale_minutes`, alerte.
  2. sources muettes : pour chaque source de `monitored_sources` (les flux
     "wire" qui produisent en continu — PAS prices/screener/adhoc_ir qui
     n'émettent que sur événement), alerte si aucun signal en base depuis
     `source_silence_hours` heures.

Anti-spam : une alerte par épisode (état persisté dans `health_state_path`),
plus un message de rétablissement quand l'incident se résout.

Usage : python -m ma_signals.health --send
        (timer systemd : deploy/masignals-health.{service,timer}, toutes les 15 min)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from sqlalchemy import func, select

from .config import settings

log = logging.getLogger("ma_signals.health")


# ------------------------------------------------------------------ heartbeat
def write_heartbeat(now: dt.datetime | None = None) -> None:
    """Appelé par le poller après chaque cycle réussi. Ne doit jamais le casser."""
    try:
        now = now or dt.datetime.now(dt.timezone.utc)
        Path(settings.heartbeat_path).write_text(now.isoformat(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.warning("écriture heartbeat impossible (%s)", settings.heartbeat_path)


def heartbeat_age_minutes(now: dt.datetime) -> float | None:
    """Âge du heartbeat en minutes. None si jamais écrit (poller jamais lancé ?)."""
    p = Path(settings.heartbeat_path)
    if not p.exists():
        return None
    try:
        ts = dt.datetime.fromisoformat(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return (now - ts).total_seconds() / 60.0


def check_heartbeat(now: dt.datetime) -> str | None:
    """Message d'alerte si le poller semble mort/bloqué, sinon None."""
    age = heartbeat_age_minutes(now)
    if age is None:
        return None  # pas encore de heartbeat : on ne crie pas à la fausse panne
    if age <= settings.heartbeat_stale_minutes:
        return None
    return (f"💔 MA-Signals : aucun cycle réussi depuis {age:.0f} min "
            f"(seuil {settings.heartbeat_stale_minutes}) — poller arrêté ou bloqué ? "
            "Voir : systemctl status masignals-poller / journalctl -u masignals-poller")


# ------------------------------------------------------------- sources muettes
def silent_sources(now: dt.datetime) -> list[tuple[str, float]]:
    """Sources monitorées sans AUCUN signal depuis source_silence_hours.

    Retourne [(source, heures_de_silence), ...]. Une source jamais vue en base
    n'est signalée que si la base contient par ailleurs des signaux plus vieux
    que la fenêtre (sinon : installation fraîche, pas un incident).
    """
    from .db import SessionLocal
    from .models import Signal

    monitored = [s.strip() for s in settings.monitored_sources.split(",") if s.strip()]
    enabled = set(settings.sources_list)
    monitored = [s for s in monitored if s in enabled]
    if not monitored:
        return []

    window = dt.timedelta(hours=settings.source_silence_hours)
    out: list[tuple[str, float]] = []
    with SessionLocal() as s:
        last_by_source = dict(s.execute(
            select(Signal.source, func.max(Signal.detected_at))
            .where(Signal.source.in_(monitored)).group_by(Signal.source)
        ).all())
        oldest = s.scalar(select(func.min(Signal.detected_at)))

    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.timezone.utc)

    for src in monitored:
        last = last_by_source.get(src)
        if last is None:
            # jamais rien produit : incident seulement si le pipeline tourne
            # depuis plus longtemps que la fenêtre.
            if oldest is not None and (now - oldest) > window:
                out.append((src, (now - oldest).total_seconds() / 3600.0))
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)  # SQLite renvoie du naive
        if (now - last) > window:
            out.append((src, (now - last).total_seconds() / 3600.0))
    return out


# ------------------------------------------------------------- flux individuels
def sick_feeds(now: dt.datetime) -> list[tuple[str, str]]:
    """Flux individuellement malades : [(url, raison), ...].

    Malade = en échec consécutif >= feed_fail_threshold (ex. rss.app 402),
    OU joignable mais sans aucun item frais depuis feed_silence_hours.
    """
    from .db import SessionLocal
    from .models import FeedHealth

    out: list[tuple[str, str]] = []
    with SessionLocal() as s:
        rows = list(s.query(FeedHealth).all())
    for r in rows:
        if (r.fail_streak or 0) >= settings.feed_fail_threshold:
            code = f"HTTP {r.last_status}" if r.last_status else "erreur réseau"
            out.append((r.url, f"{code} ×{r.fail_streak}"))
            continue
        last_item = r.last_item_at
        if last_item is not None:
            if last_item.tzinfo is None:
                last_item = last_item.replace(tzinfo=dt.timezone.utc)
            silent_h = (now - last_item).total_seconds() / 3600.0
            if silent_h > settings.feed_silence_hours:
                out.append((r.url, f"aucun item frais depuis {silent_h:.0f} h"))
    return out


# ------------------------------------------------------------------ état/anti-spam
def load_state() -> dict:
    p = Path(settings.health_state_path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"poller_down": False, "silent": [], "sick_feeds": []}


def save_state(state: dict) -> None:
    Path(settings.health_state_path).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")


def run_check(now: dt.datetime | None = None, send: bool = False) -> list[str]:
    """Exécute les contrôles. Retourne les messages émis (alertes + rétablissements)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = load_state()
    messages: list[str] = []

    # 1) poller
    hb_msg = check_heartbeat(now)
    if hb_msg and not state.get("poller_down"):
        messages.append(hb_msg)
        state["poller_down"] = True
    elif not hb_msg and state.get("poller_down"):
        messages.append("💚 MA-Signals : le poller est rétabli (cycles à nouveau réussis).")
        state["poller_down"] = False

    # 2) sources
    silent_now = silent_sources(now)
    silent_names = sorted(src for src, _ in silent_now)
    previously = set(state.get("silent", []))
    newly = [(s, h) for s, h in silent_now if s not in previously]
    recovered = sorted(previously - set(silent_names))
    if newly:
        det = ", ".join(f"{s} ({h:.0f} h)" for s, h in sorted(newly))
        messages.append(
            f"🔇 MA-Signals : source(s) muette(s) depuis plus de "
            f"{settings.source_silence_hours} h : {det} — flux cassé ?")
    if recovered:
        messages.append("🔊 MA-Signals : source(s) de nouveau active(s) : " + ", ".join(recovered))
    state["silent"] = silent_names

    # 3) flux individuels (rss_custom / disclosures)
    sick_now = sick_feeds(now)
    sick_urls = sorted(u for u, _ in sick_now)
    prev_sick = set(state.get("sick_feeds", []))
    new_sick = [(u, why) for u, why in sick_now if u not in prev_sick]
    healed = sorted(prev_sick - set(sick_urls))
    if new_sick:
        det = " ; ".join(f"{u} ({why})" for u, why in sorted(new_sick))
        messages.append(f"🩺 MA-Signals : flux RSS malade(s) : {det} — "
                        "à corriger ou commenter dans feeds.txt / disclosure_feeds.txt")
    if healed:
        messages.append("💚 MA-Signals : flux rétabli(s) : " + ", ".join(healed))
    state["sick_feeds"] = sick_urls

    save_state(state)
    if send and messages:
        from .alerting import send_message
        for m in messages:
            send_message(m)
    for m in messages:
        log.info("health: %s", m)
    return messages


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="MA-Signals healthcheck")
    parser.add_argument("--send", action="store_true", help="envoie les alertes (Telegram/Slack)")
    args = parser.parse_args()
    msgs = run_check(send=args.send)
    print("\n".join(msgs) if msgs else "OK — rien à signaler.")


if __name__ == "__main__":
    main()
