param(
    [string]$Root = "",
    [string]$Backend = "metashape",
    [string]$MetashapeExe = "metashape.exe",
    [string]$ColmapExe = "colmap",
    [switch]$IncludeDensify,
    [switch]$UseCudaDensify,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $Root = (Resolve-Path $Root).Path
}

$Python = Join-Path $Root "binaries\python\python.exe"
$Readiness = Join-Path $Root "scripts\runtime_readiness.py"
$StateRoot = Join-Path $env:LOCALAPPDATA "com.xpano.app"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Bundled xPano Python is missing: $Python"
}
if (-not (Test-Path -LiteralPath $Readiness)) {
    throw "Runtime readiness entrypoint is missing: $Readiness"
}

function Set-ToolOverride {
    param([string]$EnvironmentName, [string]$BundledPath, [string]$RequestedCommand)
    if (Test-Path -LiteralPath $BundledPath) {
        Set-Item -Path "Env:$EnvironmentName" -Value (Resolve-Path -LiteralPath $BundledPath).Path
        return
    }
    $resolved = Get-Command $RequestedCommand -ErrorAction SilentlyContinue
    if ($resolved) {
        Set-Item -Path "Env:$EnvironmentName" -Value $resolved.Source
    }
}

Set-ToolOverride "XPANO_FFMPEG" (Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe") "ffmpeg.exe"
Set-ToolOverride "XPANO_FFPROBE" (Join-Path $Root "tools\ffmpeg\bin\ffprobe.exe") "ffprobe.exe"
Set-ToolOverride "XPANO_COLMAP" (Join-Path $Root "tools\colmap\bin\colmap.exe") $ColmapExe

$CommandName = if ($CheckOnly) { "probe" } else { "ensure" }
& $Python $Readiness $CommandName `
    --root $Root `
    --state-root $StateRoot `
    --backend $Backend `
    --metashape $MetashapeExe
if ($LASTEXITCODE -ne 0) {
    throw "xPano runtime readiness failed with exit code $LASTEXITCODE."
}

if ($IncludeDensify) {
    Write-Warning "Densification is provisioned separately by runtime_bootstrap.py and is not installed by this compatibility wrapper."
}
