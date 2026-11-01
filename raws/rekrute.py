"""
rekrute.py — Scraper for www.rekrute.com
Follows the same architecture as wadifa_api.py and optioncarriere_api.py:
  - Uses db.connection.engine
  - Inserts into raw.job_postings
  - Deduplication by (source, source_job_id)
  - SHA-256 hash for payload fingerprint
"""

import os
import re
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import text
from db.connection import engine

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────
SOURCE         = "rekrute"
BASE_URL       = "https://www.rekrute.com"
LIST_URL       = f"{BASE_URL}/offres.html"
MAX_PAGES      = int(os.getenv("REKRUTE_MAX_PAGES", "20"))
REQUEST_DELAY  = float(os.getenv("REKRUTE_REQUEST_DELAY", "2.0"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.rekrute.com/",
}

# SQL identique au pattern de optioncarriere_api.py
INSERT_SQL = text("""
    INSERT INTO raw.job_postings (source, source_url, source_job_id, payload, html_hash)
    SELECT CAST(:source AS VARCHAR(50)),
           CAST(:url    AS TEXT),
           CAST(:job_id AS VARCHAR(100)),
           CAST(:payload AS JSONB),
           CAST(:hash   AS CHAR(64))
    WHERE NOT EXISTS (
        SELECT 1 FROM raw.job_postings
        WHERE source      = CAST(:source AS VARCHAR(50))
          AND source_job_id = CAST(:job_id AS VARCHAR(100))
    )
""")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> requests.Response:
    """Effectue un GET avec les headers définis ; lève une exception si KO."""
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r


def _extract_job_id(url: str) -> str | None:
    """Extrait l'ID numérique de l'URL d'une offre ReKrute.
    Ex: .../offre-emploi-...-187817.html → '187817'
    """
    m = re.search(r"-(\d{4,})\.html", url)
    return m.group(1) if m else None


def _clean(text_node) -> str | None:
    """Retourne le texte nettoyé ou None si absent."""
    if text_node is None:
        return None
    t = text_node.get_text(separator=" ", strip=True)
    return t if t else None


# ── Parsing : page de liste ────────────────────────────────────────────────────

def fetch_offer_urls(page: int) -> list[str]:
    """Retourne la liste des URLs d'offres sur la page <page>."""
    r = _get(LIST_URL, params={"page": page})
    soup = BeautifulSoup(r.text, "html.parser")

    urls = []
    # Les offres sont dans des balises <h2><a href="..."> dans le bloc principal
    # Plusieurs sélecteurs possibles selon la version du site :
    for a in soup.select("h2 a[href*='offre-emploi']"):
        href = a.get("href", "")
        if not href:
            continue
        full_url = href if href.startswith("http") else BASE_URL + href
        # Nettoie les paramètres utm
        full_url = full_url.split("?")[0]
        if full_url not in urls:
            urls.append(full_url)

    # Fallback : cherche tous les liens contenant 'offre-emploi'
    if not urls:
        for a in soup.select("a[href*='offre-emploi']"):
            href = a.get("href", "")
            if not href:
                continue
            full_url = href if href.startswith("http") else BASE_URL + href
            full_url = full_url.split("?")[0]
            if full_url not in urls:
                urls.append(full_url)

    return urls


def has_next_page(page: int, soup: BeautifulSoup) -> bool:
    """Vérifie s'il existe une page suivante via la pagination."""
    # Cherche un lien pagination avec numéro > page courante
    pagination = soup.select("ul.pagination li a, div.pagination a")
    for a in pagination:
        href = a.get("href", "")
        m = re.search(r"page=(\d+)", href)
        if m and int(m.group(1)) > page:
            return True
    # Fallback : cherche le bouton "suivant" ou "»"
    for a in pagination:
        text = a.get_text(strip=True)
        if text in ("»", "Suivant", ">", "Next"):
            return True
    return False


# ── Parsing : page détail ──────────────────────────────────────────────────────

def parse_job_detail(url: str) -> dict:
    """
    Scrape une page détail d'offre ReKrute.
    Retourne un dict avec tous les champs disponibles (None si absent).
    """
    r = _get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    job: dict = {"url": url, "source": SOURCE}

    # ── Titre ──────────────────────────────────────────────────────────────────
    h1 = soup.find("h1")
    job["title"] = _clean(h1)

    # ── Entreprise ─────────────────────────────────────────────────────────────
    # Récupère le nom de l'entreprise depuis le lien/h2/span entreprise
    company_el = (
        soup.select_one("h2.company-name")
        or soup.select_one(".company-name")
        or soup.select_one("h2 a[href*='emploi-et-offres']")
        or soup.select_one("h2 a[href*='rekrute-']")
    )
    job["company"] = _clean(company_el)

    # ── Localisation / Ville ───────────────────────────────────────────────────
    location_el = (
        soup.select_one(".location")
        or soup.select_one("span.city")
        or soup.select_one(".ville")
    )
    job["location"] = _clean(location_el)

    # ── Dates ──────────────────────────────────────────────────────────────────
    # Pattern : "Publication : du JJ/MM/AAAA au JJ/MM/AAAA"
    date_published  = None
    date_expiration = None
    date_text_el = soup.find(string=re.compile(r"Publication\s*:", re.I))
    if date_text_el:
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", date_text_el)
        if len(dates) >= 1:
            date_published = dates[0]
        if len(dates) >= 2:
            date_expiration = dates[1]
    job["date_published"]  = date_published
    job["date_expiration"] = date_expiration

    # ── Critères (sidebar) ─────────────────────────────────────────────────────
    # Rekrute place les critères dans une liste <ul> avec des <li> labellisés
    criteria: dict = {
        "sector":         None,
        "function":       None,
        "experience":     None,
        "study_level":    None,
        "contract_type":  None,
        "remote":         None,
        "positions_count": None,
    }

    # Patterns de labels reconnus
    CRITERIA_MAP = {
        r"secteur":               "sector",
        r"fonction":              "function",
        r"exp[eé]rience":         "experience",
        r"[eé]tude|diplôme":      "study_level",
        r"contrat":               "contract_type",
        r"t[eé]l[eé]travail":     "remote",
        r"poste[s]?\s+propos":    "positions_count",
    }

    # Cherche toutes les <li> ou <div> contenant un label de critère
    for li in soup.select("li, .criteria-item, .detail-offre li"):
        li_text = li.get_text(separator=" ", strip=True)
        for pattern, key in CRITERIA_MAP.items():
            if re.search(pattern, li_text, re.I):
                # Récupère la valeur après le ":" ou dans un sous-élément
                parts = re.split(r":\s*", li_text, maxsplit=1)
                if len(parts) == 2:
                    criteria[key] = parts[1].strip() or None
                break

    job.update(criteria)

    # ── Description du poste ───────────────────────────────────────────────────
    desc_block = (
        soup.select_one(".poste-block")
        or soup.select_one("#poste")
        or soup.select_one("div[id*='poste']")
        or soup.select_one("section.description")
    )
    job["description"] = _clean(desc_block)

    # ── Profil recherché ───────────────────────────────────────────────────────
    profil_block = (
        soup.select_one(".profil-block")
        or soup.select_one("#profil")
        or soup.select_one("div[id*='profil']")
    )
    job["profile"] = _clean(profil_block)

    # ── Compétences ────────────────────────────────────────────────────────────
    skills_block = (
        soup.select_one(".competences-block")
        or soup.select_one("#competences")
        or soup.select_one("div[id*='competence']")
    )
    job["skills"] = _clean(skills_block)

    # ── ID de l'offre ──────────────────────────────────────────────────────────
    job["offer_id"] = _extract_job_id(url)

    return job


# ── Déduplication + sauvegarde ────────────────────────────────────────────────

def save_raw(conn, job: dict) -> int:
    """Insère l'offre dans raw.job_postings ; retourne 1 si nouveau, 0 si doublon."""
    payload_str = json.dumps(job, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(payload_str.encode()).hexdigest()

    result = conn.execute(INSERT_SQL, {
        "source":  SOURCE,
        "url":     job.get("url"),
        "job_id":  job.get("offer_id") or job.get("url"),
        "payload": payload_str,
        "hash":    h,
    })
    return result.rowcount


# ── Point d'entrée principal ──────────────────────────────────────────────────

def collect(max_pages: int = MAX_PAGES) -> None:
    """Scrape toutes les pages d'offres ReKrute et les insère en base."""
    print(f"[INFO] ReKrute scraper started (max_pages={max_pages})")

    total_seen     = 0
    total_inserted = 0
    total_skipped  = 0

    for page in range(1, max_pages + 1):
        print(f"[INFO] Page {page}")

        try:
            offer_urls = fetch_offer_urls(page)
        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch listing page {page}: {e}")
            break

        if not offer_urls:
            print(f"[INFO] No offers found on page {page}. Stopping.")
            break

        print(f"[INFO] Offers found: {len(offer_urls)}")
        total_seen += len(offer_urls)

        for offer_url in offer_urls:
            try:
                print(f"[INFO] Processing offer: {offer_url}")
                job = parse_job_detail(offer_url)
                time.sleep(REQUEST_DELAY)

                with engine.begin() as conn:
                    rows = save_raw(conn, job)

                if rows:
                    total_inserted += 1
                    print(f"[INFO] Saved: {job.get('title')} — {job.get('company')}")
                else:
                    total_skipped += 1
                    print(f"[INFO] Duplicate skipped: {offer_url}")

            except requests.RequestException as e:
                print(f"[ERROR] Failed to fetch offer {offer_url}: {e}")
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f"[ERROR] Unexpected error for {offer_url}: {e}")
                time.sleep(REQUEST_DELAY)

        # Délai entre les pages
        time.sleep(REQUEST_DELAY)

    print(
        f"[INFO] ReKrute scraper finished — "
        f"seen={total_seen}, inserted={total_inserted}, "
        f"duplicates_skipped={total_skipped}"
    )


# ── Exécution directe ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    SLEEP_INTERVAL = int(os.getenv("REKRUTE_SLEEP_INTERVAL", str(86400)))
    while True:
        collect(max_pages=MAX_PAGES)
        print(f"[INFO] Cycle complete. Sleeping for {SLEEP_INTERVAL / 3600:.1f} hours...")
        time.sleep(SLEEP_INTERVAL)
