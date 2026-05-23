# deploy.ps1 — Build, Deploy & Validate

One-stop PowerShell script that builds the Docker image, deploys the
`tradingbot-daemon` container, validates logs, optionally runs a full scan,
and generates the postmarket report.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker Desktop running | `docker info` must succeed |
| `.env` in repo root | Broker credentials + feature flags |
| PowerShell 5.1+ | Ships with Windows 10 |
| moomoo OpenD running | Only needed for live broker features |

Host directories are created automatically if they don't exist:

```
C:\Users\pamed\Susheel\tradingbot-docker\
  logs\        ← container log files (bind-mounted)
  reports\     ← generated HTML/MD reports
  data\tradingbot\  ← positions, backtest state, bandit priors
```

---

## Quick Start

```powershell
cd C:\Users\pamed\Susheel\oa2-new

# Full deploy: build → start → health check → report
.\deploy.ps1

# Deploy + run an on-demand full scan before the report
.\deploy.ps1 -RunScan

# Skip rebuild (use existing image), scan only SPY/QQQ
.\deploy.ps1 -SkipBuild -RunScan -ScanTickers "SPY,QQQ"

# Dry-run everything (nothing written to positions)
.\deploy.ps1 -RunScan -DryRun
```

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `-SkipBuild` | switch | off | Skip `docker build` — use the existing image |
| `-SkipReport` | switch | off | Skip postmarket report generation |
| `-RunScan` | switch | off | Run `paper_trade.py --full-scan` after deploy |
| `-ScanTickers` | string | *(all 22)* | Comma-separated tickers to scan, e.g. `"SPY,QQQ,AAPL"` |
| `-DryRun` | switch | off | Pass `--dry-run` to scan and report (no writes) |
| `-Tail` | int | 50 | Number of container log lines shown in summary |

---

## What It Does — Step by Step

```
0  Pre-flight       Docker daemon up? .env present? host dirs exist?
1  Build            docker build --target prod -t tradingbot-daemon:latest
2  Stop existing    docker compose down (removes old container cleanly)
3  Start            docker compose up -d
4  Health check     Polls daemon_heartbeat.txt up to 120s
5  Validate logs    Scans container logs + bind-mounted .log files for
                    ERROR / CRITICAL / Traceback lines
6  Connection       Test-NetConnection localhost:11111 (moomoo OpenD)
7  Scan (optional)  paper_trade.py --full-scan [--dry-run] [--tickers ...]
8  Report           report.py --mode postmarket inside container
9  Log tail         Last N lines, colour-coded
10 Summary          Container state, health, image, error count
```

---

## Output Colour Guide

| Colour | Meaning |
|---|---|
| Cyan | Step header |
| Green | OK — check passed |
| Yellow | Warning — non-fatal, needs review |
| Red | Error — scan/report output contains ERROR/CRITICAL |
| Dark grey | Informational log lines |

---

## Scan Details

When `-RunScan` is passed, the script executes inside the running container:

```bash
python scripts/paper_trade.py --full-scan [--dry-run] [--tickers ...]
```

This runs the full signal pipeline:

```
Debaters → Regime → Consensus → Sizing → Exit rules → Save positions
```

After the scan completes, the script reads the latest `positions_YYYY-MM-DD.json`
from the data directory and prints the position count.

The scan fires **before** report generation so the report reflects any new
positions written.

---

## Report Details

Runs `scripts/report.py --mode postmarket` inside the container.
Output files land in the bound `reports\` directory:

```
reports\
  insights_YYYY-MM-DD.md    ← narrative summary
  insights_YYYY-MM-DD.html  ← HTML version
```

---

## Troubleshooting

### Health check never passes
The daemon writes `logs/daemon_heartbeat.txt` every cycle. If it's missing
after 2 minutes, check supervisord output:

```powershell
docker logs tradingbot-daemon --tail 100
```

### moomoo OpenD not reachable
The script warns but does **not** abort — the daemon degrades gracefully
(positions fetched from JSON cache instead of live API). Start OpenD on
the host and the next cycle picks it up automatically.

### Scan fails with import errors
Run a smoke test inside the container first:

```powershell
docker exec tradingbot-daemon python scripts/smoke_test.py
```

### Report shows no positions
If the scan ran with `-DryRun`, nothing was written. Run without `-DryRun`
or confirm `positions_YYYY-MM-DD.json` exists in the data directory.

### Permission denied on host directories
Run PowerShell as Administrator once to create the bind-mount directories,
or create them manually before running the script.

---

## Common Workflows

### Morning deploy before market open
```powershell
.\deploy.ps1
```
Daemon starts and waits for the 9:35 AM scheduled scan.

### Force a scan right now (testing / after hours)
```powershell
.\deploy.ps1 -SkipBuild -RunScan
```

### Validate a code change without touching positions
```powershell
.\deploy.ps1 -RunScan -DryRun -Tail 100
```

### Quick redeploy after config change
```powershell
.\deploy.ps1 -SkipBuild -SkipReport
```

---

## Related Files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build (base / dev / prod targets) |
| `docker-compose.yml` | Service definition, volumes, health check |
| `supervisord.conf` | Manages `market_monitor` + `watchdog` inside container |
| `scripts/market_monitor.py` | Daemon scheduler (scan, exit, reports) |
| `scripts/paper_trade.py` | Full scan + exit-only runner |
| `scripts/report.py` | Pre/postmarket report generator |
| `docs/DAEMON.md` | Daemon architecture and scheduling details |
