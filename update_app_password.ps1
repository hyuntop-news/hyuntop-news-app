$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectDir ".env"

if (-not (Test-Path $EnvPath)) {
    throw ".env file was not found."
}

$NewPassword = Read-Host "Enter new Gmail app password"
if ([string]::IsNullOrWhiteSpace($NewPassword)) {
    throw "App password is empty."
}

$Lines = Get-Content -LiteralPath $EnvPath -Encoding UTF8
$Updated = $false

$Lines = $Lines | ForEach-Object {
    if ($_ -match "^GMAIL_APP_PASSWORD=") {
        $Updated = $true
        "GMAIL_APP_PASSWORD=$NewPassword"
    }
    else {
        $_
    }
}

if (-not $Updated) {
    $Lines += "GMAIL_APP_PASSWORD=$NewPassword"
}

Set-Content -LiteralPath $EnvPath -Value $Lines -Encoding UTF8
Write-Host "App password updated."

