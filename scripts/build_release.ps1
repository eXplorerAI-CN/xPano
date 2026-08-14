[CmdletBinding()]
param(
    [string]$FfmpegExe = "",
    [string]$FfprobeExe = "",
    [string]$LichtfeldArchive = "",
    [switch]$SkipVerification,
    [switch]$DevelopmentBuild,
    [switch]$FullOffline
)

$ErrorActionPreference = "Stop"

# NOTE: The legacy portable assembler had a separate resource contract and could omit LFS GUI files.
Write-Warning "build_release.ps1 is retired. Delegating to the single installer assembly path."
& (Join-Path $PSScriptRoot "build_installer.ps1") @PSBoundParameters
exit $LASTEXITCODE
