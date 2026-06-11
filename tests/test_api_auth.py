"""Auth de l'API : X-API-Key exigee quand API_KEY est definie, /health toujours ouvert."""
from fastapi.testclient import TestClient

from ma_signals import api
from ma_signals.config import settings

client = TestClient(api.app)


def _with_key(key: str):
    old = settings.api_key
    settings.api_key = key
    return old


def test_api_ouverte_si_cle_vide():
    old = _with_key("")
    try:
        assert client.get("/signals").status_code == 200
        assert client.get("/stats").status_code == 200
    finally:
        settings.api_key = old


def test_health_reste_ouvert_avec_cle():
    old = _with_key("s3cret")
    try:
        assert client.get("/health").status_code == 200
    finally:
        settings.api_key = old


def test_refus_sans_cle():
    old = _with_key("s3cret")
    try:
        for path in ("/signals", "/signals/1", "/stats"):
            assert client.get(path).status_code == 401, path
    finally:
        settings.api_key = old


def test_refus_mauvaise_cle():
    old = _with_key("s3cret")
    try:
        r = client.get("/signals", headers={"X-API-Key": "mauvaise"})
        assert r.status_code == 401
    finally:
        settings.api_key = old


def test_accepte_bonne_cle():
    old = _with_key("s3cret")
    try:
        r = client.get("/signals", headers={"X-API-Key": "s3cret"})
        assert r.status_code == 200
        r = client.get("/stats", headers={"X-API-Key": "s3cret"})
        assert r.status_code == 200
    finally:
        settings.api_key = old
