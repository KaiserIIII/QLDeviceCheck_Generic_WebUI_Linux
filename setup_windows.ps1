$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        throw 'Python 3.10+ is required. Install CPython, then run setup_windows.ps1 again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $taskPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'This workbench requires Python 3.10+.' }
$taskInstallArgs = @('-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-runtime.lock', '--disable-pip-version-check')
if (Test-Path -LiteralPath 'wheels') { $taskInstallArgs += @('--no-index', '--find-links', 'wheels') }
& $taskPython @taskInstallArgs
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check Python version and the release platform.' }
Write-Host 'Ready. Start-Demo.cmd opens the hardware-free local station.'
Write-Host 'For reviewed live configuration: .\run_web.ps1 --config config\device_list.json --data-dir data\live'
