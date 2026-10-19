<#
.SYNOPSIS
    Build a DTSYS Agent MSI installer using WiX Toolset v4.

.DESCRIPTION
    Compiles the WiX source, links it into an MSI, and writes the output to
    the dist/ directory at the repository root.

    Requirements:
      - WiX Toolset v4  (dotnet tool install --global wix)
      - Go 1.25.9+       (for cross-compiling the agent binary)
      - WinSW 2.x        (placed or downloaded automatically)

.PARAMETER Version
    Agent version string (default: reads from ../../dist/version.txt or "0.0.0").

.PARAMETER Server
    Optional server URL to embed in the MSI properties.

.EXAMPLE
    .\build-msi.ps1 -Version "1.3.0"
#>
param(
    [string]$Version = "",
    [string]$Server  = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot    = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$ClientRoot  = Join-Path $RepoRoot "client"
$DistDir     = Join-Path $RepoRoot "dist"
$StagingDir  = Join-Path $env:TEMP "dtsys-msi-staging"
$WxsPath     = Join-Path $PSScriptRoot "dtsys-agent.wxs"
$XmlPath     = Join-Path $PSScriptRoot "dtsys-agent.xml"

# ── Resolve version ────────────────────────────────────────────────────────────
if (-not $Version) {
    $VersionFile = Join-Path $DistDir "version.txt"
    if (Test-Path $VersionFile) {
        $Version = (Get-Content $VersionFile -Raw).Trim()
    } else {
        $Version = "0.0.0"
    }
}
Write-Host "Building DTSYS Agent MSI v$Version" -ForegroundColor Cyan

# ── Prepare staging dir ────────────────────────────────────────────────────────
if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir | Out-Null
New-Item -ItemType Directory -Path $DistDir    -Force | Out-Null

# ── Build agent binary (Windows/amd64) ────────────────────────────────────────
Write-Host "Building agent binary..." -ForegroundColor Cyan
Push-Location $ClientRoot
$env:GOOS   = "windows"
$env:GOARCH = "amd64"
$env:CGO_ENABLED = "0"
# The version lives in internal/version (the old -X main.Version flag matched no symbol,
# so every MSI reported the default version). The release public key must be embedded too,
# otherwise MSI-installed agents accept unsigned updates.
$ModulePath = "github.com/dtsys/agent/internal"
$Ldflags = "-s -w -X $ModulePath/version.Version=$Version -X $ModulePath/version.BuildDate=$(Get-Date -Format 'yyyy-MM-dd')"
if ($env:RELEASE_PUBLIC_KEY) {
    $Ldflags += " -X $ModulePath/updater.PublicKey=$($env:RELEASE_PUBLIC_KEY)"
} else {
    Write-Warning "RELEASE_PUBLIC_KEY not set: building an UNSIGNED-update agent (checksum-only self-update)."
}
go build -ldflags $Ldflags -o (Join-Path $StagingDir "dtsys-agent.exe") ./cmd/agent
if ($LASTEXITCODE -ne 0) { throw "go build failed" }
Pop-Location

# ── Copy WinSW ────────────────────────────────────────────────────────────────
$WinSWSrc = Join-Path $PSScriptRoot "winsw.exe"
if (-not (Test-Path $WinSWSrc)) {
    Write-Host "Downloading WinSW..." -ForegroundColor Yellow
    $WinSWUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
    Invoke-WebRequest -Uri $WinSWUrl -OutFile $WinSWSrc -UseBasicParsing
}
Copy-Item $WinSWSrc (Join-Path $StagingDir "winsw.exe")

# ── Copy service XML ───────────────────────────────────────────────────────────
Copy-Item $XmlPath (Join-Path $StagingDir "dtsys-agent.xml")

# ── Build MSI with WiX ────────────────────────────────────────────────────────
Write-Host "Building MSI with WiX..." -ForegroundColor Cyan
$OutMsi = Join-Path $DistDir "dtsys-agent-$Version-windows-amd64.msi"

wix build $WxsPath `
    -d "AgentVersion=$Version" `
    -d "SourceDir=$StagingDir" `
    -ext WixToolset.UI.wixext `
    -o $OutMsi

if ($LASTEXITCODE -ne 0) { throw "wix build failed" }

Write-Host ""
Write-Host "MSI ready: $OutMsi" -ForegroundColor Green
Write-Host ""
Write-Host "Silent install example:" -ForegroundColor DarkGray
Write-Host "  msiexec /i dtsys-agent-$Version-windows-amd64.msi /qn DTSYS_SERVER=https://your-server DTSYS_TOKEN=YOUR_TOKEN" -ForegroundColor DarkGray
