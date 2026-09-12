$ErrorActionPreference = "Stop"

function Resolve-Python {
    foreach ($candidate in @("python", "py")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            if ($candidate -eq "py") {
                & $candidate -3 --version 2>$null | Out-Null
            } else {
                & $candidate --version 2>$null | Out-Null
            }
            if ($LASTEXITCODE -eq 0) {
                if ($candidate -eq "py") { return @($candidate, "-3") }
                return @($candidate, "")
            }
        } catch { }
    }
    throw "Python 3.12+ is required. Install Python 3.12 from https://www.python.org/downloads/"
}

$pythonCommand = Resolve-Python
if ($pythonCommand[1] -eq "-3") {
    & $pythonCommand[0] $pythonCommand[1] -m pip install -e ".[dev]"
    & $pythonCommand[0] $pythonCommand[1] -m compileall -q app migrations tests
    & $pythonCommand[0] $pythonCommand[1] -m pytest
} else {
    & $pythonCommand[0] -m pip install -e ".[dev]"
    & $pythonCommand[0] -m compileall -q app migrations tests
    & $pythonCommand[0] -m pytest
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker compose config --quiet
    Write-Host "Docker Compose configuration is valid."
} else {
    Write-Warning "Docker is not installed; skipped compose validation."
}
