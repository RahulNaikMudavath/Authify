"""
Test script for Flask API flow, cache hit/miss behavior, and rebalance invalidation.
"""

import time
from app import app, cache, db

def run_tests():
    print("\n" + "=" * 60)
    print("STARTING API & CACHE CORRECTNESS TEST SUITE")
    print("=" * 60)
    
    client = app.test_client()
    
    # Test 1: Status
    print("\n[1] Testing GET /api/status...")
    res = client.get("/api/status")
    assert res.status_code == 200, f"Status failed: {res.text}"
    status_data = res.get_json()
    print("-> Status Response:", status_data["status"])
    print(f"-> MongoDB Stations: {status_data['mongodb']['station_count']}, Trips: {status_data['mongodb']['trip_count']}")
    print(f"-> Redis Connected: {status_data['redis']['connected']}")
    
    # Test 2: Stations List
    print("\n[2] Testing GET /api/stations...")
    res = client.get("/api/stations")
    assert res.status_code == 200
    stn_data = res.get_json()
    print(f"-> Retrieved {stn_data['count']} stations. First: {stn_data['stations'][0]['name']}")
    
    # Clear cache before cold read test
    cache.clear_all()
    
    # Test 3: Cold Read (Cache Miss -> Recompute)
    print("\n[3] Testing Cold Read: GET /api/report (Expecting Cache MISS)...")
    t0 = time.time()
    res = client.get("/api/report")
    cold_time = time.time() - t0
    assert res.status_code == 200
    data = res.get_json()
    print(f"-> Source: {data.get('source')} | Cache Hit: {data.get('cache_hit')}")
    print(f"-> Computation Time: {data.get('computation_time_seconds')}s | Wall Clock: {cold_time:.2f}s")
    print(f"-> Data Age: {data.get('data_age_seconds')}s")
    print(f"-> Header X-Cache: {res.headers.get('X-Cache')}")
    assert data.get("cache_hit") is False, "Expected cache_hit=False on cold read!"
    
    # Test 4: Warm Read (Cache Hit -> ~2ms)
    print("\n[4] Testing Warm Read: GET /api/report (Expecting Cache HIT)...")
    t0 = time.time()
    res = client.get("/api/report")
    warm_time = (time.time() - t0) * 1000
    assert res.status_code == 200
    data = res.get_json()
    print(f"-> Source: {data.get('source')} | Cache Hit: {data.get('cache_hit')}")
    print(f"-> Response Latency: {warm_time:.2f} ms")
    print(f"-> Data Age: {data.get('data_age_seconds')}s")
    print(f"-> Header X-Cache: {res.headers.get('X-Cache')}")
    assert data.get("cache_hit") is True, "Expected cache_hit=True on warm read!"
    assert warm_time < 50, f"Expected sub-50ms cache hit, got {warm_time:.2f}ms"
    
    # Test 5: Rebalance Write
    target_station_id = 1
    station_before = db.stations.find_one({"station_id": target_station_id})
    prev_occ = station_before["occupied_docks"]
    new_occ = 0 if prev_occ > 5 else station_before["total_docks"]
    
    print(f"\n[5] Testing POST /api/rebalance on Station #{target_station_id}...")
    print(f"-> Changing occupied docks from {prev_occ} to {new_occ}...")
    res = client.post("/api/rebalance", json={
        "station_id": target_station_id,
        "occupied_docks": new_occ,
        "reason": "Test van rebalance"
    })
    assert res.status_code == 200
    reb_res = res.get_json()
    print("-> Rebalance Response:", reb_res["message"])
    
    # Verify Mongo was updated
    station_after = db.stations.find_one({"station_id": target_station_id})
    assert station_after["occupied_docks"] == new_occ, "MongoDB occupied docks not updated!"
    print(f"-> Verified MongoDB station document updated: occupied_docks={station_after['occupied_docks']}")
    
    # Verify Cache was invalidated
    meta = cache.client.hgetall("report:meta")
    assert not cache.client.exists("report:data"), "Cache report data was not invalidated!"
    print("-> Verified Redis cache key was invalidated immediately.")
    
    # Test 6: Read after rebalance (Single-flight fresh recompute)
    print("\n[6] Testing Read Immediately After Rebalance...")
    t0 = time.time()
    res = client.get("/api/report")
    post_reb_time = time.time() - t0
    assert res.status_code == 200
    data = res.get_json()
    print(f"-> Source: {data.get('source')} | Cache Hit: {data.get('cache_hit')}")
    print(f"-> Elapsed Time: {post_reb_time:.2f}s")
    
    # Find target station in data
    stns = data["data"]["stations"]
    stn_report = next((s for s in stns if s["station_id"] == target_station_id), None)
    assert stn_report is not None
    print(f"-> Station #{target_station_id} in newly computed report: occupied={stn_report['occupied_docks']}")
    assert stn_report["occupied_docks"] == new_occ, f"Report did not reflect new occupied docks! Got {stn_report['occupied_docks']}, expected {new_occ}"
    print("-> SUCCESS: Report correctly reflects rebalanced dock numbers!")
    
    # Test 7: Subsequent Read (Cache Hit again)
    print("\n[7] Testing Subsequent Read after recompute (Expecting Cache HIT)...")
    t0 = time.time()
    res = client.get("/api/report")
    warm_time_2 = (time.time() - t0) * 1000
    assert res.status_code == 200
    data = res.get_json()
    print(f"-> Source: {data.get('source')} | Cache Hit: {data.get('cache_hit')}")
    print(f"-> Response Latency: {warm_time_2:.2f} ms")
    assert data.get("cache_hit") is True
    print("-> SUCCESS: Cache HIT verified on new report!")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED WITH 100% CORRECTNESS!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    run_tests()
