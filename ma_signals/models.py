"""Modèles SQLAlchemy : la table `signals` historise chaque événement détecté."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Signal(Base):
    """Un signal = un item de flux normalisé + son scoring M&A."""

    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_signals_dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Clé de déduplication (hash source+id natif) : empêche les doublons.
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)

    # Cle de regroupement "histoire" (societe+type ou empreinte titre+type) :
    # fusionne le meme deal republie par plusieurs medias. Voir dedup.story_key().
    story_key: Mapped[str] = mapped_column(String(128), index=True, default="")

    source: Mapped[str] = mapped_column(String(32), index=True)      # sec_edgar, rns_uk, ...
    event_type: Mapped[str] = mapped_column(String(48), index=True)  # possible_offer, stake_13d, ...
    company: Mapped[str] = mapped_column(String(256), index=True, default="")
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    score: Mapped[int] = mapped_column(Integer, index=True, default=0)
    matched_keywords: Mapped[str] = mapped_column(Text, default="")  # CSV des mots-clés trouvés

    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), index=True
    )

    alerted: Mapped[int] = mapped_column(Integer, default=0)  # 0 = pas encore notifié, 1 = notifié

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "story_key": self.story_key,
            "event_type": self.event_type,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "score": self.score,
            "matched_keywords": self.matched_keywords.split(",") if self.matched_keywords else [],
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "alerted": bool(self.alerted),
        }
