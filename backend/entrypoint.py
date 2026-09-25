"""
Backend Entrypoint Script
Ensures MongoDB and Redis are ready, automatically seeds dataset on first boot if empty,
and starts the web server.
"""

import os
import sys
import time
from pymongo import MongoClient
import redis

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "bikeshare")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PORT = int(os.getenv("PORT", 5000))

def wait_for_services():
    print(f"Waiting for MongoDB ({MONGO_URI})...")
    for _ in range(30):
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")
            print("-> MongoDB is ready.")
            break
        except Exception:
            time.sleep(1)
    else:
        print("Error: Timed out waiting for MongoDB.")
        sys.exit(1)

    print(f"Waiting for Redis ({REDIS_URL})...")
    for _ in range(30):
        try:
            r = redis.from_url(REDIS_URL, socket_timeout=2)
            r.ping()
            print("-> Redis is ready.")
            break
        except Exception:
            time.sleep(1)
    else:
        print("Error: Timed out waiting for Redis.")
        sys.exit(1)

def ensure_dataset():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    stn_count = db.stations.count_documents({})
    trip_count = db.trips.count_documents({})
    
    if stn_count < 200 or trip_count < 500_000:
        print(f"\nDataset empty or incomplete (found {stn_count} stations, {trip_count} trips).")
        print("Automatically seeding dataset (250 stations, 550,000 trips)...")
        from data_generator import seed_database, eyeball_samples
        seed_database(mongo_uri=MONGO_URI, db_name=DB_NAME)
        eyeball_samples(mongo_uri=MONGO_URI, db_name=DB_NAME, sample_count=100)
    else:
        print(f"-> Verified existing dataset: {stn_count} stations, {trip_count:,} trips.")

def start_server():
    print(f"\nStarting Flask service on port {PORT}...")
    import subprocess
    # Run with gunicorn in production/container, fallback to python app.py
    try:
        import gunicorn
        cmd = [
            "gunicorn",
            "--bind", f"0.0.0.0:{PORT}",
            "--workers", "4",
            "--threads", "2",
            "--timeout", "60",
            "app:app"
        ]
        subprocess.run(cmd)
    except Exception as e:
        print(f"Falling back to app.py ({e})")
        from app import app
        app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    wait_for_services()
    ensure_dataset()
    start_server()
