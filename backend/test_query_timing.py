"""
Timing test for heavy MongoDB aggregation query.
Measures uncached end-to-end execution time.
"""

import time
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
db = client["bikeshare"]

def run_heavy_aggregation():
    t0 = time.time()
    
    # Aggregation pipeline joining stations to 550,000 trips with messy data handling
    pipeline = [
        {
            "$lookup": {
                "from": "trips",
                "let": { "stn_id": "$station_id" },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$or": [
                                            { "$eq": ["$start_station_id", "$$stn_id"] },
                                            { "$eq": ["$end_station_id", "$$stn_id"] }
                                        ]
                                    },
                                    # Handle messy records:
                                    # 1. Reject end before start
                                    {
                                        "$or": [
                                            { "$eq": ["$end_time", None] },
                                            { "$gte": ["$end_time", "$start_time"] }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "outbound_trips": {
                                "$sum": {
                                    "$cond": [{ "$eq": ["$start_station_id", "$$stn_id"] }, 1, 0]
                                }
                            },
                            "inbound_trips": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                { "$eq": ["$end_station_id", "$$stn_id"] },
                                                { "$ne": ["$end_time", None] }
                                            ]
                                        },
                                        1,
                                        0
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
                "as": "traffic"
            }
        },
        {
            "$project": {
                "_id": 0,
                "station_id": 1,
                "name": 1,
                "total_docks": 1,
                "occupied_docks": 1,
                "available_docks": { "$subtract": ["$total_docks", "$occupied_docks"] },
                "occupancy_rate": {
                    "$round": [
                        { "$multiply": [{ "$divide": ["$occupied_docks", "$total_docks"] }, 100] },
                        1
                    ]
                },
                "traffic_stats": { "$arrayElemAt": ["$traffic", 0] }
            }
        },
        {
            "$project": {
                "station_id": 1,
                "name": 1,
                "total_docks": 1,
                "occupied_docks": 1,
                "available_docks": 1,
                "occupancy_rate": 1,
                "outbound_trips": { "$ifNull": ["$traffic_stats.outbound_trips", 0] },
                "inbound_trips": { "$ifNull": ["$traffic_stats.inbound_trips", 0] },
                "avg_duration_minutes": {
                    "$round": [
                        { "$divide": [{ "$ifNull": ["$traffic_stats.avg_duration", 0] }, 60] },
                        1
                    ]
                },
                "net_flow": {
                    "$subtract": [
                        { "$ifNull": ["$traffic_stats.inbound_trips", 0] },
                        { "$ifNull": ["$traffic_stats.outbound_trips", 0] }
                    ]
                }
            }
        },
        { "$sort": { "station_id": 1 } }
    ]
    
    cursor = db.stations.aggregate(pipeline, allowDiskUse=True)
    results = list(cursor)
    elapsed = time.time() - t0
    
    print(f"Computed report for {len(results)} stations.")
    print(f"Elapsed time: {elapsed:.2f} seconds.")
    if results:
        print("Sample station output:", results[0])
    return elapsed

if __name__ == "__main__":
    run_heavy_aggregation()
