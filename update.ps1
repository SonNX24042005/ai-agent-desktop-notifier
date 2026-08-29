$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$TemporaryRoot = $null

if (-not $ProjectRoot -or -not (Test-Path (Join-Path $ProjectRoot "Cargo.toml"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is missing." }
    $TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("anoti-" + [guid]::NewGuid())
    git clone --depth 1 "https://github.com/SonNX24042005/ai-agent-desktop-notifier.git" $TemporaryRoot
    if ($LASTEXITCODE -ne 0) { throw "Repository download failed." }
    $ProjectRoot = $TemporaryRoot
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust toolchain is missing. Install rustup/cargo and run this script again."
}
if (-not $env:CARGO_BUILD_JOBS) { $env:CARGO_BUILD_JOBS = "2" }

Write-Host "Building the Rust update from the current source..."
Push-Location $ProjectRoot
try {
    cargo build --release -p anoti-app
    if ($LASTEXITCODE -ne 0) { throw "Cargo build failed." }
    & "$ProjectRoot\target\release\anoti.exe" update
    if ($LASTEXITCODE -ne 0) { throw "Rust updater failed." }
} finally {
    Pop-Location
    if ($TemporaryRoot -and (Test-Path $TemporaryRoot)) {
        Remove-Item -Recurse -Force $TemporaryRoot
    }
}
Write-Host "The Rust runtime and managed hooks are updated."
