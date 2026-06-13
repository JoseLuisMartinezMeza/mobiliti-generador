param(
    [string]$Token = $env:HCLOUD_TOKEN,
    [string]$ServerName = "mobiliti-worker-prod-01",
    [string]$ServerType = "ccx13",
    [string]$Image = "ubuntu-24.04",
    [string[]]$Locations = @("hil", "ash"),
    [string]$SshKeyName = "mobiliti-worker-hetzner",
    [string]$SshKeyPath = "$env:USERPROFILE\.ssh\mobiliti_hetzner_ed25519",
    [string]$FirewallName = "mobiliti-worker-ssh-only",
    [string]$SupabaseUrl = "https://hcdspekajlszcycecpml.supabase.co",
    [string]$SupabaseAnonKey = $env:SUPABASE_ANON_KEY,
    [string]$MobilitiRestSecret = $env:MOBILITI_REST_SECRET,
    [string]$QuoteStorageBucket = "quote-files",
    [switch]$SkipBootstrap,
    [switch]$SkipEnvUpload
)

$ErrorActionPreference = "Stop"

function Die($message) {
    throw $message
}

function HCloudJson([string[]]$ArgsList) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $script:HCloud @ArgsList 2>&1
    $ErrorActionPreference = $oldPreference
    if ($LASTEXITCODE -ne 0) {
        Die "hcloud failed: hcloud $($ArgsList -join ' ')`n$output"
    }
    $text = ($output | Out-String).Trim()
    if (-not $text) {
        return $null
    }
    return $text | ConvertFrom-Json
}

function HCloud([string[]]$ArgsList) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $script:HCloud @ArgsList 2>&1
    $ErrorActionPreference = $oldPreference
    if ($LASTEXITCODE -ne 0) {
        Die "hcloud failed: hcloud $($ArgsList -join ' ')`n$output"
    }
    return $output
}

function Wait-ForSsh([string]$Ip) {
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & ssh -i $SshKeyPath -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$env:USERPROFILE\.ssh\known_hosts" -o ConnectTimeout=10 -o LogLevel=ERROR "root@$Ip" "echo ok" *> $null
        $ErrorActionPreference = $oldPreference
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 10
    }
    Die "SSH did not become ready for $Ip"
}

if (-not $Token) {
    Die "Missing HCLOUD_TOKEN. Set `$env:HCLOUD_TOKEN before running."
}

$env:HCLOUD_TOKEN = $Token
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Process")
$script:HCloud = (Get-Command hcloud -ErrorAction SilentlyContinue).Source
if (-not $script:HCloud) {
    $script:HCloud = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter hcloud.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $script:HCloud) {
    Die "hcloud CLI is not installed or not in PATH."
}

$PublicKeyPath = "$SshKeyPath.pub"
if (-not (Test-Path -LiteralPath $SshKeyPath) -or -not (Test-Path -LiteralPath $PublicKeyPath)) {
    Die "Missing SSH key pair at $SshKeyPath. Generate it before provisioning."
}

Write-Host "Validating Hetzner token..."
HCloudJson @("location", "list", "-o", "json") | Out-Null

Write-Host "Ensuring SSH key '$SshKeyName' exists..."
$sshKeys = HCloudJson @("ssh-key", "list", "-o", "json")
$sshKey = @($sshKeys) | Where-Object { $_.name -eq $SshKeyName } | Select-Object -First 1
if (-not $sshKey) {
    HCloud @("ssh-key", "create", "--name", $SshKeyName, "--public-key-from-file", $PublicKeyPath) | Out-Null
}

Write-Host "Ensuring firewall '$FirewallName' exists..."
$firewalls = HCloudJson @("firewall", "list", "-o", "json")
$firewall = @($firewalls) | Where-Object { $_.name -eq $FirewallName } | Select-Object -First 1
if (-not $firewall) {
    $rule = @{
        direction = "in"
        protocol = "tcp"
        port = "22"
        source_ips = @("0.0.0.0/0", "::/0")
        description = "SSH only"
    }
    $rules = ConvertTo-Json -InputObject @($rule) -Depth 6
    $rulesFile = Join-Path $env:TEMP "mobiliti-hetzner-firewall-rules.json"
    [System.IO.File]::WriteAllText($rulesFile, $rules, [System.Text.UTF8Encoding]::new($false))
    try {
        HCloud @("firewall", "create", "--name", $FirewallName, "--rules-file", $rulesFile) | Out-Null
    }
    finally {
        Remove-Item -LiteralPath $rulesFile -Force -ErrorAction SilentlyContinue
    }
}

