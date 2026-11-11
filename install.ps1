<#
Session Orchestrator 설치 · 업데이트 · 제거 (Windows, PowerShell 5.1 이상)

  install.cmd                              설치 또는 업데이트. 감시할 작업 폴더를 묻는다(업데이트면 기존 값을 쓴다)
  install.cmd -Workspace "C:\work\repo"    묻지 않고 그 폴더로 설치
  uninstall.cmd                            트레이 종료 + 자동 실행 · /board 스킬 · 설치 폴더 제거

설치되는 곳
  도구        %USERPROFILE%\SessionOrchestrator\scripts   (판정 데이터 · 로그는 ...\board)
  /board 스킬  %USERPROFILE%\.claude\skills\board\SKILL.md
  자동 실행    시작프로그램 폴더의 "Session Orchestrator.lnk"
#>
param(
    [string]$Workspace = "",
    [string]$InstallDir = (Join-Path $env:USERPROFILE "SessionOrchestrator"),
    [switch]$Uninstall,
    [switch]$KeepData,   # 제거할 때 판정 데이터(board 폴더)는 남긴다
    [switch]$NoStart,    # 파일만 깔고 트레이는 띄우지 않는다(시험용)
    [string]$SkillRoot = (Join-Path $env:USERPROFILE ".claude\skills")   # 시험할 때만 바꾼다
)
$ErrorActionPreference = "Stop"
$Src = $PSScriptRoot
$SkillDir = Join-Path $SkillRoot "board"
$Startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Session Orchestrator.lnk"
$TrayPy = Join-Path $InstallDir "scripts\board-tray.py"
$Utf8 = New-Object System.Text.UTF8Encoding $false   # BOM 없이(파이썬 json 이 BOM 을 못 읽음)

function Say([string]$m) { Write-Host $m }
function Fail([string]$m) { Write-Host "[실패] $m" -ForegroundColor Red; exit 1 }

function Stop-Tray {
    # 이 설치 폴더의 board-tray.py 를 돌리는 프로세스만 끈다(다른 곳에 깐 트레이는 건드리지 않음)
    $pat = [Regex]::Escape($TrayPy)
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match $pat } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Say "  트레이 종료(pid $($_.ProcessId))"
    }
}

function Show-Blocked([string]$p) {
    # 덮어쓰기 · 지우기가 막힌 항목의 속성 · 소유자(원인 짐작용: 숨김 · 시스템 속성, 다른 계정 소유, 보안 프로그램 차단이 흔함)
    try {
        $it = Get-Item -LiteralPath $p -Force
        Write-Host "    $p : 속성 $($it.Attributes), 소유자 $((Get-Acl -LiteralPath $p).Owner)" -ForegroundColor Yellow
    } catch { Write-Host "    $p : 정보를 못 읽음($($_.Exception.Message))" -ForegroundColor Yellow }
}

function Clear-Attr([string]$p) {
    # 읽기 전용 · 숨김 · 시스템 속성을 푼다(이 도구가 만든 파일에 붙어 있으면 덮어쓰기가 「액세스 거부」). 풀었으면 true
    try {
        $it = Get-Item -LiteralPath $p -Force -ErrorAction Stop
        if ($it.Attributes -band [IO.FileAttributes]'ReadOnly, Hidden, System') { $it.Attributes = 'Normal'; return $true }
    } catch { }
    return $false
}

function Set-Aside([string]$p) {
    # 덮어쓸 수 없는 기존 파일을 옆 이름으로 비켜 둔다
    $leaf = (Split-Path $p -Leaf) + ".old-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    try { Rename-Item -LiteralPath $p -NewName $leaf -ErrorAction Stop }
    catch { Fail "덮어쓸 수도, 비켜 둘 수도 없습니다: $p`n  탐색기에서 이 파일을 지워 보세요(막히면 어떤 프로그램이 쓰는지 알려 줍니다). 지운 뒤 install.cmd 를 다시 실행하세요." }
    Write-Host "  [주의] 덮어쓰기가 막혀 옛 파일을 $leaf 로 비켜 두고 새로 만듭니다" -ForegroundColor Yellow
}

function Write-Text([string]$p, [string]$text) {
    try { [IO.File]::WriteAllText($p, $text, $Utf8); return } catch { }
    Show-Blocked $p
    if (Clear-Attr $p) {
        try { [IO.File]::WriteAllText($p, $text, $Utf8); Write-Host "  [주의] 읽기 전용·숨김·시스템 속성을 풀고 덮어썼습니다" -ForegroundColor Yellow; return } catch { }
    }
    Set-Aside $p
    [IO.File]::WriteAllText($p, $text, $Utf8)
}

