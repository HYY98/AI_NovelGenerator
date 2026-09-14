$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Test-Path (Join-Path $PSScriptRoot '.deps')) {
    $env:PYTHONPATH = (Join-Path $PSScriptRoot '.deps') + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
python main.py
