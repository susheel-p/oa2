#Requires -Version 5.1
<#
.SYNOPSIS
    Build, deploy, validate, and report for tradingbot-daemon.

.DESCRIPTION
    1. Build Docker image (prod target)
    2. Stop/remove any existing container
    3. Start container via docker-compose
    4. Wait for health check to pass
    5. Validate logs for errors
    6. Trigger on-demand full scan (optional)
    7. Trigger report generation
    8. Print final status summary

.PARAMETER SkipBuild
    Skip Docker image build (use existing image).

.PARAMETER SkipReport
    Skip report generation after deployment.

.PARAMETER RunScan
    Run an on-demand full scan (paper_trade.py --full-scan) after deploy.
    Useful outside of market hours to test the pipeline or force a signal run.

.PARAMETER ScanTickers
    Comma-separated ticker list to pass to the scan (e.g. "SPY,QQQ,AAPL").
    If omitted, scans the full 22-ticker watchlist.

.PARAMETER DryRun
    Pass --dry-run to both scan and report so nothing is written to positions.

.PARAMETER Tail
    Number of log lines to print in summary (default 50).
#>
param(
    [switch]$SkipBuild,
    [switch]$SkipReport,
    [switch]$RunScan,
    [string]$ScanTickers = "",
    [switch]$DryRun,
    [int]$Tail = 50
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CONTAINER   = "tradingbot-daemon"
$COMPOSE     = "docker-compose.yml"
$LOG_DIR     = "C:\Users\pamed\Susheel\tradingbot-docker\logs"
$REPORTS_DIR = "C:\Users\pamed\Susheel\tradingbot-docker\reports"
$DATA_DIR    = "C:\Users\pamed\Susheel\tradingbot-docker\data\tradingbot"

# --- Helpers -----------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==[ $msg ]" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "  OK  $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "  WARN  $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
}

function Assert-Docker {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Docker daemon is not running. Start Docker Desktop and retry."
        exit 1
    }
}

# --- 0. Pre-flight -----------------------------------------------------------

Write-Step "Pre-flight checks"
Assert-Docker

foreach ($dir in @($LOG_DIR, $REPORTS_DIR, $DATA_DIR)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-OK "Created $dir"
    } else {
        Write-OK "$dir exists"
    }
}

if (-not (Test-Path ".env")) {
    Write-Fail ".env file missing - broker credentials required."
    exit 1
}
Write-OK ".env present"

# --- 1. Build ----------------------------------------------------------------

if (-not $SkipBuild) {
    Write-Step "Building Docker image (prod target)"
    docker build --target prod -t "${CONTAINER}:latest" .
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker build failed."
        exit 1
    }
    Write-OK "Image built: ${CONTAINER}:latest"
} else {
    Write-Warn 'Skipping build (--SkipBuild)'
}

# --- 2. Stop existing container ----------------------------------------------

Write-Step "Stopping existing container (if any)"
$existing = docker ps -a --filter "name=^${CONTAINER}$" --format "{{.Names}}" 2>&1
if ($existing -match $CONTAINER) {
    try {
        docker compose -f $COMPOSE down --remove-orphans *>$null
    } catch {
        # Ignore harmless docker-compose warnings
    }
    Write-OK "Removed existing container"
} else {
    Write-OK "No existing container to remove"
}

# --- 3. Start container ------------------------------------------------------

Write-Step "Starting container via docker-compose"
try {
    docker compose -f $COMPOSE up -d 2>&1 | Out-Null
} catch {
    # Ignore harmless docker-compose warnings
}
# Check if container is actually running (ignore exit code since docker warnings cause false negatives)
Start-Sleep -Seconds 2
$running = docker ps --filter "name=$CONTAINER" --format "{{.Names}}" 2>&1 | Select-String $CONTAINER
if ($running) {
    Write-OK "Container started"
} else {
    Write-Fail "Container failed to start"
    exit 1
}

# --- 4. Wait for health check ------------------------------------------------

Write-Step "Waiting for health check to pass (up to 120s)"
$deadline = (Get-Date).AddSeconds(120)
$healthy  = $false

while ((Get-Date) -lt $deadline) {
    $status = docker inspect --format "{{.State.Health.Status}}" $CONTAINER 2>&1
    if ($status -eq "healthy") {
        $healthy = $true
        break
    }
    if ($status -eq "unhealthy") {
        Write-Fail "Container reported unhealthy."
        break
    }
    Write-Host "  ... status: $status" -ForegroundColor DarkGray
    Start-Sleep -Seconds 10
}

if ($healthy) {
    Write-OK "Health check passed"
} else {
    Write-Warn "Health check did not pass within timeout (status: $status) - continuing anyway"
}

# --- 5. Validate logs --------------------------------------------------------

Write-Step "Validating logs for errors"
Start-Sleep -Seconds 5  # give supervisord a moment to flush

# supervisord / app logs written by the container
$containerLogs = docker logs $CONTAINER --tail 200 2>&1
$errorLines = @($containerLogs | Where-Object { $_ -match "\b(ERROR|CRITICAL|Traceback|Exception|Fatal)\b" })

if ($errorLines.Count -gt 0) {
    Write-Warn "$($errorLines.Count) error-level lines found in container logs:"
    $errorLines | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
} else {
    Write-OK "No ERROR/CRITICAL lines in container logs"
}

