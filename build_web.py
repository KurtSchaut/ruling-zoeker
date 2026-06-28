#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_web.py  —  maak de lichte webversie van de data
-----------------------------------------------------
Leest het volledige rulings.json en schrijft een kleiner site_data.json dat de
website (index.html) laadt. Kleiner = sneller laden en uploadbaar via GitHub
(max 25 MB per bestand bij web-upload).

Wat eruit gaat: url (client-side afgeleid), raw + canonical per artikel
(client-side berekend), article_count. De samenvatting wordt ingekort
(volledige tekst blijft op Fisconetplus via de link).

Gebruik:
    python build_web.py                 # standaard: samenvatting tot 500 tekens
    python build_web.py --maxsum 350    # korter -> kleiner bestand
    python build_web.py --in rulings.json --out site_data.json
"""

import argparse
import json
import os

import fisconet_rulings as fr


def main():
    ap = argparse.ArgumentParser(description="Bouw lichte webdata (site_data.json)")
    ap.add_argument("--in", dest="inp", default="rulings.json")
    ap.add_argument("--out", dest="out", default="site_data.json")
    ap.add_argument("--maxsum", type=int, default=fr.SUMMARY_PREVIEW,
                    help="max tekens samenvatting (lager = kleiner bestand)")
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)

    slim = [fr.slim_record(r, args.maxsum) for r in data]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))

    mb = os.path.getsize(args.out) / 1024 / 1024
    print(f"{len(slim)} beslissingen -> {args.out}  ({mb:.1f} MB)")
    if mb >= 25:
        print("  ! Nog >=25 MB. Verlaag --maxsum (bv. 300) en draai opnieuw.")
    else:
        print("  OK: klein genoeg voor web-upload (<25 MB).")


if __name__ == "__main__":
    main()
