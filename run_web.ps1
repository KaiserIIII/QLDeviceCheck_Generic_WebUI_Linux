param([Parameter(ValueFromRemainingArguments=$true)][string[]]$WorkbenchArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { & "$PSScriptRoot\setup_windows.ps1" }
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $taskPython -X utf8 web_app.py @WorkbenchArgs
exit $LASTEXITCODE
