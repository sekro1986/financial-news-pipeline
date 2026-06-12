"""Modèles SQLAlchemy : la table `signals` historise chaque événement détecté."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Float,
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

    # Renseignes par la couche LLM (ma_signals/llm.py) quand elle est active :
    # acquirer = societe a l'initiative ; expected_move = sens attendu du cours de
    # la cible (-1/0/+1, 0 = ambigu) ; None = pas d'avis LLM -> heuristiques impact.
    acquirer: Mapped[str] = mapped_column(String(256), default="")
    expected_move: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_confidence: Mapped[int | None] = mapped_column(Integer, default=None)

    score: Mapped[int] = mapped_column(Integer, index=True, default=0)
    matched_keywords: Mapped[str] = mapped_column(Text, default="")  # CSV des mots-clés trouvés

    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), index=True
    )

    alerted: Mapped[int] = mapped_column(Integer, default=0)  # legacy : 1 = ne nécessite plus d'envoi

    # Cycle de vie de l'alerte (tracabilite fine) :
    #   sous_seuil = détecté sous le seuil (jamais envoyé) ; en_attente = à envoyer ;
    #   envoye = poussé sur un canal ; amorce = seeding initial silencieux.
    status: Mapped[str] = mapped_column(String(16), default="", index=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
            "acquirer": self.acquirer,
            "expected_move": self.expected_move,
            "llm_confidence": self.llm_confidence,
            "score": self.score,
            "matched_keywords": self.matched_keywords.split(",") if self.matched_keywords else [],
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "alerted": bool(self.alerted),
            "status": self.status,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


class WatchlistEntry(Base):
    """Emetteur surveille = pivot de la veille active.

    Sert (a) de cible pour le scraping ad-hoc par emetteur (ir_adhoc_url),
    (b) de liste de symboles pour le moniteur de prix (yf_symbol), et (c) de
    reference d'entite (lei/isin/figi) pour correler une news a un mouvement de
    cours. N'est PAS un filtre du flux news (cf. settings.watchlist pour ca)."""

    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("name", name="uq_watchlist_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), index=True)         # nom legal/usuel
    aliases: Mapped[str] = mapped_column(Text, default="")            # CSV d'alias/marques
    isin: Mapped[str] = mapped_column(String(16), index=True, default="")
    ticker: Mapped[str] = mapped_column(String(32), default="")        # ex: PGHN
    exch_code: Mapped[str] = mapped_column(String(8), default="")      # ex: SW (OpenFIGI)
    yf_symbol: Mapped[str] = mapped_column(String(32), index=True, default="")  # ex: PGHN.SW
    lei: Mapped[str] = mapped_column(String(20), default="")
    figi: Mapped[str] = mapped_column(String(16), default="")
    country: Mapped[str] = mapped_column(String(4), default="")
    ir_adhoc_url: Mapped[str] = mapped_column(Text, default="")        # page ad-hoc IR
    active: Mapped[int] = mapped_column(Integer, default=1, index=True)
    origin: Mapped[str] = mapped_column(String(8), default="")        # '' = manuel | 'auto' = autofeed
    notes: Mapped[str] = mapped_column(Text, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "aliases": [a for a in self.aliases.split(",") if a] if self.aliases else [],
            "isin": self.isin, "ticker": self.ticker, "exch_code": self.exch_code,
            "yf_symbol": self.yf_symbol, "lei": self.lei, "figi": self.figi,
            "country": self.country, "ir_adhoc_url": self.ir_adhoc_url,
            "active": bool(self.active), "notes": self.notes,
        }

    # Formes sociales a retirer en fin de nom pour obtenir le "nom de marche".
    _CORP_FORMS = {"ag", "ab", "asa", "nv", "sa", "se", "plc", "inc", "corp",
                   "corporation", "ltd", "limited", "llc", "co", "holding",
                   "holdings", "oyj", "spa", "gmbh", "as", "a/s"}

    @property
    def canonical(self) -> str:
        """Nom de référence (forme légale conservée) pour un affichage/dedup stable."""
        return self.name

    @property
    def match_terms(self) -> list[str]:
        """Termes (minuscule) servant a reconnaitre l'emetteur dans un texte :
        nom complet, alias, ticker, ISIN + 'nom de marche' (suffixes sociaux retires,
        ex: 'Partners Group Holding AG' -> 'partners group')."""
        terms = {t.strip().lower() for t in
                 ([self.name] + (self.aliases.split(",") if self.aliases else [])
                  + [self.ticker, self.isin]) if t and t.strip()}
        toks = self.name.lower().replace(",", " ").split()
        while toks and (toks[-1].strip(".") in self._CORP_FORMS or not toks[-1].strip(".").isalnum()):
            toks.pop()
        if len(toks) >= 1:
            core = " ".join(toks)
            if len(core) >= 3:
                terms.add(core)
        return [t for t in terms if t]


class WeeklyAudit(Base):
    """Trace d'un recap hebdo : permet de suivre l'evolution du taux de capture."""

    __tablename__ = "weekly_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), index=True
    )
    period_days: Mapped[int] = mapped_column(Integer, default=7)
    n_movers: Mapped[int] = mapped_column(Integer, default=0)
    n_captured: Mapped[int] = mapped_column(Integer, default=0)   # alertes
    n_detected: Mapped[int] = mapped_column(Integer, default=0)   # detecte mais sous le seuil
    n_missed: Mapped[int] = mapped_column(Integer, default=0)
    capture_rate: Mapped[int] = mapped_column(Integer, default=0) # % (capté/alerté sur movers)
    details: Mapped[str] = mapped_column(Text, default="")        # JSON lisible


