# MA-Signals — pipeline de veille d'événements M&A

Détecte en quasi temps réel les signaux de fusion-acquisition (offre possible,
offre ferme, tender offer, prise de participation, *strategic review*…) sur
plusieurs marchés, les score, les historise et envoie des alertes.

Conçu après l'étude du cas **easyJet / Castlelake** (juin 2026) : l'annonce d'une
« offre possible » est tombée vendredi soir sur le flux réglementaire RNS ;
l'avantage n'était pas l'information (publique) mais la **vitesse de détection et
de préparation**. Ce pipeline industrialise précisément cette détection.

## Architecture

```
                 ┌─────────────────── COLLECTEURS ───────────────────┐
   SEC EDGAR ───▶│ sec_edgar   (SC TO-T, SC 13D, DEFM14A, 425…)       │
   RNS UK     ──▶│ rns_uk      (Investegate / LSE)                    │──┐
   AMF France ──▶│ amf_france  (flux RSS officiels)                  │  │  RawItem
   Presse     ──▶│ press_rss   (Google News EN + FR)                 │  │ (schéma commun)
                 └───────────────────────────────────────────────────┘  │
                                                                         ▼
   ┌──────────── PIPELINE ────────────┐      ┌──── CLASSIFIER ────┐
   │ watchlist → classify → dédup →   │◀────▶│ règles pondérées    │
   │ persiste (Postgres) → alerte     │      │ FR + EN, par type   │
   └──────────────┬───────────────────┘      └─────────────────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
   ┌─────────┐         ┌──────────────┐
   │ ALERTING│         │  API FastAPI │
   │ Telegram│         │ /signals /stats │
   │  Slack  │         └──────────────┘
   └─────────┘
```

Trois services : **poller** (collecte planifiée, APScheduler), **api** (FastAPI),
**db** (Postgres). Orchestrés par `docker compose`.

## Démarrage rapide (Docker)

```bash
cd financial-news-pipeline
cp .env.example .env
nano .env            # ⚠️ mets un vrai email dans USER_AGENT (exigé par la SEC)
                     #    + tes tokens Telegram/Slack si tu veux les alertes
./install.sh         # build + up -d
```

> Déploiement pas-à-pas sur VM Ubuntu (/opt) : voir **DEPLOY.md**.

Vérifier :

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/signals?min_score=8&limit=10"
docker compose logs -f poller
```

Doc interactive de l'API : http://localhost:8000/docs

## Démarrage sans Docker (venv + systemd)

```bash
./install.sh                       # crée .venv et installe les deps
source .venv/bin/activate
python -m ma_signals.poller --once # un cycle de test
```

Pour tourner en service permanent, voir `deploy/*.service` (systemd) :

```bash
cd /opt/financial-news-pipeline
sudo useradd -r -s /usr/sbin/nologin masignals || true
sudo cp deploy/masignals-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now masignals-poller masignals-api
```

## Configuration (.env)

| Variable | Rôle | Défaut |
|---|---|---|
| `USER_AGENT` | Identité HTTP — **email obligatoire pour la SEC** | — |
| `POLL_INTERVAL_SECONDS` | Cadence de collecte | 300 |
| `ALERT_MIN_SCORE` | Score minimal pour alerter | 5 |
| `ENABLED_SOURCES` | Sources actives (CSV) | les 4 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alertes Telegram | vide |
| `SLACK_WEBHOOK_URL` | Alertes Slack | vide |
| `WATCHLIST` | Restreindre à certaines sociétés (CSV). Vide = tout le marché | vide |

Sans canal d'alerte configuré, les signaux forts sont simplement **loggés** —
pratique pour observer le système avant de brancher les notifications.

## Enrichissement LLM (optionnel)

Derrière le pré-filtre regex (gratuit, écarte ~95 % du bruit), une couche LLM
(Claude Haiku) peut enrichir chaque item retenu : société **cible** vs
**acquéreur**, type d'événement, **sens attendu** du cours et confiance. Ça
fiabilise l'extraction de noms (titres Title-Case, FR/EN/DE/SV), la dédup
cross-langue et les verdicts du rapport d'impact. Désactivé par défaut ;
activer avec `LLM_ENABLED=true` + `ANTHROPIC_API_KEY` dans le `.env`
(budget par cycle, coupe-circuit et repli automatique sur les heuristiques :
sans clé ou en cas d'erreur API, le comportement historique est inchangé).

## Échelle de score

| Score | Lecture |
|---|---|
| ≥ 8 | Signal fort : offre ferme/possible, tender offer, scheme of arrangement, OPA |
| 5–7 | Intéressant : prise de participation activiste (13D), strategic review |
| 1–4 | Mention faible / contexte |
| 0 | Aucun signal M&A (ignoré) |

## Étendre

- **Nouvelle source** : créer `collectors/ma_source.py` exposant `.collect() ->
  list[RawItem]`, l'enregistrer dans `collectors/__init__.py`. Rien d'autre à toucher.
- **Nouvelles règles** : ajouter une ligne dans `RULES` (classifier.py).
- **Vers l'angle « anticipation »** : brancher un screener fondamental (valorisation
  décotée + accumulation au capital) qui émet aussi des `RawItem` — même tuyau.

## Tests

```bash
pip install pytest
pytest -q
```

## Avertissement

Outil de **veille d'information publique** à but éducatif/analytique. Ne constitue
pas un conseil en investissement. N'utilise **que** des données publiques : agir
sur une inform