#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fisconet_rulings.py
-------------------
Prototype-pijplijn voor het project "Fisconet - voorafgaande beslissingen".

Wat dit script doet:
  1. Haalt de lijst van voorafgaande beslissingen op via de publieke
     Fisconetplus REST-API (geen login nodig).
  2. Haalt per beslissing de volledige tekst op (Base64-gecodeerde HTML).
  3. Extraheert de geciteerde wetsartikelen, inclusief sub-niveaus
     (wetboek, artikel + bis/ter/..., §, lid, °, letter).
  4. Schrijft het resultaat weg als rulings.json en rulings.csv.

BELANGRIJK / eerlijke kanttekeningen:
  - De API is officieus (gereverse-engineerd uit de MyMinfin-webapp).
    Ze kan zonder waarschuwing wijzigen. Wees beleefd: lage snelheid,
    nette User-Agent, en niet parallel hameren.
  - De artikel-extractie is een *heuristiek* (reguliere expressies).
    Op een staal van 505 beslissingen (2003-2026): ~91% van de beslissingen
    kreeg >=1 artikel; ~75% van de gevonden verwijzingen had een herkenbare
    wetcode. Verwacht dus zowel gemiste als foute treffers.
    Zie de kanttekeningen in 00_bevindingen_en_stappenplan.md.
  - Dit script herpubliceert GEEN volledige teksten. Het bewaart enkel
    de afgeleide index + een deeplink naar Fisconetplus. Dat is ook de
    juridisch veiligste route (databankrecht FOD).

Gebruik:
    pip install requests
    python fisconet_rulings.py --max 150          # staal van 150 (default)
    python fisconet_rulings.py --max 0            # ALLES (~14.400, traag!)
    python fisconet_rulings.py --max 300 --out data/

