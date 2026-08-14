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
$Root = [System.IO.Path]::GetFullPath((Resolve-Path (Join-Path $PSScriptRoot "..")).Path)
$UiDir = Join-Path $Root "xpano-ui"
$TauriDir = Join-Path $UiDir "src-tauri"
$Stage = Join-Path $Root "build\release-stage"
$Dist = Join-Path $Root "dist"

function Assert-InWorkspace {
    param([Parameter(Mandatory=$true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside workspace: $full"
    }
}

function Resolve-RealExecutable {
    param([string]$Requested, [string]$Name)
    $candidate = $Requested
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
        if (-not $command) { throw "$Name.exe was not found. Pass -${Name}Exe explicitly." }
        $candidate = $command.Source
    }
    if (-not (Test-Path -LiteralPath $candidate)) { throw "$Name.exe was not found: $candidate" }
    $item = Get-Item -LiteralPath $candidate -Force
    if ($item.LinkType -and $item.Target) {
        $candidate = $item.Target[0]
    }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    if ((Get-Item -LiteralPath $resolved).Length -le 0) { throw "$Name.exe resolved to an empty file: $resolved" }
    return $resolved
}

function Resolve-PinnedLichtfeldArchive {
    param([string]$Requested)
    $candidate = $Requested
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = $env:XPANO_LICHTFELD_ARCHIVE
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        if ($DevelopmentBuild) {
            return ""
        }
        throw "-LichtfeldArchive is required for a production installer build. Set XPANO_LICHTFELD_ARCHIVE or pass the pinned v0.5.3 archive explicitly."
    }
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Pinned LichtFeld archive was not found: $candidate"
    }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    if ((Get-Item -LiteralPath $resolved).Length -le 0) {
        throw "Pinned LichtFeld archive is empty: $resolved"
    }
    return $resolved
}

function Resolve-ReleaseSigning {
    param([string]$RequestedThumbprint, [string]$RequestedTimestampUrl)

    if ($DevelopmentBuild) {
        return $null
    }
    $thumbprint = $RequestedThumbprint
    if ([string]::IsNullOrWhiteSpace($thumbprint)) {
        $thumbprint = $env:XPANO_SIGNING_CERTIFICATE_THUMBPRINT
    }
    $timestampUrl = $RequestedTimestampUrl
    if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
        $timestampUrl = $env:XPANO_SIGNING_TIMESTAMP_URL
    }
    $thumbprint = ($thumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') {
        throw "A production installer requires a 40-character code signing certificate thumbprint. Pass -SigningCertificateThumbprint or set XPANO_SIGNING_CERTIFICATE_THUMBPRINT."
    }
    $timestamp = $null
    if (-not [Uri]::TryCreate($timestampUrl, [UriKind]::Absolute, [ref]$timestamp) -or $timestamp.Scheme -ne 'https') {
        throw "A production installer requires an HTTPS timestamp URL. Pass -TimestampUrl or set XPANO_SIGNING_TIMESTAMP_URL."
    }
    $certificate = @(
        Get-ChildItem -Path Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
            Where-Object { $_.Thumbprint -eq $thumbprint }
    ) | Select-Object -First 1
    if (-not $certificate -or -not $certificate.HasPrivateKey) {
        throw "Code signing certificate $thumbprint is unavailable or has no private key."
    }
    if ($certificate.NotAfter -le (Get-Date)) {
        throw "Code signing certificate $thumbprint is expired."
    }
    $usageIds = @($certificate.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value })
    if ($usageIds -notcontains '1.3.6.1.5.5.7.3.3') {
        throw "Certificate $thumbprint is not authorized for code signing."
    }
    $signTool = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if (-not $signTool) {
        throw "signtool.exe is required for a production installer build. Install the Windows SDK signing tools."
    }
    return [PSCustomObject]@{
        Thumbprint = $thumbprint
        TimestampUrl = $timestamp.AbsoluteUri
        SignTool = $signTool.Source
    }
}

