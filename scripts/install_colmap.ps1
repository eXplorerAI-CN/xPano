param(
    [ValidateSet("nocuda", "cuda")]
    [string] $Variant = "nocuda",
    [string] $Version = "4.0.4"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$toolsDir = Join-Path $repoRoot "tools\colmap"
$downloadDir = Join-Path $toolsDir "_downloads"
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

$assetName = if ($Variant -eq "cuda") {
    "colmap-x64-windows-cuda.zip"
} else {
    "colmap-x64-windows-nocuda.zip"
}
$url = "https://github.com/colmap/colmap/releases/download/$Version/$assetName"
$zipPath = Join-Path $downloadDir $assetName

Write-Host "Downloading COLMAP $Version ($Variant): $url"
Invoke-WebRequest -Uri $url -OutFile $zipPath

Write-Host "Extracting to $toolsDir"
Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force

$candidates = @(
    (Join-Path $toolsDir "COLMAP.bat"),
    (Join-Path $toolsDir "colmap.bat"),
    (Join-Path $toolsDir "colmap.exe"),
    (Join-Path $toolsDir "bin\colmap.exe")
) + (Get-ChildItem -Path $toolsDir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    @(
        (Join-Path $_.FullName "COLMAP.bat"),
        (Join-Path $_.FullName "colmap.bat"),
        (Join-Path $_.FullName "colmap.exe"),
        (Join-Path $_.FullName "bin\colmap.exe")
    )
})

$colmap = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $colmap) {
    throw "COLMAP executable was not found after extraction. Check $toolsDir"
}

Write-Host "Bundled COLMAP ready: $colmap"
