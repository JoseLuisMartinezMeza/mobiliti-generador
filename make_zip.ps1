$zipPath = 'Mobiliti_Generador_Windows.zip'
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$files = @(
    'mobiliti_saas/dist/Mobiliti_Generador.exe',
    'mobiliti_saas/config.json',
    'Formato Cotización 2026 GDL (1).xlsx'
)
Compress-Archive -Path $files -DestinationPath $zipPath -Force
$size = (Get-Item $zipPath).Length
Write-Host "ZIP creado: $size bytes"
