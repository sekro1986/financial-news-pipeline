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
        "anticipation": 7,
        "generic": 8,
    }

    # --- Dedup au niveau "histoire" (cross-source) ---
    # Regroupe le meme deal republie par plusieurs medias dans cette fenetre.
    story_dedup: bool = True
    story_window_hours: int = 48

    # --- Mode d'envoi des alertes ---
    # alerts_enabled=False -> MODE OBSERVATION : le pipeline capte/score/analyse tout
    # (les signaux passent en statut 'silencieux'), mais AUCUNE alerte live n'est
    # envoyee. Le rapport quotidien d'impact + le scorecard restent envoyes.
    alerts_enabled: bool = True
    # Si alerts_enabled : n'alerter en live QUE ces familles (CSV) ; vide = toutes.
    # Permet une reouverture progressive (ex: 'mna,liquidity') quand le scorecard valide.
    alert_only_families: str = ""

    # --- Garde-fous anti-spam ---
    # Filet anti-doublon a l'ENVOI : si une alerte pour la meme (societe
    # normalisee, famille) est partie il y a moins de N heures, la nouvelle
    # est mise en sourdine au lieu d'etre renvoyee. 0 = desactive.
    alert_cooldown_hours: int = 24
    max_alerts_per_cycle: int = 25
    alert_batch_size: int = 8
    telegram_send_delay: float = 1.5

    # --- Sources activées (CSV) ---
    enabled_sources: str = "sec_edgar,rns_uk,amf_france,press_rss,rss_custom,disclosures,prices,adhoc_ir,screener,mfn"

    # --- Enrichissement LLM (Claude Haiku) : entites + type + sens attendu ---
    # Desactive par defaut ; necessite ANTHROPIC_API_KEY. Voir ma_signals/llm.py.
    llm_enabled: bool = False
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-4-5"
    llm_min_score: int = 4          # pre-score regex minimal pour meriter un appel
    llm_max_per_cycle: int = 80     # budget d'appels par cycle de collecte
    llm_confidence_floor: int = 60  # sous ce niveau, l'enrichissement est ignore
    llm_timeout: float = 25.0

    # --- Service agent SDK (digest quasi temps réel) — ma_signals/agent_digest.py ---
    # Désactivé par défaut ; nécessite ANTHROPIC_API_KEY + claude-agent-sdk (+ Node).
    agent_enabled: bool = False
    agent_model: str = "claude-haiku-4-5"   # passer à un modèle Sonnet si besoin de finesse
    agent_interval_seconds: int = 900        # cadence des cycles (aligné poller)
    agent_min_score: int = 4                 # pré-score minimal pour soumettre un signal
    agent_macro_interval_minutes: int = 60   # veille macro (recherche web) au plus 1x/h
    agent_max_cycles_per_day: int = 120      # budget dur d'appels agent par jour
    agent_max_turns: int = 8                 # tours agentiques max par run
    agent_timeout: float = 240.0             # timeout d'un run complet (s)
    agent_max_budget_usd: float = 0.25       # budget API max par run (garde-fou SDK)
    agent_state_path: str = "./agent_state.json"

    # --- Alerting Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Alerting Slack ---
    slack_webhook_url: str = ""

    # --- Sauvegarde SQLite (python -m ma_signals.backup, timer quotidien) ---
    backup_dir: str = "./backups"
    backup_keep: int = 14   # nombre de sauvegardes conservees (rotation)

    # --- Healthcheck (python -m ma_signals.health, timer 15 min) ---
    # Le poller touche heartbeat_path a chaque cycle reussi ; au-dela de
    # heartbeat_stale_minutes sans cycle, alerte (poller arrete/bloque).
    heartbeat_path: str = "./heartbeat.txt"
    heartbeat_stale_minutes: int = 30
    # Sources 'wire' censees produire en continu (PAS prices/screener/adhoc_ir
    # qui n'emettent que sur evenement) : muettes au-dela de N heures -> alerte.
    monitored_sources: str = "sec_edgar,rns_uk,press_rss,mfn"
    source_silence_hours: int = 24
    health_state_path: str = "./health_state.json"

    # --- Autofeed watchlist (python -m ma_signals.autofeed, timer hebdo) ---
    autofeed_window_days: int = 14    # fenetre d'observation des candidates
    autofeed_min_stories: int = 3     # histoires distinctes minimum pour candidater
    autofeed_max_adds: int = 5        # plafond d'ajouts par run
    autofeed_prune_days: int = 90     # entree auto muette depuis N jours -> desactivee

    # --- API REST : cle d'acces (en-tete X-API-Key) ---
    # Vide = API ouverte (acceptable seulement si elle ecoute en 127.0.0.1).
    # Definie = tous les endpoints sauf /health exigent l'en-tete X-API-Key.
    api_key: str = ""

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
    curated_sources: str = "rss_custom,disclosures,adhoc_ir,mfn"

    # --- Flux de disclosures regulatoires multi-marches (collecteur disclosures) ---
    # Defauts integres (GlobeNewswire) + fichier dedie + variable d'env, comme rss_custom.
    disclosure_feeds_file: str = "disclosure_feeds.txt"
    disclosure_feeds: str = ""

    # --- MFN.se (wire nordique/EU, riche ISIN/LEI) ---
    mfn_feed_url: str = "https://mfn.se/all/rss.xml"
    # True : ne garder que les emetteurs de la watchlist (par ISIN) -> haute precision.
    mfn_watchlist_only: bool = True

    # --- Filtre watchlist optionnel (tickers/sociétés, CSV) ---
    watchlist: str = ""

    # --- Filtrage par qualite de source (denylist d'editeurs/domaines), CSV ---
    # Sous-chaines (insensibles a la casse) ; un signal dont l'editeur matche est ecarte.
    source_denylist: str = "mshale,streamlinefeed,asatunews,sekbernews,newsline.com"

    # --- Watchlist d'emetteurs surveilles (pivot ad-hoc + prix), fichier de seed ---
    watchlist_file: str = "watchlist.yaml"
    # Cle OpenFIGI optionnelle (releve la limite de debit ; non requise).
    openfigi_api_key: str = ""

    # --- Collecteur de prix (anomalie intraday via Yahoo Finance) ---
    price_min_pct: float = 3.0     # variation |%| minimale pour stocker un signal
    vol_spike_mult: float = 3.0    # ratio volume jour/moyenne -> pic de volume

    # --- Correlation news<->prix (mouvement inexplique) ---
    correlation_enabled: bool = True
    unexplained_window_hours: int = 24   # fenetre de recherche d'une news explicative
    unexplained_bonus: int = 2           # bonus de score pour un mouvement inexplique

    # --- Analyse d'impact quotidienne (signal -> reaction du cours) ---
    impact_min_pct: float = 2.0     # variation |%| mini pour qu'un signal soit "confirmé"/"infirmé"
    impact_max_names: int = 40      # plafond de societes analysees par run (bornage reseau)

    # --- Screener 'anticipation' (proies potentielles) ---
    target_cheap_pct: float = 0.20       # position <= 20% du range 52s = decote
    target_cheap_points: int = 4         # points pour la decote
    target_accum_points: int = 5         # points pour >=1 accumulation recente
    accumulation_window_days: int = 90   # fenetre de comptage des franchissements/stakes

    @property
    def sources_list(self) -> list[str]:
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def press_query_list(self) -> list[str]:
        return [q.strip() for q in self.press_queries.split("|") if q.strip()]

    @property
    def source_deny_list(self) -> list[str]:
        return [x.strip().lower() for x in self.source_denylist.split(",") if x.strip()]

    @property
    def alert_only_family_list(self) -> list[str]:
        return [x.strip().lower() for x in self.alert_only_families.split(",") if x.strip()]

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
