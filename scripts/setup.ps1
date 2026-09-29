<#
.SYNOPSIS
  One-shot TranscribeMe setup for Windows. Safe to re-run.

  1. Installs uv (Python + package manager) into your user profile
  2. Checks for the Microsoft Visual C++ runtime (torch needs it)
  3. Downloads a pinned, checksum-verified ffmpeg into .tools\ffmpeg
  4. Installs Python 3.13 and the exact locked package versions (uv.lock)
  5. Creates .env and asks for your tokens
  6. Downloads model weights and runs `transcribeme doctor`

  Nothing here needs admin rights except the VC++ runtime, and only if missing.
#>
param(
    [switch]$NonInteractive,   # CI: never prompt
    [switch]$SkipModels        # don't pre-download model weights
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is 10x slower with the bar
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Pinned ffmpeg: a versioned "shared" build, so torchcodec (a pyannote
# dependency) also finds the FFmpeg DLLs it loads at runtime.
$FfmpegVersion = "8.1.2"
$FfmpegUrl     = "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersion/ffmpeg-$FfmpegVersion-full_build-shared.zip"
$FfmpegSha256  = "274923c68904a9b76c73b908f57923dafba81155856cd742138515ded570d066"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# --- 1. uv ------------------------------------------------------------------
Step "uv (Python toolchain)"
$uvHome = Join-Path $env:USERPROFILE ".local\bin"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path (Join-Path $uvHome "uv.exe")) {
        $env:Path = "$uvHome;$env:Path"
    } else {
        Write-Host "    Installing uv..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$uvHome;$env:Path"
    }
}
Ok (uv --version)

# --- 2. Visual C++ runtime ----------------------------------------------------
Step "Microsoft Visual C++ runtime"
if (Test-Path "$env:SystemRoot\System32\vcruntime140_1.dll") {
    Ok "present"
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Warn "missing - installing via winget (you may see a UAC prompt)"
    winget install --id Microsoft.VCRedist.2015+.x64 -e --silent `
        --accept-package-agreements --accept-source-agreements
} else {
    Warn "missing and winget is unavailable. Install it manually:"
    Warn "https://aka.ms/vs/17/release/vc_redist.x64.exe"
}

# --- 3. ffmpeg ----------------------------------------------------------------
Step "ffmpeg $FfmpegVersion"
$tools   = Join-Path $Root ".tools"
$ffDir   = Join-Path $tools "ffmpeg"
$stamp   = Join-Path $ffDir "VERSION"
if ((Test-Path $stamp) -and ((Get-Content $stamp) -eq $FfmpegVersion)) {
    Ok "already installed"
} else {
    New-Item -ItemType Directory -Force $tools | Out-Null
    $zip = Join-Path $tools "ffmpeg.zip"
    Write-Host "    Downloading (~100 MB)..."
    Invoke-WebRequest $FfmpegUrl -OutFile $zip
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $FfmpegSha256) {
        Remove-Item $zip
        throw "ffmpeg checksum mismatch (got $hash). Download corrupted or tampered with."
    }
    $unpack = Join-Path $tools "ffmpeg-unpack"
    if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
    Expand-Archive $zip -DestinationPath $unpack
    if (Test-Path $ffDir) { Remove-Item -Recurse -Force $ffDir }
    # The zip holds one top-level folder (ffmpeg-8.1.2-full_build-shared\bin\...)
    Move-Item (Get-ChildItem $unpack -Directory | Select-Object -First 1).FullName $ffDir
    Remove-Item -Recurse -Force $unpack, $zip
    Set-Content $stamp $FfmpegVersion
    Ok "installed to $ffDir"
}

# --- 4. Python + packages -----------------------------------------------------
Step "Python 3.13 and locked dependencies"
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
Ok "environment ready in .venv"

# --- 5. .env ------------------------------------------------------------------
Step ".env"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Ok "created from .env.example" }

function Set-EnvValue($name, $prompt) {
    $lines = Get-Content ".env"
    $current = ($lines | Where-Object { $_ -match "^$name=" }) -replace "^$name=", ""
    if ($current.Trim()) { Ok "$name is set"; return }
    if ($NonInteractive) { Warn "$name is empty"; return }
    $value = Read-Host "    $prompt (Enter to skip)"
    if ($value.Trim()) {
        (Get-Content ".env") -replace "^$name=.*", "$name=$($value.Trim())" | Set-Content ".env"
        Ok "$name saved"
    } else {
        Warn "$name skipped - edit .env later"
    }
}
Set-EnvValue "HUGGINGFACE_TOKEN" "HuggingFace read token (needed for speaker labels)"
Set-EnvValue "ANTHROPIC_API_KEY" "Anthropic API key (needed for summaries)"

# --- 6. Models + health check ---------------------------------------------------
Step "Health check"
$doctorArgs = @("run", "--frozen", "transcribeme", "doctor")
if (-not $SkipModels) { $doctorArgs += "--download" }
if ($NonInteractive)  { $doctorArgs += "--offline" }
uv @doctorArgs
$doctorExit = $LASTEXITCODE

Write-Host ""
if ($doctorExit -eq 0) {
    Write-Host "Setup complete. Double-click run.cmd to start TranscribeMe." -ForegroundColor Green
} else {
    Write-Host "Setup finished with warnings above. Fix them, then re-run setup.cmd." -ForegroundColor Yellow
}
exit $doctorExit
