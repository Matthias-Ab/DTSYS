param(
    [Parameter(Mandatory = $true)]
    [string]$ServerURL,

    [Parameter(Mandatory = $true)]
    [string]$EnrollmentToken,

    [string]$InstallDir = "C:\Program Files\DTSYS"
)

$ErrorActionPreference = "Stop"
$serviceName = "DTSYSAgent"
$agentExe = Join-Path $InstallDir "dtsys-agent.exe"
$configPath = Join-Path $InstallDir "agent.toml"
$wrapperPath = Join-Path $InstallDir "run-agent.cmd"
$nssmExe = Join-Path $InstallDir "nssm.exe"
$agentUrl = ($ServerURL.TrimEnd('/')) + "/api/v1/downloads/dtsys-agent-windows.exe"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

Write-Host "Downloading agent from $agentUrl"
Invoke-WebRequest -Uri $agentUrl -OutFile $agentExe

$configContent = @"
[server]
url = "$ServerURL"
enrollment_token = "$EnrollmentToken"

[agent]
# device_id and api_key are populated automatically after registration

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
skip_time_check = true

[update]
auto_update = true
check_interval_hours = 6
"@
Set-Content -Path $configPath -Value $configContent -Encoding ASCII

$wrapperContent = "@echo off`r`n`"$agentExe`" --config `"$configPath`"`r`n"
Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding ASCII

function Install-WithNssm {
    param([string]$ExePath)

    if (Get-Command nssm.exe -ErrorAction SilentlyContinue) {
        $script:nssmExe = (Get-Command nssm.exe).Source
    }

    if (-not (Test-Path $script:nssmExe)) {
        if (Get-Command choco.exe -ErrorAction SilentlyContinue) {
            choco install nssm -y --no-progress
            if (Get-Command nssm.exe -ErrorAction SilentlyContinue) {
                $script:nssmExe = (Get-Command nssm.exe).Source
            }
        }
    }

    if (-not (Test-Path $script:nssmExe)) {
        $zipPath = Join-Path $env:TEMP "nssm.zip"
        $extractDir = Join-Path $env:TEMP "nssm"
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zipPath
        if (Test-Path $extractDir) {
            Remove-Item -Path $extractDir -Recurse -Force
        }
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
        Copy-Item -Path (Join-Path $extractDir "nssm-2.24\win64\nssm.exe") -Destination $script:nssmExe -Force
    }

    & $script:nssmExe install $serviceName $ExePath
    & $script:nssmExe set $serviceName AppDirectory $InstallDir
    & $script:nssmExe set $serviceName AppParameters "--config `"$configPath`""
    & $script:nssmExe set $serviceName Start SERVICE_AUTO_START
    & $script:nssmExe set $serviceName AppStdout (Join-Path $InstallDir "agent.log")
    & $script:nssmExe set $serviceName AppStderr (Join-Path $InstallDir "agent.err.log")
}

function Install-WithSc {
    param([string]$Wrapper)

    $binPath = "`"$Wrapper`""
    sc.exe create $serviceName binPath= $binPath start= auto DisplayName= "DTSYS Device Management Agent" | Out-Null
    sc.exe description $serviceName "Reports device health to DTSYS management server" | Out-Null
}

try {
    Install-WithNssm -ExePath $agentExe
}
catch {
    Write-Warning "NSSM installation failed, falling back to sc.exe: $($_.Exception.Message)"
    if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
        sc.exe delete $serviceName | Out-Null
        Start-Sleep -Seconds 2
    }
    Install-WithSc -Wrapper $wrapperPath
}

Set-Service -Name $serviceName -StartupType Automatic
Start-Service -Name $serviceName

$service = Get-Service -Name $serviceName
if ($service.Status -eq 'Running') {
    Write-Host "DTSYSAgent installed successfully."
    Write-Host ("Status: " + $service.Status)
    Write-Host ("InstallDir: " + $InstallDir)
}
else {
    Write-Error ("DTSYSAgent installation finished but service status is " + $service.Status)
}
