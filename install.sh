#!/bin/bash
# Session Orchestrator 설치 · 업데이트 · 제거 (macOS)
#
#   bash install.sh                              설치 또는 업데이트. 감시할 작업 폴더를 묻는다(업데이트면 기존 값을 쓴다)
#   bash install.sh --workspace ~/work/my-repo   묻지 않고 그 폴더로 설치
#   bash install.sh --no-start                   파일만 깔고 상주는 띄우지 않는다(시험용)
#   bash uninstall.sh [--keep-data]              상주 · 로그인 자동 실행 · /board 스킬 · 설치 폴더 제거(--keep-data 면 판정 데이터는 남김)
#
# 설치되는 곳
#   도구              ~/SessionOrchestrator/scripts   (판정 데이터 · 로그는 ~/SessionOrchestrator/board)
#   /board 스킬       ~/.claude/skills/board/SKILL.md
#   로그인 자동 실행  ~/Library/LaunchAgents/local.session-orchestrator.plist
#
# 시험용 환경변수: SO_INSTALL_DIR(설치 폴더) · SO_SKILL_ROOT(스킬 폴더의 부모) · SO_PYTHON(쓸 파이썬) · SO_ANY_OS=1(macOS 확인 건너뜀)
# macOS 기본 bash 3.2 에서 돌게 쓴다(연관 배열 · mapfile 같은 bash 4 기능, $( ) 안의 heredoc 을 쓰지 않음).

set -u

