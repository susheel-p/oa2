# Deploy Quick Start

## The One Command You Need

```powershell
.\deploy.ps1
```

**That's it.** Tests run automatically. You're done.

---

## If You Need More Options

| Need | Command |
|------|---------|
| **Standard deploy** | `.\deploy.ps1` |
| **+ Paper scan** | `.\deploy.ps1 -RunScan` |
| **+ Full output** | `.\deploy.ps1 -RunScan -Tail 200` |
| **Specific tickers** | `.\deploy.ps1 -RunScan -ScanTickers "SPY,QQQ"` |
| **Dry run (no trades)** | `.\deploy.ps1 -RunScan -DryRun` |
| **Skip rebuild** | `.\deploy.ps1 -SkipBuild` |
| **Emergency only** | `.\deploy.ps1 -SkipTests` |

---

## What Happens When You Run It

```
✓ Pre-flight checks (Docker, .env, directories)
✓ Local pytest (381 tests)
✓ Docker pytest stage (tests in container)
✓ Build Docker image
✓ Start container
✓ Wait for health check
✓ Validate logs
✓ Show summary

Total time: ~5-10 minutes
```

---

## Success Indicators

```
  OK  All tests passed
  OK  Docker pytest stage passed
  OK  Image built: tradingbot-daemon:latest
  OK  Container started
  OK  Health check passed
  OK  Deploy complete - no errors detected.
```

---

## If Something Fails

1. **Tests fail** → Fix code, run again
2. **Docker build fails** → Check logs, fix dependencies
3. **Container won't start** → Check .env, verify moomoo connection
4. **Health check fails** → Wait longer, check logs

---

## Getting Help

- **See all options:** `Get-Help .\deploy.ps1 -Full`
- **Quick reference:** See DEPLOY_CHEATSHEET.md
- **Full guide:** See DEPLOY_IMPROVEMENTS.md
- **Detailed analysis:** See DEPLOY_REVIEW.md

---

## Common Mistakes to Avoid

❌ Don't use `-SkipTests` unless it's a real emergency  
❌ Don't skip the health check  
❌ Don't forget to update .env if broker credentials change  
✅ Always run tests locally first  
✅ Use `-RunScan` to verify paper trading works  
✅ Check logs if anything looks wrong  

---

## Pro Tips

**Faster development:**
```powershell
# Skip docker rebuild but run tests
.\deploy.ps1 -SkipBuild
```

**Full validation:**
```powershell
# Run tests + scan + reports + 100 log lines
.\deploy.ps1 -RunScan -Tail 100
```

**Test before deploy:**
```powershell
# Just run pytest locally
pytest tests/ -v
```

---

## That's All You Need to Know

1. Run: `.\deploy.ps1`
2. Wait for tests to pass
3. Daemon is deployed
4. Done! ✅
