import time
import random
import json
import hashlib
import requests
from db.connection import engine
from sqlalchemy import text
from jobspy import scrape_jobs

INSERT_SQL = text("""
    INSERT INTO raw.job_postings (source, source_url, source_job_id, payload, html_hash)
    SELECT CAST(:source AS VARCHAR(50)),
           CAST(:url AS TEXT),
           CAST(:job_id AS VARCHAR(100)),
           CAST(:payload AS JSONB),
           CAST(:hash AS CHAR(64))
    WHERE NOT EXISTS (
        SELECT 1 FROM raw.job_postings
        WHERE source = CAST(:source AS VARCHAR(50))
          AND source_job_id = CAST(:job_id AS VARCHAR(100))
    )
""")

KEYWORDS = [
    "data analyst", "data scientist", "data engineer", "développeur", "software engineer",
    "devops", "chef de projet", "administrateur systèmes", "support informatique",
    "commercial", "technico-commercial", "conseiller clientèle", "téléconseiller",
    "centre d'appels", "chargé de clientèle", "vendeur", "caissier",
    "comptable", "contrôleur de gestion", "auditeur", "gestionnaire de paie",
    "responsable RH", "chargé de recrutement", "juriste", "assistante de direction",
    "ingénieur", "technicien", "technicien de maintenance", "opérateur de production",
    "électromécanicien", "électricien", "mécanicien", "soudeur", "magasinier",
    "qualité", "HSE", "conducteur de travaux", "architecte",
    "logistique", "supply chain", "chauffeur", "livreur", "acheteur",
    "infirmier", "aide-soignant", "pharmacien", "médecin", "enseignant", "formateur",
    "serveur", "cuisinier", "réceptionniste", "marketing", "community manager",
    "agent de sécurité", "stagiaire", "alternance",
]
CITIES = ["Casablanca", "Rabat", "Marrakech", "Tanger", "Fès", "Agadir",
          "Meknès", "Oujda", "Kénitra", "Tétouan", "Salé", "El Jadida"]


def search(keyword, city, max_result=1000):
    jobs = scrape_jobs(
        site_name=["indeed"],
        search_term=keyword,
        location=city,
        country_indeed="Morocco",
        results_wanted=max_result
    )

    if jobs is None or jobs.empty:
        print(f"No results found for {keyword} in {city}")
        return None

    print(f"Scraped {len(jobs)} jobs from Indeed Morocco for {keyword} in {city}")
    return jobs


def store():
    seen_run = set()
    for kw in KEYWORDS:
        for city in CITIES:
            print(f"\n--- Searching: '{kw}' in '{city}' ---")
            try:
                data = search(kw, city)
            except Exception as e:
                print(f"[error] {kw}/{city}: {e}")
                time.sleep(15)
                continue
            if data is not None and not data.empty:
                jobs = data.fillna("").to_dict(orient="records")
                inserted_count = 0

                with engine.begin() as conn:
                    for job in jobs:
                        job_id = str(job.get("id") or job.get("job_url_hyperlink") or job.get("job_url"))

                        if job_id in seen_run:
                            continue

                        seen_run.add(job_id)
                        job_url = str(job.get("job_url", ""))
                        payload_json = json.dumps(job, default=str)
                        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                        params = {
                            "source": "indeed_morocco",
                            "url": job_url,
                            "job_id": job_id,
                            "payload": payload_json,
                            "hash": payload_hash,
                        }
                        result = conn.execute(INSERT_SQL, params)
                        inserted_count += result.rowcount

                print(f"Inserted {inserted_count} new records into the database.")

            delay = random.uniform(5, 15)
            print(f"Sleeping for {delay:.2f} seconds to avoid rate-limits...")
            time.sleep(delay)


if __name__ == "__main__":
    store()