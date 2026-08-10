#!/usr/bin/env python3
"""
Bot de veille logements CROUS - Béthune (62400)
-------------------------------------------------
Scrape trouverunlogement.lescrous.fr (année en cours + année prochaine),
filtre les logements dont l'adresse contient "BETHUNE" ou "62400",
et envoie une notification Discord (via webhook) uniquement pour les
NOUVEAUX logements (pas déjà vus lors du dernier passage).

Usage:
    python check_crous.py

Variables d'environnement requises:
    DISCORD_WEBHOOK_URL   -> URL du webhook Discord (voir README.md)

Fichier d'état:
    seen.json  -> liste des IDs de logements déjà notifiés (créé automatiquement)
"""

import os
import json
import re
import sys
import time
import argparse
import requests
from bs4 import BeautifulSoup

# ---- Config ----------------------------------------------------------

# 47 = année prochaine (2026-2027), 42 = année en cours (2025-2026)
# On surveille les deux.
SEARCH_URLS = [
    ("2025-2026", "https://trouverunlogement.lescrous.fr/tools/42/search"),
    ("2026-2027", "https://trouverunlogement.lescrous.fr/tools/47/search"),
]

# Mots-clés qui déclenchent un "match Béthune" (insensible à la casse)
KEYWORDS = ["BETHUNE", "BÉTHUNE", "62400"]

STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    # On force une vraie requête fraîche à chaque fois, pas de version en cache
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}

# ---- Scraping ----------------------------------------------------------

def fetch_listings(label, url):
    """Récupère et parse tous les logements d'une page de recherche CROUS.
    Ajoute un paramètre aléatoire pour éviter tout cache intermédiaire (CDN, proxy)."""
    cache_buster = {"_": str(int(time.time() * 1000))}
    resp = requests.get(url, headers=HEADERS, params=cache_buster, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = []
    # Chaque logement est un <li> contenant un <h3><a href=".../accommodations/ID">
    for card in soup.select("li"):
        link = card.select_one("h3 a[href*='/accommodations/']")
        if not link:
            continue

        name = link.get_text(strip=True)
        href = link.get("href", "")
        m = re.search(r"/accommodations/(\d+)", href)
        if not m:
            continue
        acc_id = m.group(1)

        # Adresse: généralement le texte juste après le titre, dans le même bloc
        text_block = card.get_text(" ", strip=True)

        # Prix (ex: "237 €")
        price_match = re.search(r"([\d,\.]+)\s*€", text_block)
        price = price_match.group(0) if price_match else "prix non précisé"

        full_url = href if href.startswith("http") else f"https://trouverunlogement.lescrous.fr{href}"

        listings.append({
            "id": f"{label}:{acc_id}",
            "name": name,
            "text": text_block,
            "price": price,
            "url": full_url,
            "year": label,
        })

    return listings


def filter_bethune(listings):
    matches = []
    for item in listings:
        haystack = item["text"].upper()
        if any(kw in haystack for kw in KEYWORDS):
            matches.append(item)
    return matches


# ---- État (déjà vu) ------------------------------------------------------

def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


# ---- Discord --------------------------------------------------------------

def notify_discord(new_items):
    if not WEBHOOK_URL:
        print("⚠️  DISCORD_WEBHOOK_URL non défini — affichage console uniquement.")
        for item in new_items:
            print(f"- [{item['year']}] {item['name']} ({item['price']}) -> {item['url']}")
        return

    for item in new_items:
        payload = {
            "username": "CROUS Béthune Watcher",
            "embeds": [{
                "title": f"🏠 Nouveau logement disponible ({item['year']})",
                "description": item["name"],
                "url": item["url"],
                "color": 0xE74C3C,
                "fields": [
                    {"name": "Prix", "value": item["price"], "inline": True},
                    {"name": "Lien", "value": item["url"], "inline": False},
                ],
            }],
        }
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code >= 300:
            print(f"Erreur envoi Discord ({r.status_code}): {r.text}", file=sys.stderr)


# ---- Main -----------------------------------------------------------------

def run_once():
    seen = load_seen()
    all_bethune = []

    for label, url in SEARCH_URLS:
        try:
            listings = fetch_listings(label, url)
        except requests.RequestException as e:
            print(f"Erreur en récupérant {url}: {e}", file=sys.stderr)
            continue
        all_bethune.extend(filter_bethune(listings))

    current_ids = {item["id"] for item in all_bethune}
    new_items = [item for item in all_bethune if item["id"] not in seen]

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          f"Logements Béthune trouvés: {len(all_bethune)} | Nouveaux: {len(new_items)}")

    if new_items:
        notify_discord(new_items)

    # On met à jour l'état avec tout ce qui est actuellement affiché,
    # pour ne plus re-notifier ces logements la prochaine fois.
    save_seen(current_ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--loop", type=int, default=0,
        help="Si fourni, tourne en continu et refait un check toutes les N secondes "
             "(ex: --loop 120 pour toutes les 2 minutes). Sans cet argument, "
             "le script s'exécute une seule fois et s'arrête (mode 'cron externe')."
    )
    args = parser.parse_args()

    if args.loop and args.loop > 0:
        print(f"Mode boucle continue activé — check toutes les {args.loop} secondes.")
        while True:
            try:
                run_once()
            except Exception as e:
                # On ne veut jamais que la boucle s'arrête à cause d'une erreur ponctuelle
                print(f"Erreur inattendue: {e}", file=sys.stderr)
            time.sleep(args.loop)
    else:
        run_once()


if __name__ == "__main__":
    main()
