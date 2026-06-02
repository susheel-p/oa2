# Deploy Cheatsheet

## Common Scenarios

### ✅ Standard Deploy (Tests run by default)
```powershell
.\deploy.ps1
```
**When:** Normal deployment  
**Result:** Tests → Docker validation → Build → Start daemon → Validate health

---

### 📊 Full Validation (Tests + Scan + Reports)
```powershell
.\deploy.ps1 -RunScan -Tail 100
```
**When:** Need full pipeline validation with paper scan  
**Result:** Tests → Docker tests → Build → Scan 22 tickers → Generate reports

---

### 🎯 Specific Tickers Only
```powershell
.\deploy.ps1 -RunScan -ScanTickers "SPY,QQQ,IWM"
```
**When:** Testing specific positions  
**Result:** Tests → Docker validation → Build → Scan only SPY, QQQ, IWM

---

### 🧪 Dry Run (No Position Updates)
```powershell
.\deploy.ps1 -RunScan -DryRun
```
**When:** Validating pipeline without writing positions  
**Result:** Full simulation with tests, no trades executed

---

### 🔧 Quick Restart (Reuse Image)
```powershell
.\deploy.ps1 -SkipBuild
```
**When:** Image already built, just restart daemon  
**Result:** Skip build, restart container immediately (tests still run)

---

### 🚨 Emergency Hotfix (Skip Tests Only)
```powershell
.\deploy.ps1 -SkipTests
```
**When:** Critical fix needed and tests already passed in CI  
**Result:** Skip local/Docker tests, deploy immediately (⚠️ use sparingly)

---

### 📋 Run Tests Only (No Deploy)
```powershell
pytest tests/ -v --tb=short
```
**When:** Quick test validation  
**Result:** Run all tests, show failures with context

---

### 🐳 Test Inside Docker Only
```powershell
docker build --target pytest -t tradingbot-daemon:test .
```
**When:** Verify container environment compatibility  
**Result:** Build Docker pytest stage, validate all tests pass in container

---

### 📄 View Recent Logs (No Deploy)
```powershell
docker logs tradingbot-daemon --tail 50
```
**When:** Troubleshooting running container  
**Result:** Show last 50 container log lines

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | ✅ Deploy successful |
| `1` | ❌ Docker not running or missing .env |
| `1` | ❌ Tests failed (if -RunTests used) |
| `1` | ❌ Docker build failed |
| `1` | ❌ Container failed to start |
| `1` | ❌ Scan failed (if -RunScan used) |

## Key Parameters

| Flag | Effect | Default |
|------|--------|---------|
| `-SkipTests` | Skip pytest validation (⚠️ not recommended) | ❌ Tests enabled |
| `-SkipBuild` | Skip Docker build (reuse image) | ❌ Always build |
| `-RunScan` | Run full 22-ticker scan after deploy | ❌ Disabled |
| `-ScanTickers "SPY,QQQ"` | Limit scan to specific tickers | All 22 |
| `-DryRun` | Don't write positions (simulation mode) | ❌ Write positions |
| `-SkipReport` | Skip report generation | ❌ Always report |
| `-Tail 50` | Log lines to show (default 50) | `50` |

## Troubleshooting

### Tests fail locally but pass in Docker
```powershell
# Test inside Docker
docker build --target pytest -t tradingbot-daemon:test .
```

### Docker build fails but tests pass
```powershell
# Check Docker logs
docker logs tradingbot-daemon
```

### Container won't start
```powershell
# Verify .env file
Get-Content .env | head -20

# Check moomoo connection
Test-NetConnection -ComputerName localhost -Port 11111

# Verify volumes exist
Get-ChildItem "C:\Users\pamed\Susheel\tradingbot-docker\logs"
```

### No positions written after scan
```powershell
# Check if dry-run flag was used
# (Dry-run doesn't write positions)

# Verify scan ran with full output
.\deploy.ps1 -RunScan -Tail 200
```
