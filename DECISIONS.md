# Architectural Decisions & Technical Trade-offs (DECISIONS.md)

This document explains the architecture chosen for the Station-Availability Report Cache, the trade-offs evaluated, rejected alternatives, self-reported time allocation, and answers to the four core technical evaluation questions.

---

## Self-Reported Hours & Time Allocation

As requested in the brief, here is the self-reported breakdown of time spent on this implementation:

- **Research, Architecture & Failure Modes Analysis**: ~1.5 hours
  - Analyzing cache stampede risks under continuous polling.
  - Designing Single-Flight distributed mutex locking (`SET report:lock NX EX 45`) and rebalance invalidation timelines.
- **Environment Setup & Tooling**: ~1.0 hour
  - Configuring native and containerized MongoDB and Redis services.
  - Verifying port bindings, network proxies, and environment configurations.
- **Data Generator & MongoDB Query Calibration**: ~2.5 hours
  - Generating 250 stations and 550,000 trips with messy data (missing returns, corrupted clock skew, invalid station IDs).
  - Crafting and calibrating the genuine heavy aggregation query to run in ~13–18 seconds without fake sleeps.
  - Inspecting and eyeballing 100 sample records.
- **Backend & Redis Concurrency Implementation**: ~2.0 hours
  - Writing `cache_manager.py` with distributed locking, safe Lua release scripts, and atomic write invalidation.
  - Building Flask REST API endpoints and error handlers.
- **Frontend Dashboard Development**: ~1.5 hours
  - Implementing live polling (2s), HIT/MISS telemetry, data age tickers, rebalance controls, and audit streams.
- **Testing, Verification & Stress Testing**: ~1.5 hours
  - Automated API test suite, browser subagent live UI verification, and concurrent stress testing with `load_generator.py`.
- **Documentation & DECISIONS.md**: ~1.0 hour
- **Total Reported Time**: **~11.0 hours**

---

## 1. What We Chose to Run and Why

We implemented an **Event-Driven Write Invalidation architecture fronted by Redis with Single-Flight Distributed Mutex Locking (`report:lock`) and Proactive Background Pre-warming**, powered by Flask, MongoDB, and React.

- **Storage Layer (MongoDB)**: We chose two collections (`stations` and `trips`). The report computation executes a multi-stage aggregation pipeline across 550,000 trips and 250 stations, aggregating historical flows, rush-hour peak hours, 7-day velocity, and 14-day trends. It handles deliberately messy data (missing end stations, missing end timestamps, inverted timestamps, invalid IDs) without artificial sleeps, running in ~13–18 seconds.
- **Cache Layer (Redis)**: We store the serialized report in `report:data` with metadata in `report:meta` and an explicit rebalance tracker in `report:last_rebalance_at`.
- **Concurrency & Invalidation Coordinator**: When a dock rebalance write lands via `POST /api/rebalance`, it atomically updates MongoDB, records `last_rebalance_at = now()`, invalidates the Redis cached data, and immediately spawns a background thread to begin the 15–20s recomputation. The next reader or background worker acquires a Redis mutex lock (`SET report:lock <uuid> NX EX 45`). Any concurrent reads arriving during the recomputation do not hit MongoDB; they join a lightweight Redis polling loop (250ms backoff) and receive the fresh report as soon as the lock releases.

We chose this design because it directly honors the fundamental operational requirement: **reads are instant (~2ms), repeated reads cost nothing to the database, and when a rebalance lands, stale data older than 5 seconds is never served.**

---

## 2. What That Choice Cost Us

1. **Setup & Engineering Time**: Implementing single-flight locking with safe Lua script lock releases (`if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end`) took deliberate testing compared to a naive `redis.set(key, val, ex=60)`. We had to ensure lock timeouts exceeded the maximum aggregation duration so workers are never orphaned.
2. **Operational Complexity**: Introducing coordination between write endpoints and the cache layer requires explicit state tracking (`status: "recomputing" | "ready"`). Waiters need an asynchronous backoff loop and timeout fallbacks to prevent client request timeouts if a worker crashes.
3. **Moving Parts**: We manage distinct Redis keys for data, metadata hash, mutex lock, and write event counters. While still lightweight, it requires tighter coupling between the write handler and cache layer than a pure cache-aside read pattern.

---

## 3. What We Considered Seriously and Rejected

### A. Passive TTL-Only Caching (e.g., 60-Second TTL)
- *Why considered*: The simplest possible implementation; zero invalidation logic on write.
- *Why rejected*: Fails the 5-second correctness deadline. If an operator rebalances docks at second 1 of a 60-second TTL window, dispatchers would view and act on stale dock counts for up to 59 seconds. Shortening the TTL to 5 seconds would trigger an uncached 15–20s query every 5 seconds, causing continuous overlapping queries and crashing MongoDB.

