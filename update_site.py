#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_site.py  —  incrementele update van de WEBdata (site_data.json)
----------------------------------------------------------------------
Werkt rechtstreeks op site_data.json (de lichte versie die de website laadt en
die in de GitHub-repo staat). Voegt enkel nieuwe beslissingen toe en bewaart ze
meteen in lichte vorm. Dit is wat de dagelijkse GitHub Action draait.

Gebruik:
    python update_site.py

(Voor de volledige lokale dataset met CSV: gebruik fisconet_rulings.py /
update_rulings.py; voor het eerste site_data.json: build_web.py.)
"""

import json
import os
import time

import fisconet_rulings as fr

FILE = "site_data.json"


def main():
    if os.path.exists(FILE):
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []
        print("Geen site_data.json gevonden - maak het eerst met build_web.py.")
    known = {r.get("guid") for r in data}
    print(f"Bestaand: {len(data)} beslissingen.")

    session = fr.make_session()
    print("Volledige lijst ophalen (enkel metadata) ...")
    listing = fr.list_rulings(session, 0)
    new = [it for it in listing if it.get("guid") not in known]
    print(f"Nieuw te verwerken: {len(new)} beslissingen.")
    if not new:
        print("Up-to-date - niets toe te voegen.")
        return

    for i, it in enumerate(new, 1):
        data.append(fr.slim_record(fr.process_ruling(session, it)))
        if i % 10 == 0:
            print(f"  {i}/{len(new)} nieuwe verwerkt")
        time.sleep(fr.REQUEST_DELAY_SECONDS)

    data.sort(key=lambda r: (r.get("date") or ""), reverse=True)
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Klaar. Totaal nu: {len(data)} beslissingen in {FILE}.")


if __name__ == "__main__":
    main()
