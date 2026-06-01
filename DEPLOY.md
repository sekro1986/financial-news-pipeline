# Déploiement sur VM Ubuntu (`/opt/financial-news-pipeline`)

Runbook pas-à-pas. Toutes les commandes se lancent **sur la VM**, en SSH.
⚠️ Le fichier `.env` (qui contient le token Telegram) n'est **jamais** commité —
il est créé directement sur la VM. `.gitignore` l'exclut déjà.

## 1. Récupérer le code

```bash
sudo mkdir -p /opt/financial-news-pipeline
sudo chown "$USER" /opt/financial-news-pipeline
git clone https://github.com/sekro1986/financial-news-pipeline.git /opt/financial-news-pipeline
cd /opt/financial-news-pipeline
```

(Si le repo est déjà cloné : `cd /opt/financial-news-pipeline && git pull`.)

## 2. Créer le `.env`

```bash
cp .env.example .env
nano .env
```

À renseigner :
- `USER_AGENT` : `MASignals/1.0 (contact: christopher.hislaire@gmail.com)` — **obligatoire** (la SEC exige un email).
- `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` : tes identifiants de bot (fournis hors repo).
- `POSTGRES_PASSWORD` : un mot de passe fort (mode Docker).
- `ALERT_MIN_SCORE` : 5 par défaut (monte à 8 pour ne recevoir que les signaux forts).
- `WATCHLIST` : laisse vide pour suivre tout le marché, ou liste de sociétés (CSV).

## 3a. Déploiement Docker (recommandé)

Prérequis (si Docker absent) :

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # puis se reconnecter une fois
```

Lancer :

```bash
./install.sh           # détecte Docker -> docker compose up -d --build
docker compose ps
```

## 3b. Déploiement sans Docker (venv + systemd)

```bash
./install.sh           # crée .venv + installe les deps
sudo useradd -r -s /usr/sbin/nologin masignals || true
sudo chown -R masignals:masignals /opt/financial-news-pipeline
sudo cp deploy/masignals-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now masignals-poller masignals-api
```

## 4. Vérifier

```bash
# Santé de l'API
curl http://localhost:8000/health

# Forcer un cycle de collecte immédiat (mode venv)
source .venv/bin/activate && python -m ma_signals.poller --once

# Logs en continu (Docker)
docker compose logs -f poller

# Derniers signaux forts
curl "http://localhost:8000/signals?min_score=8&limit=10"
```

Tu dois recevoir un message Telegram dès qu'un signal de score ≥ `ALERT_MIN_SCORE`
est détecté. Pour un test immédiat du canal sans attendre un vrai événement,
baisse temporairement `ALERT_MIN_SCORE=2`, relance un cycle, puis remets la valeur.

## 5. Mettre à jour plus tard

```bash
cd /opt/financial-news-pipeline
git pull
docker compose up -d --build          # Docker
# ou : sudo systemctl restart masignals-poller masignals-api   # systemd
```

## Exposer l'API à l'extérieur (optionnel)

Par défaut l'API écoute sur le port 8000 de la VM. Pour y accéder à distance,
mets un reverse-proxy (nginx/Caddy) avec TLS devant — ne l'expose pas en clair.
```