function New-TauriReleaseConfig {
    param([object]$Signing)

    if (-not $Signing) {
        return (Join-Path $TauriDir "tauri.release.conf.json")
    }
    $config = Get-Content -Raw -Encoding UTF8 (Join-Path $TauriDir "tauri.release.conf.json") | ConvertFrom-Json
    $config.bundle.windows | Add-Member -NotePropertyName "certificateThumbprint" -NotePropertyValue $Signing.Thumbprint -Force
    $config.bundle.windows | Add-Member -NotePropertyName "digestAlgorithm" -NotePropertyValue "sha256" -Force
    $config.bundle.windows | Add-Member -NotePropertyName "timestampUrl" -NotePropertyValue $Signing.TimestampUrl -Force
    $config.bundle.windows | Add-Member -NotePropertyName "tsp" -NotePropertyValue $true -Force
    $generated = Join-Path $TauriDir ".tauri.release.signing.conf.json"
    $config | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $generated -Encoding UTF8
    return $generated
}

function Invoke-InstallerSigning {
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][object]$Signing)

    & $Signing.SignTool sign /fd SHA256 /sha1 $Signing.Thumbprint /tr $Signing.TimestampUrl /td SHA256 /v $Path
    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed while signing $Path"
    }
}

function Assert-AuthenticodeSignature {
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][object]$Signing)

    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid") {
        throw "Authenticode verification failed for ${Path}: $($signature.Status) $($signature.StatusMessage)"
    }
    $actual = (($signature.SignerCertificate.Thumbprint -replace '\s', '').ToUpperInvariant())
    if ($actual -ne $Signing.Thumbprint) {
        throw "Authenticode verification used an unexpected certificate for $Path."
    }
}

function Assert-ReleaseDllClosure {
    param(
        [string]$Entry = "",
        [string]$WebViewLoader = "",
        [string]$TreeRoot = ""
    )
    $Arguments = @((Join-Path $Root "scripts\windows_dll_closure.py"))
    if (-not [string]::IsNullOrWhiteSpace($Entry)) {
        $Arguments += @("--entry", $Entry, "--app-local", $WebViewLoader)
    }
    if (-not [string]::IsNullOrWhiteSpace($TreeRoot)) {
        $Arguments += @("--tree-root", $TreeRoot)
    }
    python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Windows DLL dependency closure validation failed." }
}

if ($SkipVerification -and -not $DevelopmentBuild) {
    throw "-SkipVerification is allowed only together with -DevelopmentBuild."
}

$Config = Get-Content -Raw -Encoding UTF8 (Join-Path $TauriDir "tauri.conf.json") | ConvertFrom-Json
$CargoText = Get-Content -Raw -Encoding UTF8 (Join-Path $TauriDir "Cargo.toml")
if ($CargoText -notmatch '(?ms)^\[package\].*?^version\s*=\s*"([^"]+)"') {
    throw "Cargo package version was not found."
}
$Version = $Matches[1]
if ($Config.version -ne $Version) {
    throw "Version mismatch: tauri.conf.json=$($Config.version), Cargo.toml=$Version"
}

$Signing = Resolve-ReleaseSigning $SigningCertificateThumbprint $TimestampUrl
$Ffmpeg = Resolve-RealExecutable $FfmpegExe "ffmpeg"
$Ffprobe = Resolve-RealExecutable $FfprobeExe "ffprobe"
$PinnedLichtfeldArchive = Resolve-PinnedLichtfeldArchive $LichtfeldArchive

Assert-InWorkspace $Stage
if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

if (-not $SkipVerification) {
    Push-Location $Root
    try {
        python -m unittest discover -s tests
        if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
    } finally { Pop-Location }
    Push-Location $UiDir
    try {
        & pnpm.cmd install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "pnpm install failed." }
        & pnpm.cmd run test:unit
        if ($LASTEXITCODE -ne 0) { throw "Node tests failed." }
        & pnpm.cmd run lint
        if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
    } finally { Pop-Location }
}

