# Session Orchestrator

Claude Code(데스크톱 앱) 세션 전체를 한 화면에서 보는 **로컬 실시간 칸반 보드**.
세션이 지금 도는지, 무슨 도구를 쓰는지, 토큰을 얼마나 쓰는지, 세션끼리 주고받은 요청이 어디서 물려 있는지를 1~2초 간격으로 보여 주고, 카드에서 바로 그 세션에 말을 보내거나 보관·삭제할 수 있다.

## 화면

- **열 4개:** 내 답 대기 · 실행 중 · 완료 · 정리 후보(영업일 3일 넘게 조용한 세션). 열 안은 최근 활동 순(방금 움직인 세션이 맨 위)
- **도는 중 판정:** 응답·도구 실행 중이거나, 턴을 끝냈어도 백그라운드 명령 결과를 기다리는 세션(사이드바의 "진행 중"과 같게)
- **판정 뒤 답한 세션:** 판정 뒤에 앱(또는 상황판 채팅)에서 직접 답했으면 판정 열 대신 실시간 상태로(돌면 실행 중, 다시 질문하면 내 답 대기, 쉬면 완료)
- **낡은 판정 표시(v1.0.1):** 판정 뒤에 세션이 더 움직였으면 카드의 마지막 활동은 실제 마지막 기록, 마지막 결과는 `↻` + Claude 앱이 턴마다 적는 상태 요약(사이드바와 같은 문구)으로 보여 준다. 상세 창엔 노란 띠가 뜨고, 턴이 끝난 세션이면 열자마자 관제 세션에 다시 판정을 맡긴다(같은 움직임엔 한 번만)
- **실시간 카드:** 상태 점(초록 = 도는 중 · 노랑 = 도구 뒤 응답 없음 · 강조색 = 질문하고 기다림 · 빈 원 = 쉼), 지금 쓰는 도구, 오늘 출력 토큰과 비중 막대. 도는 세션은 실행 중 열로 자동 이동
- **질문 대기:** 세션이 작업자에게 묻고 멈추면(다음 단계 질문 · AskUserQuestion, 물음표나 "답해 주시면" 류로 끝난 마지막 문장은 추정) 내 답 대기 열로 옮기고 카드 맨 위에 질문·선택지 띠. 상세 창의 선택지 버튼을 누르면 바로 그 세션에 보낸다. 커밋·푸시·삭제·발송 같은 승인 선택지는 다른 세션을 거치면 받는 세션이 승인으로 받지 않으므로 Claude 앱에서 그 세션을 여는 링크(↗)
- **판정 전 새 세션:** 판정 뒤 생긴 세션도 Claude 앱의 세션 기록으로 바로 짝지어 링크 있는 「· 판정 전」 카드로(드로어·보관·삭제·채팅 다 됨). 앱 기록에도 없으면 임시 카드에 「🔗 세션 찾기」(관제가 짝지음). 다른 곳에서 삭제·보관된 세션은 판정 카드라도 앱 기록으로 바로 빠진다
- **카드 제목 = 세션 이름:** 모든 레이아웃에서 세션 링크가 카드 맨 위 제목, 그 아래 질문 띠 · 불꽃 · 할 일 · 결과
- **앱 판정으로 교정:** 앱이 턴마다 매기는 상태(끝남·막힘)를 따라, 글로만 추정한 질문은 앱이 끝남으로 봤으면 질문으로 치지 않고, 질문 문장이 없어도 앱이 막힘으로 봤으면 앱이 적은 할 일로 내 답 대기에 올린다
- **세션 연결:** 세션끼리 보낸 요청을 미처리 / 처리 중 / 처리됨 / 회신함으로 판정해 카드 배지로 표시, 카드에 마우스를 올리면 연결선
- **도구줄:** 검색 · 섹션 칩 · 🛰 관제 말 걸기(「갱신」이라고 보내면 다시 판정) · 실시간 합계 · 🔗 연결선 · 🕒 타임라인 · 🟩 히트맵 · 📌 답 대기 고정
- **세션 타임라인:** 세션마다 활동 막대, 세션 간 메시지는 그 시각의 세로선(6시간 / 24시간 / 7일)
- **활동 히트맵:** 최근 7일 요일×시간 출력 토큰
- **열 지표:** 물림 수 · 오늘 토큰, 내 답 대기가 3개를 넘으면 빨강, 영업일 1일 넘게 답을 기다린 카드엔 🔥
- **카드 상세(JIRA 이슈 보기 식):** 속성 · 내가 할 일 · 요약 · 백그라운드 작업(도는 것·한 시간 안에 끝난 것) · 마지막 결과 · 실시간 · 연결 · 채팅 · 대화, 숨기기 · 보관 · 삭제. 왼쪽 가장자리를 끌어 너비 조절, 바깥을 누르면 닫힘
- **숨기기 · 보관 · 삭제:** 모든 열의 카드와 상세 창에 있다. 지금 도는 세션이면 확인창에서 "작업이 멈출 수 있음"을 한 번 더 알린다
- **⚙ 모양(네 축, 어떤 축도 다른 축 값을 바꾸지 않음):** 레이아웃 5종(칸반 · 갤러리 · 노트 · 목록 · 레일) × 스킨 10종(플랫 · Primary · Brutalist · Material · Glass · Neumorph · Sketch · Terminal · Neon · Editorial — 질감만, 모든 레이아웃에 똑같이 입혀짐) × 색 16종 × 라이트/다크, 그리고 셋을 한 번에 정하는 프리셋 12개(AnuPpuccin · Royal Velvet · Shimmering Focus 같은 옵시디언 커뮤니티 테마 포함). 강조색 위 글자색은 대비로 자동 선택. 기본값은 칸반 + Primary
- **트레이 상주:** 로고(레인 막대 4개) 아이콘. 도는 세션이 있으면 막대가 오르내리고, 내 답 대기(질문 포함)는 빨간 숫자 배지. 질문 · 턴 끝남 · 멈춤 · 30분째 미처리 요청 · 새 답 대기를 윈도우 알림으로. 왼쪽 클릭 = 상황판 창
- **로고:** 같은 모양을 트레이 · 파비콘(Edge 앱 창 아이콘) · 제목 옆 · 바로가기 아이콘(`scripts/board-logo.ico`)에 쓴다
- **대화(v1.3.0):** 상세 창에서 그 세션의 대화 전문을 읽을 수 있다(읽기 전용, 표 · 굵게 · 코드 그대로). 처음엔 최근 부분만 읽고 「이전 대화 더 보기」로 앞부분, 열어 둔 동안 새 대화가 이어 붙는다
- 세션 이름 · 「↗」는 `claude://` 링크다. 상황판 창(Edge 앱 창)은 이 링크를 앱에 못 넘기므로 상황판 서버가 대신 Claude 앱의 그 세션을 연다

