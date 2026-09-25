import logging
import os
import threading
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from pymongo import MongoClient

from report import generate_station_availability_report
from cache_manager import CacheManager
from data_generator import seed_database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, expose_headers=["X-Cache", "X-Data-Age", "X-Computation-Time"])

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "bikeshare")
REDIS_URL = os.getenv("REDIS_URL", None)

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo_client[DB_NAME]
cache = CacheManager(redis_url=REDIS_URL)


def compute_report_callback():
    return generate_station_availability_report(db)


@app.route("/api/status", methods=["GET"])
def get_status():
    mongo_ok = False
    station_count = 0
    trip_count = 0
    try:
        mongo_client.admin.command("ping")
        station_count = db.stations.count_documents({})
        trip_count = db.trips.count_documents({})
        mongo_ok = True
    except Exception as e:
        logger.error(f"Mongo status check failed: {e}")

    redis_ok = cache.ping()
    
    meta = cache.client.hgetall("report:meta")
    last_reb = cache.client.get("report:last_rebalance_at")
    reb_count = cache.client.get("report:rebalance_count")
    has_cache = bool(cache.client.exists("report:data"))

    return jsonify({
        "status": "healthy" if (mongo_ok and redis_ok) else "degraded",
        "mongodb": {
            "connected": mongo_ok,
            "station_count": station_count,
            "trip_count": trip_count
        },
        "redis": {
            "connected": redis_ok,
            "has_cached_report": has_cache,
            "cache_status": meta.get("status", "empty"),
            "cached_at": meta.get("cached_at"),
            "rebalance_count": int(reb_count or 0),
            "last_rebalance_at": last_reb
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.route("/api/stations", methods=["GET"])
def get_stations():
    try:
        stations = list(db.stations.find({}, {
            "_id": 0,
            "station_id": 1,
            "name": 1,
            "total_docks": 1,
            "occupied_docks": 1
        }).sort("station_id", 1))
        
        for s in stations:
            s["available_docks"] = s["total_docks"] - s["occupied_docks"]
            
        return jsonify({
            "count": len(stations),
            "stations": stations
        })
    except Exception as e:
        logger.error(f"Failed to fetch stations: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/report", methods=["GET"])
def get_report():
    t_req = time.time()
    force = request.args.get("force", "false").lower() == "true"
    
    meta = cache.client.hgetall("report:meta")
    current_status = meta.get("status")
    
    non_blocking = request.args.get("non_blocking", "false").lower() == "true"
    if non_blocking and current_status == "recomputing":
        return jsonify({
            "source": "recomputing",
            "cache_hit": False,
            "status": "recomputing",
            "message": "Report computation in progress in single-flight worker.",
            "data": None
        }), 202

    try:
        result = cache.get_report(compute_report_callback, force_refresh=force)
        duration_ms = round((time.time() - t_req) * 1000, 2)
        result["request_latency_ms"] = duration_ms
        
        response = make_response(jsonify(result))
        response.headers["X-Cache"] = "HIT" if result.get("cache_hit") else "MISS"
        response.headers["X-Data-Age"] = str(result.get("data_age_seconds", 0))
        response.headers["X-Computation-Time"] = str(result.get("computation_time_seconds", 0))
        return response
    except Exception as e:
        logger.error(f"Error serving report: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/report/benchmark", methods=["GET"])
def benchmark_report():
    try:
        t0 = time.time()
        report = compute_report_callback()
        elapsed = round(time.time() - t0, 2)
        return jsonify({
            "source": "raw_mongodb_uncached",
            "computation_time_seconds": elapsed,
            "data": report
        })
    except Exception as e:
        logger.error(f"Benchmark failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance", methods=["POST"])
def trigger_rebalance():
    payload = request.get_json() or {}
    station_id = payload.get("station_id")
    occupied_docks = payload.get("occupied_docks")
    reason = payload.get("reason", "Dispatcher van rebalance")
    
    if station_id is None or occupied_docks is None:
        return jsonify({"error": "station_id and occupied_docks are required"}), 400
        
    try:
        station_id = int(station_id)
        occupied_docks = int(occupied_docks)
    except ValueError:
        return jsonify({"error": "station_id and occupied_docks must be integers"}), 400
        
    station = db.stations.find_one({"station_id": station_id})
    if not station:
        return jsonify({"error": f"Station with id {station_id} not found"}), 404
        
    total_docks = station["total_docks"]
    if occupied_docks < 0 or occupied_docks > total_docks:
        return jsonify({
            "error": f"occupied_docks must be between 0 and total docks ({total_docks})"
        }), 400
        
    prev_occupied = station["occupied_docks"]
    
    db.stations.update_one(
        {"station_id": station_id},
        {"$set": {"occupied_docks": occupied_docks}}
    )
    
    rebalance_event = {
        "station_id": station_id,
        "station_name": station["name"],
        "previous_occupied": prev_occupied,
        "new_occupied": occupied_docks,
        "total_docks": total_docks,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc)
    }
    db.rebalance_log.insert_one(rebalance_event)
    
    reb_meta = cache.record_rebalance(station_id, occupied_docks)
    
    def async_prewarm():
        try:
            cache.get_report(compute_report_callback, force_refresh=True)
        except Exception as e:
            logger.error(f"Error during async prewarm: {e}")
            
    threading.Thread(target=async_prewarm, daemon=True).start()
    
    return jsonify({
        "success": True,
        "message": f"Successfully rebalanced Station #{station_id} ('{station['name']}'). Occupied docks changed from {prev_occupied} to {occupied_docks}.",
        "station_id": station_id,
        "station_name": station["name"],
        "previous_occupied": prev_occupied,
        "new_occupied": occupied_docks,
        "total_docks": total_docks,
        "last_rebalance_at": reb_meta["last_rebalance_at"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


@app.route("/api/seed", methods=["POST"])
def seed_data_endpoint():
    try:
        stations, stats = seed_database()
        cache.clear_all()
        return jsonify({
            "success": True,
            "message": "Database successfully reseeded.",
            "stations_count": len(stations),
            "stats": stats
        })
    except Exception as e:
        logger.error(f"Seed failed: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
