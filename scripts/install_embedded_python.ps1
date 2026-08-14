param(
    [string]$Root = "",
    [string]$PythonVersion = "3.12.3",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $Root = Resolve-Path $Root
}

$versionParts = $PythonVersion.Split(".")
$VersionNoDots = "$($versionParts[0])$($versionParts[1])"
$ArchiveName = "python-$PythonVersion-embed-amd64.zip"
$ArchiveUrl = "https://www.python.org/ftp/python/$PythonVersion/$ArchiveName"
$DownloadDir = Join-Path $Root "tools\offline-wheels\python"
$ArchivePath = Join-Path $DownloadDir $ArchiveName
$PythonDir = Join-Path $Root "binaries\python"
$SitePackages = Join-Path $PythonDir "Lib\site-packages"
$Wheelhouse = Join-Path $Root "tools\offline-wheels\app"

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
if (-not (Test-Path $ArchivePath)) {
    Write-Host "Downloading embedded Python: $ArchiveUrl"
    Invoke-WebRequest -Uri $ArchiveUrl -OutFile $ArchivePath
}

if (Test-Path $PythonDir) {
    Remove-Item -LiteralPath $PythonDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
Expand-Archive -LiteralPath $ArchivePath -DestinationPath $PythonDir -Force

$Pth = Join-Path $PythonDir "python$VersionNoDots._pth"
if (Test-Path $Pth) {
    $lines = Get-Content -LiteralPath $Pth
    $next = New-Object System.Collections.Generic.List[string]
    $hasSitePackages = $false
    foreach ($line in $lines) {
        if ($line.Trim() -eq "Lib\site-packages") {
            $hasSitePackages = $true
        }
        if ($line.Trim() -eq "#import site") {
            $next.Add("import site")
        } else {
            $next.Add($line)
        }
    }
    if (-not $hasSitePackages) {
        $next.Insert([Math]::Max(0, $next.Count - 1), "Lib\site-packages")
    }
    $next | Set-Content -LiteralPath $Pth -Encoding ASCII
}

if (-not (Test-Path $Wheelhouse)) {
    throw "App wheelhouse is missing: $Wheelhouse. Run scripts\download_offline_wheels.ps1 first."
}

New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
& $Python -m pip install `
    --no-index `
    --find-links $Wheelhouse `
    --target $SitePackages `
    -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install app packages into embedded Python."
}

$EmbeddedPython = Join-Path $PythonDir "python.exe"
& $EmbeddedPython -c "import cv2, numpy, PIL, piexif, tqdm; print('embedded python ok')"
if ($LASTEXITCODE -ne 0) {
    throw "Embedded Python import check failed."
}

Write-Host "Embedded Python ready: $EmbeddedPython"