Vereist: Python 3.9+ en het pakket 'requests'.
"""

import argparse
import base64
import csv
import html
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional

import requests

# --------------------------------------------------------------------------
# API-configuratie (geverifieerd werkend op 27-06-2026)
# --------------------------------------------------------------------------
BASE = "https://www.minfin.fgov.be/myminfin-rest/fisconetPlus/public"
# Taxonomie-node "Voorafgaande beslissingen (W 24.12.2002)":
TAXONOMY_VOORAFGAANDE_BESLISSINGEN = "94f19832-3ba5-47c2-bb13-4766e822774f"
# Permalink-sjabloon naar de beslissing op Fisconetplus:
DOC_URL = "https://www.minfin.fgov.be/myminfin-web/pages/public/fisconet/document/{guid}"

REQUEST_DELAY_SECONDS = 0.4  # beleefd throttelen
SUMMARY_PREVIEW = 500        # max tekens van de samenvatting in de webdata (site_data.json)
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "fisconet-rulings-prototype/0.1 (persoonlijk onderzoek)",
}

# --------------------------------------------------------------------------
# Artikel-extractie
# --------------------------------------------------------------------------
# Herkende wetboeken/codes. Breid deze lijst gerust uit.
CODES = (
    r"W\.?\s?BTW|BTW-Wetboek|Btw-Wetboek|WIB\s?92|KB/?WIB\s?92|"
    r"W\.Venn\.?|WVV|W\.Succ\.?|W\.Reg\.?|WDRT|Ger\.?\s?W\.?|"
    r"Grondwet|CIR\s?92|C\.I\.R\.?\s?92|WGB|W\.Div"
)
CODE_RE = re.compile("(" + CODES + ")", re.IGNORECASE)
# NL 'artikel/art.' EN FR 'article/l'article'. (?!\.\d) belet dat het eerste
# cijfer van een VCF-nummer (2.9.1.0.3) als gewoon artikel wordt opgepikt.
ART_RE = re.compile(
    r"\b(?:l['’]\s*)?art(?:ikel|icle|\.|s)?\s*"
    r"([0-9]+(?:/[0-9]+)?)(?!\.\d)"                           # nummer, bv. 205/1
    r"(\s*(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?",
    re.IGNORECASE,
)
DIR_RE = re.compile(r"Richtlijn\s+(\d{4}/\d+)/(E[EGU]+)", re.IGNORECASE)
# VCF: punt-genummerde artikelen (bv. 2.9.1.0.3) met VCF-cue in de buurt.
VCF_RE = re.compile(r"\b\d+(?:\.\d+){3,}")
VCF_CUE = re.compile(
    r"VCF|Vlaamse Codex Fiscaliteit|CFF|Code flamand de la fiscalité",
    re.IGNORECASE)
# Verwijzingen naar andere voorafgaande beslissingen (bv. 2019.0236, 2016.447).
RULING_RE = re.compile(
    r"(?:beslissing|décision|ruling)(?:en|s)?\s*(?:anticipées?\s*)?"
    r"(?:nr\.?|n°|nummer|numéro)?\s*(\d{3,4}\.\d{3,4})", re.IGNORECASE)
# Circulaires: oud (Ci.RH.241/596.009, Ci.D.19/...) en nieuw (2022/C/119).
CIRC_OLD_RE = re.compile(r"Ci\.[A-Z]{1,3}[.\s]?\d+[./]\d+(?:[./]\d+)?")
CIRC_NEW_RE = re.compile(r"\b20\d{2}\s*/\s*C\s*/\s*\d+")

# Canonieke wetcodes. Sleutel = code zonder punten/spaties/koppeltekens, in
# hoofdletters. Zo vallen schrijfwijzevarianten (en de Franse CIR 92 = WIB 92)
# samen onder een noemer.
_CODE_CANON = {
    "WIB92": "WIB92", "CIR92": "WIB92",          # CIR 92 = Franse naam van WIB 92
    "KB/WIB92": "KB/WIB92",
    "WBTW": "W.BTW", "BTWWETBOEK": "W.BTW",
    "WVV": "WVV", "WVENN": "W.Venn",
    "WREG": "W.Reg", "WSUCC": "W.Succ", "WDRT": "WDRT",
    "GERW": "Ger.W", "GRONDWET": "Grondwet", "WGB": "WGB", "WDIV": "W.Div",
}

_LID_WOORDEN = {
    "eerste": 1, "tweede": 2, "derde": 3, "vierde": 4, "vijfde": 5,
    "zesde": 6, "zevende": 7, "achtste": 8, "negende": 9, "tiende": 10,
    "premier": 1, "première": 1, "deuxième": 2, "troisième": 3, "quatrième": 4,
}


def _ordinal_lid(text: str) -> Optional[int]:
    m = re.search(
        r"(eerste|tweede|derde|vierde|vijfde|zesde|zevende|achtste|negende|tiende)\s+lid",
        text, re.IGNORECASE)
    if m:
        return _LID_WOORDEN[m.group(1).lower()]
    # Frans: "premier alinéa", "deuxième alinéa", "alinéa 2"
    m = re.search(r"(premier|première|deuxième|troisième|quatrième)\s+alinéa",
                  text, re.IGNORECASE)
    if m:
        return _LID_WOORDEN[m.group(1).lower()]
    m = re.search(r"alinéa\s+(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"lid\s+(\d+)", text, re.IGNORECASE) or \
        re.search(r"(\d+)\s*(?:ste|de)?\s*lid", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _normalize_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    cleaned = re.sub(r"\s+", "", code).rstrip(".")
    key = re.sub(r"[.\s\-]", "", cleaned).upper()
    return _CODE_CANON.get(key, cleaned)


def parse_articles(text: str) -> List[Dict]:
    """Extraheer artikelverwijzingen uit vrije tekst.

    Retourneert een lijst van dicts met velden:
      article (str, bv. '44' of '184bis'), code (str of None),
      par (int of None), lid (int of None), point (int of None),
      letter (str of None), raw (str).
    """
    if not text:
        return []
    s = re.sub(r"\s+", " ", text.replace(" ", " "))
    # HTML-entiteiten (&#160; = harde spatie, &amp; ...) decoderen; anders breken
    # citaten als "artikel &#160; 31" de regex. Witruimte normaliseren.
    s = re.sub(r"\s+", " ", html.unescape(text))
    out: List[Dict] = []

    for m in ART_RE.finditer(s):
        after = s[m.start():m.start() + 120]
        code_m = CODE_RE.search(after)
        code = _normalize_code(code_m.group(1)) if code_m else None
        sub_end = code_m.start() if code_m else 90
        sub = after[len(m.group(0)):sub_end]

        par_m = re.search(r"§\s*(\d+)", sub)
        # punt (°) met optioneel achtervoegsel: "2°", "2°quater", "9°ter" ...
        point_m = re.search(
            r"(\d+)\s*°\s*"
            r"(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)?",
            sub, re.IGNORECASE)
        letter_m = re.search(r"\b([a-z])\)", sub)
        lid = _ordinal_lid(sub)

        art_num = (m.group(1) + (m.group(2).strip() if m.group(2) else "")).lower()
        raw = re.sub(r"\s+", " ",
                     (m.group(0) + (sub + code_m.group(0) if code_m else ""))).strip()
        out.append({
            "article": art_num,
            "code": code,
            "par": int(par_m.group(1)) if par_m else None,
            "lid": lid,
            "point": int(point_m.group(1)) if point_m else None,
            "point_suffix": (point_m.group(2).lower()
                             if point_m and point_m.group(2) else None),
            "letter": letter_m.group(1) if letter_m else None,
            "raw": raw,
        })

    # EU-richtlijnen apart
    for dm in DIR_RE.finditer(s):
        out.append({
            "article": dm.group(1), "code": "Richtlijn/" + dm.group(2),
            "par": None, "lid": None, "point": None, "point_suffix": None,
            "letter": None, "raw": dm.group(0),
        })

    # VCF (Vlaamse Codex Fiscaliteit): punt-genummerde artikelen, bv. 2.9.1.0.3
    for vm in VCF_RE.finditer(s):
        win = s[max(0, vm.start() - 30): vm.end() + 90]
        if not VCF_CUE.search(win):
            continue
        sub = s[vm.end(): vm.end() + 50]
        par_m = re.search(r"§\s*(\d+)", sub)
        point_m = re.search(
            r"(\d+)\s*°\s*"
            r"(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)?",
            sub, re.IGNORECASE)
        out.append({
            "article": vm.group(0).rstrip("."), "code": "VCF",
            "par": int(par_m.group(1)) if par_m else None,
            "lid": _ordinal_lid(sub),
            "point": int(point_m.group(1)) if point_m else None,
            "point_suffix": (point_m.group(2).lower()
                             if point_m and point_m.group(2) else None),
            "letter": None, "raw": vm.group(0),
        })

    # ontdubbelen op (article, code, par, lid, point, letter)
    seen = set()
    uniq = []
    for r in out:
        key = (r["article"], r["code"], r["par"], r["lid"],
               r["point"], r.get("point_suffix"), r["letter"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def canonical(a: Dict) -> str:
    """Bouw een leesbare, genormaliseerde citatie voor weergave/zoeken."""
    parts = ["art. " + a["article"]]
    if a["par"] is not None:
        parts.append("§" + str(a["par"]))
    if a["lid"] is not None:
        parts.append(str(a["lid"]) + "e lid")
    if a["point"] is not None:
        parts.append(str(a["point"]) + "°" + (a.get("point_suffix") or ""))
    if a["letter"]:
        parts.append(a["letter"] + ")")
    head = ", ".join(parts)
    return head + (" " + a["code"] if a["code"] else "")


def parse_cited_rulings(text: str, self_nr: Optional[str] = None) -> List[str]:
    """Verwijzingen naar andere voorafgaande beslissingen (eigen nummer uitgesloten)."""
    s = re.sub(r"\s+", " ", html.unescape(text))
    found = {m.group(1) for m in RULING_RE.finditer(s)}
    found.discard(self_nr)
    return sorted(found)


def parse_circulaires(text: str) -> List[str]:
    """Verwijzingen naar circulaires (oud Ci.RH.-formaat en nieuw 20XX/C/NN)."""
    s = re.sub(r"\s+", " ", html.unescape(text))
    out = set()
    for m in CIRC_OLD_RE.finditer(s):
        out.add(re.sub(r"\s+", "", m.group(0)))
    for m in CIRC_NEW_RE.finditer(s):
        out.add(re.sub(r"\s+", "", m.group(0)))
    return sorted(out)


def extract_samenvatting(body: str) -> str:
    """Haal de inhoudelijke 'Samenvatting'/'Résumé'-sectie uit de tekst.

    Dit is de uitgebreide samenvatting bovenaan de beslissing (niet de korte
    topic-regel uit de metadata). Eindigt bij de eerste sectiekop 'I. ...'.
    """
    if not body:
        return ""
    s = re.sub(r"\s+", " ", body)
    # Tolerant voor opmaak die het kopwoord opsplitst ("Samenvat ting", "Samen vatting")
    m = re.search(
        r"S\s*a\s*m\s*e\s*n\s*v\s*a\s*t\s*t\s*i\s*n\s*g"
        r"|R\s*[ée]\s*s\s*u\s*m\s*[ée]",
        s, re.IGNORECASE)
    if not m:
        return ""
    rest = s[m.end():]
    b = re.search(r"\bI\.\s+[A-ZÉÈÀ]", rest)        # bv. "I. Voorwerp" / "I. Objet"
    seg = rest[:b.start()] if b else rest[:1200]
    # standaard boilerplate wegknippen
    seg = re.sub(r"De beslissing wordt enkel gepubliceerd[^.]*\.", "", seg,
                 flags=re.IGNORECASE)
    seg = re.sub(r"La décision n['’]est publiée[^.]*\.", "", seg,
                 flags=re.IGNORECASE)
    return seg.strip(" :-–")


# --------------------------------------------------------------------------
# API-client
# --------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def search_page(session: requests.Session, page: int, size: int,
                terms: str = "") -> Dict:
    body = {
        "searchCriteria": {
            "language": "nl",
            "taxonomies": [TAXONOMY_VOORAFGAANDE_BESLISSINGEN],
            "documentTypes": [],
            "keywords": [],
            "orderBy": "RELEVANCE",
            "searchTerms": terms,
        },
        "paginationParameters": {"currentPageNumber": page, "pageSize": size},
    }
    last = None
    for attempt in range(4):
        try:
            r = session.post(BASE + "/search", data=json.dumps(body), timeout=(10, 60))
            r.raise_for_status()
            return r.json()["data"]
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def get_document(session: requests.Session, guid: str):
    """Retourneer (metadata, platte_tekst) voor een beslissing (met herpogingen)."""
    last = None
    for attempt in range(4):
        try:
            r = session.get(BASE + "/document/" + guid, timeout=(10, 60))
            r.raise_for_status()
            data = r.json()["data"]
            raw_b64 = data["content"]["content"]
            raw_html = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", raw_html)
            text = html.unescape(text)  # &#160; e.d. decoderen
            return data["metadata"], text
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def list_rulings(session: requests.Session, max_items: int) -> List[Dict]:
    """Haal de beslissingslijst op (gepagineerd)."""
    page_size = 500
    first = search_page(session, 0, page_size)
    total = first["pageProperties"]["total"]
    target = total if max_items in (0, None) else min(max_items, total)
    print(f"  Totaal in taxonomie: {total} | op te halen: {target}")

    items = list(first["pageContents"])
    page = 1
    while len(items) < target:
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            data = search_page(session, page, page_size)
        except Exception as e:
            print(f"  ! lijst-pagina {page} mislukt na herpogingen ({e}); "
                  f"gestopt met {len(items)} items.", file=sys.stderr)
            break
        chunk = data["pageContents"]
        if not chunk:
            break
        items.extend(chunk)
        page += 1
    return items[:target]


# --------------------------------------------------------------------------
# Hoofdroutine
# --------------------------------------------------------------------------
def process_ruling(session: requests.Session, it: Dict) -> Dict:
    """Haal een beslissing volledig op en bouw het record (herbruikbaar)."""
    guid = it["guid"]
    title = it.get("title", "")
    summary = it.get("summary", "") or ""
    nr_m = re.search(r"nr\.\s*([\d.]+)", title)
    meta = {}
    body = ""
    try:
        meta, body = get_document(session, guid)
    except Exception as e:  # netwerk/parse-fout: sla over, log
        print(f"  ! fout bij {guid}: {e}", file=sys.stderr)
    language = meta.get("language")
    keywords = []
    for k in (meta.get("keywords") or []):
        lab = k.get("label") or {}
        kw = lab.get("nl") or lab.get("fr")
        if kw and kw not in keywords:
            keywords.append(kw)
    full_text = f"{title} || {summary} || {body}"
    articles = parse_articles(full_text)
    for a in articles:
        a["canonical"] = canonical(a)
    self_nr = nr_m.group(1) if nr_m else None
    return {
        "nr": self_nr,
        "date": it.get("documentDate"),
        "guid": guid,
        "url": DOC_URL.format(guid=guid),
        "summary": summary,
        "samenvatting": extract_samenvatting(body),
        "language": language,
        "keywords": keywords,
        "articles": articles,
        "article_count": len(articles),
        "cited_rulings": parse_cited_rulings(full_text, self_nr),
        "circulaires": parse_circulaires(full_text),
    }


def build_dataset(max_items: int) -> List[Dict]:
    session = make_session()
    print("Stap 1/2 - beslissingslijst ophalen ...")
    raw_items = list_rulings(session, max_items)

    print(f"Stap 2/2 - volledige teksten + artikel-extractie ({len(raw_items)}) ...")
    dataset = []
    for i, it in enumerate(raw_items, 1):
        dataset.append(process_ruling(session, it))
        if i % 20 == 0:
            print(f"    {i}/{len(raw_items)} verwerkt")
        time.sleep(REQUEST_DELAY_SECONDS)
    return dataset


def slim_record(r: Dict, maxsum: int = SUMMARY_PREVIEW) -> Dict:
    """Lichte versie van een record voor de website (kleiner = sneller laden).

    Laat weg: url (wordt client-side afgeleid uit guid), raw + canonical per
    artikel (client-side berekend), article_count. Kort de samenvatting in.
    """
    s = r.get("samenvatting") or ""
    if maxsum and len(s) > maxsum:
        s = s[:maxsum].rstrip() + "…"
    return {
        "nr": r.get("nr"), "date": r.get("date"), "guid": r.get("guid"),
        "summary": r.get("summary"), "samenvatting": s,
        "language": r.get("language"), "keywords": r.get("keywords") or [],
        "articles": [{k: a.get(k) for k in
                      ("article", "code", "par", "lid", "point",
                       "point_suffix", "letter")}
                     for a in (r.get("articles") or [])],
        "cited_rulings": r.get("cited_rulings") or [],
        "circulaires": r.get("circulaires") or [],
    }


def write_outputs(dataset: List[Dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "rulings.json")
    csv_path = os.path.join(out_dir, "rulings.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=1)

    # CSV: een rij per (beslissing x artikel) - handig voor filteren/draaitabellen
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nr", "datum", "taal", "url", "topic", "samenvatting",
                    "trefwoorden", "thema",
                    "artikel", "wetcode", "paragraaf", "lid", "punt",
                    "punt_suffix", "letter", "citatie",
                    "verwezen_beslissingen", "circulaires"])
        for d in dataset:
            verw = "|".join(d.get("cited_rulings", []))
            circ = "|".join(d.get("circulaires", []))
            kw = "|".join(d.get("keywords", []))
            lang = d.get("language") or ""
            base = [d["nr"], d["date"], lang, d["url"], d["summary"],
                    d.get("samenvatting", ""), kw, d.get("thema", "")]
            if not d["articles"]:
                w.writerow(base + ["", "", "", "", "", "", "", "", verw, circ])
            for a in d["articles"]:
                w.writerow(base + [
                    a["article"], a["code"], a["par"], a["lid"],
                    a["point"], a.get("point_suffix"), a["letter"],
                    a["canonical"], verw, circ])

    print(f"\nKlaar. Geschreven:\n  {json_path}\n  {csv_path}")


def print_stats(dataset: List[Dict]) -> None:
    n = len(dataset)
    zero = sum(1 for d in dataset if d["article_count"] == 0)
    total_refs = sum(d["article_count"] for d in dataset)
    with_code = sum(1 for d in dataset for a in d["articles"] if a["code"])
    pct_art = 100 * (n - zero) // max(n, 1)
    print("\n--- Kwaliteitsoverzicht ---")
    print(f"  beslissingen           : {n}")
    print(f"  met >=1 artikel        : {n - zero} ({pct_art}%)")
    print(f"  zonder artikel         : {zero}")
    print(f"  totaal verwijzingen    : {total_refs}")
    print(f"  met herkenbare wetcode : {with_code}")


def main():
    ap = argparse.ArgumentParser(description="Fisconet voorafgaande beslissingen - extractie")
    ap.add_argument("--max", type=int, default=150,
                    help="max aantal beslissingen (0 = alles, ~14.400)")
    ap.add_argument("--out", default=".", help="uitvoermap")
    args = ap.parse_args()

    dataset = build_dataset(args.max)
    print_stats(dataset)
    write_outputs(dataset, args.out)


if __name__ == "__main__":
    main()
