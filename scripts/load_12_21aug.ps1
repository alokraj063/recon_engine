<#
Loads the 12-21 Aug 2026 window (docs/runbook-12-21aug-ingestion.md) through the
deployed API: per day, oldest first, POST /api/ingest then POST /api/reconcile
(incremental). Stops at the first error.

  $env:RECON_PASSWORD = '...'
  .\scripts\load_12_21aug.ps1 -Email you@example.com
  .\scripts\load_12_21aug.ps1 -Email you@example.com -Site http://localhost:8000
  .\scripts\load_12_21aug.ps1 -Email you@example.com -DryRun     # list files only
#>
param(
    [Parameter(Mandatory)] [string] $Email,
    [string] $Site = 'https://wabtec.reconalpha-staging.joulestowatts.com',
    [string] $Customer = 'default',
    [string] $Root = 'C:\Users\Lehen Zehra\Desktop\Wabtec\sample_data',
    [switch] $DryRun
)
$ErrorActionPreference = 'Stop'
$BK = Join-Path $Root 'Bank_stmt_PDF\_statement_attachments_only'
$BS = Join-Path $Root 'Bill_Status_Split\Bill_Status_Split'

# file date DDMM -> statement file (day 5, 16 Aug, has no Bill Status export)
$days = [ordered]@{
    '12' = 'HSBCINRHSBCINR2026-08-12-03.00.03.000974.PDF'
    '13' = 'HSBCINRHSBCINR2026-08-13-03.00.11.000870.PDF'
    '14' = 'HSBCINRHSBCINR2026-08-14-03.00.36.000765.PDF'
    '15' = 'HSBCINRHSBCINR2026-08-15-03.00.05.000602.PDF'
    '16' = 'HSBCINRHSBCINR2026-08-16-03.00.12.000184.PDF'
    '17' = 'HSBCINRHSBCINR2026-08-17-03.00.13.000303.PDF'
    '18' = 'HSBCINRHSBCINR2026-08-18-03.00.10.000520.PDF'
    '19' = 'HSBCINRHSBCINR2026-08-19-03.00.08.000694.PDF'
    '20' = 'HSBCINRHSBCINR2026-08-20-03.00.12.000545.PDF'
    '21' = 'HSBCINRHSBCINR2026-08-21-03.00.14.000716.PDF'
}
$noBills = @('16')

# resolve + check every file up front so a typo fails before anything is posted
$plan = foreach ($d in $days.Keys) {
    $stmt = Join-Path $BK $days[$d]
    $bills = @()
    if ($noBills -notcontains $d) {
        $bills = 'Friction', 'Hosur', 'Rohtak' | ForEach-Object { Join-Path $BS "$_-${d}082026.xlsx" }
    }
    foreach ($f in @($stmt) + $bills) {
        if (-not (Test-Path -LiteralPath $f)) { throw "missing file: $f" }
    }
    [pscustomobject]@{ Day = $d; Statement = $stmt; Bills = $bills }
}
Write-Host "$($plan.Count) days, all files present."
if ($DryRun) { $plan | ForEach-Object { "$($_.Day) Aug: 1 statement + $($_.Bills.Count) bills" }; return }

$pw = $env:RECON_PASSWORD
if (-not $pw) { throw 'set $env:RECON_PASSWORD first' }
$cookies = Join-Path $env:TEMP 'recon_cookies.txt'
Remove-Item $cookies -ErrorAction SilentlyContinue

function Call-Api([string[]] $CurlArgs) {
    $out = & curl.exe -s -S -w "`n%{http_code}" @CurlArgs
    $lines = $out -split "`n"
    $code = [int]$lines[-1]
    $body = ($lines[0..($lines.Count - 2)] -join "`n")
    if ($code -ge 400) { throw "HTTP $code : $body" }
    return $body | ConvertFrom-Json
}

$login = @{ email = $Email; password = $pw } | ConvertTo-Json -Compress
$loginFile = Join-Path $env:TEMP 'recon_login.json'
Set-Content -Path $loginFile -Value $login -Encoding ascii
try {
    Call-Api @('-c', $cookies, '-H', 'Content-Type: application/json',
               '--data-binary', "@$loginFile", "$Site/api/auth/login") | Out-Null
} finally { Remove-Item $loginFile -ErrorAction SilentlyContinue }
Write-Host "signed in to $Site"

$results = @()
foreach ($p in $plan) {
    Write-Host "`n== $($p.Day) Aug =="
    $a = @('-b', $cookies, '-X', 'POST', "$Site/api/ingest", '-F', "customer_id=$Customer",
           '-F', "statement=@$($p.Statement)")
    foreach ($b in $p.Bills) { $a += @('-F', "bills=@$b") }
    $ing = Call-Api $a
    $stmtFile = $ing.files | Where-Object { $_.field -eq 'statement' } | Select-Object -First 1
    if (-not $stmtFile) { throw "ingest response has no statement file: $($ing | ConvertTo-Json -Depth 4)" }
    $sid = $stmtFile.bronze_file_id
    Write-Host "  ingested: statement bronze id $sid ($($stmtFile.outcome))"
    if ($ing.stats) { Write-Host "  stats: $($ing.stats | ConvertTo-Json -Compress -Depth 3)" }

    $bodyFile = Join-Path $env:TEMP 'recon_reconcile.json'
    Set-Content -Path $bodyFile -Encoding ascii -Value (@{
        customer_id = $Customer; statement_bronze_id = $sid; mode = 'incremental' } | ConvertTo-Json -Compress)
    $rec = Call-Api @('-b', $cookies, '-X', 'POST', "$Site/api/reconcile",
                      '-H', 'Content-Type: application/json', '--data-binary', "@$bodyFile")
    Remove-Item $bodyFile -ErrorAction SilentlyContinue
    $fin = $rec.meta.ledger
    Write-Host "  reconciled: run $($rec.run_id)  ledger: $($fin | ConvertTo-Json -Compress)"
    $results += [pscustomobject]@{ Day = $p.Day; Ingest = $ing; Reconcile = $rec.meta }
}

$outFile = Join-Path $PSScriptRoot 'load_12_21aug_results.json'
$results | ConvertTo-Json -Depth 8 | Set-Content -Path $outFile -Encoding utf8
Write-Host "`nDone. Full per-day JSON: $outFile"
Write-Host 'Expected end state (runbook §6): 131 matches (124 LOCKED / 7 OPEN), 339 open BANK_ONLY, 5 open BILL_ONLY, 2,733 gold bills.'
