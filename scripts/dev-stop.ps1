param(
  [int[]]$Ports = @(8000, 5174)
)

$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".mobiliti_dev_store\logs\pids.json"

if (Test-Path $pidFile) {
  $items = Get-Content -Raw $pidFile | ConvertFrom-Json
  foreach ($item in @($items)) {
    if ($item.Id) {
      Stop-Process -Id $item.Id -Force -ErrorAction SilentlyContinue
      Write-Host "Stopped tracked process $($item.Id)"
    }
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

foreach ($port in $Ports) {
  $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
  foreach ($connection in $connections) {
    $pid = $connection.OwningProcess
    if ($pid) {
      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
      Write-Host "Stopped process $pid on port $port"
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
    Write-Host "Stopped worker process $($_.ProcessId)"
  }