function Move-Over([string]$from, [string]$to) {
    try { Move-Item -LiteralPath $from -Destination $to -Force -ErrorAction Stop; return } catch { }
    Show-Blocked $to
    if (Clear-Attr $to) {
        try { Move-Item -LiteralPath $from -Destination $to -Force -ErrorAction Stop; Write-Host "  [주의] 읽기 전용·숨김·시스템 속성을 풀고 덮어썼습니다" -ForegroundColor Yellow; return } catch { }
    }
    Set-Aside $to
    Move-Item -LiteralPath $from -Destination $to -Force
}

function Test-OurStartup {
    # 시작프로그램 바로가기가 이 설치 폴더의 트레이를 가리킬 때만 true
    if (-not (Test-Path -LiteralPath $Startup)) { return $false }
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($Startup)
    return ($lnk.Arguments -like "*$TrayPy*")
}

# ───────── 제거 ─────────
if ($Uninstall) {
    Say "Session Orchestrator 제거: $InstallDir"
    Stop-Tray
    if (Test-OurStartup) { Remove-Item -LiteralPath $Startup -Force; Say "  자동 실행 바로가기 삭제" }
    $skill = Join-Path $SkillDir "SKILL.md"
    if ((Test-Path -LiteralPath $skill) -and ((Get-Content -LiteralPath $skill -Raw -Encoding UTF8) -like "*$($InstallDir -replace '\\','/')*")) {
        Remove-Item -LiteralPath $SkillDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $skill) { Write-Host "  [주의] /board 스킬을 지우지 못했습니다:" -ForegroundColor Yellow; Show-Blocked $skill }
        elseif (Test-Path -LiteralPath $SkillDir) { Write-Host "  /board 스킬 삭제(막힌 옛 파일은 남음: $SkillDir)" -ForegroundColor Yellow }
        else { Say "  /board 스킬 삭제" }
    }
    if (Test-Path -LiteralPath $InstallDir) {
        $targets = if ($KeepData) { @(Get-ChildItem -LiteralPath $InstallDir -Directory -Filter "scripts*" | ForEach-Object { $_.FullName }) } else { @($InstallDir) }
        foreach ($t in $targets) { Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue }
        $left = @($targets | Where-Object { Test-Path -LiteralPath $_ })
        if ($left.Count) {
            Write-Host "  [주의] 일부를 지우지 못했습니다(막힌 파일 몇 개):" -ForegroundColor Yellow
            $left | ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -File -Force -ErrorAction SilentlyContinue } | Select-Object -First 3 | ForEach-Object { Show-Blocked $_.FullName }
            Write-Host "  탐색기에서 지워 보세요(막히면 어떤 프로그램이 쓰는지 알려 줍니다): $InstallDir" -ForegroundColor Yellow
        } else { Say $(if ($KeepData) { "  도구 삭제(board 데이터는 남김)" } else { "  설치 폴더 삭제" }) }
    }
    Say "끝. Claude 앱에서 고정해 둔 관제 세션은 직접 보관하거나 지우면 됩니다."
    exit 0
}

# ───────── 설치 · 업데이트 ─────────
Say "Session Orchestrator 설치: $InstallDir"
if (-not (Test-Path -LiteralPath (Join-Path $Src "scripts\board-tray.py"))) { Fail "압축을 푼 폴더에서 실행하세요(scripts 폴더가 없음): $Src" }

# 1) Python 3.9 이상 찾기(py 런처 → python → python3). 찾은 실행 파일 경로를 스킬에 적는다
$Py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    $exe = $cand[0]; $pre = @($cand | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $out = & $exe @pre -c "import sys; print(sys.executable); print(sys.version_info >= (3, 9))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and $out[-1] -eq "True") { $Py = $out[0].Trim(); break }
    } catch { }
}
if (-not $Py) { Fail "Python 3.9 이상이 없습니다. https://www.python.org/downloads/ 에서 설치(추가 패키지 필요 없음)한 뒤 다시 실행하세요." }
Say "  Python: $Py"

