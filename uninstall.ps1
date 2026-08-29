$ErrorActionPreference = "Stop"
$InstalledBinary = Join-Path $env:USERPROFILE ".local\bin\anoti.exe"
$SourceBinary = Join-Path $PSScriptRoot "target\release\anoti.exe"

if (Test-Path $InstalledBinary) {
    & $InstalledBinary uninstall
} elseif (Test-Path $SourceBinary) {
    & $SourceBinary uninstall
} else {
    throw "No installed Rust runtime or local release build was found."
}
if ($LASTEXITCODE -ne 0) { throw "Rust uninstaller failed." }
Write-Host "The Rust runtime and managed hooks are removed."
