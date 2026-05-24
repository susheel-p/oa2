# Development Container Setup

This directory contains the development container configuration for oa2-trading.

## Quick Start

### Option 1: VSCode Remote Containers (Recommended)

1. Install [Remote - Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) in VSCode
2. Open the repo in VSCode
3. Press **Ctrl+Shift+P** and run: `Dev Containers: Reopen in Container`
4. Wait for the container to build and dependencies to install (~2-3 min)
5. You're now in the container — run tests, daemons, backtest as normal

### Option 2: Docker Compose (Manual)

```bash
# Build and start dev environment
docker-compose up -d devcontainer

# Enter the container
docker-compose exec devcontainer /bin/bash

# Run tests inside container
docker-compose exec devcontainer pytest tests/

# Run backtest
docker-compose exec devcontainer python scripts/backtest.py
```

### Option 3: Running Daemons

To run market monitor and paper trade daemons alongside dev container:

```bash
docker-compose --profile daemon up -d
```

This starts:
- `devcontainer` — main dev environment
- `market-monitor` — runs `scripts/market_monitor.py`
- `paper-trade` — runs `scripts/paper_trade.py`

View logs:
```bash
docker-compose logs -f market-monitor
docker-compose logs -f paper-trade
```

## Environment Variables

Create `.env` in the repo root with your configuration:
```
TRADINGBOT_HOME=/workspace/.tradingbot
MOOMOO_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
```

The `.env` file is automatically mounted into all containers via `docker-compose.yml`.

## What's Included

- **Python 3.11** with all dependencies from `pyproject.toml`
- **VSCode Extensions**: Pylance, Ruff linter, pytest integration
- **Git integration** for commit/push from within container
- **Port forwarding**: 8000-8002 for daemon services
- **SSH mounts** (readonly) for git/auth

## Cleanup

```bash
# Stop all services
docker-compose down

# Remove dev container from VSCode
Ctrl+Shift+P → Dev Containers: Remove Container

# Remove all images and volumes
docker system prune -a
```

## Troubleshooting

**Container takes forever to build:**
- First build downloads ~500MB of Python deps. Subsequent rebuilds are cached.
- Use `docker system prune` if disk space is tight.

**Port 8000/8001/8002 already in use:**
- Edit `docker-compose.yml` to change host port (left side of `:`)
- Or kill the process: `lsof -i :8000` (Mac/Linux) / `netstat -ano | findstr :8000` (Windows)

**SSH not working from container:**
- Ensure `~/.ssh` exists on your machine
- Check mount path in `devcontainer.json` matches your OS (Windows uses `%USERPROFILE%`, Unix uses `$HOME`)

**pytest not discovering tests:**
- Run from repo root: `python -m pytest tests/`
- Check `pyproject.toml` → `[tool.pytest.ini_options]`