## 설치(팀원용, Windows)

macOS 는 아래 [macOS 설치](#macos-설치) 절(dmg 앱).

**필요한 것:** Windows 10/11 · Claude 데스크톱 앱(Code 탭) · Edge. Python 은 필요 없다(zip 안에 들어 있음).

1. [Releases](../../releases/latest) 에서 `SessionOrchestrator-<버전>-win.zip` 을 받아 원하는 곳에 푼다(예: 바탕화면의 도구 폴더). `SessionOrchestrator` 폴더가 생긴다.
2. 그 폴더의 `SessionOrchestrator.exe` 를 두 번 클릭한다. **압축을 푼 그 자리에서 돈다** — 다른 곳으로 복사하지 않는다.
   - **처음 실행은 1~2분 동안 아무 반응이 없을 수 있다.** 보안 프로그램(백신)이 처음 보는 파일을 검사하는 시간이다. 여러 번 누르지 말고 기다린다.
   - "Windows의 PC 보호" 창이 뜨면 「추가 정보 → 실행」. 서명 없는 exe 라서 뜬다.
   - 같은 폴더의 `_internal` 폴더는 프로그램의 일부다. 지우거나 exe 만 따로 옮기지 않는다.
3. 처음이면 감시할 작업 폴더(Claude Code 세션을 여는 폴더)를 고르는 창이 뜬다. 고르면 「준비했습니다」 창이 뜬다.
4. 시계 옆 트레이에 막대 4개 아이콘이 생긴다. 누르면 상황판이 열리고, 그 폴더의 세션이 바로 실시간으로 보인다. exe 를 다시 두 번 클릭해도 상황판이 열린다.
5. Claude 앱에서 그 작업 폴더로 새 세션을 열어 「관제 (상황판)」 처럼 이름을 바꾸고 사이드바에 고정한 뒤 `/board` 를 보낸다. 이때부터 내 답 대기 · 완료 · 정리 후보 판정과 카드 채팅 · 보관 · 삭제가 켜진다.

| 무엇 | 어디 |
|---|---|
| 프로그램 | 압축을 푼 `SessionOrchestrator` 폴더(`SessionOrchestrator.exe` + `_internal`) |
| 판정 데이터 · 로그 · 설정(`config.json` 의 `workspace`) | `%USERPROFILE%\SessionOrchestrator\board` |
| `/board` 스킬(사용자 단위) | `%USERPROFILE%\.claude\skills\board\SKILL.md` (명령은 그 exe 로 돈다) |
| 로그인 자동 실행 | 레지스트리 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 의 `SessionOrchestrator`(트레이 메뉴로 켜고 끔) |

- **업데이트:** 트레이가 켜질 때와 6시간마다 [최신 릴리스](../../releases/latest)를 확인해, 새 버전이면 알림만 띄운다(누르면 릴리스 페이지). 새 zip 을 받아 풀고 그 안의 `SessionOrchestrator.exe` 를 한 번 실행하면, 떠 있는 옛 트레이를 닫고 설정 · 스킬 · 자동 실행을 새 exe 에 맞춘 뒤 다시 띄운다(작업 폴더 · 판정 데이터는 그대로). 그다음 옛 폴더는 지워도 된다. 트레이 메뉴 「새 버전 알림」으로 끄고, 「새 버전 확인」으로 바로 확인한다.
- **프로그램 폴더를 옮기면:** 옮긴 곳의 `SessionOrchestrator.exe` 를 한 번 실행한다(스킬 · 자동 실행이 새 경로로 바뀐다).
- **v1.2.0 이하에서 넘어올 때:** 위 업데이트와 같다. v1.3.0 exe 를 처음 실행하면 예전 단일 exe(`%USERPROFILE%\SessionOrchestrator\SessionOrchestrator.exe`)와 업데이트 찌꺼기를 지운다. v1.2.0 의 자동 업데이트는 v1.3.0 을 받지 못하므로(받을 exe 가 없음) 한 번은 손으로 받아야 한다.
- **알림:** 「Session Orchestrator」 이름과 로고로 뜬다. 트레이 메뉴 「알림 시험 보내기」로 확인. 안 보이면 Windows 설정 → 시스템 → 알림에서 Session Orchestrator 를 켠다.
- **제거:** 명령 프롬프트에서 `"<푼 폴더>\SessionOrchestrator.exe" uninstall` (판정 데이터를 남기려면 끝에 ` --keep-data`). 트레이 · 자동 실행 · 스킬 · 데이터 폴더를 지운다. 끝난 뒤 압축을 푼 프로그램 폴더를 직접 지운다.
- **Python 으로 깔고 싶으면(예전 방식):** Release 의 `SessionOrchestrator-<버전>-python.zip` 을 풀고 `install.cmd`. Python 3.9 이상이 필요하고, 제거는 `uninstall.cmd`.
- **감시 폴더 바꾸기:** `%USERPROFILE%\SessionOrchestrator\board\config.json` 의 `workspace` 를 고치고 트레이를 다시 띄운다(압축 파일 설치는 `install.cmd -Workspace "C:\다른\폴더"`).
- **「이 페이지에 연결할 수 없음」이 뜨면:** 트레이가 상황판 서버를 못 띄운 것이다. 트레이 아이콘에 마우스를 올리거나 오른쪽 클릭 메뉴의 「서버:」 줄에 이유가 보이고, 그 상태에서 아이콘을 누르면 빈 창 대신 이유를 알림으로 띄운다(알림을 누르면 로그). 포트 18799 를 못 쓰는 PC(Hyper-V · WSL · Docker 가 Windows 에 포트 범위를 예약해 둔 경우 등)면 트레이가 28799 등 다른 포트로 옮겨 `board\config.json` 의 `port` 에 적는다. 로그 `board\tray.log`. 설치 끝에도 서버가 응답하는지 확인하고, 안 되면 로그 끝부분을 보여 준다.
- **토큰:** 실시간 화면(상태 · 토큰 · 연결)은 토큰을 쓰지 않는다. 판정 · 요약 · 채팅 전달은 관제 세션이 하므로 그 세션의 사용량으로 잡힌다.
- **한계:** Claude 앱 내부 파일(`%LOCALAPPDATA%\Claude-Data\…`)을 읽어 세션 이름 · 보관 여부를 맞춘다. 앱 업데이트로 형식이 바뀌면 그 부분만 꺼지고 나머지는 돈다. 트레이는 PC 한 대에 하나만 뜬다(포트 기본 18799).

## macOS 설치

> **사람이 쓰는 Mac 에서는 아직 시험 전이다.** 릴리스 빌드(`build-mac.yml`)가 매번 GitHub macOS 러너(애플 실리콘)에서 dmg 의 앱을 실제로 설치 · 제거해 본다 — 11-11 첫 통과 때 서버 응답 · 스킬 명령(Python 없이) · 메뉴 막대 아이콘 · Safari 화면 · 제거, `install.sh`(bash 3.2) 설치 · 제거까지 확인했다. 사람이 확인해야 할 것은 처음 열 때의 「그래도 열기」 · 작업 폴더 고르는 창 · 알림 · 메뉴 클릭 · 로그아웃 뒤 자동 실행 · 인텔 Mac 이다. 처음 쓰는 팀원은 아래 [macOS 시험 절차](#macos-시험-절차mac-쓰는-팀원용)를 따라 결과를 알려 주면 된다.

**필요한 것:** macOS 11 이상(애플 실리콘 · 인텔) · Claude 데스크톱 앱(Code 탭) · 기본 브라우저. **Python 은 필요 없다**(앱 안에 들어 있다).

1. [Releases](../../releases/latest) 에서 `SessionOrchestrator-mac.dmg`([항상 최신 바로 받기](../../releases/latest/download/SessionOrchestrator-mac.dmg))를 받아 연다.
2. 창에 보이는 **Session Orchestrator** 를 옆의 **Applications(응용 프로그램)** 폴더로 끌어다 놓는다. dmg 안에서 바로 열면 설치하지 않고 「응용 프로그램 폴더로 옮긴 뒤 여세요」라고 안내한다.
3. 응용 프로그램 폴더(또는 Launchpad · Spotlight)에서 Session Orchestrator 를 연다. Apple 개발자 서명이 없어 처음엔 경고가 뜬다 → 시스템 설정 → 개인정보 보호 및 보안 → 아래쪽 「그래도 열기」 → 다시 연다.
4. 감시할 작업 폴더(Claude Code 세션을 여는 폴더)를 고르는 창이 뜬다. 고르면 설치가 끝나고 기본 브라우저에 상황판이 열리며, 메뉴 막대에 막대 4개 아이콘이 생긴다.
5. 관제 세션은 Windows 와 같다(Claude 앱에서 그 작업 폴더로 새 세션 → 이름 바꾸고 고정 → `/board`).

| 무엇 | 어디 |
|---|---|
| 앱 | `/Applications/Session Orchestrator.app` (Python 내장, 애플 실리콘 · 인텔 공용) |
| 도구 | `~/SessionOrchestrator/scripts` (앱을 열 때 앱 판에 맞춰 복사) |
| 판정 데이터 · 로그 · 설정 | `~/SessionOrchestrator/board` (`config.json` · `agent.log` · `app.log` · `agent-status.json`) |
| `/board` 스킬(사용자 단위) | `~/.claude/skills/board/SKILL.md` — 스킬의 `PY` 가 앱 실행 파일이다(파이썬처럼 `-I 스크립트.py` 를 돌린다) |
| 로그인 자동 실행 | `~/Library/LaunchAgents/local.session-orchestrator.plist` (launchd, 비정상 종료면 다시 띄움) |

- **메뉴 막대 아이콘(Windows 트레이 자리):** 도는 세션이 있으면 막대가 오르내리고, 내 답 대기가 있으면 옆에 숫자. 누르면 메뉴 — 상황판 열기 · 알림 받기 · 알림 시험 보내기 · 로그인 시 자동 실행 · 상태 3줄 · 종료(다음 로그인 때 다시 뜸). 독 · ⌘Tab 에는 안 나온다.
- **다시 열기:** 메뉴 막대 아이콘 → 상황판 열기, 또는 앱을 다시 연다(떠 있으면 상황판만 열린다).
- **알림:** 질문 · 턴 끝남 · 멈춤 · 30분째 미처리 요청 · 새 답 대기(트레이와 같은 조건). `osascript` 로 띄워 「스크립트 편집기」 이름으로 뜬다 — 안 보이면 시스템 설정 → 알림 → 스크립트 편집기를 허용. 알림을 눌러도 상황판은 열리지 않는다.
- **상태 보기(터미널):** `"/Applications/Session Orchestrator.app/Contents/MacOS/SessionOrchestrator" status` — 상주 · 서버 · 내 답 대기 수 · 서버를 못 띄운 이유.
- **업데이트:** 새 dmg 의 앱을 응용 프로그램 폴더에 바꿔 놓고 한 번 연다(도구 · 스킬 · 자동 실행을 새 앱에 맞추고 상주를 다시 띄운다). 작업 폴더 · 포트 · 판정 데이터는 그대로. 열지 않아도 다음 로그인 때 상주가 새 도구로 맞춘다.
- **감시 폴더 바꾸기:** `"/Applications/Session Orchestrator.app/Contents/MacOS/SessionOrchestrator" setup --workspace ~/다른/폴더`
- **제거:** `"/Applications/Session Orchestrator.app/Contents/MacOS/SessionOrchestrator" uninstall` (`--keep-data` 면 판정 데이터는 남김) → 앱을 휴지통으로.
- **「백그라운드 항목이 추가됨」 알림(macOS 13 이상, 예상):** 자동 실행을 등록하면 뜰 수 있다. 시스템 설정 → 일반 → 로그인 항목의 「백그라운드에서 허용」에서 끄면 로그인 때 상주가 뜨지 않는다.
- **터미널 설치(대안, Python 3.9 이상 필요):** 저장소 화면의 Code → Download ZIP 을 풀고 터미널에서 그 폴더로 가 `bash install.sh`. 메뉴 막대 아이콘 없이 돈다(상태는 `python3 -I ~/SessionOrchestrator/scripts/board-agent.py --status`, 제거는 `bash uninstall.sh`). 앱과 같은 폴더 · 같은 자동 실행 이름을 써서, 나중에 앱을 열면 앱이 이어받는다.
- **Windows 와 다른 점:** Claude 앱 세션 기록(사이드바 id · 보관 여부 · 앱의 턴별 판정)을 읽지 않는다. Mac 에서 그 파일 위치를 아직 확인하지 않아 끈 상태다. 그래서
  - 판정 전 새 세션은 「🔗 세션 찾기」 카드로 뜬다(관제가 짝지음).
  - 앱에서 삭제 · 보관한 세션은 바로 빠지지 않고 다음 「갱신」 판정을 따른다.
  - 글로만 추정한 질문은 질문 띠만 보이고 열은 옮기지 않는다(앱 판정으로 바로잡는 기능이 꺼짐).
  - 판정 뒤 움직인 카드의 `↻` 뒤에는 앱 상태 요약 대신 마지막 문장 첫 줄이 나온다.

### macOS 시험 절차(Mac 쓰는 팀원용)

단계마다 「기대」와 다르면 그 단계 번호와 화면 · 출력을 그대로 보내 주면 된다. 끝나면 `E status` 출력과 `~/SessionOrchestrator/board/agent.log` · `app.log` 도 같이 보내 준다. 아래에서 `E` = `"/Applications/Session Orchestrator.app/Contents/MacOS/SessionOrchestrator"`.

| # | 할 일 | 기대 |
|---|---|---|
| 1 | dmg 를 열고 Session Orchestrator 를 Applications 로 끌어다 놓는다 | 응용 프로그램 폴더에 앱이 생긴다 |
| 2 | 응용 프로그램 폴더에서 앱을 연다 | 경고 → 시스템 설정 → 개인정보 보호 및 보안 → 「그래도 열기」 → 다시 열면 열린다 |
| 3 | 작업 폴더를 고른다 | 몇 초 뒤 브라우저에 상황판, 메뉴 막대에 막대 아이콘, 「설치를 마쳤습니다」 창 |
| 4 | 메뉴 막대 아이콘을 누른다 | 상황판 열기 · 알림 받기(체크) · 알림 시험 보내기 · 로그인 시 자동 실행(체크) · 상태 3줄 · 종료 |
| 5 | 메뉴 → 알림 시험 보내기 | 「🎼 알림 시험」. 안 뜨면 시스템 설정 → 알림 → 스크립트 편집기 허용 후 다시 |
| 6 | 상황판 화면을 본다 | 그 작업 폴더의 세션 카드가 보이고, 도는 세션의 상태 점 · 토큰이 1~2초 안에 바뀐다 |
| 7 | Claude 앱에서 그 폴더의 세션에 20초 넘게 걸리는 일을 시키고 기다린다 | 도는 동안 막대가 오르내리고, 끝나면 「✅ 턴 끝남 · <세션 이름>」 알림 |
| 8 | 관제 세션을 만들고 `/board` | 내 답 대기 · 완료 열이 채워지고, 카드 채팅으로 보낸 말이 그 세션에 도착한다 |
| 9 | 로그아웃 → 다시 로그인(또는 재부팅) | 메뉴 막대 아이콘이 다시 보인다 |
| 10 | 앱을 다시 연다 | 설치 창 없이 상황판만 열린다 |
| 11 | `E uninstall` 후 앱을 휴지통으로 | 아이콘이 사라지고 상황판 주소가 안 열림 · plist · `~/.claude/skills/board` · `~/SessionOrchestrator` 없음 |

## 구성

| 파일 | 역할 |
|---|---|
| `scripts/so_app.py` | exe 시작점. 그냥 실행 = 설정을 이 exe 에 맞추고 트레이(떠 있으면 상황판 열기), `render`·`map`·`queue`·`server`·`uninstall` 하위 명령. 자기 복사 · 받은 파일 실행은 하지 않는다 |
| `.github/workflows/build-win.yml` | 버전 태그를 push 하면 GitHub Actions 가 PyInstaller 폴더형(onedir)으로 빌드해 `SessionOrchestrator-<버전>-win.zip` 으로(tkinter 제외) |
| `install.ps1` · `install.cmd` · `uninstall.cmd` | Python 으로 까는 예전 방식의 설치 · 업데이트 · 제거 |
| `scripts/so_mac.py` | macOS 앱 시작점(PyInstaller 로 `Session Orchestrator.app` 으로 묶음). 설치 · 업데이트 · 제거, 파이썬 대역(`-I 스크립트.py`) |
| `install.sh` · `uninstall.sh` | macOS 터미널 설치(대안, Python 필요) |
| `.github/workflows/build-mac.yml` | 태그 push → macOS 러너에서 앱 · dmg 빌드, 그 러너에서 설치 시험, 릴리스가 게시되면 dmg 를 붙임(아래 「릴리스 내기」) |
| `build/mac/so_mac.spec` · `build/mac/make_icns.py` | 앱 묶기 설정(universal2, 메뉴 막대용 PyObjC 포함) · 앱 아이콘 |
| `build/attach-mac.sh` · `build/release-notes.sh` | 있는 릴리스에 dmg 붙이기 · 릴리스 본문 만들기(CI 와 로컬 공용) |
| `skill/SKILL.md` | 관제 세션이 따르는 판정 · 갱신 · 요청 처리 규칙(`/board`). 설치 때 경로를 채워 사용자 스킬 폴더에 둔다 |
| `scripts/board-render.py` | 판정 JSON(`board/board-sessions.json`) → `board/session-board.html`. 필드 검사, API 토큰·요청 큐 폴더 생성 |
| `scripts/board-template.html` | 페이지 전체(레이아웃·스킨·색·프리셋·실시간·연결·타임라인·히트맵·드로어·채팅) |
| `scripts/board-server.py` | `127.0.0.1:18799`(또는 `config.json` 의 `port`). 정적 서빙(페이지·스니펫만) + 요청 적재 + 실시간 API |
| `scripts/board-tray.py` | 트레이 상주(Windows). 서버를 프로세스 안에 품고(포트가 닫혀 있으면 띄우고, 다른 서버가 죽으면 넘겨받음) 아이콘·알림을 맡는다. 표준 라이브러리 + ctypes 만 |
| `scripts/board-agent.py` | macOS 상주(launchd). 트레이와 같이 서버를 품고 알림(`osascript`)을 맡는다. PyObjC 가 있으면(앱) 메뉴 막대 아이콘, 없으면(터미널 설치) 아이콘 없이. 상태는 `--status` |
| `scripts/board_live.py` | 대화 파일(jsonl)을 이어 읽어 상태·토큰·연결·타임라인·히트맵 계산 |
| `scripts/board-map.py` | 사이드바 세션 id ↔ 대화 파일 짝짓기 |
| `scripts/board-queue.py` | 페이지 요청을 관제 세션이 처리하는 큐(`wait` · `take` · `done` · `list`) |

## 동작 방식

세 층으로 나뉜다.

| 층 | 누가 | 무엇을 | 지연 | 토큰 |
|---|---|---|---|---|
| ① 실시간 | `board_live.py`(서버 스레드)가 `~/.claude/projects/<작업 폴더 슬러그>*/<cli id>.jsonl` 을 이어 읽음 | 상태 · 도구 · 토큰 · 세션 간 연결 · 타임라인 · 히트맵 | 1~2초(SSE) | 0 |
| ② 릴레이 | 페이지 → `board-server.py` → `board/inbox/*.json` → 관제 세션이 `board-queue.py wait` 로 깨어남 | 채팅 전달(`send_message`) · 요약 · 보관 · 삭제 · 「갱신」 | 수 초 | 처리할 때만 |
| ③ 판정 | 관제 세션(`/board`) | 내 답 대기 / 완료 / 정리 후보 분류, 할 일, 요약 | 1~2분 | 씀 |

- 데스크톱 앱 세션도 대화를 jsonl 로 실시간 기록한다(`entrypoint: claude-desktop`). 줄마다 `message.usage`(토큰)와 `stop_reason` 이 있다.
- 사이드바 id(`local_…`)와 대화 파일 이름(cli id)이 다르다. 서버가 Claude 앱의 세션 기록(`%LOCALAPPDATA%\Claude-Data\…\claude-code-sessions\…\local_<id>.json`, 읽기 전용)으로 정확히 짝짓고, 판정 때 `board-map.py` 의 추정 짝(`lastActivityAt` ±3초, 실패하면 문장 검색)은 보조로 쓴다. 앱 내부 형식이라 바뀌면 추정 짝으로 돌아간다.
- 서버는 트레이(macOS 는 상주 `board-agent.py`)가 품고 로그인 때 자동으로 뜬다. 앱을 재시작하면 관제 세션의 요청 대기만 사라지고, 관제 세션에 직접 「갱신」을 치면 다시 걸린다.
- 사이드바의 파란 점(안 읽음) 같은 앱 내부 상태는 읽을 수 없어 보드에 나오지 않는다.

## 릴리스 내기

- **릴리스 등록은 손으로 한다**(Windows zip 을 받아 풀어 시험한 뒤). 본문을 「변경 사항 · 설치 · 전체 변경 내역」 형식으로 쓰려면 `bash build/release-notes.sh vX.Y.Z > notes.md` 로 만들어 `gh release create … --notes-file notes.md` 에 넘긴다.
- **macOS dmg 는 자동으로 붙는다.** 태그 `vX.Y.Z` 를 push 하면 `build-mac.yml` 이 macOS 러너에서 앱을 묶어 `SessionOrchestrator-mac.dmg`(버전 없는 이름 — 「항상 최신」 링크용, 애플 실리콘 · 인텔 공용)를 만든다.
  - 그 러너에서 dmg 의 앱을 실제로 설치 · 제거하고, `install.sh` 도 macOS 기본 bash 3.2 로 설치 · 제거해 본다. 하나라도 실패하면 붙이지 않는다. 화면 캡처 · 기록은 Actions 결과물 `mac-smoke-test`.
  - 빌드가 끝났을 때 그 태그 릴리스가 이미 있으면 바로 붙인다. 아직 없으면 기다렸다가, 릴리스를 게시하는 순간 `attach` 작업(ubuntu)이 그 빌드의 dmg 를 받아 붙인다. 이 워크플로는 릴리스를 만들지 않는다.
  - 붙일 때 본문에 macOS 설치 단락이 없으면 끝에 덧붙인다(`<!-- macos-install -->` 표시로 한 번만). Windows 용 파일은 다루지 않는다.
- 태그가 `vX.Y.Z` 꼴이 아니면 워크플로가 실패로 끝난다. Actions 탭의 수동 실행(Build macOS app → Run workflow)은 빌드 · 설치 시험만 한다.
- macOS 러너는 비공개 레포에서 사용 시간이 10배로 잡힌다(한 번에 약 10분 → 100분).

## 감시 폴더 정하는 법

- `board/config.json` 의 `"workspace"` 가 있으면 그 폴더(설치 스크립트가 쓰는 방식).
- 없으면 `scripts/` 의 상위 폴더 — 도구를 작업 폴더 리포 안에 직접 둔 경우. 이때는 그 폴더에서 `python -I scripts/board-tray.py --install` 로 트레이를 깔고, 스킬은 `skill/SKILL.md` 의 `{{HOME}}` · `{{WORKSPACE}}` · `{{PY}}` 를 채워 `.claude/skills/board/` 에 둔다.

`board/` 폴더(판정 데이터 · 토큰 · 요청 큐 · 로그)는 세션 제목에 고객사 이름이 들어갈 수 있어 커밋하지 않는다.

## 보안

- 서버는 `127.0.0.1` 에만 붙는다. 요청 API 와 실시간 API 는 토큰(`board/.board-token`, 렌더러가 만들어 페이지에 박음)이 필요하다.
- 정적 서빙은 `session-board.html` · `board-snippet.css` 만 내준다.
- 페이지로 보내는 실시간 데이터는 상태 · 도구 이름 · 마지막 문장 첫 줄 · 토큰 · 연결 요약뿐이다.

## 데이터 형식(`board/board-sessions.json`)

행 객체 배열. 필수: `session` `status`(`내 답 대기`·`실행 중`·`완료`·`정리 후보`·`관제`) `section` `todo` `due` `result` `last_active` `handed_by` `judged_at`. 선택: `link`(`claude://claude.ai/epitaxy/local_…`) `summary`(문자열 배열) `branch` `cli_id`(대화 파일 이름) `name`(세션 이름).