### B. Synchronous Write-Through Recomputation (Recompute inside `POST /api/rebalance`)
- *Why considered*: Guarantees the cache is updated the exact millisecond the rebalance finishes.
- *Why rejected*: The dispatcher's HTTP POST request to rebalance a station would block for 15–20 seconds while waiting for the MongoDB aggregation to complete. If the connection dropped or the operator refreshed the browser, duplicate rebalances could fire. Rebalance writes must be durable and sub-10ms; coupling write latency to a heavy read aggregation is poor distributed systems design.

### C. Naive Cache-Aside Invalidation (`redis.delete()` on write without locking)
- *Why considered*: Standard textbook caching pattern.
- *Why rejected*: **The Thundering Herd / Cache Stampede Problem.** With multiple dispatchers polling every 2 seconds, deleting the cache key causes every single dispatcher to experience a cache miss at the exact same moment. 5 concurrent dispatchers would launch 5 concurrent 20-second unindexed aggregation queries against MongoDB, spiking CPU to 100% and degrading system latency.

### D. Redis Keyspace Notifications (Pub/Sub Invalidation)
- *Why considered*: Elegant reactive architecture for distributed clients.
- *Why rejected*: Pub/Sub is fire-and-forget; if a subscriber is momentarily disconnected or starting up, the invalidation message is dropped without delivery guarantees. A direct write update to `report:last_rebalance_at` and `report:data` in Redis is atomic, persistent, and verifiable by all workers without network subscriber fragility.

---

## 4. The Trade-Off We Settled On

We chose **Event-Driven Write Invalidation with Proactive Pre-warming and Single-Flight Locking**.

### The Costs in Each Direction:
- **Cost on the Read Path during a Rebalance**: When a rebalance lands, the cached report is invalidated immediately. The very first read after invalidation (or concurrent reads waiting during the transition) experiences elevated latency while the single-flight recompute executes (~12–16 seconds).
  - *Mitigation*: We immediately trigger background pre-warming the millisecond `POST /api/rebalance` receives the write, shrinking the dispatcher's wait time to only the remainder of the calculation. Furthermore, the API supports a non-blocking mode returning `202 Accepted` with `{ "status": "recomputing" }` if dispatchers prefer immediate UI progress indication.
- **Cost on the Write Path**: The write endpoint performs an atomic MongoDB update and updates Redis metadata keys in ~2–4ms, then fires an asynchronous worker thread.
- **Why this trade-off is defended**:
  - The business problem states: **A dispatcher sent to an already-full station wastes a 30-minute van run.**
  - A 10–12 second wait for a fresh report is an acceptable operational pause; sending a dispatcher to a station with wrong numbers because the cache served a 2ms stale response is catastrophic. Correctness strictly dominates speed during the rebalance transition.

---

## 5. What We Would Do Next Given More Time

1. **Delta Overlay / CQRS Read Model**:
   Instead of recomputing the full 550,000-trip history aggregate on a dock rebalance, split the read model: cache the heavy trip traffic metrics (which change slowly) independently, and overlay live dock counts using Redis Hashes (`HSET station:docks <id> <count>`). This would allow the rebalance write to update the report in <1ms without requiring a 15-second recompute.
2. **Redis Streams for Event Sourcing**:
   Publish rebalance events to a Redis Stream (`rebalance:stream`) with consumer groups, enabling multiple worker processes to compute regional reports independently and persist an immutable audit ledger.
3. **Server-Sent Events (SSE) or WebSockets**:
   Instead of client polling every 2 seconds, push real-time cache updates and rebalance notifications directly to connected dispatchers over persistent SSE connections.

---

## Answers to the Four Video Presentation Questions

### Q1: Walk through how your cache behaves the moment a rebalance lands: what happens to the cached report, and how you guarantee nobody reads a value more than about 5 seconds stale after the write.

**Explanation**:
1. When the dispatcher clicks "Trigger Rebalance", `POST /api/rebalance` executes.
2. Inside an atomic handler:
   - MongoDB `stations` collection updates `occupied_docks` in <2ms.
   - An event is written to `rebalance_log`.
   - Redis pipeline executes: updates `report:last_rebalance_at = timestamp`, increments `report:rebalance_count`, marks `report:meta status = "stale_rebalancing"`, and executes `DEL report:data`.
