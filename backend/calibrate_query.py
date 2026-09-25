import time
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
db = client["bikeshare"]

def compute_station_availability_report():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    recent_window_start = now - timedelta(days=7)
    
    # 1. Historical traffic, rush hours, weekend patterns, and top corridors (scans 550,000 trips)
    historical_pipeline = [
        {
            "$match": {
                "$or": [
                    { "end_time": None },
                    { "$expr": { "$gte": ["$end_time", "$start_time"] } }
                ]
            }
        },
        {
            "$project": {
                "start_station_id": 1,
                "end_station_id": 1,
                "duration_seconds": 1,
                "start_time": 1,
                "end_time": 1,
                "hour": { "$hour": "$start_time" },
                "is_weekend": { "$in": [{ "$dayOfWeek": "$start_time" }, [1, 7]] }
            }
        },
        {
            "$facet": {
                "outbound": [
                    { "$match": { "start_station_id": { "$gte": 1, "$lte": 250 } } },
                    {
                        "$group": {
                            "_id": "$start_station_id",
                            "total_departures": { "$sum": 1 },
                            "morning_rush_departures": {
                                "$sum": {
                                    "$cond": [
                                        { "$and": [{ "$gte": ["$hour", 7] }, { "$lte": ["$hour", 10] }] },
                                        1, 0
                                    ]
                                }
                            },
                            "evening_rush_departures": {
                                "$sum": {
                                    "$cond": [
                                        { "$and": [{ "$gte": ["$hour", 16] }, { "$lte": ["$hour", 19] }] },
                                        1, 0
                                    ]
                                }
                            },
                            "weekend_departures": {
                                "$sum": { "$cond": ["$is_weekend", 1, 0] }
                            }
                        }
                    }
                ],
                "inbound": [
                    {
                        "$match": {
                            "end_station_id": { "$gte": 1, "$lte": 250 },
                            "end_time": { "$ne": None }
                        }
                    },
                    {
                        "$group": {
                            "_id": "$end_station_id",
                            "total_arrivals": { "$sum": 1 },
                            "morning_rush_arrivals": {
                                "$sum": {
                                    "$cond": [
                                        { "$and": [{ "$gte": ["$hour", 7] }, { "$lte": ["$hour", 10] }] },
                                        1, 0
                                    ]
                                }
                            },
                            "evening_rush_arrivals": {
                                "$sum": {
                                    "$cond": [
                                        { "$and": [{ "$gte": ["$hour", 16] }, { "$lte": ["$hour", 19] }] },
                                        1, 0
                                    ]
                                }
                            },
                            "avg_duration": {
                                "$avg": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                { "$ne": ["$duration_seconds", None] },
                                                { "$gt": ["$duration_seconds", 0] }
                                            ]
                                        },
                                        "$duration_seconds",
                                        None
                                    ]
                                }
                            }
                        }
                    }
                ],
                "corridors": [
                    {
                        "$match": {
                            "start_station_id": { "$gte": 1, "$lte": 250 },
                            "end_station_id": { "$gte": 1, "$lte": 250 }
                        }
                    },
                    {
                        "$group": {
                            "_id": { "start": "$start_station_id", "end": "$end_station_id" },
                            "trips": { "$sum": 1 }
                        }
                    },
                    { "$sort": { "trips": -1 } },
                    { "$limit": 10 }
                ]
            }
        }
    ]
    
    # 2. Recent 7-Day Velocity Pipeline
    recent_pipeline = [
        {
            "$match": {
                "start_time": { "$gte": recent_window_start },
                "$or": [
                    { "end_time": None },
                    { "$expr": { "$gte": ["$end_time", "$start_time"] } }
                ]
            }
        },
        {
            "$facet": {
                "recent_outbound": [
                    { "$match": { "start_station_id": { "$gte": 1, "$lte": 250 } } },
                    { "$group": { "_id": "$start_station_id", "recent_departures": { "$sum": 1 } } }
                ],
                "recent_inbound": [
                    {
                        "$match": {
                            "end_station_id": { "$gte": 1, "$lte": 250 },
                            "end_time": { "$ne": None }
                        }
                    },
                    { "$group": { "_id": "$end_station_id", "recent_arrivals": { "$sum": 1 } } }
                ]
            }
        }
    ]
    
    # 3. User Segmentation Matrix
    matrix_pipeline = [
        {
            "$match": {
                "start_station_id": { "$gte": 1, "$lte": 250 },
                "end_station_id": { "$gte": 1, "$lte": 250 },
                "$or": [
                    { "end_time": None },
                    { "$expr": { "$gte": ["$end_time", "$start_time"] } }
                ]
            }
        },
        {
            "$group": {
                "_id": {
                    "stn": "$start_station_id",
                    "user_type": "$user_type"
                },
                "total": { "$sum": 1 },
                "avg_sec": { "$avg": "$duration_seconds" }
            }
        }
    ]

    # 4. Hourly Demand Curve across 24 hours per station (heavy join & grouping)
    hourly_curve_pipeline = [
        {
            "$match": {
                "start_station_id": { "$gte": 1, "$lte": 250 },
                "$or": [
                    { "end_time": None },
                    { "$expr": { "$gte": ["$end_time", "$start_time"] } }
                ]
            }
        },
        {
            "$group": {
                "_id": {
                    "stn": "$start_station_id",
                    "hour": { "$hour": "$start_time" }
                },
                "hourly_count": { "$sum": 1 }
            }
        }
    ]
    
    print("[1/4] Running historical traffic analytics pipeline...")
    hist_res = list(db.trips.aggregate(historical_pipeline, allowDiskUse=True))[0]
    
    print("[2/4] Running 7-day velocity pipeline...")
    rec_res = list(db.trips.aggregate(recent_pipeline, allowDiskUse=True))[0]
    
    print("[3/4] Running user segmentation matrix...")
    matrix_res = list(db.trips.aggregate(matrix_pipeline, allowDiskUse=True))
    
    print("[4/4] Running 24h hourly demand curve...")
    hourly_res = list(db.trips.aggregate(hourly_curve_pipeline, allowDiskUse=True))
    
    # Fetch all stations with current dock state
    stations = list(db.stations.find({}, {"_id": 0}).sort("station_id", 1))
    
    # Fast hash maps
    out_map = {item["_id"]: item for item in hist_res.get("outbound", [])}
    in_map = {item["_id"]: item for item in hist_res.get("inbound", [])}
    rec_out = {item["_id"]: item.get("recent_departures", 0) for item in rec_res.get("recent_outbound", [])}
    rec_in = {item["_id"]: item.get("recent_arrivals", 0) for item in rec_res.get("recent_inbound", [])}
    
    # Hourly peak calculation
    stn_peak_hours = {}
    for h in hourly_res:
        sid = h["_id"]["stn"]
        hr = h["_id"]["hour"]
        cnt = h["hourly_count"]
        if sid not in stn_peak_hours or cnt > stn_peak_hours[sid]["count"]:
            stn_peak_hours[sid] = { "hour": hr, "count": cnt }
            
    # Combine dock state with trip traffic
    station_reports = []
    for s in stations:
        sid = s["station_id"]
        out_info = out_map.get(sid, {})
        in_info = in_map.get(sid, {})
        
        total_dep = out_info.get("total_departures", 0)
        total_arr = in_info.get("total_arrivals", 0)
        recent_dep = rec_out.get(sid, 0)
        recent_arr = rec_in.get(sid, 0)
        
        net_flow_overall = total_arr - total_dep
        net_flow_recent = recent_arr - recent_dep
        
        occupied = s["occupied_docks"]
        total = s["total_docks"]
        available = total - occupied
        occ_rate = round((occupied / total) * 100, 1) if total > 0 else 0
        
        if available <= 2:
            status = "CRITICAL_FULL"
        elif occupied <= 2:
            status = "CRITICAL_EMPTY"
        elif occ_rate >= 80:
            status = "HIGH_OCCUPANCY"
        elif occ_rate <= 20:
            status = "LOW_OCCUPANCY"
        else:
            status = "BALANCED"
            
        peak_info = stn_peak_hours.get(sid, { "hour": 8, "count": 0 })
        
        station_reports.append({
            "station_id": sid,
            "name": s["name"],
            "total_docks": total,
            "occupied_docks": occupied,
            "available_docks": available,
            "occupancy_rate_pct": occ_rate,
            "status": status,
            "total_departures": total_dep,
            "total_arrivals": total_arr,
            "net_flow_overall": net_flow_overall,
            "recent_7d_departures": recent_dep,
            "recent_7d_arrivals": recent_arr,
            "recent_7d_net_flow": net_flow_recent,
            "peak_hour": f"{peak_info['hour']:02d}:00",
            "morning_rush_departures": out_info.get("morning_rush_departures", 0),
            "evening_rush_departures": out_info.get("evening_rush_departures", 0),
            "avg_trip_duration_min": round((in_info.get("avg_duration") or 0) / 60, 1)
        })
        
    elapsed = time.time() - t0
    print(f"=== FULL REPORT GENERATED: {len(station_reports)} STATIONS ===")
    print(f"Elapsed Time: {elapsed:.2f} seconds.")
    return elapsed, station_reports

if __name__ == "__main__":
    compute_station_availability_report()
