$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Assets = Join-Path $Root 'dashboard\assets'
$Svg = Join-Path $Assets 'favicon.svg'

$EdgeCommand = Get-Command 'msedge' -ErrorAction SilentlyContinue
$EdgeCandidates = @(
    if ($EdgeCommand) { $EdgeCommand.Source }
    if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe'
    }
    if (${env:ProgramFiles(x86)}) {
        Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
    }
    if ($env:ProgramFiles) {
        Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'
    }
)
$Edge = $EdgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Edge) {
    throw 'Microsoft Edge is required to render dashboard icon PNG assets.'
}
if (-not (Test-Path -LiteralPath $Svg)) {
    throw "SVG source not found: $Svg"
}

New-Item -ItemType Directory -Force -Path $Assets | Out-Null

function Assert-Png {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "PNG was not generated: $Path"
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $signature = [byte[]](137, 80, 78, 71, 13, 10, 26, 10)
    if ($bytes.Length -lt $signature.Length) {
        throw "PNG is too small: $Path"
    }
    for ($index = 0; $index -lt $signature.Length; $index++) {
        if ($bytes[$index] -ne $signature[$index]) {
            throw "Invalid PNG signature: $Path"
        }
    }
    return $bytes
}

function Render-Png {
    param([Parameter(Mandatory = $true)][int]$Size)

    $Output = Join-Path $Assets "favicon-${Size}x${Size}.png"
    $Profile = Join-Path ([System.IO.Path]::GetTempPath()) "czc-good-job-dashboard-icon-$PID-$Size"
    New-Item -ItemType Directory -Force -Path $Profile | Out-Null
    try {
        $SvgUri = ([System.Uri]$Svg).AbsoluteUri
        $Arguments = @(
            '--headless=new',
            '--disable-gpu',
            '--hide-scrollbars',
            '--force-device-scale-factor=1',
            '--no-first-run',
            '--no-default-browser-check',
            "--user-data-dir=$Profile",
            "--window-size=$Size,$Size",
            "--screenshot=$Output",
            $SvgUri
        )
        & $Edge @Arguments | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Edge icon render failed for ${Size}px with exit code $LASTEXITCODE."
        }
    }
    finally {
        $ResolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $ResolvedProfile = [System.IO.Path]::GetFullPath($Profile)
        if ($ResolvedProfile.StartsWith($ResolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $ResolvedProfile -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    return Assert-Png -Path $Output
}

$Images = @(
    [pscustomobject]@{ Size = 16; Bytes = Render-Png -Size 16 },
    [pscustomobject]@{ Size = 32; Bytes = Render-Png -Size 32 }
)

$Ico = Join-Path $Assets 'favicon.ico'
$Stream = [System.IO.File]::Open($Ico, [System.IO.FileMode]::Create)
$Writer = New-Object System.IO.BinaryWriter($Stream)
try {
    $Writer.Write([uint16]0)
    $Writer.Write([uint16]1)
    $Writer.Write([uint16]$Images.Count)

    $Offset = 6 + (16 * $Images.Count)
    foreach ($Image in $Images) {
        $Writer.Write([byte]$Image.Size)
        $Writer.Write([byte]$Image.Size)
        $Writer.Write([byte]0)
        $Writer.Write([byte]0)
        $Writer.Write([uint16]1)
        $Writer.Write([uint16]32)
        $Writer.Write([uint32]$Image.Bytes.Length)
        $Writer.Write([uint32]$Offset)
        $Offset += $Image.Bytes.Length
    }
    foreach ($Image in $Images) {
        $Writer.Write([byte[]]$Image.Bytes)
    }
}
finally {
    $Writer.Dispose()
    $Stream.Dispose()
}

if ((Get-Item -LiteralPath $Ico).Length -le 38) {
    throw "ICO output is invalid: $Ico"
}

Write-Host "Dashboard icon assets generated in $Assets"
