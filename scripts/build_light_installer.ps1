param(
    [string]$FfmpegExe = "",
    [string]$FfprobeExe = "",
    [string]$LichtfeldArchive = "",
    [string]$SigningCertificateThumbprint = "",
    [string]$TimestampUrl = "",
    [switch]$SkipVerification,
    [switch]$DevelopmentBuild,
    [switch]$FullOffline
)

$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "build_installer.ps1"
& $builder @PSBoundParameters
exit $LASTEXITCODE
