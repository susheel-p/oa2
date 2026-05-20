# Multi-stage build for oa2 daemon
# Stage 1: base — Python environment + dependencies
FROM python:3.12-slim AS base

# Install tzdata for America/New_York timezone (required for market hours)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
ENV TZ=America/New_York

WORKDIR /app

# Install supervisor for process management (market_monitor + watchdog)
RUN pip install --no-cache-dir supervisor

# Copy dependency spec first for better layer caching
COPY pyproject.toml .
COPY oa2/ oa2/
COPY scripts/ scripts/
COPY docs/ docs/
COPY supervisord.conf .

# Install package with broker extra (includes moomoo-api)
RUN pip install --no-cache-dir -e ".[broker]"

# Create logs and reports directories (will be overridden by volume mounts)
RUN mkdir -p /app/logs /app/reports

# ---
# Stage 2: dev — for local development with hot-reload
# ---
FROM base AS dev
# Code is bind-mounted at runtime, no COPY needed
CMD ["python", "scripts/market_monitor.py"]

# ---
# Stage 3: prod — production image with supervisord
# ---
FROM base AS prod
# Copy entire codebase (not in dev to enable hot-reload)
COPY . .

# Create logs/reports dirs in case they don't exist
RUN mkdir -p /app/logs /app/reports

# Run under supervisord to manage both market_monitor + watchdog
CMD ["supervisord", "-c", "/app/supervisord.conf", "-n"]
