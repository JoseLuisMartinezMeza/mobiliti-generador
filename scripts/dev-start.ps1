param(
  [int]$ApiPort = 8000,
  [int]$WebPort = 5174
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root ".mobiliti_dev_store\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

foreach ($port in @($ApiPort, $WebPort)) {
  $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
  foreach ($connection in $connections) {
    if ($connection.OwningProcess) {
      Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
    }
  }
}

Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -and
    (
      $_.CommandLine -match "mobiliti_saas\\worker\\quote_worker.py" -or
      $_.CommandLine -match "\.mobiliti_dev_store\\logs\\worker.ps1"
    )
  } |
  ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

function Start-HiddenProcess($Name, $FilePath, $Arguments, $WorkingDirectory, $EnvVars) {
  $script = @"
`$ErrorActionPreference = "Stop"
Set-Location "$WorkingDirectory"
$(($EnvVars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = `"$($_.Value)`"" }) -join "`n")
& "$FilePath" $Arguments
"@
  $scriptPath = Join-Path $logDir "$Name.ps1"
  $outPath = Join-Path $logDir "$Name.out.log"
  $errPath = Join-Path $logDir "$Name.err.log"
  Set-Content -Path $scriptPath -Value $script -Encoding UTF8
  return Start-Process -FilePath "powershell" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $outPath -RedirectStandardError $errPath -WindowStyle Hidden -PassThru
}

$processes = @()

$processes += Start-HiddenProcess `
  -Name "api" `
  -FilePath "python" `
  -Arguments "-m uvicorn index:app --app-dir vercel_deploy\api --host 127.0.0.1 --port $ApiPort --reload --reload-dir vercel_deploy\api --reload-dir mobiliti_saas\quote_engine" `
  -WorkingDirectory $root `
  -EnvVars @{
    MOBILITI_DEV_MODE = "1"
    JWT_SECRET_KEY = "dev-secret-change-me-32-chars"
    CORS_ORIGINS = "http://127.0.0.1:$WebPort"
    MOBILITI_DEV_PUBLIC_BASE_URL = "http://127.0.0.1:$ApiPort"
    CATALOG_ENABLED_SUPPLIERS = "cr-global,sonara,sunon,alma,lumbro"
  }

$processes += Start-HiddenProcess `
  -Name "worker" `
  -FilePath "python" `
  -Arguments "mobiliti_saas\worker\quote_worker.py" `
  -WorkingDirectory $root `
  -EnvVars @{
    MOBILITI_DEV_MODE = "1"
    QUOTE_ENGINE = "python"
    WORKER_STALE_MINUTES = "30"
    CATALOG_ENABLED_SUPPLIERS = "cr-global,sonara,sunon,alma,lumbro"
  }

$processes += Start-HiddenProcess `
  -Name "web" `
  -FilePath "npm.cmd" `
  -Arguments "run dev -- --port $WebPort" `
  -WorkingDirectory (Join-Path $root "mobiliti_saas\web") `
  -EnvVars @{
    VITE_API_BASE_URL = "http://127.0.0.1:$ApiPort"
  }

Start-Sleep -Seconds 4
$pidFile = Join-Path $logDir "pids.json"
$processes | Select-Object Id,ProcessName,StartTime | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8
Write-Host "Mobiliti dev API: http://127.0.0.1:$ApiPort"
Write-Host "Mobiliti dev web: http://127.0.0.1:$WebPort"
Write-Host "Login: dev@mobiliti.local / dev12345"
Write-Host "Logs: $logDir"
Write-Host "PID file: $pidFile"
