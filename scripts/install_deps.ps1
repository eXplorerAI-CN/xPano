$ErrorActionPreference = "Stop"

function Find-Metashape {
    if ($env:XPANO_METASHAPE -and (Test-Path $env:XPANO_METASHAPE)) {
        return $env:XPANO_METASHAPE
    }

    $meta = Get-Command metashape.exe -ErrorAction SilentlyContinue
    if ($meta) {
        return $meta.Source
    }

    $candidates = @(
        "E:\FastProgram\Metashape\metashape.exe",
        "C:\Program Files\Agisoft\Metashape Pro\metashape.exe",
        "C:\Program Files\Agisoft\Metashape\metashape.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

Write-Host "[1/4] Checking ffmpeg..."
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    throw "ffmpeg.exe was not found in PATH. Install ffmpeg and add it to PATH before running xPano."
}

Write-Host "[2/4] Installing app Python dependencies..."
python -m pip install -r "$PSScriptRoot\..\requirements.txt"

Write-Host "[3/4] Locating Metashape..."
$metashapeExe = Find-Metashape
if (-not $metashapeExe) {
    throw "metashape.exe was not found. Add it to PATH, or set XPANO_METASHAPE to the full metashape.exe path."
}

$metaDir = Split-Path -Parent $metashapeExe
$metaPython = Join-Path $metaDir "python\python.exe"
if (-not (Test-Path $metaPython)) {
    throw "Metashape Python was not found at $metaPython"
}

Write-Host "[4/4] Installing Metashape Python dependencies..."
& $metaPython -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 -r "$PSScriptRoot\..\metashape_requirements.txt"

Write-Host "Done."