Push-Location $TauriDir
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { throw "Release runtime prebuild failed." }
} finally { Pop-Location }
$WebView2Loader = Join-Path $TauriDir "target\release\WebView2Loader.dll"
if (-not (Test-Path -LiteralPath $WebView2Loader) -or (Get-Item -LiteralPath $WebView2Loader).Length -le 0) {
    throw "Fresh WebView2Loader.dll was not produced: $WebView2Loader"
}
$ReleaseExe = Join-Path $TauriDir "target\release\xpano-ui.exe"
Assert-ReleaseDllClosure -Entry $ReleaseExe -WebViewLoader $WebView2Loader

Push-Location $Root
try {
    $StagingArgs = @(
        "scripts\release_staging.py",
        "--root", $Root,
        "--destination", $Stage,
        "--ffmpeg", $Ffmpeg,
        "--ffprobe", $Ffprobe,
        "--webview2-loader", $WebView2Loader,
        "--version", $Version
    )
    if ($FullOffline) {
        $StagingArgs += @(
            "--full-offline-artifacts",
            (Join-Path $Root "tools\offline-densify-artifacts\sha256")
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($PinnedLichtfeldArchive)) {
        $StagingArgs += @("--lichtfeld-archive", $PinnedLichtfeldArchive)
    }
    python @StagingArgs
    if ($LASTEXITCODE -ne 0) { throw "Release staging failed." }
} finally { Pop-Location }

Assert-ReleaseDllClosure -TreeRoot (Join-Path $Stage "binaries\python")
Assert-ReleaseDllClosure -TreeRoot (Join-Path $Stage "tools\colmap")
Assert-ReleaseDllClosure -TreeRoot (Join-Path $Stage "runtime\lichtfeld-studio")

$BuildStarted = Get-Date
$TauriBuildConfig = New-TauriReleaseConfig $Signing
Push-Location $UiDir
try {
    & pnpm.cmd exec tauri build --bundles nsis --config $TauriBuildConfig
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed." }
} finally {
    Pop-Location
    if ($Signing -and (Test-Path -LiteralPath $TauriBuildConfig)) {
        Remove-Item -LiteralPath $TauriBuildConfig -Force
    }
}

# NOTE: Tauri performs its own release build, so validate the exact binary used by NSIS as well.
Assert-ReleaseDllClosure -Entry $ReleaseExe -WebViewLoader $WebView2Loader
if ($Signing) {
    Assert-AuthenticodeSignature -Path $ReleaseExe -Signing $Signing
}

$Installer = Get-ChildItem -LiteralPath (Join-Path $TauriDir "target\release\bundle\nsis") -File -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $Installer -or $Installer.LastWriteTime -lt $BuildStarted) {
    throw "A fresh NSIS installer was not produced."
}
$Flavor = if ($FullOffline) { "-full-offline" } else { "" }
$DevelopmentFlavor = if ($DevelopmentBuild) { "-unsigned-dev" } else { "" }
$Output = Join-Path $Dist "xPano-$Version$Flavor$DevelopmentFlavor-windows-x64-setup.exe"
Copy-Item -LiteralPath $Installer.FullName -Destination $Output -Force
if ($Signing) {
    Invoke-InstallerSigning -Path $Output -Signing $Signing
    Assert-AuthenticodeSignature -Path $Output -Signing $Signing
} else {
    "UNSIGNED DEVELOPMENT BUILD - do not promote or distribute as a production release." |
        Set-Content -LiteralPath "$Output.unsigned.txt" -Encoding ASCII
}
$Hash = Get-FileHash -LiteralPath $Output -Algorithm SHA256
"$($Hash.Hash.ToLowerInvariant())  $(Split-Path -Leaf $Output)" |
    Set-Content -LiteralPath "$Output.sha256" -Encoding ASCII

Write-Host "Installer ready: $Output"
Write-Host "SHA-256: $($Hash.Hash)"
