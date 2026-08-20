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

# On surveille uniquement l'année prochaine (2026-2027)
SEARCH_URLS = [
    ("2026-2027", "https://trouverunlogement.lescrous.fr/tools/47/search"),
]

# Mots-clés qui déclenchent un "match Béthune" (insensible à la casse)
KEYWORDS = ["BETHUNE", "BÉTHUNE", "62400"]

STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    # On force une vraie requête fraîche à chaque fois, pas de version en cache
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}

# ---- Scraping ----------------------------------------------------------

def fetch_listings(label, base_url):
    """Récupère et parse TOUS les logements d'une recherche CROUS, en parcourant
    automatiquement toutes les pages de résultats (pas seulement la première)."""
    listings = []
    page = 1
    max_pages = 30  # garde-fou pour ne jamais boucler à l'infini

    while page <= max_pages:
        cache_buster = {"page": page, "_": str(int(time.time() * 1000))}
        resp = requests.get(base_url, headers=HEADERS, params=cache_buster, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_cards = soup.select("li")
        found_on_page = 0

        for card in page_cards:
            link = card.select_one("h3 a[href*='/accommodations/']")
            if not link:
                continue

            name = link.get_text(strip=True)
            href = link.get("href", "")
            m = re.search(r"/accommodations/(\d+)", href)
            if not m:
                continue
            acc_id = m.group(1)

            text_block = card.get_text(" ", strip=True)

            price_match = re.search(r"([\d,\.]+)\s*€", text_block)
            price = price_match.group(0) if price_match else "prix non précisé"

            address_match = re.search(
                r"([\d].{0,60}?\d{5}\s+[A-ZÀ-ÜÇ' \-]+)", text_block
            )
            address = address_match.group(1).strip() if address_match else "adresse non détectée"

            surface_match = re.search(r"(de\s+[\d,\.]+\s+à\s+[\d,\.]+\s*m²|[\d,\.]+\s*m²)", text_block)
            surface = surface_match.group(1) if surface_match else "surface non précisée"

            full_url = href if href.startswith("http") else f"https://trouverunlogement.lescrous.fr{href}"

            listings.append({
                "id": f"{label}:{acc_id}",
                "name": name,
                "text": text_block,
                "price": price,
                "address": address,
                "surface": surface,
                "url": full_url,
                "year": label,
            })
            found_on_page += 1

        if found_on_page == 0:
            # Page vide -> on a dépassé la dernière page, on arrête.
            break

        page += 1

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

def send_summary(all_france_count, bethune_count):
    """Envoie le résumé (total France 2026-2027 + total Béthune) — à chaque check."""
    if not WEBHOOK_URL:
        print(f"⚠️  DISCORD_WEBHOOK_URL non défini — résumé: {all_france_count} France / {bethune_count} Béthune")
        return

    payload = {
        "username": "CROUS Béthune Watcher",
        "embeds": [{
            "title": "📊 Résumé du check (2026-2027)",
            "description": (
                f"**{all_france_count}** logements au total en France\n"
                f"**{bethune_count}** logements à Béthune actuellement"
            ),
            "color": 0x3498DB,
        }],
    }
    r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    if r.status_code >= 300:
        print(f"Erreur envoi Discord (résumé) ({r.status_code}): {r.text}", file=sys.stderr)


def notify_new_bethune(new_items):
    """Envoie une carte détaillée + PING pour chaque nouveau logement à Béthune."""
    if not WEBHOOK_URL:
        print("⚠️  DISCORD_WEBHOOK_URL non défini — affichage console uniquement.")
        for item in new_items:
            print(f"- [{item['year']}] {item['name']} ({item['price']}) -> {item['url']}")
        return

    ping = f"<@{DISCORD_USER_ID}>" if DISCORD_USER_ID else ""

    for item in new_items:
        payload = {
            "username": "CROUS Béthune Watcher",
            "content": f"{ping} 🚨 Nouveau logement dispo à Béthune !" if ping else "🚨 Nouveau logement dispo à Béthune !",
            "embeds": [{
                "title": f"🏠 Nouveau logement à Béthune ({item['year']})",
                "description": item["name"],
                "url": item["url"],
                "color": 0xE74C3C,
                "fields": [
                    {"name": "Adresse", "value": item["address"], "inline": False},
                    {"name": "Prix", "value": item["price"], "inline": True},
                    {"name": "Surface", "value": item["surface"], "inline": True},
                    {"name": "Lien", "value": item["url"], "inline": False},
                ],
            }],
        }
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code >= 300:
            print(f"Erreur envoi Discord ({r.status_code}): {r.text}", file=sys.stderr)


# ---- Main -----------------------------------------------------------------

def print_details(items, title):
    print(f"\n=== {title} ({len(items)}) ===")
    if not items:
        print("  (aucun)")
    for item in items:
        print(f"- {item['name']} [{item['year']}]")
        print(f"    Adresse : {item['address']}")
        print(f"    Prix    : {item['price']}")
        print(f"    Surface : {item['surface']}")
        print(f"    Lien    : {item['url']}")


def run_once():
    seen = load_seen()
    all_france = []

    for label, url in SEARCH_URLS:
        try:
            listings = fetch_listings(label, url)
        except requests.RequestException as e:
            print(f"Erreur en récupérant {url}: {e}", file=sys.stderr)
            continue
        all_france.extend(listings)

    bethune = filter_bethune(all_france)

    current_ids = {item["id"] for item in bethune}
    new_items = [item for item in bethune if item["id"] not in seen]

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          f"Total France (2026-2027): {len(all_france)} | Total Béthune: {len(bethune)} | Nouveaux Béthune: {len(new_items)}")

    print_details(all_france, "TOUS les logements en France (2026-2027)")
    print_details(bethune, "Logements à BÉTHUNE")

    # Résumé envoyé à CHAQUE check
    send_summary(len(all_france), len(bethune))

    # Ping + carte détaillée uniquement pour les NOUVEAUX logements à Béthune
    if new_items:
        print_details(new_items, "Dont NOUVEAUX à Béthune depuis le dernier check")
        notify_new_bethune(new_items)

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
