param(
    [string]$Root = "",
    [string]$Python = "python",
    [string[]]$AppPythonVersions = @("39", "310", "311", "312"),
    [string[]]$MetashapePythonVersions = @("39", "310", "311", "312"),
    [switch]$IncludeDensify,
    [switch]$UseCudaTorch
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $Root = Resolve-Path $Root
}

$WheelRoot = Join-Path $Root "tools\offline-wheels"
$AppWheelhouse = Join-Path $WheelRoot "app"
$MetaWheelhouse = Join-Path $WheelRoot "metashape"
$DensifyWheelhouse = Join-Path $WheelRoot "densify"
New-Item -ItemType Directory -Force -Path $AppWheelhouse, $MetaWheelhouse | Out-Null

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$Command,
        [Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs
    )
    & $Command @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command $CommandArgs"
    }
}

Write-Host "[1/3] Downloading app Python wheels..."
foreach ($version in $AppPythonVersions) {
    $abi = "cp$version"
    Invoke-Checked $Python -m pip download `
        --dest $AppWheelhouse `
        --only-binary=:all: `
        --prefer-binary `
        --platform win_amd64 `
        --implementation cp `
        --python-version $version `
        --abi $abi `
        -r (Join-Path $Root "requirements.txt")
}

Write-Host "[2/3] Downloading Metashape Python wheels..."
foreach ($version in $MetashapePythonVersions) {
    $abi = "cp$version"
    Invoke-Checked $Python -m pip download `
        --dest $MetaWheelhouse `
        --only-binary=:all: `
        --prefer-binary `
        --platform win_amd64 `
        --implementation cp `
        --python-version $version `
        --abi $abi `
        -r (Join-Path $Root "metashape_requirements.txt")
}

if ($IncludeDensify) {
    Write-Host "[3/3] Downloading LichtFeld densification wheels..."
    New-Item -ItemType Directory -Force -Path $DensifyWheelhouse | Out-Null
    $DensifyRequirements = Join-Path $WheelRoot "densify-requirements.txt"
    @(
        "numpy",
        "pycolmap==4.0.4",
        "Pillow",
        "scipy",
        "tqdm",
        "einops>=0.8.1",
        "rich>=14.2.0",
        "open3d"
    ) | Set-Content -LiteralPath $DensifyRequirements -Encoding ASCII

    if ($UseCudaTorch) {
        Invoke-Checked $Python -m pip download `
            --dest $DensifyWheelhouse `
            --only-binary=:all: `
            --prefer-binary `
            --index-url https://download.pytorch.org/whl/cu128 `
            torch==2.8.0+cu128 `
            torchvision==0.23.0+cu128
    } else {
        Invoke-Checked $Python -m pip download `
            --dest $DensifyWheelhouse `
            --only-binary=:all: `
            --prefer-binary `
            torch==2.8.0 `
            torchvision==0.23.0
    }

    Invoke-Checked $Python -m pip download `
        --dest $DensifyWheelhouse `
        --only-binary=:all: `
        --prefer-binary `
        -r $DensifyRequirements
} else {
    Write-Host "[3/3] Skipping densification wheels. Use -IncludeDensify to download them."
}

Write-Host "Offline wheels ready under $WheelRoot"
