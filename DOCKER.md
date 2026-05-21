# Docker Setup for tradingbot

Complete containerization guide for the trading daemon. All logs, reports, and RAG state persist to local folders.

---

## Quick Start

### Production (supervisord manages both market_monitor + watchdog)

```bash
# Build image
docker compose build

# Start daemon (detached)
docker compose up -d

# View logs live
docker compose logs -f tradingbot-daemon

# Stop
docker compose down
```

### Development (hot-reload, single market_monitor only)

```bash
# Start with dev compose override (code bind-mounted)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Edit code locally, restart container to reload
docker compose restart

# Stop
docker compose down
```

---

## Architecture

**Host (Windows)**
```
├── moomoo OpenD (port 11111) — must run on host
├── Project root: c:\Users\pamed\Susheel\oa2-new
│   ├── tradingbot/    (source code)
│   ├── scripts/
│   ├── tests/
│   └── docker-compose.yml
│
└── Docker Data Directory: c:\Users\pamed\Susheel\tradingbot-docker
    ├── logs/          ← daemon.log, heartbeats, paper trades
    ├── reports/       ← daily premarket/postmarket, weekly analysis
    └── data/
        └── tradingbot/  ← knowledge_base.json, outcomes, calibration
            └── logs/    (within container's /data/tradingbot)

Docker Container: tradingbot-daemon
├── market_monitor.py   (main daemon)
├── watchdog.py         (sidecar monitor)
└── Volume mounts
    ├── C:\Users\pamed\Susheel\tradingbot-docker\logs → /app/logs
    ├── C:\Users\pamed\Susheel\tradingbot-docker\reports → /app/reports
    └── C:\Users\pamed\Susheel\tradingbot-docker\data\tradingbot → /data/tradingbot
```

---

## Configuration

### Set `MOOMOO_OPEND_HOST` in `.env`

When running in Docker, moomoo OpenD is on the host machine, not in the container:

```env
# For Docker Desktop on Windows/Mac:
MOOMOO_OPEND_HOST=host.docker.internal

# For Docker on Linux with OpenD on host:
MOOMOO_OPEND_HOST=<host-ip>
```

The compose file sets this automatically for Windows/Mac Docker Desktop.

### Other Docker Variables (set in docker-compose.yml)

- `TRADINGBOT_HOME=/data/tradingbot` — persistent ML state (knowledge base, outcomes, calibration)
- `REPORTS_DIR=/app/reports` — daily/weekly markdown reports
- `OA2_SUBMIT_ORDERS=0` — 0 for paper trading, 1 for live orders
- `OA2_TRADE_ENV=SIMULATE` — paper trading mode
- `TZ=America/New_York` — market hours timezone

---

## Volume Mounts Explained

| Mount | Type | Purpose | Location |
|-------|------|---------|----------|
| logs | bind mount | daemon.log, paper_trade logs, heartbeats | `C:\Users\pamed\Susheel\tradingbot-docker\logs` |
| reports | bind mount | daily premarket.md, postmarket.md, weekly_analysis.md | `C:\Users\pamed\Susheel\tradingbot-docker\reports` |
| tradingbot data | bind mount | knowledge_base.json, outcomes history, calibration priors | `C:\Users\pamed\Susheel\tradingbot-docker\data\tradingbot` |

**Persistent state** (never lost on container restart):
- All outcomes from live trading
- Knowledge base (KB) that learns from trades
- Bandit posteriors and calibration state
- Blacklist derived from KB

**Logs & reports** (view locally):
- Daily daemon logs
- Premarket/postmarket reports
- Weekly backtest analysis

---

## Schedule

The daemon runs on Eastern Time (`TZ=America/New_York`):

| Time | Event |
|------|-------|
| 8:30 AM | Premarket report |
| 9:35 AM | Full scan (debaters → consensus → sizing) |
| 9:30 AM–4:00 PM | Exit-only every minute |
| 4:15 PM | Postmarket report |
| 5:00 PM | EOD outcomes + daily learn (RAG learning loop) |
| 5:30 PM **Sunday only** | Weekly backtest analysis |
| Midnight | Daily reset |

---

## Verification Checklist

```bash
# 1. Build succeeds
docker compose build
# Expected: Image built with Python 3.12, supervisor, tradingbot package

# 2. Daemon starts and creates logs
docker compose up -d
sleep 5
ls logs/
# Expected: daemon.log, supervisord.log files exist

# 3. Verify mounts work
docker compose exec tradingbot-daemon ls /app/logs
docker compose exec tradingbot-daemon ls /data/tradingbot
# Expected: Directories listed

# 4. Check reports directory (appears at 8:30 AM ET on market days)
ls reports/
# Expected: subdirectories with dates (after daemon runs)

# 5. Inspect named volume
docker volume inspect tradingbot-data
# Expected: volume listed with local mount path

# 6. Logs show daemon running
docker compose logs tradingbot-daemon | head -20
# Expected: "Market monitor started" message
```

