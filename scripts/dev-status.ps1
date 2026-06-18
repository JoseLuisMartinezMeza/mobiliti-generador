param(
  [int]$ApiPort = 8000,
  [int]$WebPort = 5174
)

$api = try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2).Content } catch { "DOWN" }
$web = try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort" -TimeoutSec 2).StatusCode } catch { "DOWN" }

Write-Host "API ${ApiPort}: $api"
Write-Host "WEB ${WebPort}: $web"
Get-Process -Name python,node -ErrorAction SilentlyContinue |
  Select-Object Id,ProcessName,StartTime |
  Sort-Object StartTime -Descending |
  Select-Object -First 12