$servers = HCloudJson @("server", "list", "-o", "json")
$server = @($servers) | Where-Object { $_.name -eq $ServerName } | Select-Object -First 1
if (-not $server) {
    $created = $false
    foreach ($location in $Locations) {
        Write-Host "Creating server '$ServerName' type '$ServerType' in '$location'..."
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = & $script:HCloud server create --name $ServerName --type $ServerType --image $Image --location $location --ssh-key $SshKeyName --firewall $FirewallName -o json 2>&1
        $ErrorActionPreference = $oldPreference
        if ($LASTEXITCODE -eq 0) {
            $created = $true
            break
        }
        Write-Warning "Create failed in ${location}: $output"
    }
    if (-not $created) {
        Die "Could not create server in any requested location: $($Locations -join ', ')"
    }
}
else {
    Write-Host "Server '$ServerName' already exists; reusing it."
}

$server = HCloudJson @("server", "describe", $ServerName, "-o", "json")
$ip = $server.public_net.ipv4.ip
if (-not $ip) {
    Die "Server has no public IPv4."
}
Write-Host "Server IPv4: $ip"

if (-not $SkipBootstrap) {
    Write-Host "Waiting for SSH..."
    Wait-ForSsh $ip

    Write-Host "Running bootstrap on server..."
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & ssh -i $SshKeyPath -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "root@$ip" "curl -fsSL https://raw.githubusercontent.com/REMOVED_PASSWORD/mobiliti-generador/master/deploy/hetzner/bootstrap.sh | bash"
    $ErrorActionPreference = $oldPreference
    if ($LASTEXITCODE -ne 0) {
        Die "Remote bootstrap failed."
    }

    if (-not $SkipEnvUpload) {
        if (-not $SupabaseAnonKey -or -not $MobilitiRestSecret) {
            Die "Missing SUPABASE_ANON_KEY or MOBILITI_REST_SECRET. Set local env vars or run with -SkipEnvUpload."
        }

        $envContent = @"
SUPABASE_URL=$SupabaseUrl
SUPABASE_ANON_KEY=$SupabaseAnonKey
MOBILITI_REST_SECRET=$MobilitiRestSecret
QUOTE_STORAGE_BUCKET=$QuoteStorageBucket
QUOTE_ENGINE=python
WORKER_POLL_SECONDS=10
WORKER_STALE_MINUTES=30
WORKER_ISOLATE_JOBS=1
WORKER_JOB_TIMEOUT_SECONDS=0
IMAGE_PROVIDER=pillow
"@
        $tmpEnv = Join-Path $env:TEMP "mobiliti-worker.env"
        [System.IO.File]::WriteAllText($tmpEnv, $envContent, [System.Text.UTF8Encoding]::new($false))
        try {
            $oldPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & scp -i $SshKeyPath -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR $tmpEnv "root@${ip}:/tmp/mobiliti-worker.env"
            $ErrorActionPreference = $oldPreference
            if ($LASTEXITCODE -ne 0) {
                Die "Env upload failed."
            }
            $oldPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & ssh -i $SshKeyPath -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "root@$ip" "install -d -m 0700 /etc/mobiliti-worker && install -m 0600 /tmp/mobiliti-worker.env /etc/mobiliti-worker/worker.env && rm -f /tmp/mobiliti-worker.env && mobiliti-worker-deploy"
            $ErrorActionPreference = $oldPreference
            if ($LASTEXITCODE -ne 0) {
                Die "Remote deploy failed."
            }
        }
        finally {
            Remove-Item -LiteralPath $tmpEnv -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Provisioning complete."
Write-Host "SSH: ssh -i `"$SshKeyPath`" root@$ip"
