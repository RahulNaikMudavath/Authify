import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
import redis

logger = logging.getLogger(__name__)

KEY_REPORT_DATA = "report:data"
KEY_REPORT_META = "report:meta"
KEY_RECOMPUTE_LOCK = "report:lock"
KEY_LAST_REBALANCE = "report:last_rebalance_at"
KEY_REBALANCE_COUNT = "report:rebalance_count"

LOCK_TIMEOUT_SECONDS = 45
POLL_INTERVAL_SECONDS = 0.25
MAX_WAIT_SECONDS = 40
DEFAULT_TTL = 3600


class CacheManager:
    def __init__(self, redis_url=None):
        if not redis_url:
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", 6379))
            self.client = redis.Redis(host=host, port=port, decode_responses=True)
        else:
            self.client = redis.from_url(redis_url, decode_responses=True)
            
    def ping(self):
        try:
            return self.client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    def get_report(self, compute_fn, force_refresh=False):
        now = time.time()
        meta = self.client.hgetall(KEY_REPORT_META)
        cached_at = float(meta.get("cached_at", 0)) if meta and meta.get("cached_at") else 0
        last_rebalance_at = float(self.client.get(KEY_LAST_REBALANCE) or 0)
        rebalance_count = int(self.client.get(KEY_REBALANCE_COUNT) or 0)
        
        is_stale_from_rebalance = (last_rebalance_at > 0 and last_rebalance_at > cached_at)
        cached_data = self.client.get(KEY_REPORT_DATA)
        
        if cached_data and not is_stale_from_rebalance and not force_refresh:
            try:
                report = json.loads(cached_data)
                age_seconds = round(now - cached_at, 2)
                return {
                    "source": "cache",
                    "cache_hit": True,
                    "data_age_seconds": age_seconds,
                    "computation_time_seconds": float(meta.get("computation_time_seconds", 0)),
                    "cached_at": datetime.fromtimestamp(cached_at, tz=timezone.utc).isoformat(),
                    "last_rebalance_at": datetime.fromtimestamp(last_rebalance_at, tz=timezone.utc).isoformat() if last_rebalance_at else None,
                    "rebalance_count": rebalance_count,
                    "data": report
                }
            except Exception as e:
                logger.warning(f"Error deserializing cached report: {e}")
                
        return self._single_flight_recompute(compute_fn, force_refresh=force_refresh)

    def _single_flight_recompute(self, compute_fn, force_refresh=False):
        lock_token = str(uuid.uuid4())
        wait_start = time.time()
        
        acquired = self.client.set(
            KEY_RECOMPUTE_LOCK,
            lock_token,
            nx=True,
            ex=LOCK_TIMEOUT_SECONDS
        )
        
        if acquired:
            try:
                self.client.hset(KEY_REPORT_META, "status", "recomputing")
                
                t0 = time.time()
                fresh_report = compute_fn()
                comp_time = round(time.time() - t0, 2)
                now = time.time()
                
                pipe = self.client.pipeline()
                pipe.set(KEY_REPORT_DATA, json.dumps(fresh_report), ex=DEFAULT_TTL)
                pipe.hset(KEY_REPORT_META, mapping={
                    "cached_at": str(now),
                    "computation_time_seconds": str(comp_time),
                    "status": "ready"
                })
                pipe.execute()
                
                last_reb = float(self.client.get(KEY_LAST_REBALANCE) or 0)
                rebalance_count = int(self.client.get(KEY_REBALANCE_COUNT) or 0)
                
                return {
                    "source": "fresh",
                    "cache_hit": False,
                    "data_age_seconds": 0.0,
                    "computation_time_seconds": comp_time,
                    "cached_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                    "last_rebalance_at": datetime.fromtimestamp(last_reb, tz=timezone.utc).isoformat() if last_reb else None,
                    "rebalance_count": rebalance_count,
                    "data": fresh_report
                }
            finally:
                release_lua = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                self.client.eval(release_lua, 1, KEY_RECOMPUTE_LOCK, lock_token)
        else:
            while time.time() - wait_start < MAX_WAIT_SECONDS:
                time.sleep(POLL_INTERVAL_SECONDS)
                meta = self.client.hgetall(KEY_REPORT_META)
                if meta.get("status") == "ready":
                    cached_data = self.client.get(KEY_REPORT_DATA)
                    cached_at = float(meta.get("cached_at", 0))
                    last_reb = float(self.client.get(KEY_LAST_REBALANCE) or 0)
                    
                    if cached_data and cached_at >= last_reb:
                        report = json.loads(cached_data)
                        now = time.time()
                        waited = round(now - wait_start, 2)
                        return {
                            "source": "cache_await_fresh",
                            "cache_hit": True,
                            "waited_seconds": waited,
                            "data_age_seconds": round(now - cached_at, 2),
                            "computation_time_seconds": float(meta.get("computation_time_seconds", 0)),
                            "cached_at": datetime.fromtimestamp(cached_at, tz=timezone.utc).isoformat(),
                            "last_rebalance_at": datetime.fromtimestamp(last_reb, tz=timezone.utc).isoformat() if last_reb else None,
                            "rebalance_count": int(self.client.get(KEY_REBALANCE_COUNT) or 0),
                            "data": report
                        }
            
            return self._single_flight_recompute(compute_fn, force_refresh=True)

    def record_rebalance(self, station_id, new_occupied):
        now = time.time()
        pipe = self.client.pipeline()
        pipe.set(KEY_LAST_REBALANCE, str(now))
        pipe.incr(KEY_REBALANCE_COUNT)
        pipe.hset(KEY_REPORT_META, "status", "stale_rebalancing")
        pipe.delete(KEY_REPORT_DATA)
        pipe.execute()
        
        return {
            "last_rebalance_at": now,
            "station_id": station_id,
            "new_occupied": new_occupied
        }

    def clear_all(self):
        self.client.delete(KEY_REPORT_DATA, KEY_REPORT_META, KEY_RECOMPUTE_LOCK, KEY_LAST_REBALANCE, KEY_REBALANCE_COUNT)
