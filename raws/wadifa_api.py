import json, hashlib, time, requests
from sqlalchemy import text
from db.connection import engine


API_URL="https://www.wadifa-info.com/api/v1/concours"
SOURCE="wadifa_info"

def save_raw(conn,item:dict):
    payload_str = json.dumps(item,sort_keys=True,ensure_ascii=False)
    h = hashlib.sha256(payload_str.encode()).hexdigest()
    query = text("""
        INSERT INTO raw.job_postings (source, source_url, source_job_id, payload, html_hash)
        VALUES (:source, :url, :job_id, CAST(:payload AS JSONB), :hash)
        ON CONFLICT (source, source_url, html_hash) DO NOTHING
    """)
    result = conn.execute(query, {
        "source": SOURCE,
        "url": item.get("url"),
        "job_id": str(item.get("id")),
        "payload": payload_str,
        "hash": h,
    })

    return result.rowcount

def fetch_all():
    r = requests.get(API_URL,timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise ValueError(f"No items found. Keys were: {list(data.keys())}")

    print("Attribution:", json.dumps(data.get("attribution"), indent=2, ensure_ascii=False))

    inserted = 0
    with engine.begin() as conn:
        for item in items:
            inserted += save_raw(conn, item)

    print(f"Fetched {len(items)} items, inserted {inserted} new, "
          f"{len(items) - inserted} duplicates")

if __name__ == "__main__":
    fetch_all()