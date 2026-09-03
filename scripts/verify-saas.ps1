param(
  [string]$ApiUrl = "http://127.0.0.1:8000",
  [switch]$Prod,
  [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Step($Name, $ScriptBlock) {
  Write-Host ""
  Write-Host "== $Name ==" -ForegroundColor Cyan
  & $ScriptBlock
  if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw "$Name fallo con codigo $LASTEXITCODE"
  }
}

Set-Location $root

Invoke-Step "Doctor" {
  if ($Prod) {
    python scripts\saas_doctor.py --api-url $ApiUrl
  } else {
    python scripts\saas_doctor.py --api-url $ApiUrl --dev --skip-supabase
  }
}

Invoke-Step "Python tests" {
  python -m pytest -q
}

Invoke-Step "Web build" {
  Push-Location (Join-Path $root "mobiliti_saas\web")
  try {
    npm.cmd run build
  } finally {
    Pop-Location
  }
}

if (-not $SkipSmoke) {
  Invoke-Step "SaaS smoke" {
    python scripts\smoke-saas.py --api-url $ApiUrl
  }
}

Write-Host ""
Write-Host "Mobiliti SaaS verify OK" -ForegroundColor Green
