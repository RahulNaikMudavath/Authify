import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "bikeshare")

TOTAL_STATIONS = 250
TOTAL_TRIPS = 550_000
WEEKS = 6

STATION_PREFIXES = [
    "Market St", "Mission St", "Powell St", "California St", "Montgomery St",
    "Folsom St", "Howard St", "Valencia St", "Castro St", "Columbus Ave",
    "Broadway", "Pine St", "Bush St", "Geary Blvd", "Post St",
    "Sutter St", "Van Ness Ave", "Polk St", "Hyde St", "Larkin St",
    "King St", "Berry St", "4th St", "2nd St", "Beale St", "Fremont St"
]

CROSS_STREETS = [
    "1st St", "2nd St", "3rd St", "4th St", "5th St", "6th St", "7th St",
    "8th St", "9th St", "10th St", "11th St", "12th St", "The Embarcadero",
    "Kearny St", "Grant Ave", "Stockton St", "Civic Center", "Pier 39",
    "Ferry Building", "Caltrain Depot", "Transbay Terminal", "Ballpark"
]


def generate_stations(count=TOTAL_STATIONS):
    stations = []
    used_names = set()
    
    for i in range(1, count + 1):
        prefix = STATION_PREFIXES[(i - 1) % len(STATION_PREFIXES)]
        cross = CROSS_STREETS[((i - 1) // len(STATION_PREFIXES)) % len(CROSS_STREETS)]
        name = f"{prefix} & {cross}"
        if name in used_names:
            name = f"{name} #{i}"
        used_names.add(name)
        
        total_docks = random.choice([16, 20, 24, 30, 36, 40, 48])
        occupied_docks = random.randint(0, total_docks)
        
        station = {
            "station_id": i,
            "name": name,
            "total_docks": total_docks,
            "occupied_docks": occupied_docks,
            "installed_at": datetime.now(timezone.utc) - timedelta(days=random.randint(180, 720)),
            "location": {
                "lat": round(37.76 + random.random() * 0.05, 5),
                "lng": round(-122.44 + random.random() * 0.06, 5)
            }
        }
        stations.append(station)
    return stations


def generate_trips(stations, count=TOTAL_TRIPS, weeks=WEEKS):
    station_ids = [s["station_id"] for s in stations]
    now = datetime.now(timezone.utc)
    start_window = now - timedelta(weeks=weeks)
    total_seconds_window = int((now - start_window).total_seconds())
    
    trips = []
    stats = {
        "clean": 0,
        "missing_end_station": 0,
        "missing_end_time": 0,
        "end_before_start": 0,
        "invalid_station_id": 0,
        "corrupt_duration": 0
    }
    
    invalid_ids = [99991, 99992, 99999, -1, 88888]
    user_types = ["Subscriber", "Customer"]
    
    for trip_id in range(1, count + 1):
        trip_start = start_window + timedelta(seconds=random.randint(0, total_seconds_window - 7200))
        duration = random.randint(180, 5400)
        trip_end = trip_start + timedelta(seconds=duration)
        
        start_id = random.choice(station_ids)
        end_id = random.choice(station_ids)
        user_type = random.choice(user_types)
        
        rand_val = random.random()
        
        if rand_val < 0.030:
            end_id = None
            trip_end = None
            duration = None
            stats["missing_end_station"] += 1
            
        elif rand_val < 0.050:
            trip_end = None
            duration = None
            stats["missing_end_time"] += 1
            
        elif rand_val < 0.080:
            skew_seconds = random.randint(300, 3600)
            trip_end = trip_start - timedelta(seconds=skew_seconds)
            duration = -skew_seconds
            stats["end_before_start"] += 1
            
        elif rand_val < 0.110:
            if random.random() < 0.5:
                start_id = random.choice(invalid_ids)
            else:
                end_id = random.choice(invalid_ids)
            stats["invalid_station_id"] += 1
            
        elif rand_val < 0.125:
            duration = -random.randint(100, 900)
            stats["corrupt_duration"] += 1
            
        else:
            stats["clean"] += 1
            
        trip = {
            "trip_id": trip_id,
            "start_station_id": start_id,
            "end_station_id": end_id,
            "start_time": trip_start,
            "end_time": trip_end,
            "duration_seconds": duration,
            "user_type": user_type
        }
        trips.append(trip)
        
    return trips, stats


def seed_database(mongo_uri=MONGO_URI, db_name=DB_NAME, batch_size=25_000):
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    
    stations = generate_stations(TOTAL_STATIONS)
    db.stations.drop()
    db.stations.insert_many(stations)
    db.stations.create_index("station_id", unique=True)
    
    trips, stats = generate_trips(stations, TOTAL_TRIPS)
    
    db.trips.drop()
    for i in range(0, len(trips), batch_size):
        batch = trips[i:i + batch_size]
        db.trips.insert_many(batch)
        
    return stations, stats


def eyeball_samples(mongo_uri=MONGO_URI, db_name=DB_NAME, sample_count=100):
    client = MongoClient(mongo_uri)
    db = client[db_name]
    
    samples = list(db.trips.aggregate([{"$sample": {"size": sample_count}}]))
    messy_count = 0
    clean_count = 0
    
    for doc in samples:
        notes = []
        if doc.get("end_station_id") is None:
            notes.append("MISSING_END_STATION")
        if doc.get("end_time") is None:
            notes.append("MISSING_END_TIME")
        if doc.get("start_time") and doc.get("end_time") and doc.get("end_time") < doc.get("start_time"):
            notes.append("END_BEFORE_START")
        if doc.get("start_station_id") in [99991, 99992, 99999, -1, 88888] or doc.get("end_station_id") in [99991, 99992, 99999, -1, 88888]:
            notes.append("INVALID_STATION_ID")
        if doc.get("duration_seconds") is not None and doc.get("duration_seconds") < 0:
            notes.append("NEGATIVE_DURATION")
            
        if notes:
            messy_count += 1
        else:
            clean_count += 1
            
    return clean_count, messy_count


if __name__ == "__main__":
    seed_database()
    clean_cnt, messy_cnt = eyeball_samples(sample_count=100)
    print(f"Dataset generated. Sample: {clean_cnt} clean, {messy_cnt} messy.")
