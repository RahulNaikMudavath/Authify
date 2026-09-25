# Video Recording Cheatsheet (Quick Reference for the 4 Questions)

Keep this file open on your second monitor or next to your recording window so you can deliver a smooth, natural explanation on camera.

---

## Question 1: How the cache behaves on a rebalance & guaranteeing <5s freshness

### Key Concepts to Hit:
- **Event-Driven Write Invalidation**: The write directly controls cache state, not a passive timer.
- **Atomic Operations**: MongoDB update + Redis key deletion + timestamp update occur within the same write request in <2ms.
- **Proactive Background Warming**: Recomputation starts at $t=0.001\text{s}$ post-write.
- **Single-Flight Lock**: Readers await the in-flight recomputation rather than reading stale data or duplicating queries.

### Talking Points:
1. **At $t=0$**: Operator triggers rebalance (`POST /api/rebalance`).
2. **Database Write**: MongoDB `stations` updates occupied docks in <2ms, and an event is logged in `rebalance_log`.
3. **Cache Invalidation**: Redis pipeline runs:
   - Sets `report:last_rebalance_at = timestamp`
   - Increments `report:rebalance_count`
   - Executes `DEL report:data` (pre-rebalance cache is destroyed immediately)
4. **Proactive Worker**: Write endpoint fires a background worker that acquires `report:lock` (`SET NX EX 45`) and begins the 15-second MongoDB query immediately.
5. **Why nobody reads >5s stale**:
   - Any read arriving post-write sees `last_rebalance_at > cached_at` and `report:data` missing.
   - Readers join the single-flight wait loop instead of serving stale numbers.
   - Once computed, the fresh report is served.
   - Verified: Zero reads ever exceed 5s staleness under live writes.

---

## Question 2: Approaches/Redis features considered and rejected

### 1. Passive TTL-Only Caching (e.g. 60-second TTL)
- **Why rejected**: Violates the 5-second correctness deadline. If a rebalance happens at $t=1\text{s}$, dispatchers see wrong data for up to 59 seconds, causing a wasted 30-minute van run.
- **Why 5s TTL fails**: A 5s TTL would trigger a 15-second recomputation every 5 seconds, causing infinite query queuing and server collapse.

### 2. Synchronous Write-Through (recomputing inside POST /api/rebalance)
- **Why rejected**: Freezes the operator's write request for 15 to 20 seconds. Writes must be sub-10ms and durable. Tying write latency to a heavy read aggregation is poor distributed systems design.

### 3. Naive Cache-Aside Invalidation (`DEL` without locking)
- **Why rejected**: **The Thundering Herd / Cache Stampede**. Deleting the key under continuous polling causes every polling dispatcher to miss the cache simultaneously, launching 10 concurrent 20-second unindexed queries against MongoDB and spiking CPU to 100%.

### 4. Redis Keyspace Notifications (Pub/Sub)
- **Why rejected**: Pub/Sub is fire-and-forget; if a subscriber is momentarily disconnected or restarting, the invalidation message is dropped without delivery guarantees. Direct atomic key deletion in Redis is persistent, reliable, and simpler.

---

## Question 3: If reads went 10x higher (thousands/hr) — what breaks first & what to change?

### What Breaks First:
- **Flask's synchronous worker thread pool during the 15-second recompute window**.
- When the single-flight worker is recomputing, hundreds of incoming HTTP requests enter the 250ms Redis polling wait loop.
- In a synchronous WSGI server like Flask with Gunicorn (4–8 worker threads), all worker threads quickly become occupied waiting for the Redis lock, causing connection timeouts for other API endpoints (`/api/stations`, health checks).

### What to Change:
1. **Asynchronous / Non-blocking Runtime (FastAPI / ASGI with asyncio)**:
   - Waiting on the Redis lock becomes non-blocking. Thousands of concurrent requests consume minimal event-loop memory rather than blocking OS threads.
2. **Two-Tier Cache (1-second in-process RAM cache)**:
   - Place an in-memory cache (`cachetools` in RAM) inside each API instance. Thousands of reads are served in microseconds without network roundtrips to Redis.
3. **CQRS / Split Read-Model**:
   - Decouple the 550,000-trip traffic analysis (which changes slowly and can be cached hourly) from station dock occupancy (stored in a Redis Hash). On read, join them in <0.5ms dynamically in Redis, eliminating the 15-second recompute altogether.

---

## Question 4: Where an AI tool gave wrong/misleading guidance & how you caught it

### The AI's Misleading Advice:
- When prompted to implement cache invalidation, AI tools standardly output naive cache-aside:
  ```python
  # AI default advice:
  if not redis.get(key):
      data = compute_heavy_query()
      redis.set(key, data)
  # On write:
  redis.delete(key)
  ```

### Why It Was Wrong:
- The AI presented this as "clean, production-ready code."
- But on an expensive 15–20s query under continuous 2s polling, this causes a catastrophic **Cache Stampede (Thundering Herd)**.
- The instant `redis.delete()` runs, every polling dispatcher misses simultaneously and fires a duplicate 20-second unindexed aggregation against MongoDB, crashing the database.

### How We Caught and Fixed It:
- We caught it by analyzing concurrency under continuous polling.
- We rejected the naive boilerplate and implemented **Single-Flight Distributed Mutex Locking (`SET report:lock NX EX 45`)** with an atomic waiting loop and a safe Lua script lock release.
- Exactly **one** recomputation runs regardless of reader burst, protecting MongoDB completely.