---

## Troubleshooting

### "Connection refused" at MOOMOO_OPEND_HOST:11111

**Problem:** Container can't reach moomoo OpenD.

**Solution:**
1. Ensure OpenD is running on the host machine (`netstat -an | grep 11111`)
2. Check `.env` has `MOOMOO_OPEND_HOST=host.docker.internal` (Windows/Mac) or the host's IP (Linux)
3. Ensure Docker Desktop's WSL2 backend is enabled (Windows)

### Logs not appearing in `./logs/`

**Problem:** Volume mount issue.

**Solution:**
```bash
# Check volume mount
docker compose exec tradingbot-daemon mount | grep logs

# Check container can write
docker compose exec tradingbot-daemon touch /app/logs/test.txt
ls logs/test.txt
```

### Knowledge base not persisting

**Problem:** Outcomes or calibration lost after restart.

**Solution:**
```bash
# Check named volume is mounted
docker compose exec tradingbot-daemon ls /data/tradingbot/outcomes/

# Backup volume (before deletion)
docker run --rm -v tradingbot-data:/data -v $(pwd)/backup:/backup \
  busybox tar czf /backup/tradingbot-data.tar.gz -C /data .
```

### Weekly analysis not running

**Problem:** analyze_weekly.py doesn't run on Sunday at 5:30 PM.

**Solution:**
1. Check container timezone is ET: `docker compose exec tradingbot-daemon date`
2. Check daemon log for "Weekly analysis trigger": `docker compose logs tradingbot-daemon | grep -i weekly`
3. Ensure `scripts/analyze_weekly.py` exists and is executable

---

## Advanced: Backup and Restore

### Backup tradingbot-data volume

```bash
docker run --rm -v tradingbot-data:/data -v $(pwd):/backup \
  busybox tar czf /backup/tradingbot-data-$(date +%Y%m%d).tar.gz -C /data .
```

### Restore from backup

```bash
docker run --rm -v tradingbot-data:/data -v $(pwd):/backup \
  busybox tar xzf /backup/tradingbot-data-20260520.tar.gz -C /data --strip-components=1
```

### Convert named volume to bind mount

If you prefer local filesystem instead of Docker-managed volume:

```yaml
# In docker-compose.yml, change:
volumes:
  - ./data/tradingbot:/data/tradingbot     # Local bind mount instead of named volume
```

Then remove the named volume:
```bash
docker volume rm tradingbot-data
```

---

## Files Reference

- **Dockerfile** — multi-stage (dev/prod targets), Python 3.12-slim + supervisord
- **supervisord.conf** — manages market_monitor.py + watchdog.py processes
- **docker-compose.yml** — production setup with env vars, volumes, health check
- **docker-compose.dev.yml** — dev override with hot-reload code mount
- **.dockerignore** — excludes .env, __pycache__, logs, reports, tests
- **scripts/market_monitor.py** — updated with Sunday weekly analysis trigger

---

## Commands Quick Reference

```bash
# Build
docker compose build

# Start/stop
docker compose up -d           # detached (production)
docker compose up              # foreground (development)
docker compose down

# Logs
docker compose logs -f         # follow all services
docker compose logs -f tradingbot-daemon --tail=100

# Debug
docker compose exec tradingbot-daemon bash      # shell into container
docker compose exec tradingbot-daemon python -c "import tradingbot; print(tradingbot.__version__)"

# Volume management
docker volume ls               # list volumes
docker volume inspect tradingbot-data # inspect named volume
docker volume rm tradingbot-data      # delete (destructive)

# Development reload
docker compose restart          # restart to apply code changes
docker compose -f docker-compose.yml -f docker-compose.dev.yml down --rmi local
```

---

## Next Steps

1. Ensure `.env` has valid moomoo credentials and `MOOMOO_OPEND_HOST=host.docker.internal`
2. Start moomoo OpenD on the host
3. Run `docker compose build` to validate the Dockerfile
4. Run `docker compose up -d` to start the daemon
5. Watch logs in `C:\Users\pamed\Susheel\tradingbot-docker\logs\daemon.log`
6. Verify premarket report appears in `C:\Users\pamed\Susheel\tradingbot-docker\reports\` at 8:30 AM ET
