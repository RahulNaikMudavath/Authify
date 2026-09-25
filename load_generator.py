import argparse
import sys
import threading
import time
import requests

API_URL = "http://localhost:5000"

stats = {
    "total_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "awaited_recomputes": 0,
    "latencies_ms": [],
    "rebalances_triggered": 0,
    "rebalances_completed": 0,
    "violations_count": 0,
    "max_staleness_observed": 0.0
}
stats_lock = threading.Lock()
stop_event = threading.Event()

recent_rebalances = {}
reb_lock = threading.Lock()


def dispatcher_worker(worker_id, poll_interval=1.5):
    session = requests.Session()
    
    while not stop_event.is_set():
        t0 = time.time()
        try:
            res = session.get(f"{API_URL}/api/report", timeout=45)
            latency_ms = (time.time() - t0) * 1000
            
            if res.status_code == 200:
                data = res.json()
                source = data.get("source")
                
                with reb_lock:
                    for sid, reb_info in list(recent_rebalances.items()):
                        time_since_reb = time.time() - reb_info["timestamp"]
                        stations = data.get("data", {}).get("stations", [])
                        stn_entry = next((s for s in stations if s["station_id"] == sid), None)
                        
                        if stn_entry:
                            served_occupied = stn_entry["occupied_docks"]
                            expected_occupied = reb_info["new_occupied"]
                            
                            if served_occupied != expected_occupied:
                                if time_since_reb > 5.0:
                                    print(f"\n[!] STALENESS VIOLATION on Worker {worker_id}: Station {sid} served {served_occupied} instead of {expected_occupied} after {time_since_reb:.2f}s!")
                                    with stats_lock:
                                        stats["violations_count"] += 1
                                        if time_since_reb > stats["max_staleness_observed"]:
                                            stats["max_staleness_observed"] = time_since_reb
                                
                with stats_lock:
                    stats["total_requests"] += 1
                    stats["latencies_ms"].append(latency_ms)
                    if source == "cache":
                        stats["cache_hits"] += 1
                    elif source == "cache_await_fresh":
                        stats["awaited_recomputes"] += 1
                    else:
                        stats["cache_misses"] += 1
                        
        except Exception:
            pass
                
        time.sleep(poll_interval)


def rebalance_worker(interval=25):
    session = requests.Session()
    reb_step = 0
    
    try:
        res = session.get(f"{API_URL}/api/stations")
        stations = res.json().get("stations", [])
    except Exception:
        return
        
    if not stations:
        return
        
    while not stop_event.is_set():
        time.sleep(interval)
        if stop_event.is_set():
            break
            
        reb_step += 1
        stn = stations[reb_step % min(len(stations), 20)]
        sid = stn["station_id"]
        total = stn["total_docks"]
        new_occ = 0 if (reb_step % 2 == 1) else total
        
        t_reb = time.time()
        with reb_lock:
            recent_rebalances[sid] = {
                "timestamp": t_reb,
                "new_occupied": new_occ
            }
            
        with stats_lock:
            stats["rebalances_triggered"] += 1
            
        try:
            res = session.post(f"{API_URL}/api/rebalance", json={
                "station_id": sid,
                "occupied_docks": new_occ,
                "reason": f"Load test run #{reb_step}"
            }, timeout=10)
            if res.status_code == 200:
                with stats_lock:
                    stats["rebalances_completed"] += 1
        except Exception:
            pass


def run_load_test(duration=60, num_workers=8, rebalance_interval=20):
    try:
        res = requests.get(f"{API_URL}/api/status", timeout=5)
        if res.status_code != 200:
            return
    except Exception as e:
        print(f"Could not connect to {API_URL}: {e}")
        return

    workers = []
    for i in range(num_workers):
        t = threading.Thread(target=dispatcher_worker, args=(i + 1,), daemon=True)
        t.start()
        workers.append(t)
        
    reb_thread = threading.Thread(target=rebalance_worker, args=(rebalance_interval,), daemon=True)
    reb_thread.start()
    
    start_time = time.time()
    try:
        while time.time() - start_time < duration:
            elapsed = time.time() - start_time
            with stats_lock:
                reqs = stats["total_requests"]
                hits = stats["cache_hits"]
                hit_rate = (hits / reqs * 100) if reqs > 0 else 0
            sys.stdout.write(f"\rProgress: {elapsed:.0f}s / {duration}s | Requests: {reqs} | Hits: {hits} ({hit_rate:.1f}%)")
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        
    with stats_lock:
        reqs = stats["total_requests"]
        hits = stats["cache_hits"]
        lats = stats["latencies_ms"]
        violations = stats["violations_count"]
        rebs = stats["rebalances_triggered"]
        
        hit_ratio = (hits / reqs * 100) if reqs > 0 else 0
        lats.sort()
        p50 = lats[len(lats) // 2] if lats else 0
        p95 = lats[int(len(lats) * 0.95)] if lats else 0
        avg_lat = sum(lats) / len(lats) if lats else 0
        
        print("\n\n" + "=" * 50)
        print("LOAD TEST RESULTS")
        print("=" * 50)
        print(f"Total Requests: {reqs}")
        print(f"Cache Hits:     {hits} ({hit_ratio:.1f}%)")
        print(f"Rebalances:     {rebs}")
        print(f"Avg Latency:    {avg_lat:.2f} ms")
        print(f"P50 Latency:    {p50:.2f} ms")
        print(f"P95 Latency:    {p95:.2f} ms")
        print(f"Staleness Violations (>5s): {violations}")
        print("=" * 50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=45)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--reb-interval", type=int, default=15)
    args = parser.parse_args()
    
    run_load_test(duration=args.duration, num_workers=args.workers, rebalance_interval=args.reb_interval)
