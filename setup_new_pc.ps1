$ErrorActionPreference = "Stop"

$projectPath = $PSScriptRoot
$workerPath = Join-Path $projectPath "cloudflare_worker"
$desktopPath = [Environment]::GetFolderPath("Desktop")

function Write-Step([string]$message) {
    Write-Host "`n== $message ==" -ForegroundColor Cyan
}

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
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Find-Npm {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:ProgramFiles "nodejs\npm.cmd"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return $null
}

Write-Step "Python 3.12 확인"
$pythonPath = Find-Python312
if (-not $pythonPath) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.12가 없습니다. https://www.python.org/downloads/ 에서 Python 3.12를 설치한 뒤 다시 실행하세요."
    }
    & $winget.Source install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    $pythonPath = Find-Python312
}
if (-not $pythonPath) { throw "Python 3.12 설치를 확인할 수 없습니다. PC 재부팅 후 다시 실행하세요." }
Write-Host "Python: $pythonPath"

Write-Step "Python 패키지 설치"
& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -r (Join-Path $projectPath "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python 패키지 설치에 실패했습니다." }

Write-Step "Microsoft Edge 확인"
$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
)
if (-not ($edgeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) })) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id Microsoft.Edge -e --accept-package-agreements --accept-source-agreements
    } else {
        Write-Warning "Microsoft Edge가 없습니다. Edge를 설치해야 객실 조회를 실행할 수 있습니다."
    }
}

Write-Step "Node.js와 Cloudflare 개발 패키지 확인"
$npmPath = Find-Npm
if (-not $npmPath) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
        $npmPath = Find-Npm
    }
}
if ($npmPath) {
    Push-Location $workerPath
    try {
        & $npmPath ci
        if ($LASTEXITCODE -ne 0) { throw "Cloudflare 개발 패키지 설치에 실패했습니다." }
    } finally {
        Pop-Location
    }
} else {
    Write-Warning "Node.js를 찾지 못했습니다. 객실 감시는 실행되지만 Cloudflare 코드 배포는 할 수 없습니다."
}

Write-Step "에이전트 키 확인"
$agentEnvPath = Join-Path $projectPath ".env.agent"
$validAgentKey = $false
if (Test-Path -LiteralPath $agentEnvPath) {
    $validAgentKey = [bool](Get-Content -LiteralPath $agentEnvPath | Where-Object {
        $_ -match '^AGENT_API_KEY=.+$'
    } | Select-Object -First 1)
}
if (-not $validAgentKey) {
    Write-Host "Cloudflare Worker의 AGENT_API_KEY를 입력하세요. 입력 내용은 화면에 표시되지 않습니다."
    $secureKey = Read-Host "AGENT_API_KEY" -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        if ([string]::IsNullOrWhiteSpace($plainKey)) { throw "AGENT_API_KEY가 비어 있습니다." }
        [IO.File]::WriteAllText($agentEnvPath, "AGENT_API_KEY=$plainKey`r`n", [Text.UTF8Encoding]::new($false))
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        $plainKey = $null
    }
}

Write-Step "바탕화면 실행 아이콘 만들기"
$shortcutPath = Join-Path $desktopPath "밀라이두 객실감시.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $projectPath "launch_monitor.ps1") + '"'
$shortcut.WorkingDirectory = $projectPath
$shortcut.Description = "밀라이두 객실 감시 시작"
$shortcut.Save()

$folderShortcutPath = Join-Path $desktopPath "밀라이두 개발폴더.lnk"
$folderShortcut = $shell.CreateShortcut($folderShortcutPath)
$folderShortcut.TargetPath = $projectPath
$folderShortcut.WorkingDirectory = $projectPath
$folderShortcut.Description = "밀라이두 챗봇 프로젝트 개발 폴더"
$folderShortcut.Save()

Write-Step "로그인 시 객실 감시 자동 실행 등록"
$taskName = "Milaidhoo Room Monitor"
$monitorScriptPath = Join-Path $projectPath "launch_monitor.ps1"
$taskAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $monitorScriptPath + '"')
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $taskAction `
    -Trigger $taskTrigger `
    -Principal $taskPrincipal `
    -Settings $taskSettings `
    -Description "사용자 로그인 후 밀라이두 객실 감시를 자동 시작합니다." `
    -Force | Out-Null

Write-Host "`n설치가 완료되었습니다." -ForegroundColor Green
Write-Host "객실 감시: 바탕화면의 '밀라이두 객실감시' 아이콘"
Write-Host "자동 실행: 사용자 로그인 시 'Milaidhoo Room Monitor' 작업"
Write-Host "개발 폴더: 바탕화면의 '밀라이두 개발폴더' 아이콘"
Write-Host "Cloudflare 로그인: Cloudflare_로그인.bat"
Write-Host "Cloudflare 배포: Cloudflare_배포.bat"
