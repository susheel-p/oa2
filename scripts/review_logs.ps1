#Requires -Version 5.1
<#
.SYNOPSIS
    Daily log review for tradingbot-daemon.

.PARAMETER Date
    Date to review in YYYY-MM-DD format. Defaults to today.

.PARAMETER LogDir
    Path to the log directory. Defaults to the Docker volume mount.

.EXAMPLE
    .\scripts\review_logs.ps1
    .\scripts\review_logs.ps1 -Date 2026-05-29
#>
param(
    [string]$Date   = (Get-Date -Format "yyyy-MM-dd"),
    [string]$LogDir = "C:\Users\pamed\Susheel\tradingbot-docker\data\tradingbot\logs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

$C = @{
    Header  = "Cyan"
    Section = "DarkCyan"
    OK      = "Green"
    Warn    = "Yellow"
    Err     = "Red"
    Dim     = "DarkGray"
    Bull    = "Green"
    Bear    = "Red"
    Neutral = "Yellow"
    White   = "White"
    Mag     = "Magenta"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Banner([string]$text) {
    $line = "=" * 80
    Write-Host ""
    Write-Host $line                    -ForegroundColor $C.Header
    Write-Host ("  " + $text)           -ForegroundColor $C.Header
    Write-Host $line                    -ForegroundColor $C.Header
}

function Write-Section([string]$text) {
    $pad = "-" * ([Math]::Max(0, 74 - $text.Length))
    Write-Host ""
    Write-Host ("-- $text $pad")        -ForegroundColor $C.Section
}

function Write-Row([string]$label, [string]$value, [string]$color = "White") {
    Write-Host ("  {0,-28} {1}" -f $label, $value) -ForegroundColor $color
}

function Write-TableHeader([string[]]$cols, [int[]]$widths) {
    $hdr = "  "
    for ($i = 0; $i -lt $cols.Count; $i++) {
        $hdr += ("{0,-$($widths[$i])}  " -f $cols[$i])
    }
    Write-Host $hdr                     -ForegroundColor $C.Section
    Write-Host ("  " + ("-" * ($hdr.Length - 2))) -ForegroundColor $C.Dim
}

function Get-DirColor([string]$dir) {
    if ($dir -eq "BULLISH") { return $C.Bull }
    if ($dir -eq "BEARISH") { return $C.Bear }
    return $C.Neutral
}

function Get-StatusColor([string]$status) {
    switch ($status) {
        "sized_approved" { return $C.OK      }
        "sized_rejected" { return $C.Warn    }
        "full_pipeline"  { return $C.Neutral }
        "debaters_only"  { return $C.Dim     }
        "scaffold_only"  { return $C.Dim     }
        "error"          { return $C.Err     }
        default          { return $C.White   }
    }
}

function Read-Jsonl([string]$path) {
    if (-not (Test-Path $path)) { return @() }
    $out = [System.Collections.Generic.List[object]]::new()
    foreach ($line in (Get-Content $path -Encoding UTF8)) {
        $line = $line.Trim()
        if ($line -eq "") { continue }
        try { $out.Add(($line | ConvertFrom-Json)) } catch {}
    }
    return $out.ToArray()
}

function Read-Json([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    try { return (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function Make-Bar([int]$count, [int]$scale = 1) {
    return "#" * [Math]::Max(0, $count * $scale)
}

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

$paths = @{
    scan       = Join-Path $LogDir "paper_trade_$Date.jsonl"
    premarket  = Join-Path $LogDir "paper_trade_${Date}_premarket.jsonl"
    executions = Join-Path $LogDir "executions_$Date.jsonl"
    exits      = Join-Path $LogDir "exit_alerts_$Date.jsonl"
    positions  = Join-Path $LogDir "positions_$Date.json"
    summary    = Join-Path $LogDir "summary_$Date.json"
}

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

$scan       = Read-Jsonl $paths.scan
$premarket  = Read-Jsonl $paths.premarket
$executions = Read-Jsonl $paths.executions
$exitAlerts = Read-Jsonl $paths.exits
$positions  = Read-Json  $paths.positions
$summary    = Read-Json  $paths.summary

# Merge premarket + main scan; keep latest record per ticker
$allScans = @()
if ($premarket.Count -gt 0) { $allScans += $premarket }
if ($scan.Count -gt 0)      { $allScans += $scan }

$byTicker = @{}
foreach ($r in $allScans) {
    $t = $r.ticker
    if (-not $byTicker.ContainsKey($t) -or $r.ts -gt $byTicker[$t].ts) {
        $byTicker[$t] = $r
    }
}
$tickerRecords = @($byTicker.Values | Sort-Object ticker)

if ($tickerRecords.Count -eq 0 -and $summary -eq $null) {
    Write-Host ""
    Write-Host "  No logs found for $Date in $LogDir" -ForegroundColor $C.Err
    Write-Host "  Expected: $($paths.scan)" -ForegroundColor $C.Dim
    exit 1
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

Write-Banner "tradingbot daily review  |  $Date"

# ---------------------------------------------------------------------------
# 1. Summary
# ---------------------------------------------------------------------------

Write-Section "Run Summary"

if ($summary) {
    $approvedColor = if ($summary.approved_count -gt 0) { $C.OK   } else { $C.Warn }
    $errorColor    = if ($summary.error_count    -gt 0) { $C.Err  } else { $C.Dim  }

    Write-Row "Date"            $summary.date
    Write-Row "Run timestamp"   $summary.run_ts
    Write-Row "Account size"    ('${0:N0}' -f [double]$summary.account_size)
    Write-Row "Tickers scanned" "$($summary.tickers_scanned)"
    Write-Row "Approved trades" "$($summary.approved_count)"  $approvedColor
    Write-Row "Rejected"        "$($summary.rejected_count)"  $C.Warn
    Write-Row "Errors"          "$($summary.error_count)"     $errorColor
    Write-Row "Exit alerts"     "$($summary.exit_alert_count)"

    if ($summary.book_state) {
        $b = $summary.book_state
        Write-Host ""
        Write-Host "  Book state:" -ForegroundColor $C.Section
        Write-Row "  Open positions" "$($b.position_count)"
        Write-Row "  Net delta"      ("{0:+0.00;-0.00}" -f [double]$b.net_delta)
        Write-Row "  Net vega"       ("{0:+0.00;-0.00}" -f [double]$b.net_vega)
        Write-Row "  Net theta"      ("{0:+0.00;-0.00}" -f [double]$b.net_theta)
    }
} else {
    Write-Host "  summary_$Date.json not found" -ForegroundColor $C.Warn
    Write-Row "Tickers in scan" "$($tickerRecords.Count)"
}

# ---------------------------------------------------------------------------
# 2. Per-ticker scan table
# ---------------------------------------------------------------------------

Write-Section "Ticker Scan Results  ($($tickerRecords.Count) tickers)"

if ($tickerRecords.Count -gt 0) {

    $cols   = @("TICKER","STATUS","DIR","SCORE","p_bull","REGIME","STRUCTURE","REJECT / NOTE")
    $widths = @(7,        17,      8,    7,      7,       10,      22,         30)
    Write-TableHeader $cols $widths

    foreach ($r in $tickerRecords) {
        $status = $r.status
        $dir    = if ($r.consensus) { $r.consensus.direction } else { "-" }
        $score  = if ($r.consensus) { "{0:0.000}" -f [double]$r.consensus.score } else { "-" }
        $pbull  = if ($r.consensus) { "{0:0.000}" -f [double]$r.consensus.p_bull } else { "-" }
        $regId  = if ($r.regime)    { $r.regime.regime_id }   else { "-" }
        $vol    = if ($r.regime)    { $r.regime.vol_state }   else { "" }
        $regime = "R$regId $vol"

        $structPick = "-"
        if ($r.structure_pick) {
            $sp = $r.structure_pick
            if ($sp.status -eq "no_viable_structure") { $structPick = "no_viable_structure" }
            elseif ($sp.structure)                    { $structPick = $sp.structure }
        }

        $rejectNote = ""
        if ($r.sizing -and -not $r.sizing.approved) {
            $gate   = if ($r.sizing.reject_gate)   { $r.sizing.reject_gate }   else { "" }
            $reason = if ($r.sizing.reject_reason) { $r.sizing.reject_reason } else { "" }
            if ($gate -eq "structure") {
                $rejectNote = "no viable structure"
            } else {
                $short = if ($reason.Length -gt 28) { $reason.Substring(0,27) + "~" } else { $reason }
                $rejectNote = if ($gate) { "[$gate] $short" } else { $short }
            }
        }
        if ($r.error) {
            $errStr = $r.error.ToString()
            $rejectNote = if ($errStr.Length -gt 28) { $errStr.Substring(0,27) + "~" } else { $errStr }
        }

        $sColor = Get-StatusColor $status
        $dColor = Get-DirColor    $dir

        $noteColor = if ($rejectNote -ne "") { $C.Warn } else { $C.Dim }

        Write-Host ("  {0,-7}  " -f $r.ticker)   -ForegroundColor $C.White   -NoNewline
        Write-Host ("{0,-17}  "  -f $status)      -ForegroundColor $sColor    -NoNewline
        Write-Host ("{0,-8}"     -f $dir)         -ForegroundColor $dColor    -NoNewline
        Write-Host ("  {0,-7}  {1,-7}  {2,-10}  {3,-22}  " -f $score, $pbull, $regime, $structPick) `
                   -ForegroundColor $C.Dim -NoNewline
        Write-Host $rejectNote -ForegroundColor $noteColor
    }

    # Status breakdown bar chart
    $statusGroups = @($tickerRecords | Group-Object status)
    Write-Host ""
    Write-Host "  Status breakdown:" -ForegroundColor $C.Dim
    foreach ($g in ($statusGroups | Sort-Object Count -Descending)) {
        $bar   = Make-Bar $g.Count
        $color = Get-StatusColor $g.Name
        Write-Host ("    {0,-20} {1,2}  {2}" -f $g.Name, $g.Count, $bar) -ForegroundColor $color
    }
}

# ---------------------------------------------------------------------------
# 3. Debater weights
# ---------------------------------------------------------------------------

$sampleW = @($tickerRecords | Where-Object { $_.consensus -and $_.consensus.weights }) | Select-Object -First 1
if ($sampleW) {
    Write-Section "Debater Weights  (sample: $($sampleW.ticker))"
    $w = $sampleW.consensus.weights
    foreach ($d in @("directional","income","volatility","flow","sentiment","dealer")) {
        $wv    = [double]($w.$d)
        $bar   = Make-Bar ([Math]::Round($wv * 40))
        $pct   = "{0:0.0%}" -f $wv
        $color = if ($wv -gt 0.3) { $C.OK } elseif ($wv -gt 0.1) { $C.White } else { $C.Dim }
        Write-Host ("  {0,-14} {1,6}  {2}" -f $d, $pct, $bar) -ForegroundColor $color
    }
    $calib = $sampleW.consensus
    Write-Host ""
    Write-Host ("  Calibrator: mode={0}  n_samples={1}" -f $calib.calibrator_mode, $calib.calibrator_n_samples) `
               -ForegroundColor $C.Dim
}

# ---------------------------------------------------------------------------
# 4. Approved trades
# ---------------------------------------------------------------------------

$approved = @($tickerRecords | Where-Object { $_.status -eq "sized_approved" })
Write-Section "Approved Trades  ($($approved.Count))"

if ($approved.Count -eq 0) {
    Write-Host "  No trades approved this session." -ForegroundColor $C.Warn
} else {
    $cols   = @("TICKER","DIR","STRUCTURE","CTRS","PREMIUM","MAX_RISK","DELTA","DTE","REGIME")
    $widths = @(7,        8,    24,         5,     9,        9,         8,      12,   7)
    Write-TableHeader $cols $widths

    foreach ($r in $approved) {
        $d    = $r.decision
        $dir  = $d.direction
        $str  = if ($d.sizing_structure) { $d.sizing_structure }
                elseif ($r.structure_pick -and $r.structure_pick.structure) { $r.structure_pick.structure }
                else { "-" }
        $ctr  = $d.contracts
        $prem = if ($r.entry_premium -and [double]$r.entry_premium -gt 0) {
                    '${0:N0}' -f ([double]$r.entry_premium * 100 * [int]$ctr)
                } else { "-" }
        $risk = if ($d.max_dollars_at_risk) { '${0:N0}' -f [double]$d.max_dollars_at_risk } else { "-" }
        $dlt  = if ($r.delta_per_contract)  { "{0:+0.00}" -f ([double]$r.delta_per_contract * [int]$ctr) } else { "-" }
        $dte  = if ($d.recommended_expiry)  { $d.recommended_expiry } else { "-" }
        $reg  = "R$($d.regime_id)"

        $dColor = Get-DirColor $dir
        Write-Host ("  {0,-7}  " -f $r.ticker) -ForegroundColor $C.White -NoNewline
        Write-Host ("{0,-8}  " -f $dir)         -ForegroundColor $dColor  -NoNewline
        Write-Host ("{0,-24}  {1,-5}  {2,-9}  {3,-9}  {4,-8}  {5,-12}  {6}" -f $str,$ctr,$prem,$risk,$dlt,$dte,$reg) `
                   -ForegroundColor $C.OK
    }
}

# ---------------------------------------------------------------------------
# 5. Executions
# ---------------------------------------------------------------------------

Write-Section "Broker Executions  ($($executions.Count) events)"

if ($executions.Count -eq 0) {
    Write-Host "  No execution events logged for $Date." -ForegroundColor $C.Dim
} else {
    $cols   = @("TIME","EVENT","TICKER","LEGS","CTRS","P&L","REASON","FILLS")
    $widths = @(8,      6,      7,       24,    5,     10,    16,      12)
    Write-TableHeader $cols $widths

    foreach ($e in ($executions | Sort-Object ts)) {
        $time   = try { ([datetime]::Parse($e.ts)).ToString("HH:mm:ss") } catch { $e.ts }
        $event  = $e.event
        $ticker = $e.ticker
        $ctr    = $e.contracts

        $legSummary = "-"
        if ($e.legs -and $e.legs.Count -gt 0) {
            $parts = @()
            foreach ($leg in $e.legs) {
                if ($leg.error) { $parts += "ERR"; continue }
                $side = if ($leg.side -eq 1) { "B" } else { "S" }
                $parts += "$side$($leg.strike)$($leg.right)"
            }
            $raw = $parts -join "|"
            $legSummary = if ($raw.Length -gt 24) { $raw.Substring(0,23) + "~" } else { $raw }
        }

        $pnlStr   = "-"
        $pnlColor = $C.Dim
        if ($null -ne $e.current_pnl) {
            $pnl    = [double]$e.current_pnl
            $pnlStr = "{0:+0.00;-0.00}" -f $pnl
            $pnlColor = if ($pnl -ge 0) { $C.OK } else { $C.Err }
        }

        $reason = if ($e.exit_reason) { $e.exit_reason } else { "-" }

        $fillStatus = "-"
        if ($e.legs -and $e.legs.Count -gt 0) {
            $sts = @($e.legs | Where-Object { $_.status } | ForEach-Object { $_.status } | Select-Object -Unique)
            if ($sts.Count -gt 0) { $fillStatus = $sts -join "/" }
        }

        $eColor = switch ($event) { "ENTRY" { $C.OK } "EXIT" { $C.Mag } default { $C.White } }

        Write-Host ("  {0,-8}  " -f $time)   -ForegroundColor $C.Dim  -NoNewline
        Write-Host ("{0,-6}  "   -f $event)  -ForegroundColor $eColor -NoNewline
        Write-Host ("{0,-7}  {1,-24}  {2,-5}  " -f $ticker, $legSummary, $ctr) -ForegroundColor $C.White -NoNewline
        Write-Host ("{0,-10}  " -f $pnlStr)  -ForegroundColor $pnlColor -NoNewline
        Write-Host ("{0,-16}  {1}" -f $reason, $fillStatus) -ForegroundColor $C.Dim
    }
}

# ---------------------------------------------------------------------------
# 6. Exit alerts
# ---------------------------------------------------------------------------

Write-Section "Exit Alerts  ($($exitAlerts.Count))"

if ($exitAlerts.Count -eq 0) {
    Write-Host "  No exit alerts for $Date." -ForegroundColor $C.Dim
} else {
    $cols   = @("TIME","TICKER","TRADE_ID","REASON","URGENCY","P&L","DTE")
    $widths = @(8,      7,       20,        18,       10,       10,    5)
    Write-TableHeader $cols $widths

    foreach ($a in ($exitAlerts | Sort-Object ts)) {
        $time    = try { ([datetime]::Parse($a.ts)).ToString("HH:mm:ss") } catch { $a.ts }
        $ticker  = $a.ticker
        $tid     = if ($a.trade_id) { $a.trade_id.Substring(0, [Math]::Min(18, $a.trade_id.Length)) } else { "-" }
        $reason  = if ($a.reason)   { $a.reason }  else { "-" }
        $urgency = if ($a.urgency)  { $a.urgency } else { "-" }
        $pnlStr  = "-"; $pnlColor = $C.Dim
        if ($null -ne $a.current_pnl) {
            $pnl    = [double]$a.current_pnl
            $pnlStr = "{0:+0.00;-0.00}" -f $pnl
            $pnlColor = if ($pnl -ge 0) { $C.OK } else { $C.Err }
        }
        $dte = if ($null -ne $a.current_dte) { "$($a.current_dte)" } else { "-" }

        $uColor = switch ($urgency) {
            "immediate" { $C.Err     }
            "execute"   { $C.Warn    }
            "evaluate"  { $C.Neutral }
            default     { $C.Dim     }
        }
        $dteColor = $C.Dim
        if ($dte -ne "-") {
            $dteInt = [int]$dte
            $dteColor = if ($dteInt -le 0) { $C.Err } elseif ($dteInt -le 5) { $C.Warn } else { $C.Dim }
        }

        Write-Host ("  {0,-8}  {1,-7}  {2,-20}  " -f $time, $ticker, $tid) -ForegroundColor $C.White  -NoNewline
        Write-Host ("{0,-18}  " -f $reason)   -ForegroundColor $uColor    -NoNewline
        Write-Host ("{0,-10}  " -f $urgency)  -ForegroundColor $uColor    -NoNewline
        Write-Host ("{0,-10}  " -f $pnlStr)   -ForegroundColor $pnlColor  -NoNewline
        Write-Host ("{0}"       -f $dte)      -ForegroundColor $dteColor
    }
}

# ---------------------------------------------------------------------------
# 7. Open positions
# ---------------------------------------------------------------------------

$openPos = @()
if ($null -ne $positions) {
    if ($positions -is [array]) { $openPos = $positions }
    else { $openPos = @($positions) }
}

Write-Section "Open Positions  ($($openPos.Count))"

if ($openPos.Count -eq 0) {
    Write-Host "  No open positions." -ForegroundColor $C.Dim
} else {
    $cols   = @("TICKER","STRUCTURE","CTRS","P&L","DTE","ENTRY_PRICE","DELTA","THETA","DIR")
    $widths = @(7,        24,         5,     10,    5,    12,           8,      8,      10)
    Write-TableHeader $cols $widths

    foreach ($p in $openPos) {
        $ticker = $p.ticker
        $struct = $p.structure
        $ctrs   = $p.contracts
        $pnl    = [double]$p.current_pnl
        $dte    = $p.current_dte
        $ep     = if ($p.entry_price) { '${0:N2}' -f [double]$p.entry_price } else { "-" }
        $dlt    = if ($p.delta)       { "{0:+0.00}" -f [double]$p.delta }     else { "-" }
        $tht    = if ($p.theta)       { "{0:+0.00}" -f [double]$p.theta }     else { "-" }
        $dir    = if ($p.direction)   { $p.direction } else { "-" }

        $pnlFmt   = "{0:+0.00;-0.00}" -f $pnl
        $pnlColor = if ($pnl -ge 0) { $C.OK } else { $C.Err }
        $dteInt   = try { [int]$dte } catch { 999 }
        $dteColor = if ($dteInt -le 0) { $C.Err } elseif ($dteInt -le 5) { $C.Warn } elseif ($dteInt -le 14) { $C.Neutral } else { $C.White }
        $dColor   = Get-DirColor $dir

        Write-Host ("  {0,-7}  {1,-24}  {2,-5}  " -f $ticker, $struct, $ctrs) -ForegroundColor $C.White  -NoNewline
        Write-Host ("{0,-10}  " -f $pnlFmt)   -ForegroundColor $pnlColor  -NoNewline
        Write-Host ("{0,-5}  "  -f $dte)      -ForegroundColor $dteColor  -NoNewline
        Write-Host ("{0,-12}  {1,-8}  {2,-8}  " -f $ep, $dlt, $tht) -ForegroundColor $C.Dim  -NoNewline
        Write-Host $dir -ForegroundColor $dColor
    }
}

# ---------------------------------------------------------------------------
# 8. Regime distribution
# ---------------------------------------------------------------------------

Write-Section "Regime Distribution"

$regimeMap = @{
    "0" = "high-vol  bearish"
    "1" = "high-vol  neutral"
    "2" = "high-vol  bullish"
    "3" = "norm-vol  bearish"
    "4" = "norm-vol  neutral"
    "5" = "norm-vol  bullish"
    "6" = "low-vol   bearish"
    "7" = "low-vol   neutral"
    "8" = "low-vol   bullish"
}

$regGroups = @($tickerRecords | Where-Object { $_.regime } | Group-Object { $_.regime.regime_id })
if ($regGroups.Count -gt 0) {
    foreach ($g in ($regGroups | Sort-Object Count -Descending)) {
        $label = $regimeMap["$($g.Name)"]
        if (-not $label) { $label = "regime $($g.Name)" }
        $conf  = ($g.Group | ForEach-Object { [double]$_.regime.confidence } | Measure-Object -Average).Average
        $bar   = Make-Bar $g.Count 2
        Write-Host ("  R{0}  {1,-20}  {2,2} tickers  conf={3:0.00}  {4}" -f $g.Name, $label, $g.Count, $conf, $bar) `
                   -ForegroundColor $C.White
    }
} else {
    Write-Host "  No regime data." -ForegroundColor $C.Dim
}

# ---------------------------------------------------------------------------
# 9. Anomalies
# ---------------------------------------------------------------------------

Write-Section "Anomalies"
$anyAnomaly = $false

# Pipeline errors
$errRecords = @($tickerRecords | Where-Object { $_.status -eq "error" -or ($null -ne $_.error -and $_.error -ne "") })
if ($errRecords.Count -gt 0) {
    $anyAnomaly = $true
    Write-Host "  Pipeline errors ($($errRecords.Count)):" -ForegroundColor $C.Err
    foreach ($e in $errRecords) {
        $msg = if ($null -ne $e.error -and $e.error -ne "") { $e.error.ToString() } else { "status=error" }
        Write-Host ("    [$($e.ticker)]  $msg") -ForegroundColor $C.Err
    }
}

# All rejected
$approvedCount = @($tickerRecords | Where-Object { $_.status -eq "sized_approved" }).Count
if ($tickerRecords.Count -gt 0 -and $approvedCount -eq 0) {
    $anyAnomaly = $true
    Write-Host "  All tickers rejected -- no trades approved." -ForegroundColor $C.Warn
    $gates = @($tickerRecords | Where-Object { $_.sizing -and $_.sizing.reject_gate } | Group-Object { $_.sizing.reject_gate })
    if ($gates.Count -gt 0) {
        Write-Host "  Reject gate breakdown:" -ForegroundColor $C.Dim
        foreach ($g in ($gates | Sort-Object Count -Descending)) {
            Write-Host ("    {0,-22} {1,2}  {2}" -f $g.Name, $g.Count, (Make-Bar $g.Count)) -ForegroundColor $C.Warn
        }
    }
}

# Identity-mode calibrator
$identityCount = @($tickerRecords | Where-Object { $_.consensus -and $_.consensus.calibrator_mode -eq "identity" }).Count
if ($identityCount -gt 0) {
    $anyAnomaly = $true
    Write-Host "  Calibrator running in identity mode -- no trained Platt calibrator loaded." -ForegroundColor $C.Warn
    Write-Host "  Run: python scripts/backtest.py --fit-calibrator" -ForegroundColor $C.Dim
}

# Low regime confidence
$lowConf = @($tickerRecords | Where-Object { $_.regime -and [double]$_.regime.confidence -lt 0.4 })
if ($lowConf.Count -gt ($tickerRecords.Count / 2) -and $tickerRecords.Count -gt 0) {
    $anyAnomaly = $true
    Write-Host ("  Low regime confidence on {0}/{1} tickers (< 0.40)." -f $lowConf.Count, $tickerRecords.Count) `
               -ForegroundColor $C.Warn
}

# Expired positions still in book
$expiredPos = @($openPos | Where-Object { $null -ne $_.current_dte -and [int]$_.current_dte -le 0 })
if ($expiredPos.Count -gt 0) {
    $anyAnomaly = $true
    Write-Host "  Expired positions still in book ($($expiredPos.Count)) -- exit cycle may not have run:" -ForegroundColor $C.Err
    foreach ($p in $expiredPos) {
        Write-Host ("    [$($p.ticker)]  DTE=$($p.current_dte)  struct=$($p.structure)  P&L={0:+0.00;-0.00}" -f [double]$p.current_pnl) `
                   -ForegroundColor $C.Err
    }
}

# Immediate exit alerts not acted on
$immediateAlerts = @($exitAlerts | Where-Object { $_.urgency -eq "immediate" })
if ($immediateAlerts.Count -gt 0) {
    $anyAnomaly = $true
    Write-Host "  IMMEDIATE exit alerts logged ($($immediateAlerts.Count)) -- verify these were executed:" -ForegroundColor $C.Err
    foreach ($a in $immediateAlerts) {
        Write-Host ("    [$($a.ticker)]  reason=$($a.reason)  trade_id=$($a.trade_id)") -ForegroundColor $C.Err
    }
}

if (-not $anyAnomaly) {
    Write-Host "  None detected." -ForegroundColor $C.OK
}

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host ("-" * 80) -ForegroundColor $C.Dim
Write-Host ("  Log dir : $LogDir") -ForegroundColor $C.Dim
Write-Host ("  Date    : $Date")   -ForegroundColor $C.Dim
Write-Host "  Files:" -ForegroundColor $C.Dim
foreach ($key in ($paths.Keys | Sort-Object)) {
    $p = $paths[$key]
    if (Test-Path $p) {
        $sz = (Get-Item $p).Length
        Write-Host ("    {0,-12}  OK      {1}  ({2} bytes)" -f $key, $p, $sz) -ForegroundColor $C.Dim
    } else {
        Write-Host ("    {0,-12}  missing {1}" -f $key, $p) -ForegroundColor $C.Warn
    }
}
Write-Host ""
