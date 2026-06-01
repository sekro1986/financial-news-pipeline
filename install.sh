#!/usr/bin/env bash
# Déploiement MA-Signals sur Ubuntu. Idempotent : ré-exécutable sans danger.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> MA-Signals : installation"

# 1) .env
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "    .env créé depuis .env.example — PENSE À L'ÉDITER (email SEC, tokens)."
else
  echo "    .env déjà présent, conservé."
fi

# 2) Docker présent ?
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "==> Docker détecté. Build & démarrage des services..."
  docker compose up -d --build
  echo
  echo "==> OK. Services lancés :"
  docker compose ps
  echo
  echo "    API   : http://localhost:8000/health  et  /docs"
  echo "    Logs  : docker compose logs -f poller"
else
  echo "==> Docker absent. Installation en mode Python local (venv)."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  echo
  echo "==> OK. Pour lancer :"
  echo "    source .venv/bin/activate"
  echo "    python -m ma_signals.poller            # collecte en continu"
  echo "    uvicorn ma_signals.api:app --port 8000 # API (autre terminal)"
  echo
  echo "    (Pour Docker : installe docker.io + docker-compose-plugin puis relance ./install.sh)"
fi
