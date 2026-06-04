"""Symbologie : resolution d'entite / d'instrument via API gratuites.

- OpenFIGI (api.openfigi.com) : ISIN -> ticker / code place / FIGI / nom.
  Cle API optionnelle (settings.openfigi_api_key) pour relever la limite de debit.
- GLEIF  (api.gleif.org)      : nom ou ISIN -> LEI / nom legal / pays.

Sert a enrichir la watchlist (cf. watchlist.enrich) : on part d'un ISIN ou d'un
nom et on remplit ticker, yf_symbol, lei, country pour brancher prix + correlation.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("ma_signals.symbology")

# Code place OpenFIGI (exchCode) -> suffixe yfinance. Marches majeurs.
EXCH_TO_YF: dict[str, str] = {
    "SW": ".SW",   # SIX Swiss
    "SE": ".SW",   # SIX (variante)
    "LN": ".L",    # London
    "GR": ".DE", "GF": ".F", "GY": ".DE",  # Allemagne (Xetra/Francfort)
    "FP": ".PA",   # Euronext Paris
    "NA": ".AS",   # Euronext Amsterdam
    "BB": ".BR",   # Euronext Bruxelles
    "IM": ".MI",   # Borsa Italiana
    "SM": ".MC",   # Madrid
    "SS": ".ST", "SF": ".ST",  # Stockholm
    "DC": ".CO",   # Copenhague
    "NO": ".OL",   # Oslo
    "HE": ".HE",   # Helsinki
    "ID": ".IR",   # Dublin
    "JT": ".T",    # Tokyo
    "HK": ".HK",   # Hong Kong
    "CT": ".TO",   # Toronto
    "AT": ".AX",   # Australie (ASX)
    "SP": ".SI",   # Singapour
    # US : pas de suffixe yfinance
    "US": "", "UN": "", "UW": "", "UQ": "", "UR": "",
}


# Pays de l'ISIN (2 lettres) -> codes place OpenFIGI a privilegier (cotation primaire).
COUNTRY_EXCH: dict[str, tuple[str, ...]] = {
    "CH": ("SW", "SE"), "GB": ("LN",), "US": ("US", "UN", "UW", "UQ", "UA"),
    "SE": ("SS",), "DE": ("GR", "GY"), "FR": ("FP",), "NL": ("NA",),
    "BE": ("BB",), "IT": ("IM",), "ES": ("SM",), "DK": ("DC",), "NO": ("NO",),
    "FI": ("HE",), "IE": ("ID",), "JP": ("JT",), "HK": ("HK",), "CA": ("CT",),
    "AU": ("AT",), "SG": ("SP",),
}

_FALLBACK_PREF = ("US", "LN", "SW", "GR", "FP")


def to_yf_symbol(ticker: str, exch_code: str) -> str:
    """Construit le symbole yfinance (ticker + suffixe de place)."""
    if not ticker:
        return ""
    suffix = EXCH_TO_YF.get((exch_code or "").upper(), "")
    return f"{ticker}{suffix}"


def _client() -> httpx.Client:
    headers = {"User-Agent": settings.user_agent, "Content-Type": "application/json"}
    if settings.openfigi_api_key:
        headers["X-OPENFIGI-APIKEY"] = settings.openfigi_api_key
    return httpx.Client(timeout=20.0, headers=headers)


def openfigi_by_isin(isin: str, prefer_exch: tuple[str, ...] | None = None) -> dict:
    """ISIN -> {ticker, exch_code, figi, name, yf_symbol}. Choisit la cotation
    PRIMAIRE selon le pays de l'ISIN (evite de prendre une cotation secondaire
    etrangere), sinon un ordre de repli. Renvoie {} si rien."""
    if not isin:
        return {}
    if prefer_exch is None:
        country = isin[:2].upper()
        prefer_exch = COUNTRY_EXCH.get(country, ()) + _FALLBACK_PREF
    with _client() as c:
        r = c.post("https://api.openfigi.com/v3/mapping", json=[{"idType": "ID_ISIN", "idValue": isin}])
        r.raise_for_status()
        data = (r.json() or [{}])[0].get("data", []) or []
    if not data:
        return {}
    # securites de type action en priorite
    equities = [d for d in data if "stock" in (d.get("securityType", "") or "").lower()] or data
    chosen = None
    for ex in prefer_exch:
        for d in equities:
            if (d.get("exchCode", "") or "").upper() == ex:
                chosen = d
                break
        if chosen:
            break
    chosen = chosen or equities[0]
    ticker = chosen.get("ticker", "") or ""
    exch = chosen.get("exchCode", "") or ""
    return {
        "ticker": ticker,
        "exch_code": exch,
        "figi": chosen.get("figi", "") or "",
        "name": chosen.get("name", "") or "",
        "yf_symbol": to_yf_symbol(ticker, exch),
    }


def gleif_lookup(name: str = "", isin: str = "") -> dict:
    """Nom ou ISIN -> {lei, legal_name, country}. Renvoie {} si rien."""
    params = {"page[size]": "1"}
    if isin:
        params["filter[isin]"] = isin
    elif name:
        params["filter[entity.legalName]"] = name
    else:
        return {}
    with _client() as c:
        r = c.get("https://api.gleif.org/api/v1/lei-records",
                  params=params, headers={"Accept": "application/vnd.api+json"})
        r.raise_for_status()
        data = (r.json() or {}).get("data", []) or []
    if not data:
        return {}
    a = data[0].get("attributes", {}) or {}
    ent = a.get("entity", {}) or {}
    return {
        "lei": a.get("lei", "") or "",
        "legal_name": (ent.get("legalName", {}) or {}).get("name", "") or "",
        "country": (ent.get("legalAddress", {}) or {}).get("country", "") or "",
    }
