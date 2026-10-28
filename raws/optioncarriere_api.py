import os, json, time, hashlib, requests
from dotenv import load_dotenv
from sqlalchemy import text
from db.connection import engine

load_dotenv()

API_KEY = os.getenv("CAREERJET_API_KEY")
USER_IP = os.getenv("CAREERJET_USER_IP")
REFERER = os.getenv("CAREERJET_REFERER")
URL = "https://search.api.careerjet.net/v4/query"
SOURCE = "careerjet"

KEYWORDS = ["data", "développeur", "ingénieur", "comptable", "commercial",
            "technicien", "assistant", "responsable", "manager", "logistique",
            "marketing", "infirmier", "enseignant", "call center", "chauffeur"]
CITIES = ["Casablanca", "Rabat", "Marrakech", "Tanger", "Fès", "Agadir",
          "Meknès", "Oujda", "Kénitra", "Tétouan", "Salé", "El Jadida"]

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


def fingerprint(job: dict) -> str:
    key = "|".join(str(job.get(k, "")).lower().strip()
                   for k in ("title", "company", "locations", "date"))
    return hashlib.sha256(key.encode()).hexdigest()


def search(keyword, city, page):
    params = {
        "locale_code": "fr_MA",
        "keywords": keyword,
        "location": city,
        "page": page,
        "page_size": 100,
        "sort": "date",
        "fragment_size": 500,
        "user_ip": USER_IP,
        "user_agent": "MarocJobScope research project",
    }
    params = {k: v for k, v in params.items() if v is not None}
    r = requests.get(URL, params=params, auth=(API_KEY, ""),
                     headers={"Referer": REFERER}, timeout=30)
    r.raise_for_status()
    return r.json()


def collect(keywords=KEYWORDS, cities=CITIES, max_pages=10):
    seen_run = set()
    total_new = 0

    for kw in keywords:
        for city in cities:
            page = 1
            while page <= max_pages:
                try:
                    data = search(kw, city, page)
                except requests.RequestException as e:
                    print(f"[error] {kw}/{city}/p{page}: {e}")
                    break

                if data.get("type") == "LOCATIONS":
                    print(f"[skip] location '{city}': {data.get('message')} {data.get('locations')}")
                    break

                jobs = data.get("jobs", [])
                new_here = 0
                with engine.begin() as conn:
                    for job in jobs:
                        fp = fingerprint(job)
                        if fp in seen_run:
                            continue
                        seen_run.add(fp)
                        payload = json.dumps(job, sort_keys=True, ensure_ascii=False)
                        res = conn.execute(INSERT_SQL, {
                            "source": SOURCE,
                            "url": job.get("url"),
                            "job_id": fp,
                            "payload": payload,
                            "hash": hashlib.sha256(payload.encode()).hexdigest(),
                        })
                        new_here += res.rowcount

                total_new += new_here
                print(f"{kw} | {city} | page {page}/{data.get('pages')} "
                      f"| {len(jobs)} jobs, {new_here} new")

                if page >= (data.get("pages") or 1):
                    break
                page += 1

    print(f"Done. {total_new} new jobs saved.")


if __name__ == "__main__":
    if __name__ == "__main__":
        SLEEP_INTERVAL = 86400

        while True:
            collect(max_pages=20)
            print(f"Cycle complete. Sleeping for {SLEEP_INTERVAL / 3600} hours...")
            time.sleep(SLEEP_INTERVAL)