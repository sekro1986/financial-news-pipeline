"""Schéma commun : chaque collecteur produit des `RawItem` normalisés.

C'est le contrat qui découple les sources (formats hétérogènes) du reste du
pipeline (classifier, stockage, alerting). Ajouter une source = écrire un
collecteur qui émet des RawItem.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field


@dataclass
class RawItem:
    source: str                       # identifiant du collecteur (sec_edgar, rns_uk, ...)
    native_id: str                    # id natif (accession number, guid RSS, ...)
    title: str
    url: str = ""
    summary: str = ""
    company: str = ""
    event_hint: str = ""              # type d'événement déduit par le collecteur (ex: form EDGAR)
    score_override: int | None = None # score impose par le collecteur (ex: anomalie de prix, sans mots-cles)
    published_at: dt.datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Hash stable source+native_id pour éviter les doublons inter-runs."""
        raw = f"{self.source}::{self.native_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]

    @property
    def text(self) -> str:
        """Texte agrégé sur lequel le classifier travaille."""
        return " ".join(p for p in (self.title, self.summary, self.company) if p)
