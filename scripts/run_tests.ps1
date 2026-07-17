$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONIOENCODING = 'utf-8'

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = 'python'
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

Push-Location $Root
try {
    Write-Host '[1/5] Python compile check'
    Invoke-Checked $Python -m compileall -q app main.py

    Write-Host '[2/5] Python unit and API tests'
    Invoke-Checked $Python -m unittest discover -s tests -v

    Write-Host '[3/5] Userscript syntax check'
    Invoke-Checked node --check web_script.js

    Write-Host '[4/5] Dashboard syntax check'
    Invoke-Checked node --check dashboard/app.js

    Write-Host '[5/5] Node logic and frontend contract tests'
    $JavaScriptTests = Get-ChildItem -LiteralPath (Join-Path $Root 'tests') -File | Where-Object {
        $_.Name -like '*.test.js' -or $_.Name -like 'test_*.js'
    }
    if ($JavaScriptTests.Count -gt 0) {
        foreach ($JavaScriptTest in $JavaScriptTests) {
            Invoke-Checked node --test --test-isolation=none $JavaScriptTest.FullName
        }
    } else {
        Write-Host 'No JavaScript test files found.'
    }

    Write-Host 'All checks passed.' -ForegroundColor Green
}
finally {
    Pop-Location
}
