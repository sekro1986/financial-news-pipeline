"""API REST (FastAPI) pour requêter les signaux historisés.

Endpoints :
  GET /health                 -> état du service
  GET /signals                -> liste filtrable (source, event_type, min_score, q, since)
  GET /signals/{id}           -> détail d'un signal
  GET /stats                  -> agrégats (par source, par type, par jour)
"""
from __future__ import annotations

import datetime as dt
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, init_db
from .models import Signal

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="MA-Signals API", version="1.0.0", lifespan=lifespan)


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Si API_KEY est definie dans la conf, exige l'en-tete X-API-Key correspondant.

    /health reste ouvert (sondes de supervision). Comparaison en temps constant.
    """
    if not settings.api_key:
        return  # API ouverte (mode historique) : ne s'utilise qu'en 127.0.0.1
    if not (x_api_key and secrets.compare_digest(x_api_key, settings.api_key)):
        raise HTTPException(401, "cle API absente ou invalide (en-tete X-API-Key)")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()}


@app.get("/signals", dependencies=[Depends(require_api_key)])
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


@app.get("/signals/{signal_id}", dependencies=[Depends(require_api_key)])
def get_signal(signal_id: int) -> dict:
    with SessionLocal() as s:
        obj = s.get(Signal, signal_id)
        if not obj:
            raise HTTPException(404, "signal introuvable")
        return obj.to_dict()


@app.get("/stats", dependencies=[Depends(require_api_key)])
def stats() -> dict:
    with SessionLocal() as s:
        by_source = dict(s.execute(select(Signal.source, func.count()).group_by(Signal.source)).all())
        by_type = dict(s.execute(select(Signal.event_type, func.count()).group_by(Signal.event_type)).all())
        total = s.scalar(select(func.count()).select_from(Signal))
        strong = s.scalar(select(func.count()).select_from(Signal).where(Signal.score >= 8))
        return {"total": total, "strong_signals": strong, "by_source": by_source, "by_event_type": by_type}