SRC="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${SO_INSTALL_DIR:-$HOME/SessionOrchestrator}"
SKILL_ROOT="${SO_SKILL_ROOT:-$HOME/.claude/skills}"
SKILL_DIR="$SKILL_ROOT/board"
SKILL="$SKILL_DIR/SKILL.md"
LABEL="local.session-orchestrator"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AGENT="$INSTALL_DIR/scripts/board-agent.py"
BOARD="$INSTALL_DIR/board"
CFG="$BOARD/config.json"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m[실패] %s\033[0m\n' "$*"; exit 1; }

WORKSPACE=""
NO_START=0
UNINSTALL=0
KEEP_DATA=0
while [ $# -gt 0 ]; do
  case "$1" in
    --workspace|-w) [ $# -ge 2 ] || fail "--workspace 뒤에 폴더 경로를 주세요"; WORKSPACE="$2"; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --keep-data) KEEP_DATA=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) fail "모르는 인자: $1 (bash install.sh --help 참고)" ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ] && [ "${SO_ANY_OS:-}" != "1" ]; then
  fail "macOS 용 설치 스크립트입니다. Windows 는 install.cmd 를 쓰세요."
fi

stop_agent() {
  # launchd 등록을 내리고(상주도 꺼짐), 터미널에서 직접 띄운 이 설치 폴더의 상주도 끈다. 잠금 · 포트를 놓을 때까지 최대 5초 기다린다
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
      || { [ -f "$PLIST" ] && launchctl unload "$PLIST" >/dev/null 2>&1; } || true
  fi
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "$AGENT" >/dev/null 2>&1 || true
    i=0
    while pgrep -f "$AGENT" >/dev/null 2>&1 && [ $i -lt 10 ]; do sleep 0.5; i=$((i + 1)); done
  fi
}

# ───────── 제거 ─────────
if [ "$UNINSTALL" = 1 ]; then
  say "Session Orchestrator 제거: $INSTALL_DIR"
  stop_agent
  if [ -f "$PLIST" ] && grep -qF "$AGENT" "$PLIST"; then
    rm -f "$PLIST" && say "  로그인 자동 실행(LaunchAgent) 삭제"
  fi
  if [ -f "$SKILL" ] && grep -qF "$INSTALL_DIR" "$SKILL"; then
    # 설치 때 백업해 둔 다른 board 스킬(SKILL.md.bak-*)이 있으면 폴더째 지우지 않는다
    rm -f "$SKILL" && rmdir "$SKILL_DIR" 2>/dev/null
    if [ -d "$SKILL_DIR" ]; then say "  /board 스킬 삭제(백업 파일은 남김: $SKILL_DIR)"; else say "  /board 스킬 삭제"; fi
  fi
  case "$INSTALL_DIR" in
    ""|"/"|"$HOME"|"$HOME/") fail "설치 폴더 경로가 이상해 지우지 않습니다: $INSTALL_DIR" ;;
  esac
  if [ -d "$INSTALL_DIR" ]; then
    if [ ! -f "$INSTALL_DIR/scripts/board-server.py" ] && [ ! -f "$CFG" ]; then
      warn "  [주의] Session Orchestrator 설치 폴더로 보이지 않아 남겨 둡니다: $INSTALL_DIR"
    elif [ "$KEEP_DATA" = 1 ]; then
      rm -rf "$INSTALL_DIR/scripts" "$INSTALL_DIR"/scripts.* && say "  도구 삭제(board 데이터는 남김)"
    else
      rm -rf "$INSTALL_DIR" && say "  설치 폴더 삭제"
    fi
  fi
  say "끝. Claude 앱에서 고정해 둔 관제 세션은 직접 보관하거나 지우면 됩니다."
  exit 0
fi

# ───────── 설치 · 업데이트 ─────────
say "Session Orchestrator 설치: $INSTALL_DIR"
[ -f "$SRC/scripts/board-agent.py" ] || fail "압축을 푼 폴더에서 실행하세요(scripts/board-agent.py 가 없음): $SRC"

# 1) Python 3.9 이상 찾기. 찾은 실행 파일 경로를 스킬 · LaunchAgent 에 적는다(launchd 는 PATH 가 짧아 절대 경로가 필요)
PY=""
for cand in "${SO_PYTHON:-}" python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$cand" ] || continue
  p="$(command -v "$cand" 2>/dev/null)" || continue
  [ -n "$p" ] || continue
  ok="$("$p" -c 'import sys; print(sys.version_info >= (3, 9))' 2>/dev/null | tr -d '\r')"
  if [ "$ok" = "True" ]; then PY="$p"; break; fi
done
[ -n "$PY" ] || fail "Python 3.9 이상이 없습니다. https://www.python.org/downloads/ 에서 macOS 용을 설치(추가 패키지 필요 없음)하거나 터미널에서 xcode-select --install 로 개발 도구를 깐 뒤 다시 실행하세요."
say "  Python: $PY"

# 파이썬 조각들. bash 3.2 는 $( ) 안의 heredoc 을 잘못 읽는 일이 있어 -c 문자열로 넘긴다(안에 작은따옴표를 쓰지 않음)
PY_NORM='import os, re, sys
s = sys.argv[1].strip()
if len(s) >= 2 and s[0] == s[-1] and s[0] in "\x27\x22":
    s = s[1:-1]
if not os.path.exists(os.path.expanduser(s)):
    s = re.sub(r"\\(.)", r"\1", s)
print(os.path.expanduser(s))'
PY_SLUG='import re, sys
print(re.sub(r"[^A-Za-z0-9]", "-", sys.argv[1]))'
PY_GETWS='import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("workspace") or "")'
PY_CFG='import json, sys
p, ws = sys.argv[1], sys.argv[2]
cfg = {"workspace": ws}
try:
    port = json.load(open(p, encoding="utf-8")).get("port")
    if port:
        cfg["port"] = int(port)
except Exception:
    pass
with open(p, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=1)'
PY_SKILL='import sys
src, dst, home, ws, py = sys.argv[1:6]
t = open(src, encoding="utf-8").read()
t = t.replace("{{HOME}}", home).replace("{{WORKSPACE}}", ws).replace("{{PY}}", py)
with open(dst, "w", encoding="utf-8") as f:
    f.write(t)'
PY_PORT='import json, sys
try:
    print(int(json.load(open(sys.argv[1], encoding="utf-8")).get("port") or 18799))
except Exception:
    print(18799)'

# 2) 감시할 작업 폴더(Claude Code 세션을 여는 폴더)
if [ -z "$WORKSPACE" ] && [ -f "$CFG" ]; then
  WORKSPACE="$("$PY" -I -c "$PY_GETWS" "$CFG" 2>/dev/null | tr -d '\r')"
  [ -n "$WORKSPACE" ] && say "  감시 작업 폴더(기존 설정): $WORKSPACE"
fi
if [ -z "$WORKSPACE" ]; then
  printf '감시할 작업 폴더 전체 경로(Claude Code 세션을 여는 폴더. Finder 에서 폴더를 이 창으로 끌어 놓아도 됨): '
  read -r WORKSPACE || true
fi
# 끌어 놓기로 붙는 끝 공백 · 따옴표 · 공백 앞 역슬래시를 걷어 내고 ~ 를 펼친다
WORKSPACE="$("$PY" -I -c "$PY_NORM" "$WORKSPACE" | tr -d '\r')"
[ -n "$WORKSPACE" ] && [ -d "$WORKSPACE" ] || fail "폴더가 없습니다: $WORKSPACE"
WORKSPACE="$(cd "$WORKSPACE" && pwd -P)"   # 실제 경로(대화 폴더 이름이 이 경로로 만들어짐)
SLUG="$("$PY" -I -c "$PY_SLUG" "$WORKSPACE" | tr -d '\r')"
if ! ls -d "$HOME/.claude/projects/$SLUG"* >/dev/null 2>&1; then
  warn "  [주의] 이 폴더에서 연 Claude Code 세션 기록이 아직 없습니다. 세션을 열면 그때부터 보입니다."
fi

# 3) 도구 복사: 새 폴더(scripts.new)에 넣고 폴더째 바꿔 끼운다. 판정 데이터(board)는 그대로 둔다
stop_agent
mkdir -p "$BOARD" || fail "설치 폴더를 만들지 못했습니다: $BOARD"
NEW="$INSTALL_DIR/scripts.new"
rm -rf "$NEW"
mkdir -p "$NEW" || fail "임시 폴더를 만들지 못했습니다: $NEW"
for f in "$SRC"/scripts/*; do
  [ -f "$f" ] || continue   # __pycache__ 같은 폴더는 건너뛴다
  cp "$f" "$NEW/" || fail "도구 복사 실패: $f"
done
OLD=""
if [ -d "$INSTALL_DIR/scripts" ]; then
  OLD="$INSTALL_DIR/scripts.old-$STAMP"
  mv "$INSTALL_DIR/scripts" "$OLD" || fail "기존 도구 폴더를 비켜 두지 못했습니다: $INSTALL_DIR/scripts"
fi
if ! mv "$NEW" "$INSTALL_DIR/scripts"; then
  [ -n "$OLD" ] && mv "$OLD" "$INSTALL_DIR/scripts"
  fail "새 도구 폴더를 제자리에 놓지 못했습니다: $INSTALL_DIR/scripts"
fi
rm -rf "$INSTALL_DIR"/scripts.old-* 2>/dev/null || true
# 상주가 옮겨 둔 포트는 유지한다
"$PY" -I -c "$PY_CFG" "$CFG" "$WORKSPACE" || fail "설정 저장 실패: $CFG"
say "  도구 복사 · 설정 저장"

# 4) /board 스킬(사용자 단위라 어느 폴더의 세션에서든 쓸 수 있다)
mkdir -p "$SKILL_DIR" || fail "스킬 폴더를 만들지 못했습니다: $SKILL_DIR"
if [ -f "$SKILL" ] && ! grep -q "Session Orchestrator" "$SKILL"; then
  cp "$SKILL" "$SKILL.bak-$STAMP" && warn "  [주의] 다른 board 스킬이 있어 백업했습니다: $SKILL.bak-$STAMP"
fi
"$PY" -I -c "$PY_SKILL" "$SRC/skill/SKILL.md" "$SKILL" "$INSTALL_DIR" "$WORKSPACE" "$PY" || fail "/board 스킬 쓰기 실패: $SKILL"
say "  /board 스킬: $SKILL"

# 5) 상황판 페이지를 새 템플릿으로 다시 만든다(판정 데이터는 그대로). 임시 파일에 만든 뒤 바꿔 끼운다
ROWS="$BOARD/board-sessions.json"
HTML="$BOARD/session-board.html"
TMP_HTML="$BOARD/session-board.new.html"
[ -f "$ROWS" ] || printf '[]' > "$ROWS"
if "$PY" -I "$INSTALL_DIR/scripts/board-render.py" "$ROWS" "$TMP_HTML" >/dev/null && [ -f "$TMP_HTML" ]; then
  mv -f "$TMP_HTML" "$HTML"
elif [ -f "$HTML" ]; then
  warn "  [주의] 상황판 페이지를 새로 만들지 못해 옛 페이지를 그대로 씁니다(다음 /board 때 바뀜)"
else
  fail "상황판 페이지 만들기 실패"
fi

# 6) 상주: LaunchAgent 를 쓰고 launchctl 로 띄운다(로그인할 때마다 자동 실행)
if [ "$NO_START" = 1 ]; then say "  --no-start: 상주는 띄우지 않았습니다"; exit 0; fi
"$PY" -I "$AGENT" --install --python "$PY" || fail "상주 등록 실패"

# 7) 상주가 서버를 실제로 띄웠는지 확인(최대 20초). 포트는 상주가 옮겼을 수 있어 매번 config 에서 다시 읽는다
URL=""
END=$((SECONDS + 20))
while [ -z "$URL" ] && [ "$SECONDS" -lt "$END" ]; do
  sleep 0.5
  port="$("$PY" -I -c "$PY_PORT" "$CFG" | tr -d '\r')"
  if curl -s --noproxy '*' --max-time 2 "http://127.0.0.1:$port/api/health" 2>/dev/null | grep -q '"ok"'; then
    URL="http://127.0.0.1:$port/"
  fi
done
if [ -n "$URL" ]; then
  say "  상황판 서버 확인: $URL"
  "$PY" -I "$AGENT" --open >/dev/null 2>&1 || true
else
  say ""
  warn "[주의] 20초 안에 상황판 서버가 응답하지 않았습니다. 로그 끝부분:"
  for f in "$BOARD/agent.log" "$BOARD/agent-launchd.log"; do
    if [ -f "$f" ]; then say "  ── $f"; tail -n 15 "$f" | sed 's/^/    /'; fi
  done
  warn "  이 내용을 그대로 전달해 주세요. 상태 확인: \"$PY\" -I \"$AGENT\" --status"
fi

say ""
say "설치 끝. 다음 순서로 시작하세요."
say "  1) 브라우저에 열린 페이지가 상황판입니다(세션은 바로 실시간으로 보임). 북마크해 두면 다음부터 바로 열립니다."
say "     다시 열기: \"$PY\" -I \"$AGENT\" --open"
say "  2) Claude 앱에서 위 작업 폴더로 새 세션을 열고 이름을 「관제 (상황판)」 처럼 바꿔 사이드바에 고정합니다."
say "  3) 그 세션에서 /board 를 보내면 내 답 대기 · 완료 · 정리 후보 판정과 카드 채팅이 켜집니다."
say "  알림이 안 보이면: 시스템 설정 → 알림 → '스크립트 편집기' 허용. 시험: \"$PY\" -I \"$AGENT\" --test-notify"
say "  업데이트: 새 압축 파일을 풀고 bash install.sh 다시 실행.  제거: bash uninstall.sh"
