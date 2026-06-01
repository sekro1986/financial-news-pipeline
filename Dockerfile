FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances système minimales (lxml / psycopg2 compilent depuis wheels, mais on
# garde build-essential par sécurité pour certaines archis).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ma_signals ./ma_signals

# Par défaut : lance le poller. L'API a sa propre commande dans docker-compose.
CMD ["python", "-m", "ma_signals.poller"]
