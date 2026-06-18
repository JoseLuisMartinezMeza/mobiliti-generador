param(
    [string]$Source = ".agents\skills\codex-token-optimizer",
    [string]$DestinationRoot = "$env:USERPROFILE\.agents\skills"
)

$ErrorActionPreference = "Stop"

$sourcePath = Resolve-Path -LiteralPath $Source
$destinationRootPath = [System.IO.Path]::GetFullPath($DestinationRoot)
$destinationPath = Join-Path $destinationRootPath "codex-token-optimizer"

New-Item -ItemType Directory -Force -Path $destinationRootPath | Out-Null

if (Test-Path -LiteralPath $destinationPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $incomingPath = "$destinationPath.incoming-$stamp"
    Copy-Item -LiteralPath $sourcePath -Destination $incomingPath -Recurse
    Write-Host "Skill already exists. Copied incoming version to: $incomingPath"
    Write-Host "Review and merge manually to avoid overwriting local edits."
    exit 0
}

Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse
Write-Host "Installed skill to: $destinationPath"