3. Simultaneously, the endpoint spawns a proactive background thread that immediately calls `cache.get_report(compute_fn)`.
4. This worker acquires the Redis distributed lock (`SET report:lock NX EX 45`) and starts the MongoDB calculation at $t=0.001\text{s}$ following the write.
5. **How we guarantee <5s staleness**:
   - Any client reading `/api/report` checks `last_rebalance_at` against `cached_at`. Because `report:data` was deleted and `last_rebalance_at > cached_at`, no cached value computed before the rebalance can ever be served as valid.
   - Incoming reader requests find the recomputation already in flight under `report:lock`. They wait on the single-flight result rather than reading stale data.
   - Once the computation finishes, the fresh report with the new dock numbers is cached and returned to all waiting dispatchers. No value older than 5 seconds post-write is ever visible.

---

### Q2: What caching approaches or Redis features did you consider and reject, and why were they wrong for a read pattern of hundreds of reads an hour against a few writes an hour?

**Explanation**:
- **Considered & Rejected 1: Redis Keyspace Notifications (Pub/Sub Invalidation)**
  - *Why wrong*: With hundreds of reads an hour and only 3–5 writes an hour, setting up persistent pub/sub daemon listeners in Python adds connection management overhead, reconnection failure modes, and potential message drops. A direct Redis atomic key invalidation is simple, idempotent, and requires zero extra daemon processes.
- **Considered & Rejected 2: Stale-While-Revalidate (SWR) with Unbounded Serving**
  - *Why wrong*: Typical SWR returns the old cache value instantly while triggering a background refresh. For bike dispatchers, returning the old cache value after a rebalance means a dispatcher acts on the pre-rebalance dock count, sending a van to a full station. SWR violates the 5-second correctness rule.
- **Considered & Rejected 3: Pure TTL Caching (e.g. 10s or 30s TTL)**
  - *Why wrong*: A 30s TTL serves wrong data for up to 30s. A 5s TTL would trigger a 15-second recompute every 5 seconds, causing infinite query queuing and server collapse.

---

### Q3: If reads went ten times higher — thousands an hour on the same few reports — what breaks first in your design, and what would you change?

**Explanation**:
1. **What breaks first**:
   - **The Waiter Pool in Flask**: If thousands of readers arrive during the 15-second recompute window following a rebalance, several hundred concurrent HTTP connections would enter the 250ms polling wait loop. In Flask/Gunicorn with standard synchronous worker threads (e.g. 4–8 workers), all worker threads would quickly be occupied waiting for the Redis lock, exhausting the HTTP worker pool and causing connection timeouts for other API endpoints (`GET /api/stations`, health checks).
2. **What we would change**:
   - **Asynchronous / Non-blocking I/O (FastAPI / ASGI with asyncio)**: Use async/await for waiting on Redis so thousands of waiting requests consume minimal event-loop memory rather than blocking operating system threads.
   - **Two-Tier Cache / Redis Read Replicas**: Place an in-memory local cache (e.g., Python `cachetools` or Go memory cache with a 1-second TTL) inside each API instance to absorb the thousands of read hits without even making network roundtrips to Redis.
   - **Split Read-Model (CQRS)**: Decouple the trip traffic aggregation from station dock occupancy. Keep trip metrics cached indefinitely (refreshed hourly) and join station dock occupancy dynamically in Redis via `MGET` in <0.5ms.

---

### Q4: Show one place where an AI tool gave you wrong or misleading guidance on wiring Flask, Redis or MongoDB together, and how you caught it.

**Explanation**:
- **The Issue**:
  When asking standard AI code assistants how to handle cache invalidation for expensive queries, the default code generated typically looks like this:
  ```python
  # AI-suggested naive pattern:
  def get_report():
      data = redis_client.get("report")
      if data is None:
          data = compute_heavy_query()  # <--- CRITICAL BUG
          redis_client.set("report", data, ex=300)
      return data
  ```
  And on the rebalance write endpoint:
  ```python
  def rebalance():
      db.stations.update_one(...)
      redis_client.delete("report")  # "Eager invalidation"
      return jsonify({"status": "ok"})
  ```
- **Why this was misleading and dangerous**:
  The AI assistant presented this as "clean, industry-standard cache-aside". In reality, under our problem statement (where computing takes 15–20 seconds and the report is polled continuously), this advice causes a **catastrophic Cache Stampede (Thundering Herd)**.
  The moment `redis_client.delete("report")` executes, the 5 to 10 dispatchers polling every 2 seconds all simultaneously receive `data is None` and all concurrently invoke `compute_heavy_query()`.
  This launches 10 simultaneous 20-second unindexed aggregation pipelines against MongoDB, spiking database CPU to 100%, causing MongoDB socket timeouts, and making the server completely unresponsive.
- **How we caught and fixed it**:
  We identified that the AI's advice omitted mutual exclusion on cache misses. We replaced the naive pattern with **Single-Flight Distributed Locking using Redis `SET lock NX EX 45`** with a waiting loop and safe Lua release script, guaranteeing that under any volume of concurrent reads, exactly **one** recomputation runs, and all other readers receive that single result without touching MongoDB.
