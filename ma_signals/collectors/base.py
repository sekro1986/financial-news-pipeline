"""Classe de base des collecteurs + client HTTP partagé (poli, robuste, retry)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings
from ..schema import RawItem

log = logging.getLogger("ma_signals.collectors")


class Collector(ABC):
    name: str = "base"

    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=20.0,
            follow_redirects=True,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _get(self, url: str, **kwargs) -> httpx.Response:
        r = self.client.get(url, **kwargs)
        r.raise_for_status()
        return r

    @abstractmethod
    def collect(self) -> list[RawItem]:
        """Récupère les items récents de la source, normalisés en RawItem."""
        ...

    def safe_collect(self) -> list[RawItem]:
        """Wrapper qui isole les pannes d'une source (n'arrête pas les autres)."""
        try:
            items = self.collect()
            log.info("collector %s: %d items", self.name, len(items))
            return items
        except Exception as exc:  # noqa: BLE001
            log.warning("collector %s a échoué: %s", self.name, exc)
            return []

    def close(self) -> None:
        self.client.close()
