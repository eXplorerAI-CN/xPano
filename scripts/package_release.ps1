param(
    [string]$ReleaseName = "xPano-release",
    [int]$PartSizeMB = 1900,
    [switch]$NoSplit
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DistRoot = Join-Path $Root "dist"
$ReleaseDir = Join-Path $DistRoot $ReleaseName
$ZipPath = Join-Path $DistRoot "$ReleaseName.zip"
$ChecksumsPath = Join-Path $DistRoot "$ReleaseName.SHA256SUMS.txt"
$RecombinePath = Join-Path $DistRoot "RECOMBINE_RELEASE_PARTS.txt"

function Assert-InDist {
    param([Parameter(Mandatory=$true)][string]$Path)
    $resolvedDist = [System.IO.Path]::GetFullPath((Resolve-Path $DistRoot).Path).TrimEnd("\", "/")
    $full = [System.IO.Path]::GetFullPath($Path)
    $isDistRoot = $full.Equals($resolvedDist, [System.StringComparison]::OrdinalIgnoreCase)
    $isDistChild = $full.StartsWith($resolvedDist + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not ($isDistRoot -or $isDistChild)) {
        throw "Refusing to modify path outside dist: $full"
    }
}

function Resolve-7Zip {
    $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    foreach ($candidate in @(
        "C:\Program Files\7-Zip\7z.exe",
        "C:\Program Files (x86)\7-Zip\7z.exe"
    )) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Resolve-Tar {
    $cmd = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

if (-not (Test-Path $ReleaseDir)) {
    throw "Release folder not found: $ReleaseDir"
}
Assert-InDist $ZipPath
Assert-InDist $ChecksumsPath
Assert-InDist $RecombinePath

Write-Host "[1/4] Removing previous zip assets..."
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ChecksumsPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $RecombinePath -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $DistRoot -File -Filter "$ReleaseName.zip.part*" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Assert-InDist $_.FullName
        Remove-Item -LiteralPath $_.FullName -Force
    }

Write-Host "[2/4] Creating zip archive..."
$sevenZip = Resolve-7Zip
if ($sevenZip) {
    Push-Location $DistRoot
    try {
        & $sevenZip a -tzip "$ReleaseName.zip" "$ReleaseName\*" -mx=5
        if ($LASTEXITCODE -ne 0) {
            throw "7-Zip failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
} elseif ($tar = Resolve-Tar) {
    Push-Location $DistRoot
    try {
        & $tar -a -cf "$ReleaseName.zip" "$ReleaseName"
        if ($LASTEXITCODE -ne 0) {
            throw "tar failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "7-Zip not found; falling back to Compress-Archive."
    Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath $ZipPath -Force
}

if ($NoSplit) {
    Write-Host "[3/4] Skipping split archive creation."
} else {
    Write-Host "[3/4] Splitting zip archive..."
    $partSize = [int64]$PartSizeMB * 1MB
    $buffer = New-Object byte[] (4MB)
    $inputStream = [System.IO.File]::OpenRead($ZipPath)
    try {
        $partIndex = 1
        while ($inputStream.Position -lt $inputStream.Length) {
            $partPath = Join-Path $DistRoot ("{0}.zip.part{1:D2}" -f $ReleaseName, $partIndex)
            Assert-InDist $partPath
            $outputStream = [System.IO.File]::Create($partPath)
            try {
                $written = [int64]0
                while ($written -lt $partSize -and $inputStream.Position -lt $inputStream.Length) {
                    $remaining = [Math]::Min($buffer.Length, $partSize - $written)
                    $read = $inputStream.Read($buffer, 0, [int]$remaining)
                    if ($read -le 0) {
                        break
                    }
                    $outputStream.Write($buffer, 0, $read)
                    $written += $read
                }
            } finally {
                $outputStream.Dispose()
            }
            $partIndex += 1
        }
    } finally {
        $inputStream.Dispose()
    }
}

Write-Host "[4/4] Writing checksums and recombine instructions..."
$partFiles = Get-ChildItem -LiteralPath $DistRoot -File -Filter "$ReleaseName.zip.part*" | Sort-Object Name
$assets = @($ZipPath) + ($partFiles | ForEach-Object { $_.FullName })
$checksumLines = foreach ($asset in $assets) {
    $hash = Get-FileHash -LiteralPath $asset -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $(Split-Path -Leaf $asset)"
}
$checksumLines | Set-Content -LiteralPath $ChecksumsPath -Encoding ASCII

if ($NoSplit) {
    @"
Single-file release zip:

  $ReleaseName.zip

Verify SHA256 against $ReleaseName.SHA256SUMS.txt.
"@ | Set-Content -LiteralPath $RecombinePath -Encoding ASCII
} else {
    $copyCommand = "copy /b " + (($partFiles | ForEach-Object { $_.Name }) -join "+") + " $ReleaseName.zip"
    $powershellCommand = "Get-Content " + (($partFiles | ForEach-Object { $_.Name }) -join ",") + " -Encoding Byte -ReadCount 0 | Set-Content $ReleaseName.zip -Encoding Byte"

@"
To recombine the split release zip on Windows:

  $copyCommand

Then verify SHA256 against $ReleaseName.SHA256SUMS.txt.

PowerShell alternative:

  $powershellCommand
"@ | Set-Content -LiteralPath $RecombinePath -Encoding ASCII
}

Write-Host "Packaged release:"
Write-Host $ZipPath
