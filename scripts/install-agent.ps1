param(
    [string]$Server,
    [string]$Token,
    [string]$InstallDir = "C:\Program Files\DTSYS",
    [switch]$AllowInsecureHttp
)

if (-not $Server) { $Server = $env:DTSYS_SERVER }
if (-not $Token) { $Token = $env:DTSYS_TOKEN }
if (-not $Server -or -not $Token) {
    Write-Error "Usage: .\install-agent.ps1 -Server <url> -Token <token>"
    Write-Error "   or: set DTSYS_SERVER and DTSYS_TOKEN env vars first"
    exit 1
}

if ($Server -match '^http://') {
    if (-not $AllowInsecureHttp) {
        Write-Error "Server must use https:// (otherwise the enrollment token and API key are sent in plaintext). Pass -AllowInsecureHttp to override for local/dev testing only."
        exit 1
    }
    Write-Warning "Installing over plaintext http:// -- credentials will be sent unencrypted."
} elseif ($Server -notmatch '^https://') {
    Write-Error "Server must start with https:// or http://"
    exit 1
}

$ErrorActionPreference = "Stop"
$ServiceName = "DTSYSAgent"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "386" }
$agentUrl = ($Server.TrimEnd('/')) + "/api/v1/agent/download?arch=$arch&platform=windows"
$versionUrl = ($Server.TrimEnd('/')) + "/api/v1/agent/version?arch=$arch&platform=windows"
$agentExe = Join-Path $InstallDir "dtsys-agent.exe"

$versionInfo = Invoke-RestMethod -Uri $versionUrl
$expectedSha256 = if ($versionInfo.sha256) { $versionInfo.sha256.ToLower() } else { "" }
if ($expectedSha256.Length -ne 64) {
    Write-Error "Server did not provide a sha256 checksum for this build; refusing to install an unverified binary."
    exit 1
}

Write-Host "Downloading agent from $agentUrl"
$tempExe = Join-Path $env:TEMP ([System.IO.Path]::GetRandomFileName())
Invoke-WebRequest -Uri $agentUrl -OutFile $tempExe

$actualSha256 = (Get-FileHash -Path $tempExe -Algorithm SHA256).Hash.ToLower()
if ($actualSha256 -ne $expectedSha256) {
    Remove-Item -Path $tempExe -Force
    Write-Error "Checksum mismatch: expected $expectedSha256, got $actualSha256. Refusing to install."
    exit 1
}

Move-Item -Path $tempExe -Destination $agentExe -Force

$hostname = $env:COMPUTERNAME
$osVersion = (Get-CimInstance Win32_OperatingSystem).Version
$fingerprint = [System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA256Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($hostname))).Replace("-", "").ToLower()

$enrollPayload = @{
    hostname         = $hostname
    os_type          = "windows"
    os_version       = $osVersion
    arch             = $arch
    fingerprint      = $fingerprint
    enrollment_token = $Token
} | ConvertTo-Json

$enrollUrl = ($Server.TrimEnd('/')) + "/api/v1/enroll"
$response = Invoke-RestMethod -Method Post -Uri $enrollUrl -Body $enrollPayload -ContentType "application/json"

if (-not $response.device_id -or -not $response.api_key) {
    throw "Enrollment failed: $response"
}

$configPath = Join-Path $InstallDir "agent.toml"
$configContent = @"
[server]
url = "$Server"
device_id = "$($response.device_id)"
api_key = "$($response.api_key)"

[agent]

[collect]
telemetry_interval_secs = 60
software_scan_interval_m = 60
event_poll_interval_secs = 120

[events]
dedup_max_entries = 50
exclude_patterns = ["event handler.*EOF", "event streamer.*EOF"]
rate_limit_max = 20
rate_limit_window_s = 30

[tls]
skip_time_check = false

[update]
auto_update = true
check_interval_hours = 6
"@
Set-Content -Path $configPath -Value $configContent -Encoding ASCII

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    sc.exe stop $ServiceName | Out-Null
    sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 2
}

New-Service -Name $ServiceName -BinaryPathName "`"$agentExe`" --config `"$configPath`"" -DisplayName "DTSYS Device Management Agent" -StartupType Automatic

sc.exe failure $ServiceName reset= 86400 actions= restart/10000/restart/10000/restart/10000 | Out-Null
Set-Service -Name $ServiceName -StartupType Automatic
sc.exe config $ServiceName start= delayed-auto | Out-Null

Start-Service -Name $ServiceName

$service = Get-Service -Name $ServiceName
if ($service.Status -eq 'Running') {
    Write-Host "DTSYSAgent installed successfully."
    Write-Host ("Device ID: " + $response.device_id)
    Write-Host ("Status: " + $service.Status)
}
else {
    Write-Error ("DTSYSAgent installation finished but service status is " + $service.Status)
}
