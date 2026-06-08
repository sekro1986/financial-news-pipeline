"""Analyse quotidienne SIGNAL -> REALITE : impact sur le cours + verdict de correlation.

Chaque jour ouvrable, on prend les signaux du jour ouvrable PRECEDENT et, pour
chacun (société résolue en ticker), on mesure la reaction du cours puis on compare
au sens ATTENDU de l'evenement :
  - M&A/cible, offre, guidance relevee, prise de participation -> hausse attendue ;
  - profit warning, gating, faillite, short-seller, coupe de dividende... -> baisse ;
  - certains types sont neutres (depart dirigeant, divers) -> pas de verdict.

Verdict : confirmé (le cours a bougé dans le sens attendu, au-dela du seuil),
infirmé (sens oppose), neutre (mouvement faible), sans_attente, ou non_résolu
(ticker introuvable de façon fiable -> candidat a ajouter a la watchlist).

Resolution du ticker (FIABILITE > exhaustivite) : watchlist d'abord (ISIN curé),
sinon recherche Yahoo AVEC garde-fou de nom (on n'analyse jamais un titre dont le
nom ne correspond pas clairement -> evite 'Partners Group' -> 'Axon Partners Group').
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import urllib.parse

import httpx
from sqlalchemy import select

from .classifier import family_of
from .config import settings
from .db import SessionLocal, get_session, init_db
from .models import Signal, SignalOutcome
from .watchlist import active_entries

log = logging.getLogger("ma_signals.impact")

_UA = "Mozilla/5.0 (compatible; MASignals/1.0)"
_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=8&newsCount=0"
_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"

# Sens attendu de la reaction du cours par type d'evenement.
_EXPECTED: dict[str, int] = {
    # hausse attendue (cible / valorisation)
    "possible_offer": 1, "firm_offer": 1, "tender_offer": 1, "merger_agt": 1, "scheme": 1,
    "take_private": 1, "buyout": 1, "interest": 1, "stake": 1, "stake_13d": 1,
    "strategic_review": 1, "guidance_raise": 1, "buyback": 1, "price_spike": 1,
    "target_candidate": 1, "undervalued": 1, "accumulation": 1,
    # baisse attendue (mauvaise nouvelle)
    "profit_warning": -1, "guidance_cut": -1, "earnings_miss": -1, "dividend_cut": -1,
    "equity_raise": -1, "rights_issue": -1, "insolvency": -1, "default_event": -1,
    "covenant_breach": -1, "going_concern": -1, "restructuring": -1, "redemption_gating": -1,
    "fund_suspension": -1, "nav_cut": -1, "short_seller": -1, "accounting_irregularity": -1,
    "auditor_resignation": -1, "investigation": -1, "sanction": -1, "price_drop": -1,
    # ambigu -> 0 (pas de verdict)
    "exec_departure": 0, "generic": 0, "none": 0,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Mots qui ne sont JAMAIS un nom de société (adjectifs/fragments) -> pas de résolution
# (evite 'Scottish'->Scottish Mortgage, 'From'->titre coréen, 'Tire', 'Product'...).
_NOT_A_COMPANY = {
    "from", "tire", "scottish", "product", "immobilier", "nearly", "prominent",
    "here", "action", "repli", "palmar", "palmares", "short", "seller", "tyremaker",
    "group", "new", "the", "law", "firm", "directs", "slides", "offers", "union",
}

# Deal qui CAPOTE/se retire -> la cible BAISSE (sens inverse d'une offre vivante).
_COLLAPSE = re.compile(
    r"\b(walk[s]?\s+away|walked\s+away|pull[s]?\s+out|pulled\s+out|withdraw\w+|"
    r"abandon\w+|drop[s]?\s+(?:its\s+)?bid|dropped\s+(?:its\s+)?bid|call(?:s|ed)?\s+off|"
    r"terminat\w+|scrap[s]?\w*|reject\w+|fail[s]?\b|failed|collaps\w+|end[s]?\s+talks|"
    r"ended\s+talks|renonce|abandonne|retrait\w*\s+(?:de\s+)?(?:l['’]\s*)?offre|retir\w+\s+son\s+offre|échoue|echoue|capote)\b",
    re.IGNORECASE)
# Verbes indiquant que le SUJET en tete est l'ACQUEREUR (sa reaction est ambigue).
_ACQUIRER_VERB = re.compile(
    r"\b(offers?\s+to\s+acquire|agrees?\s+to\s+acquire|to\s+acquire|bid[s]?\s+for|"
    r"make[s]?\s+(?:an?\s+)?(?:bid|offer)|launch\w*\s+(?:a\s+)?(?:bid|offer|opa)|"
    r"augmente\s+sa\s+participation|monte\s+au\s+capital|lance\s+une\s+(?:opa|offre)|"
    r"fait\s+une\s+offre|propose\s+de\s+rachet)",
    re.IGNORECASE)
_LEAD_RE = re.compile(r"\W*([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})")


def _norm(s: str) -> str:
    return " ".join(_TOKEN_RE.findall((s or "").lower()))


def refine_expected(event_type: str, company: str, title: str) -> int:
    """Affine le sens attendu selon le contexte (deal qui capote, acquereur vs cible)."""
    exp = _EXPECTED.get(event_type, 0)
    if family_of(event_type) != "mna":
        return exp
    if _COLLAPSE.search(title or ""):
        return -1   # offre retiree/echouee -> la cible decroche
    # Si la societe analysee EST le sujet en tete d'une phrase d'acquisition,
    # c'est l'ACQUEREUR -> reaction ambigue, pas de verdict.
    m = _LEAD_RE.match(title or "")
    if m and _ACQUIRER_VERB.search(title or ""):
        a, c = _norm(m.group(1)), _norm(company)
        if c and (c == a or a.startswith(c) or c.startswith(a)):
            return 0
    return exp


def prev_business_day(today: dt.date | None = None) -> dt.date:
    d = (today or dt.datetime.now(dt.timezone.utc).date()) - dt.timedelta(days=1)
    while d.weekday() >= 5:   # 5=samedi, 6=dimanche
        d -= dt.timedelta(days=1)
    return d


def _lead_token(name: str) -> str:
    toks = _TOKEN_RE.findall(name.lower())
    return toks[0] if toks else ""


def _norm(s: str) -> str:
    return " ".join(_TOKEN_RE.findall((s or "").lower()))


def yahoo_search_symbol(company: str) -> str:
    """Nom -> ticker, garde-fou RENFORCÉ (fiabilité > exhaustivité) :
    - on rejette les noms génériques/fragments (_NOT_A_COMPANY) et trop courts ;
    - on n'accepte un résultat que si son nom officiel COMMENCE par le nom cherché
      (préfixe), pas seulement le 1er mot -> évite 'Scottish'->Scottish Mortgage."""
    norm = _norm(company)
    lead = _lead_token(company)
    if len(norm) < 3 or lead in _NOT_A_COMPANY or (len(norm.split()) == 1 and lead in _NOT_A_COMPANY):
        return ""
    try:
        r = httpx.get(_SEARCH.format(q=urllib.parse.quote(company)), headers={"User-Agent": _UA},
                      timeout=15, follow_redirects=True)
        r.raise_for_status()
        quotes = r.json().get("quotes", []) or []
    except Exception as exc:  # noqa: BLE001
        log.debug("yahoo_search %s: %s", company, exc)
        return ""
    for q in quotes:
        if q.get("quoteType") != "EQUITY" or not q.get("symbol"):
            continue
        nm = _norm(f"{q.get('shortname','')} {q.get('longname','')}")
        if nm.startswith(norm):   # nom officiel commence par le nom cherché -> identité fiable
            return q["symbol"]
    return ""


def price_reaction(symbol: str, signal_date: dt.date, price_fn=None) -> dict | None:
    """Variation du cours depuis le signal : ref = clôture du jour ouvrable AVANT le
    signal ; last = clôture la plus recente. Renvoie {pct_since, ref, last} ou None."""
    if price_fn:
        return price_fn(symbol, signal_date)
    try:
        r = httpx.get(_CHART.format(sym=symbol), headers={"User-Agent": _UA},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
    except Exception as exc:  # noqa: BLE001
        log.debug("price_reaction %s: %s", symbol, exc)
        return None
    ts = res.get("timestamp", []) or []
    closes = (res.get("indicators", {}).get("quote", [{}]) or [{}])[0].get("close", []) or []
    series = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c) for t, c in zip(ts, closes) if c]
    if len(series) < 2:
        return None
    before = [c for d, c in series if d < signal_date]
    ref = before[-1] if before else series[0][1]
    last_date, last = series[-1]
    if not ref:
        return None
    # reaction du JOUR du signal (clôture J vs clôture J-1)
    day_close = next((c for d, c in series if d == signal_date), None)
    pct_day = (day_close - ref) / ref * 100.0 if day_close else None
    return {"pct_since": (last - ref) / ref * 100.0, "pct_day": pct_day,
            "ref": ref, "last": last, "last_date": last_date.isoformat()}


def _verdict(expected: int, pct: float, thr: float) -> str:
    if expected == 0:
        return "sans_attente"
    if abs(pct) < thr:
        return "neutre"
    moved_up = pct > 0
    want_up = expected > 0
    return "confirmé" if moved_up == want_up else "infirmé"


def build_report(day: dt.date | None = None, resolve_fn=None, price_fn=None,
                 max_names: int | None = None) -> dict:
    """Analyse les signaux du jour 'day' (defaut: jour ouvrable precedent)."""
    day = day or prev_business_day()
    thr = settings.impact_min_pct
    cap = max_names or settings.impact_max_names
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)

    wl = [(e.canonical, e.yf_symbol, e.match_terms) for e in active_entries()]

    def _resolve(company: str, text: str) -> tuple[str, str]:
        if resolve_fn:
            return resolve_fn(company, text)
        low = text.lower()
        for canon, sym, terms in wl:
            if sym and any(t and t in low for t in terms):
                return sym, "watchlist"
        sym = yahoo_search_symbol(company)
        return (sym, "recherche") if sym else ("", "")

    rows: list[dict] = []
    with get_session() as session:
        sigs = session.scalars(
            select(Signal).where(Signal.detected_at >= start, Signal.detected_at < end,
                                  Signal.score > 0).order_by(Signal.score.desc())
        ).all()
        # dedup par (société, type) pour ne pas analyser 4x le meme deal
        seen: set[tuple[str, str]] = set()
        analyzed = 0
        for sig in sigs:
            key = (sig.company.lower(), sig.event_type)
            if key in seen:
                continue
            seen.add(key)
            if analyzed >= cap:
                break
            analyzed += 1
            fam = family_of(sig.event_type)
            expected = refine_expected(sig.event_type, sig.company, sig.title)
            symbol, by = _resolve(sig.company, f"{sig.title} {sig.company}")
            if not symbol:
                rows.append({"company": sig.company, "symbol": "", "resolved_by": "",
                             "event_type": sig.event_type, "family": fam, "expected_dir": expected,
                             "pct_since": 0.0, "pct_day": None, "last_date": "",
                             "verdict": "non_résolu", "signal_id": sig.id, "title": sig.title})
                continue
            pr = price_reaction(symbol, day, price_fn=price_fn)
            if not pr:
                rows.append({"company": sig.company, "symbol": symbol, "resolved_by": by,
                             "event_type": sig.event_type, "family": fam, "expected_dir": expected,
                             "pct_since": 0.0, "pct_day": None, "last_date": "",
                             "verdict": "non_résolu", "signal_id": sig.id, "title": sig.title})
                continue
            pct = pr["pct_since"]
            rows.append({"company": sig.company, "symbol": symbol, "resolved_by": by,
                         "event_type": sig.event_type, "family": fam, "expected_dir": expected,
                         "pct_since": pct, "pct_day": pr.get("pct_day"),
                         "last_date": pr.get("last_date", ""),
                         "verdict": _verdict(expected, pct, thr),
                         "signal_id": sig.id, "title": sig.title})

    graded = [r for r in rows if r["verdict"] in ("confirmé", "infirmé")]
    n_conf = sum(1 for r in graded if r["verdict"] == "confirmé")
    hit = round(100 * n_conf / len(graded)) if graded else 0
    return {"day": day.isoformat(), "rows": rows, "n": len(rows),
            "n_confirmed": n_conf, "n_infirmed": len(graded) - n_conf,
            "n_unresolved": sum(1 for r in rows if r["verdict"] == "non_résolu"),
            "hit_rate": hit}


_ICON = {"confirmé": "✅", "infirmé": "❌", "neutre": "➖", "sans_attente": "•", "non_résolu": "❓"}
_ARROW = {1: "↑", -1: "↓", 0: "·"}


def render_markdown(rep: dict) -> str:
    L = [f"# Analyse d'impact — signaux du {rep['day']}",
         f"{rep['n']} signaux analysés · confirmés {rep['n_confirmed']} · infirmés {rep['n_infirmed']} "
         f"· non résolus {rep['n_unresolved']} → **fiabilité {rep['hit_rate']}%** (sur les signaux notés)", ""]
    for r in sorted(rep["rows"], key=lambda x: (x["verdict"] != "infirmé", x["verdict"] != "confirmé")):
        sym = r["symbol"] or "?"
        if r["verdict"] == "non_résolu":
            move = "—"
        else:
            day = f"{r['pct_day']:+.1f}%" if r.get("pct_day") is not None else "n/d"
            cum = f"{r['pct_since']:+.1f}%"
            ld = f" au {r['last_date']}" if r.get("last_date") else ""
            move = f"jour {day} · cumulé {cum}{ld}"
        L.append(f"{_ICON.get(r['verdict'],'?')} **{r['company']}** ({sym}) "
                 f"{r['event_type']} attendu {_ARROW[r['expected_dir']]} → {move} — {r['verdict']}")
    unres = [r for r in rep["rows"] if r["verdict"] == "non_résolu"]
    if unres:
        L += ["", "## Non résolus (à ajouter à la watchlist pour un suivi fiable)",
              ", ".join(sorted({r["company"] for r in unres}))]
    return "\n".join(L)


def _telegram_summary(rep: dict) -> str:
    head = (f"🔎 IMPACT signaux du {rep['day']} — {rep['n']} analysés\n"
            f"confirmés {rep['n_confirmed']} · infirmés {rep['n_infirmed']} · "
            f"non résolus {rep['n_unresolved']} (fiabilité {rep['hit_rate']}%)\n")
    def _mv(r):
        d = f"{r['pct_day']:+.1f}%" if r.get("pct_day") is not None else "n/d"
        return f"J {d}/cum {r['pct_since']:+.1f}%"
    body = [f"{_ICON.get(r['verdict'],'?')} {r['company']} {r['event_type']} {_mv(r)}"
            for r in rep["rows"] if r["verdict"] in ("confirmé", "infirmé")][:15]
    return head + "\n".join(body)


def _persist(rep: dict) -> None:
    with SessionLocal() as s:
        for r in rep["rows"]:
            s.add(SignalOutcome(
                signal_id=r["signal_id"], signal_date=rep["day"], company=r["company"][:256],
                symbol=r["symbol"][:32], resolved_by=r["resolved_by"], event_type=r["event_type"][:48],
                family=r["family"][:24], expected_dir=r["expected_dir"], pct_since=r["pct_since"],
                verdict=r["verdict"],
            ))
        s.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Analyse d'impact quotidienne (signal -> réalité)")
    ap.add_argument("--date", default="", help="jour à analyser YYYY-MM-DD (defaut: jour ouvrable précédent)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--save", default="")
    args = ap.parse_args()

    init_db()
    day = dt.date.fromisoformat(args.date) if args.date else prev_business_day()
    rep = build_report(day=day)
    md = render_markdown(rep)
    print(md)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(md)
    _persist(rep)
    if args.send:
        from .alerting import send_message
        send_message(_telegram_summary(rep))


if __name__ == "__main__":
    main()
