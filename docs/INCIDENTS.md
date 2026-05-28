# Incident & Implementation Records

Historical post-mortems and feature implementation notes. Newest first.

---

## May 28, 2026 — DTE Wiring Fix

**Commit:** `a929a7f`

### Problem

The structure picker always hardcoded `dte=30` regardless of the actual option expiry, so:
1. Kelly sizing was less conservative than warranted for near-dated options
2. `_recommend_expiry()` could still return 9-DTE options, which would immediately trigger the `DTE_EMERGENCY_THRESHOLD` exit rule on the same day they were entered

### Fix

- `pick_structure()` now accepts an `expiry` parameter and calculates real DTE from the ISO date
- `_pick_debit_vertical()` and `_pick_outright()` pass the real `expiry`/`dte` to `StructurePick`
- `_recommend_expiry()` now enforces a **14-day minimum entry** filter to prevent short-dated recommendations

### Files Changed

- `tradingbot/strategy/structure_picker.py` — expiry/dte parameters, real DTE calculation
- `tradingbot/graph/pipeline.py` — pass `recommended_expiry` to `pick_structure()`
- `tradingbot/dataflows/moomoo_data.py` — 14-day minimum entry in `_recommend_expiry()`

---

## May 27, 2026 — Exit Engine Configuration

**Commits:** (staged, not yet merged)

### Problem

Exit thresholds were hardcoded — no way to tune stop-loss, profit targets, or DTE triggers without editing source. DTE emergency exit only covered short-leg trades, not all structures.

### Fix

- All exit thresholds moved to environment variables (see `.env.example`)
- Structure-aware thresholds: `SHORT_PREMIUM_*` vs `LONG_OPTION_*` for profit targets and DTE
- DTE emergency exit now covers all structures
- Regime flip + direction conflict escalated from review flag to automatic forced close

### Key Env Vars Added

```
EXIT_STOP_LOSS_PCT=0.50
EXIT_PROFIT_TARGET_SHORT=0.50
EXIT_PROFIT_TARGET_LONG=0.35
DTE_EMERGENCY_SHORT=5
DTE_EMERGENCY_LONG=3
FRIDAY_SWEEP_WEEKS=3
```

---

## May 27, 2026 — Open Positions Telegram Fix

**Commit:** `ac0293a`

### Problem

Telegram position summary was filtering by `pnl > 0 AND contracts > 0`, hiding positions that were underwater or zero-contract. Users couldn't see the full picture.

### Fix

Removed the filter from `paper_trade.py` position summary. `PositionMonitor.all_positions` now returns unfiltered internal dict.

---

## May 27, 2026 — Scan Hang Fix

**Commit:** `fcb02d9`

### Problem

`paper_trade.py --full-scan` would hang for 30+ minutes. Root cause was moomoo `OpenQuoteContext` with no socket timeout — the connection would block indefinitely if OpenD was unreachable or slow.

### Fix (in `tradingbot/dataflows/moomoo_data.py`)

1. **OpenD reachability check** — socket probe before opening context; skip ticker if unreachable
2. **Per-call timeout** — `concurrent.futures.ThreadPoolExecutor` wraps each option chain fetch with a 30-second deadline
3. **Metadata fetch timeout** — `get_option_chain` metadata call also wrapped

---

## May 22, 2026 — Structure Picker Guard Gate

**Commit:** `1388df6`

### Problem

Pipeline Layer L6 (Sizing) ran even when L5b (Structure Picker) returned `no_viable_structure`. The approved trade then failed at broker submission because `long_strike` was None. System *looked* like it was working (logs showed approved) but submitted nothing.

### Fix

Added guard gate in `pipeline.py` before L6:

```python
structure_pick_status = ctx.attribution.get("structure_pick", {}).get("status")
if structure_pick_status == "no_viable_structure":
    ctx.sizing = {"approved": False, "reject_gate": "structure", ...}
```

Also added `long_strike` validation in `paper_trade.py` before broker submission as a second safety check.

---

## May 22, 2026 — Intelligent Expiry Selection

**Commit:** `0b48476`

### What Changed

Replaced hardcoded "next Friday" option selection with flow-driven expiration choice.

**Before:** Single chain fetched for next Friday only.  
**After:** 5 expirations fetched. Dominant smart-money DTE bucket determines which expiry to use.

### How It Works

```
L0: fetch_market_snapshot() fetches 5 chains (next Friday, 2nd, 3rd, 6w, 10w)
      _recommend_expiry() classifies dominant flow bucket
        front_week (0-7 DTE)  → index 0 (next Friday)
        near_term (8-21 DTE)  → index 1 (2nd Friday)
        mid_term (22-45 DTE)  → index 2 (3rd Friday / monthly)
        longer (46+ DTE)      → index 4 (10-week)

L5b: Structure picker receives recommended chain instead of hardcoded weekly
```

Fallback: if no dominant bucket, defaults to next Friday.

### Decision Record Fields Added

- `recommended_expiry` — which expiry was selected
- `chains_analyzed` — how many expirations were evaluated

---

## May 22, 2026 — Daemon Recovery (May 21 Incident)

**Commits:** `b145aa5`, `2304840`

### What Happened (May 21)

1. `FULL-SCAN` at 09:35 AM hung — subprocess blocked indefinitely
2. Heartbeat stopped updating → watchdog detected staleness
3. Supervisord killed `market_monitor` and `watchdog` at 10:02 AM
4. No trades executed all day; no alert sent

### Root Causes

- Heartbeat write was silently catching exceptions
- Telegram alerts existed in watchdog but triggered *after* failures, not during

### Fixes

**Heartbeat:** dedicated `_update_heartbeat()` with explicit error logging.

**Telegram alerts:** `_alert_telegram()` now fires immediately on FULL-SCAN timeout/failure/error.

**Watchdog alert improved:**
```
ALERT: oa2 daemon is STALE (no heartbeat for XXs)
Immediate Actions:
1. Check logs: tail -f logs/daemon.log
2. Restart: python scripts/market_monitor.py
3. Verify: no duplicate daemon instances running
```

**Market hours sleep:** daemon now sleeps until next market open (not every 60 seconds) on weekends/holidays. Uses `tradingbot/core/market_hours.py` — NYSE/NASDAQ holidays computed dynamically, no hardcoded list.

### Alert Types You Will Receive

| Alert | Trigger |
|-------|---------|
| Stale Daemon | No heartbeat for >5 minutes |
| Recovery | Daemon came back online |
| Health Confirmation | Every 1 hour (if `WATCHDOG_HEALTH_INTERVAL=3600`) |
| FULL-SCAN | Scan timeout or failure |
| POSTMARKET | Report generation failure |
