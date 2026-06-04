"""Configuration centralisée, chargée depuis les variables d'environnement (.env)."""
from __future__ import annotations

import os

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
    alert_min_score: int = 8

    # --- Resserrage du bruit "generic" ---
    # Score maximal d'un item purement generique (aucune ancre de deal precise).
    # Doit rester < alert_min_score pour qu'un empilement de synonymes
    # ('takeover' + 'merger' + 'acquisition'...) n'alerte jamais seul.
    generic_score_cap: int = 5

    # Seuil d'alerte PAR FAMILLE d'evenement (un profit warning n'a pas la meme
    # barre qu'une OPA). Surchargeable via la variable d'env FAMILY_THRESHOLDS (JSON).
    # Les familles absentes retombent sur alert_min_score.
    family_thresholds: dict[str, int] = {
        "mna": 8,
        "liquidity": 6,
        "earnings": 7,
        "distress": 6,
        "capital": 7,
        "governance": 7,
        "regulatory": 7,
        "market": 6,
        "generic": 8,
    }

    # --- Dedup au niveau "histoire" (cross-source) ---
    # Regroupe le meme deal republie par plusieurs medias dans cette fenetre.
    story_dedup: bool = True
    story_window_hours: int = 48

    # --- Garde-fous anti-spam ---
    max_alerts_per_cycle: int = 25
    alert_batch_size: int = 8
    telegram_send_delay: float = 1.5

    # --- Sources activées (CSV) ---
    enabled_sources: str = "sec_edgar,rns_uk,amf_france,press_rss,rss_custom,disclosures,prices"

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

    # --- Flux RSS personnalisés (rss.app : X/Twitter, blogs, newsletters) ---
    # Deux sources cumulatives :
    #   1) le fichier dédié rss_custom_feeds_file (recommandé, 1 URL par ligne)
    #   2) la variable rss_custom_feeds (CSV, optionnelle)
    rss_custom_feeds_file: str = "feeds.txt"
    rss_custom_feeds: str = ""
    # Bonus de score pour ces sources curées (fiables / triées à la main).
    curated_score_bonus: int = 2

    # Sources beneficiant du bonus "curee" (wires officiels / triees main), CSV.
    curated_sources: str = "rss_custom,disclosures"

    # --- Flux de disclosures regulatoires multi-marches (collecteur disclosures) ---
    # Defauts integres (GlobeNewswire) + fichier dedie + variable d'env, comme rss_custom.
    disclosure_feeds_file: str = "disclosure_feeds.txt"
    disclosure_feeds: str = ""

    # --- Filtre watchlist optionnel (tickers/sociétés, CSV) ---
    watchlist: str = ""

    # --- Watchlist d'emetteurs surveilles (pivot ad-hoc + prix), fichier de seed ---
    watchlist_file: str = "watchlist.yaml"
    # Cle OpenFIGI optionnelle (releve la limite de debit ; non requise).
    openfigi_api_key: str = ""

    # --- Collecteur de prix (anomalie intraday via Yahoo Finance) ---
    price_min_pct: float = 3.0     # variation |%| minimale pour stocker un signal
    vol_spike_mult: float = 3.0    # ratio volume jour/moyenne -> pic de volume

    @property
    def sources_list(self) -> list[str]:
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def press_query_list(self) -> list[str]:
        return [q.strip() for q in self.press_queries.split("|") if q.strip()]

    @property
    def curated_source_list(self) -> list[str]:
        return [x.strip() for x in self.curated_sources.split(",") if x.strip()]

    def _file_env_feeds(self, env_csv: str, path: str) -> list[str]:
        """Fusionne URLs d'une variable CSV et d'un fichier (1/ligne, # = commentaire),
        dedupliquees en conservant l'ordre."""
        urls: list[str] = []
        raw = env_csv.replace("\n", ",")
        urls += [u.strip() for u in raw.split(",") if u.strip()]
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        t = line.strip()
                        if not t or t.startswith("#"):
                            continue
                        urls.append(t.split()[0])
            except OSError:
                pass
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @property
    def disclosure_feed_list(self) -> list[str]:
        """URLs supplementaires (hors defauts integres du collecteur) : env + fichier."""
        return self._file_env_feeds(self.disclosure_feeds, self.disclosure_feeds_file)

    @property
    def rss_custom_feed_list(self) -> list[str]:
        """Fusionne les URLs du fichier dédié et de la variable d'env (dédupliquées,
        ordre préservé). Dans le fichier : 1 URL par ligne, lignes vides et lignes
        commençant par # ignorées, commentaire en fin de ligne autorisé."""
        urls: list[str] = []

        # 1) variable d'environnement (CSV)
        raw = self.rss_custom_feeds.replace("\n", ",")
        urls += [u.strip() for u in raw.split(",") if u.strip()]

        # 2) fichier dédié
        path = self.rss_custom_feeds_file
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        s = line.strip()
                        if not s or s.startswith("#"):
                            continue
                        urls.append(s.split()[0])  # 1er token = URL (commentaire en fin OK)
            except OSError:
                pass

        # dédup en conservant l'ordre
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @property
    def watchlist_list(self) -> list[str]:
        return [w.strip().lower() for w in self.watchlist.split(",") if w.strip()]


settings = Settings()
