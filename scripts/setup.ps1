$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bootstrap = Join-Path $ScriptDir "bootstrap.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Bootstrap @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Bootstrap @args
} else {
    Write-Error "Python 3.10+ is required. Install Python, then rerun setup.ps1."
    exit 1
}
exit $LASTEXITCODE
