param([Parameter(ValueFromRemainingArguments=$true)][string[]]$WorkbenchArgs)
& "$PSScriptRoot\run_web.ps1" --demo --data-dir data/demo @WorkbenchArgs
exit $LASTEXITCODE
