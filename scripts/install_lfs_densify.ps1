param(
    [string]$Root = "",
    [string]$PluginUrl = "https://github.com/shadygm/Lichtfeld-Densification-Plugin.git",
    [string]$PluginRef = "main",
    [string]$Python = "python",
    [string]$PipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$Wheelhouse = "",
    [switch]$UseCudaTorch,
    [switch]$SkipDeps,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $Root = Resolve-Path $Root
}
$Tools = Join-Path $Root "tools"
$PluginDir = Join-Path $Tools "lichtfeld-densification-plugin"
$VenvDir = Join-Path $Root ".venv-densify"
if ([string]::IsNullOrWhiteSpace($Wheelhouse)) {
    $Wheelhouse = Join-Path $Root "tools\offline-wheels\densify"
}

function Test-VenvCapablePython {
    param([Parameter(Mandatory=$true)][string]$Candidate)
    try {
        if ([string]::IsNullOrWhiteSpace($Candidate)) {
            return $false
        }
        if (([System.IO.Path]::IsPathRooted($Candidate) -or $Candidate.Contains("\") -or $Candidate.Contains("/")) -and -not (Test-Path $Candidate)) {
            return $false
        }
        & $Candidate -m venv --help *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Resolve-VenvCapablePython {
    param([string]$Requested)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Requested) -and $Requested -ne "python") {
        $candidates.Add($Requested)
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($version in @("-3.12", "-3.11", "-3.10", "-3")) {
            try {
                $resolved = & $pyLauncher.Source $version -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($resolved)) {
                    $candidates.Add($resolved.Trim())
                }
            } catch {
            }
        }
    }

    foreach ($name in @("python.exe", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $candidates.Add($cmd.Source)
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates.Add($Requested)
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-VenvCapablePython $candidate) {
            return $candidate
        }
    }

    throw "No venv-capable Python was found. Install Python 3.10-3.12 from python.org, enable 'Add python.exe to PATH', then run one-click densification setup again."
}

New-Item -ItemType Directory -Force -Path $Tools | Out-Null

if (-not (Test-Path (Join-Path $PluginDir "densify.py"))) {
    if ($Offline) {
        throw "Densification plugin is missing and offline mode is enabled: $PluginDir"
    }
    git clone --depth 1 --branch $PluginRef $PluginUrl $PluginDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone LichtFeld densification plugin. Install Git or use a release package that already bundles tools\lichtfeld-densification-plugin."
    }
} else {
    $pluginGitDir = Join-Path $PluginDir ".git"
    if ((-not $Offline) -and (Test-Path $pluginGitDir)) {
        Push-Location $PluginDir
        try {
            git fetch --depth 1 origin $PluginRef
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to update LichtFeld densification plugin from git remote."
            }
            git checkout FETCH_HEAD
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to checkout LichtFeld densification plugin revision."
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "Using bundled LichtFeld densification plugin at $PluginDir"
    }
}

if ($SkipDeps) {
    Write-Host "Plugin installed at $PluginDir"
    Write-Host "Skipped dependency installation."
    exit 0
}

$InstallPython = Resolve-VenvCapablePython $Python
Write-Host "Using Python for densification environment: $InstallPython"

if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    & $InstallPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create densification virtual environment with $InstallPython"
    }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments=$true)]
        [string[]]$CommandArgs
    )
    & $Command @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command $CommandArgs"
    }
}

if ((Test-Path $Wheelhouse) -and (Get-ChildItem -LiteralPath $Wheelhouse -Filter "*.whl" -File -ErrorAction SilentlyContinue)) {
    $TorchSpec = if ($UseCudaTorch) { "torch==2.8.0+cu128" } else { "torch==2.8.0" }
    $VisionSpec = if ($UseCudaTorch) { "torchvision==0.23.0+cu128" } else { "torchvision==0.23.0" }
    Invoke-Checked $VenvPython -m pip install `
        --no-index `
        --find-links $Wheelhouse `
        $TorchSpec `
        $VisionSpec `
        numpy `
        pycolmap==4.0.4 `
        Pillow `
        scipy `
        tqdm `
        "einops>=0.8.1" `
        "rich>=14.2.0" `
        open3d
    Write-Host "LichtFeld densification dependencies installed from offline wheelhouse: $Wheelhouse"
    Write-Host "LichtFeld densification plugin installed at $PluginDir"
    Write-Host "Python environment: $VenvPython"
    exit 0
}

if ($Offline) {
    throw "Offline densification wheelhouse is missing or empty: $Wheelhouse"
}

Invoke-Checked $VenvPython -m pip install --upgrade pip -i $PipIndex

if ($UseCudaTorch) {
    Invoke-Checked $VenvPython -m pip install `
        --index-url https://download.pytorch.org/whl/cu128 `
        torch==2.8.0+cu128 `
        torchvision==0.23.0+cu128
} else {
    Invoke-Checked $VenvPython -m pip install `
        -i $PipIndex `
        torch==2.8.0 `
        torchvision==0.23.0
}

Invoke-Checked $VenvPython -m pip install `
    -i $PipIndex `
    numpy `
    pycolmap==4.0.4 `
    Pillow `
    scipy `
    tqdm `
    "einops>=0.8.1" `
    "rich>=14.2.0" `
    open3d

Write-Host "LichtFeld densification plugin installed at $PluginDir"
Write-Host "Python environment: $VenvPython"