# 2) 감시할 작업 폴더(Claude Code 세션을 여는 폴더)
$CfgPath = Join-Path $InstallDir "board\config.json"
if (-not $Workspace -and (Test-Path -LiteralPath $CfgPath)) {
    try { $Workspace = (Get-Content -LiteralPath $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).workspace } catch { }
    if ($Workspace) { Say "  감시 작업 폴더(기존 설정): $Workspace" }
}
if (-not $Workspace) { $Workspace = Read-Host "감시할 작업 폴더 전체 경로(Claude Code 세션을 여는 폴더, 예: C:\work\my-repo)" }
$Workspace = $Workspace.Trim().Trim('"')
if (-not $Workspace -or -not (Test-Path -LiteralPath $Workspace -PathType Container)) { Fail "폴더가 없습니다: $Workspace" }
$Workspace = (Resolve-Path -LiteralPath $Workspace).Path.TrimEnd('\')
$slug = $Workspace -replace '[^A-Za-z0-9]', '-'
$proj = Join-Path $env:USERPROFILE ".claude\projects"
if (-not (Get-ChildItem -LiteralPath $proj -Directory -Filter "$slug*" -ErrorAction SilentlyContinue)) {
    Write-Host "  [주의] 이 폴더에서 연 Claude Code 세션 기록이 아직 없습니다. 세션을 열면 그때부터 보입니다." -ForegroundColor Yellow
}

# 3) 도구 복사: 새 폴더(scripts.new)에 넣고 폴더째 바꿔 끼운다. 기존 파일을 덮어쓰지 않으므로
#    덮어쓰기가 막힌 PC 에서도 된다. 판정 데이터(board)는 그대로 둔다
Stop-Tray
$Scripts = Join-Path $InstallDir "scripts"
$New = Join-Path $InstallDir "scripts.new"
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "board") | Out-Null
if (Test-Path -LiteralPath $New) { Remove-Item -LiteralPath $New -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path -LiteralPath $New) { $New = Join-Path $InstallDir ("scripts.new-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
New-Item -ItemType Directory -Force -Path $New | Out-Null
Copy-Item -Path (Join-Path $Src "scripts\*") -Destination $New -Force
Get-ChildItem -LiteralPath $New -File | Unblock-File
$OldName = $null
if (Test-Path -LiteralPath $Scripts) {
    $OldName = "scripts.old-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $moved = $false
    for ($i = 0; $i -lt 10 -and -not $moved; $i++) {   # 막 끈 트레이가 폴더를 놓을 때까지 잠깐 기다린다
        try { Rename-Item -LiteralPath $Scripts -NewName $OldName -ErrorAction Stop; $moved = $true } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $moved) {
        Show-Blocked $Scripts
        Fail "기존 도구 폴더를 비켜 두지 못했습니다: $Scripts`n  탐색기에서 이 폴더를 지운 뒤(막히면 어떤 프로그램이 쓰는지 알려 줍니다) install.cmd 를 다시 실행하세요."
    }
}
try { Rename-Item -LiteralPath $New -NewName "scripts" -ErrorAction Stop }
catch {
    if ($OldName) { Rename-Item -LiteralPath (Join-Path $InstallDir $OldName) -NewName "scripts" -ErrorAction SilentlyContinue }
    Fail "새 도구 폴더를 제자리에 놓지 못했습니다: $($_.Exception.Message)"
}
# 옛 폴더는 지워 보고, 안 지워지면 남겨 둔다(동작에는 영향 없음, 다음 설치 때 다시 시도). 막힌 파일은 원인 짐작용으로 보여 준다
foreach ($d in @(Get-ChildItem -LiteralPath $InstallDir -Directory -Filter "scripts.old-*") + @(Get-ChildItem -LiteralPath $InstallDir -Directory -Filter "scripts.new-*")) {
    Remove-Item -LiteralPath $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $d.FullName) {
        Write-Host "  [주의] 옛 도구 폴더를 못 지워 남겨 둡니다(동작에는 영향 없음): $($d.FullName)" -ForegroundColor Yellow
        Get-ChildItem -LiteralPath $d.FullName -File -Force | Select-Object -First 3 | ForEach-Object { Show-Blocked $_.FullName }
    }
}
$cfg = @{ workspace = $Workspace }
try { $oldPort = (Get-Content -LiteralPath $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).port; if ($oldPort) { $cfg.port = [int]$oldPort } } catch { }   # 트레이가 옮겨 둔 포트는 유지
Write-Text $CfgPath ($cfg | ConvertTo-Json)
Say "  도구 복사 · 설정 저장"

# 4) /board 스킬(사용자 단위라 어느 폴더의 세션에서든 쓸 수 있다)
$tpl = [IO.File]::ReadAllText((Join-Path $Src "skill\SKILL.md"), [Text.Encoding]::UTF8)
$skillText = $tpl.Replace("{{HOME}}", ($InstallDir -replace '\\', '/')).Replace("{{WORKSPACE}}", $Workspace).Replace("{{PY}}", ($Py -replace '\\', '/'))
New-Item -ItemType Directory -Force -Path $SkillDir | Out-Null
$skill = Join-Path $SkillDir "SKILL.md"
if ((Test-Path -LiteralPath $skill) -and -not ((Get-Content -LiteralPath $skill -Raw -Encoding UTF8) -like "*Session Orchestrator*")) {
    $bak = "$skill.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item -LiteralPath $skill -Destination $bak
    Write-Host "  [주의] 다른 board 스킬이 있어 백업했습니다: $bak" -ForegroundColor Yellow
}
Write-Text $skill $skillText
Say "  /board 스킬: $skill"

# 5) 상황판 페이지를 새 템플릿으로 다시 만든다(판정 데이터는 그대로). 전엔 처음 설치 때만 만들어,
#    /board 를 안 쓰는 팀원은 업데이트해도 옛 페이지가 남았다. 임시 파일에 만든 뒤 바꿔 끼운다
$board = Join-Path $InstallDir "board"
$rows = Join-Path $board "board-sessions.json"
$html = Join-Path $board "session-board.html"
$tmpHtml = Join-Path $board "session-board.new.html"
if (-not (Test-Path -LiteralPath $rows)) { [IO.File]::WriteAllText($rows, "[]", $Utf8) }
& $Py -I (Join-Path $InstallDir "scripts\board-render.py") $rows $tmpHtml | Out-Null
if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $tmpHtml)) { Move-Over $tmpHtml $html }
elseif (Test-Path -LiteralPath $html) { Write-Host "  [주의] 상황판 페이지를 새로 만들지 못해 옛 페이지를 그대로 씁니다(다음 /board 때 바뀜)" -ForegroundColor Yellow }
else { Fail "상황판 페이지 만들기 실패" }
# 전에 비켜 둔 옛 파일(*.old-*)은 지워 보고, 안 지워지면 그대로 둔다
foreach ($dir in @($board, $SkillDir)) {
    Get-ChildItem -LiteralPath $dir -File -Filter "*.old-*" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

# 6) 트레이: 런처 · 로그인 자동 실행 바로가기를 만들고 띄운다
if ($NoStart) { Say "  -NoStart: 트레이는 띄우지 않았습니다"; exit 0 }
& $Py -I $TrayPy --install
if ($LASTEXITCODE -ne 0) { Fail "트레이 설치 실패" }

# 7) 트레이가 서버를 실제로 띄웠는지 확인(최대 20초). 포트는 트레이가 옮겼을 수 있어 매번 config 에서 다시 읽는다
$Log = Join-Path $board "tray.log"
$okUrl = $null
$deadline = (Get-Date).AddSeconds(20)   # 닫힌 포트는 한 번 묻는 데 2초쯤 걸려 횟수가 아니라 시각으로 끊는다
while (-not $okUrl -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $port = 18799
    try { $p = (Get-Content -LiteralPath $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).port; if ($p) { $port = [int]$p } } catch { }
    try {
        $wc = New-Object System.Net.WebClient; $wc.Proxy = $null   # 시스템 프록시를 거치지 않게
        if ($wc.DownloadString("http://127.0.0.1:$port/api/health") -match '"ok"') { $okUrl = "http://127.0.0.1:$port/" }
    } catch { }
}
if ($okUrl) {
    Say "  상황판 서버 확인: $okUrl"
} else {
    Write-Host ""
    Write-Host "[주의] 20초 안에 상황판 서버가 응답하지 않았습니다. 트레이 로그 끝부분:" -ForegroundColor Yellow
    if (Test-Path -LiteralPath $Log) { Get-Content -LiteralPath $Log -Tail 15 -Encoding UTF8 | ForEach-Object { Write-Host "    $_" } }
    else { Write-Host "    (로그 없음 — 트레이가 시작되지 않았습니다. 시계 옆 트레이에 막대 아이콘이 있는지 확인하세요)" }
    Write-Host "  이 내용을 그대로 전달해 주세요. 로그 파일: $Log" -ForegroundColor Yellow
}

Say ""
Say "설치 끝. 다음 순서로 시작하세요."
Say "  1) 시계 옆 트레이의 막대 4개 아이콘을 누르면 상황판이 열립니다(세션은 바로 실시간으로 보임)."
Say "  2) Claude 앱에서 위 작업 폴더로 새 세션을 열고 이름을 「관제 (상황판)」 처럼 바꿔 사이드바에 고정합니다."
Say "  3) 그 세션에서 /board 를 보내면 내 답 대기 · 완료 · 정리 후보 판정과 카드 채팅이 켜집니다."
Say "  업데이트: 새 압축 파일을 풀고 install.cmd 를 다시 실행.  제거: uninstall.cmd"
