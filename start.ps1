$env:PYTHONUTF8=1
Set-Location $PSScriptRoot

# Load .env if present
if (Test-Path "$PSScriptRoot\.env") {
    Get-Content "$PSScriptRoot\.env" | ForEach-Object {
        if ($_ -match "^([^#][^=]*)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
}

if (-not $env:GEMINI_API_KEY) {
    $key = Read-Host "GEMINI_API_KEY (enter to skip vision features)"
    if ($key) { $env:GEMINI_API_KEY = $key }
}

Write-Host "Starting Clawmetheus v2 on http://127.0.0.1:7331" -ForegroundColor Cyan
python main.py
