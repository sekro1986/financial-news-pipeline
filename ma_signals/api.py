"""API REST (FastAPI) pour requêter les signaux historisés.

Endpoints :
  GET /health                 -> état du service
  GET /signals                -> liste filtrable (source, event_type, min_score, q, since)
  GET /signals/{id}           -> détail d'un signal
  GET /stats                  -> agrégats (par source, par type, par jour)
"""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import func, select

from .db import SessionLocal, init_db
from .models import Signal

app = FastAPI(title="MA-Signals API", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()}


@app.get("/signals")
def list_signals(
    source: str | None = None,
    event_type: str | None = None,
    min_score: int = 0,
    q: str | None = Query(None, description="recherche texte dans titre/société"),
    since_hours: int | None = Query(None, description="ne garder que les N dernières heures"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
) -> dict:
    with SessionLocal() as s:
        stmt = select(Signal)
        if source:
            stmt = stmt.where(Signal.source == source)
        if event_type:
            stmt = stmt.where(Signal.event_type == event_type)
        if min_score:
            stmt = stmt.where(Signal.score >= min_score)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                func.lower(Signal.title).like(like) | func.lower(Signal.company).like(like)
            )
        if since_hours:
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours)
            stmt = stmt.where(Signal.detected_at >= cutoff)

        total = s.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(Signal.detected_at.desc()).limit(limit).offset(offset)
        rows = s.scalars(stmt).all()
        return {"total": total, "count": len(rows), "items": [r.to_dict() for r in rows]}


@app.get("/signals/{signal_id}")
def get_signal(signal_id: int) -> dict:
    with SessionLocal() as s:
        obj = s.get(Signal, signal_id)
        if not obj:
            raise HTTPException(404, "signal introuvable")
        return obj.to_dict()


@app.get("/stats")
def stats() -> dict:
    with SessionLocal() as s:
        by_source = dict(s.execute(select(Signal.source, func.count()).group_by(Signal.source)).all())
        by_type = dict(s.execute(select(Signal.event_type, func.count()).group_by(Signal.event_type)).all())
        total = s.scalar(select(func.count()).select_from(Signal))
        strong = s.scalar(select(func.count()).select_from(Signal).where(Signal.score >= 8))
        return {"total": total, "strong_signals": strong, "by_source": by_source, "by_event_type": by_type}