class PriceMark(Base):
    """Cotation d'un titre a t0 (detection du signal) puis a horizons fixes.

    Matiere premiere de la courbe de reaction intraday par type d'evenement
    (ma_signals.reaction). Une marque non capturee a captured_at NULL et est
    retentee a chaque cycle tant qu'elle est due."""

    __tablename__ = "price_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, default="")
    label: Mapped[str] = mapped_column(String(8), default="")          # t0, 1h, 4h, 8h, 24h
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    captured_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    price: Mapped[float | None] = mapped_column(Float)
    pct_vs_t0: Mapped[float | None] = mapped_column(Float)
    market_state: Mapped[str] = mapped_column(String(8), default="")   # open/closed/unknown


class FeedHealth(Base):
    """Sante de CHAQUE flux RSS individuel (rss_custom, disclosures).

    Le healthcheck par source ne voit pas un flux mort au milieu d'une source
    qui produit encore (cas rss.app 402 du 12/06/2026 : rss_custom donnait
    115 items, deux flux etaient morts depuis des jours)."""

    __tablename__ = "feed_health"
    __table_args__ = (UniqueConstraint("url", name="uq_feed_health_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="", index=True)
    last_status: Mapped[int] = mapped_column(Integer, default=0)      # HTTP, 0 = erreur reseau
    fail_streak: Mapped[int] = mapped_column(Integer, default=0, index=True)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_item_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SignalOutcome(Base):
    """Resultat mesure d'un signal : impact reel sur le cours + verdict de correlation.

    Alimente l'analyse quotidienne (signal -> reaction du cours) et le suivi de la
    qualite predictive du bot dans le temps."""

    __tablename__ = "signal_outcome"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), index=True
    )
    signal_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    signal_date: Mapped[str] = mapped_column(String(10), default="")   # YYYY-MM-DD analysé
    company: Mapped[str] = mapped_column(String(256), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")        # "" = non résolu
    resolved_by: Mapped[str] = mapped_column(String(16), default="")   # watchlist | recherche | ""
    event_type: Mapped[str] = mapped_column(String(48), default="")
    family: Mapped[str] = mapped_column(String(24), default="")
    expected_dir: Mapped[int] = mapped_column(Integer, default=0)      # +1 hausse / -1 baisse / 0
    pct_since: Mapped[float] = mapped_column(default=0.0)              # variation depuis le signal (%)
    verdict: Mapped[str] = mapped_column(String(16), default="", index=True)  # confirmé/infirmé/neutre/sans_attente/non_résolu

    def to_dict(self) -> dict:
        return {"signal_id": self.signal_id, "signal_date": self.signal_date,
                "company": self.company, "symbol": self.symbol, "resolved_by": self.resolved_by,
                "event_type": self.event_type, "family": self.family,
                "expected_dir": self.expected_dir, "pct_since": round(self.pct_since, 2),
                "verdict": self.verdict}
