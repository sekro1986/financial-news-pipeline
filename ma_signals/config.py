"""Configuration centralisée, chargée depuis les variables d'environnement (.env)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Base de données ---
    database_url: str = "sqlite:///./ma_signals.db"

    # --- Identité réseau (obligatoire et poli pour la SEC) ---
    user_agent: str = "MASignals/1.0 (contact: change-me@example.com)"

    # --- Cadence de polling (secondes) ---
    poll_interval_seconds: int = 300

    # --- Seuil de score pour déclencher une alerte ---
    # 8 = signaux forts uniquement (offre ferme/possible, tender offer, OPA, scheme).
    alert_min_score: int = 8

    # --- Garde-fous anti-spam ---
    max_alerts_per_cycle: int = 25
    alert_batch_size: int = 8
    telegram_send_delay: float = 1.5

    # --- Sources activées (CSV) ---
    enabled_sources: str = "sec_edgar,rns_uk,amf_france,press_rss"

    # --- Alerting Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Alerting Slack ---
    slack_webhook_url: str = ""

    # --- Presse : requêtes Google News (séparées par |) ---
    press_queries: str = (
        '"possible offer" OR takeover OR "takeover approach" when:3d|'
        '"strategic review" OR "exploring strategic alternatives" when:3d|'
        '"tender offer" OR "agreed to acquire" OR "buyout" when:3d'
    )

    # --- Filtre watchlist optionnel (tickers/sociétés, CSV) ---
    watchlist: str = ""

    @property
    def sources_list(self) -> list[str]:
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def press_query_list(self) -> list[str]:
        return [q.strip() for q in self.press_queries.split("|") if q.strip()]

    @property
    def watchlist_list(self) -> list[str]:
        return [w.strip().lower() for w in self.watchlist.split(",") if w.strip()]


settings = Settings()
