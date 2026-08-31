$ErrorActionPreference = "Stop"

$projectPath = $PSScriptRoot
$monitorScriptPath = Join-Path $projectPath "hilton_kakao_monitor.py"
$agentEnvironmentPath = Join-Path $projectPath ".env.agent"
$popup = New-Object -ComObject WScript.Shell

function Find-Python312 {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $resolved = & $pyLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            return ($resolved | Select-Object -First 1).Trim()
        }
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    foreach ($candidate in $candidates) {
        $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") {
            return $candidate
        }
    }
    return $null
}

$runningMonitor = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @("python.exe", "pythonw.exe") -and
    $_.CommandLine -match "hilton_kakao_monitor\.py"
} | Select-Object -First 1

if ($runningMonitor) {
    [void]$popup.Popup("Milaidhoo room monitor is already running.", 3, "Milaidhoo Monitor", 64)
    exit 0
}

$pythonPath = Find-Python312
if (-not $pythonPath) {
    [void]$popup.Popup("Python 3.12 is not installed. Run 새PC_설치.bat first.", 7, "Milaidhoo Monitor", 16)
    exit 1
}

if (-not (Test-Path -LiteralPath $agentEnvironmentPath)) {
    [void]$popup.Popup(".env.agent is missing. Run 새PC_설치.bat first.", 7, "Milaidhoo Monitor", 16)
    exit 1
}

$agentKeyLine = Get-Content -LiteralPath $agentEnvironmentPath | Where-Object {
    $_ -match "^AGENT_API_KEY="
} | Select-Object -First 1
if (-not $agentKeyLine -or [string]::IsNullOrWhiteSpace(($agentKeyLine -split "=", 2)[1])) {
    [void]$popup.Popup("AGENT_API_KEY is not configured. Run 새PC_설치.bat first.", 7, "Milaidhoo Monitor", 16)
    exit 1
}

$env:AGENT_API_KEY = ($agentKeyLine -split "=", 2)[1].Trim()
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) "pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonwPath)) {
    $pythonwPath = $pythonPath
}

Start-Process `
    -FilePath $pythonwPath `
    -ArgumentList @("`"$monitorScriptPath`"") `
    -WorkingDirectory $projectPath `
    -WindowStyle Hidden

Start-Sleep -Seconds 2
$startedMonitor = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @("python.exe", "pythonw.exe") -and
    $_.CommandLine -match "hilton_kakao_monitor\.py"
} | Select-Object -First 1

if ($startedMonitor) {
    [void]$popup.Popup("Milaidhoo room monitor started.", 3, "Milaidhoo Monitor", 64)
    exit 0
}

[void]$popup.Popup("Monitor failed to start. Check hilton_monitor.log.", 7, "Milaidhoo Monitor", 16)
exit 1