# Check bind-mounted log files
$logFiles = Get-ChildItem -Path $LOG_DIR -Filter "*.log" -ErrorAction SilentlyContinue
if ($logFiles) {
    $fileErrors = @()
    foreach ($f in $logFiles) {
        $hits = Select-String -Path $f.FullName -Pattern "\b(ERROR|CRITICAL|Traceback)\b" -SimpleMatch | Select-Object -Last 5
        if ($hits) { $fileErrors += $hits }
    }
    if ($fileErrors.Count -gt 0) {
        Write-Warn "$($fileErrors.Count) error-level lines in log files:"
        $fileErrors | ForEach-Object { Write-Host "    $($_.Filename):$($_.LineNumber)  $($_.Line)" -ForegroundColor Yellow }
    } else {
        Write-OK "No errors in bound log files ($($logFiles.Count) files checked)"
    }
} else {
    Write-Warn "No .log files found in $LOG_DIR yet"
}

# Check heartbeat
$hb = Join-Path $LOG_DIR "daemon_heartbeat.txt"
if (Test-Path $hb) {
    $hbAge = (Get-Date) - (Get-Item $hb).LastWriteTime
    if ($hbAge.TotalMinutes -lt 5) {
        Write-OK "Heartbeat fresh ($([int]$hbAge.TotalSeconds)s ago)"
    } else {
        Write-Warn "Heartbeat stale ($([int]$hbAge.TotalMinutes)m ago)"
    }
} else {
    Write-Warn "Heartbeat file not found yet (daemon may still be initializing)"
}

# --- 6. Connection check (moomoo OpenD) -------------------------------------

Write-Step "Checking moomoo OpenD connection (port 11111)"
$tcpTest = Test-NetConnection -ComputerName localhost -Port 11111 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($tcpTest) {
    Write-OK "moomoo OpenD reachable on localhost:11111"
} else {
    Write-Warn "moomoo OpenD NOT reachable on localhost:11111 - broker features will degrade gracefully"
}

# --- 7. On-demand full scan --------------------------------------------------

if ($RunScan) {
    Write-Step "Running on-demand full scan inside container"

    $scanArgs = '--full-scan'
    if ($DryRun)       { $scanArgs += " --dry-run" }
    if ($ScanTickers)  { $scanArgs += " --tickers $($ScanTickers -replace ',', ' ')" }

    $scanCmd = "cd /app; python scripts/paper_trade.py $scanArgs 2>&1"
    Write-Host "  Command: paper_trade.py $scanArgs" -ForegroundColor DarkGray

    $scanOutput = docker exec $CONTAINER bash -c $scanCmd
    $scanOutput | ForEach-Object {
        if ($_ -match "\b(ERROR|CRITICAL|Traceback)\b") {
            Write-Host "    $_" -ForegroundColor Red
        } elseif ($_ -match "\bWARN(ING)?\b") {
            Write-Host "    $_" -ForegroundColor Yellow
        } else {
            Write-Host "    $_" -ForegroundColor DarkGray
        }
    }

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Full scan completed"
        # Show any positions written
        $posFile = Get-ChildItem -Path $DATA_DIR -Filter "positions_*.json" -ErrorAction SilentlyContinue |
                   Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($posFile) {
            $pos = Get-Content $posFile.FullName | ConvertFrom-Json
            $count = if ($pos -is [array]) { $pos.Count } else { ($pos | Get-Member -MemberType NoteProperty).Count }
            $unit = if ($count -eq 1) { "position" } else { "positions" }
            Write-OK "Positions file: $($posFile.Name) [$count $unit]"
        }
    } else {
        Write-Warn "Full scan returned non-zero exit code - check logs above"
    }
} else {
    Write-Host "  (Use -RunScan to trigger an on-demand full scan)" -ForegroundColor DarkGray
}

# --- 9. Generate / update reports -------------------------------------------

if (-not $SkipReport) {
    Write-Step "Triggering report generation inside container"
    $reportCmd = "cd /app; python scripts/report.py --mode postmarket 2>&1 | tail -30"
    docker exec $CONTAINER bash -c $reportCmd
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Report generation completed"
    } else {
        Write-Warn "Report generation returned non-zero exit code"
    }

    # List generated reports
    $rpts = Get-ChildItem -Path $REPORTS_DIR -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10
    if ($rpts) {
        Write-OK "Recent reports:"
        $rpts | ForEach-Object { Write-Host "    $($_.LastWriteTime.ToString('HH:mm'))  $($_.Name)  [$([int]($_.Length/1KB)) KB]" }
    } else {
        Write-Warn "No report files found in $REPORTS_DIR"
    }
} else {
    Write-Warn 'Skipping report generation (--SkipReport)'
}

# --- 10. Final tail of container logs ----------------------------------------

Write-Step "Last $Tail lines of container logs"
docker logs $CONTAINER --tail $Tail 2>&1 | ForEach-Object {
    if ($_ -match "\b(ERROR|CRITICAL|Traceback)\b") {
        Write-Host $_ -ForegroundColor Red
    } elseif ($_ -match "\bWARN(ING)?\b") {
        Write-Host $_ -ForegroundColor Yellow
    } else {
        Write-Host $_ -ForegroundColor DarkGray
    }
}

# --- 11. Summary --------------------------------------------------------------

Write-Step "Deployment Summary"
$info = docker inspect $CONTAINER | ConvertFrom-Json
$state = $info[0].State
Write-Host "  Container : $CONTAINER"
Write-Host "  Status    : $($state.Status)"
Write-Host "  Health    : $($state.Health.Status)"
Write-Host "  Started   : $($state.StartedAt)"
Write-Host "  Image     : $((docker inspect --format '{{.Config.Image}}' $CONTAINER))"
if ($errorLines.Count -eq 0) {
    Write-OK "Deploy complete - no errors detected."
} else {
    Write-Warn "Deploy complete - $($errorLines.Count) warning(s) need review."
}
