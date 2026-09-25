# Station-Availability Report Cache That Is Never Wrong

A high-performance caching architecture built with **Flask**, **MongoDB**, **Redis**, and **React**. It solves the core tension of city bike-share operations: serving heavy 15–20 second reports in milliseconds while guaranteeing that dispatcher numbers are **never stale by more than 5 seconds after a dock rebalance**, without falling victim to cache stampedes.

---

## Quick Start (Single Command Boot)

On any clean machine with Docker and Docker Compose installed:

```bash
docker compose up --build
```
*(or run `./start.sh` on Linux/macOS or `start.bat` on Windows)*

Once initialized:
- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **Flask REST API**: [http://localhost:5000](http://localhost:5000)
- **API Status**: [http://localhost:5000/api/status](http://localhost:5000/api/status)

*Note: On first startup, the service automatically detects if MongoDB is empty and seeds **250 stations** and **550,000 trips** with deliberate messy data, followed by sampling 100 records for inspection.*

---

## Running Native (Without Docker)

If you prefer to run services natively:

### Prerequisites:
- Python 3.10+
- Node.js 18+ and npm
- MongoDB Server running on `localhost:27017`
- Redis Server running on `localhost:6379`

### Setup & Run:
```bash
# 1. Setup Python Virtual Environment & Dependencies
python -m venv backend/venv
# Windows:
backend\venv\Scripts\pip install -r backend/requirements.txt
# Linux/macOS:
source backend/venv/bin/activate && pip install -r backend/requirements.txt

# 2. Seed Dataset (250 stations, 550,000 trips with messy data)
python backend/data_generator.py

# 3. Start Flask Backend (Port 5000)
python backend/app.py

# 4. Start React Frontend (In another terminal, Port 3000)
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## Running the Load Generator Under Pressure

To simulate continuous dispatcher traffic (hundreds to thousands of reads per hour) and concurrent rebalance writes, run the included load generator:

```bash
# Windows
backend\venv\Scripts\python load_generator.py --duration 45 --workers 6 --reb-interval 15

# Linux / macOS
python3 load_generator.py --duration 45 --workers 6 --reb-interval 15
```

### What the Load Generator verifies:
1. **Cache Hit Ratio**: Asserts that >95% of reads return in ~2ms from Redis cache.
2. **Recompute Economics**: Asserts that during a 15–20s recomputation, concurrent readers await the single in-flight query rather than launching duplicate queries (zero cache stampede).
3. **Correctness Deadline**: Tracks every rebalance write and verifies that **zero reads exceed the 5-second staleness bound**.

---

## Architectural Highlights

### 1. The Core Tension
- **Aggressive Caching**: Serves reads in 2ms, but risks serving stale dock numbers after an operator rebalances docks.
- **Eager Invalidation**: Deleting the cache on write forces the next reader to pay the 15–20s recomputation cost. Under concurrent polling, multiple readers trigger duplicate 20s queries simultaneously, crushing MongoDB (cache stampede).
- **Our Resolution**: **Event-Driven Write Invalidation with Redis Single-Flight Mutex Locking & Proactive Background Pre-warming**.

### 2. How Invalidation & Locking Work
```
[ Operator Rebalance Write ]
            │
            ├─► Atomically update dock occupancy in MongoDB
            ├─► Record last_rebalance_at timestamp in Redis
            ├─► Invalidate cache key ('report:data')
            └─► Spawn immediate proactive recompute worker
                          │
                          ▼
            [ Redis Distributed Mutex Lock: 'report:lock' ]
            ┌─────────────────────────────┴─────────────────────────────┐
            │                                                           │
     [ Lock Winner ]                                            [ Waiters / Re-pollers ]
  - Runs 15-20s MongoDB aggregation                         - Poll Redis every 250ms
  - Saves new report to Redis                               - Receive fresh report once ready
  - Releases lock                                           - 0 duplicate database queries!
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/report` | Returns the station-availability report with cache diagnostics (`source: "cache" \| "fresh"`, `data_age_seconds`, `computation_time_seconds`). |
| `POST` | `/api/rebalance` | Rebalances docks for a station (`station_id`, `occupied_docks`). Writes to Mongo, invalidates cache, starts pre-warm. |
| `GET` | `/api/stations` | Fast list of all stations (direct from indexed Mongo collection). |
| `GET` | `/api/status` | System health check, MongoDB document counts, and Redis cache metadata. |
| `GET` | `/api/report/benchmark` | Uncached direct execution over MongoDB to verify query timing (~15–20s). |
| `POST` | `/api/seed` | Reseeds 250 stations and 550,000 trips on demand. |

---

## Project Structure

```
authifylabs/
├── backend/
│   ├── app.py                # Flask REST API with cache coordination
│   ├── cache_manager.py      # Redis cache manager with Single-Flight Mutex lock
│   ├── report.py             # Heavy aggregation query (calibrated 15-20s)
│   ├── data_generator.py     # 250 stations & 550k trips generator with messy data
│   ├── entrypoint.py         # Container boot script with auto-seeding
│   ├── test_api_flow.py      # Automated test suite for cache hit/miss & rebalance
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # Backend container definition
├── frontend/
│   ├── src/
│   │   ├── App.jsx           # Real-time dashboard with polling & rebalance controls
│   │   ├── index.css         # Modern, responsive dark UI theme
│   │   └── main.jsx          # React DOM entry
│   ├── index.html            # Web entrypoint
│   ├── nginx.conf            # Nginx reverse proxy configuration
│   ├── package.json          # Node dependencies
│   ├── vite.config.js        # Vite build & proxy config
│   └── Dockerfile            # Frontend multi-stage container build
├── docker-compose.yml        # Multi-container orchestration
├── load_generator.py         # Concurrent load tester & staleness auditor
├── start.sh                  # Linux/macOS single-command launcher
├── start.bat                 # Windows single-command launcher
├── DECISIONS.md              # Architectural decisions, trade-offs, and video questions
└── README.md                 # System overview and quick start guide
```
