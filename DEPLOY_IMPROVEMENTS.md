# Deploy Script Improvements

## Issues Fixed

✅ **Tests run by default** — Pytest validates codebase before every Docker build  
✅ **Docker validation built-in** — Tests always verified inside container environment  
✅ **Fail-fast behavior** — Deployment stops immediately if tests fail  
✅ **Safe production defaults** — Tests required unless explicitly skipped  

## How to Use

### Deploy with full validation (default, RECOMMENDED)
```powershell
.\deploy.ps1
```

This will:
1. Run pytest locally against the full test suite
2. If tests pass, build the Docker image (prod target)
3. Build the Docker pytest stage to validate the containerized environment
4. Start the container
5. Validate logs and generate reports

### Skip tests only when necessary
```powershell
.\deploy.ps1 -SkipTests
```

**⚠️ Warning:** Only use `-SkipTests` for emergency hotfixes or when tests already passed in CI

### Test without deploying
```powershell
pytest tests/ -v
```

Or use pytest inside Docker:
```powershell
docker build --target pytest -t tradingbot-daemon:test .
```

## Deployment Workflow

### Standard Production Deployment (RECOMMENDED)
```powershell
.\deploy.ps1
```

Automatically:
1. Runs local pytest (381 tests)
2. Verifies tests pass inside Docker
3. Builds prod image
4. Starts daemon and validates health

### Quick Deploy (reuse existing image)
```powershell
.\deploy.ps1 -SkipBuild
```

### Full Validation with Scan & Reports
```powershell
.\deploy.ps1 -RunScan -Tail 100
```

### Emergency Hotfix (skip tests only if needed)
```powershell
.\deploy.ps1 -SkipTests  # ⚠️ Only when absolutely necessary
```

## What Changed

### `deploy.ps1`
- Added `-RunTests` parameter
- Added Section 0.5: local pytest execution
- Added Section 1: Docker pytest stage validation
- Tests fail fast with clear error messages
- Helpful suggestions when not using `-RunTests`

### `Dockerfile`
- Added `pytest` stage (between base and dev)
- Installs test dependencies
- Runs full test suite during build
- Can be built independently: `docker build --target pytest`

## Test Coverage

The pytest stage runs all tests in `tests/`:
- test_bandit.py
- test_consensus_engine.py
- test_debaters_individual.py
- test_exit_engine.py
- test_flow_adapter.py
- test_pipeline_l6_l7_l8.py
- test_sizing.py
- test_structure_picker.py
- test_regime_classifier.py
- test_paper_trade_exits_and_carryover.py
- ...and more

## Benefits

1. **Catch bugs before deployment** — Tests run by default (not optional)
2. **Verify container environment** — Docker pytest stage validates setup
3. **Clear failure messages** — Know exactly why deploy failed
4. **Safe defaults** — Tests required for production safety
5. **Escape hatch available** — Can skip with `-SkipTests` for emergencies

## Example Usage

```powershell
# Standard deployment (tests run by default)
PS> .\deploy.ps1

==[ Pre-flight checks ]
  OK  Docker is running
  OK  Log directory exists
  OK  .env present

==[ Running pytest suite ]
  ✓ All tests passed (381 passed in 45.3s)

==[ Building Docker pytest stage... ]
  ✓ Docker pytest stage passed - container environment valid

==[ Building Docker image (prod target) ]
  ✓ Image built: tradingbot-daemon:latest

==[ Starting container via docker-compose ]
  ✓ Container started
  ✓ Health check passed

==[ Deployment Summary ]
  Container  : tradingbot-daemon
  Status     : running
  Health     : healthy
  Deploy complete - no errors detected.
```

```powershell
# Full validation with scan and reports
PS> .\deploy.ps1 -RunScan

[... all tests pass first ...]

==[ Running on-demand full scan ]
  ✓ Full scan completed
  ✓ Positions file: positions_2026-05-29.json [5 positions]

==[ Triggering report generation ]
  ✓ Report generation completed
  ✓ Recent reports: morning_briefing.html [125 KB]
```
