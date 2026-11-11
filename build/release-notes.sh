#!/bin/bash
# 릴리스 본문을 찍는다(형식: 변경 사항 · 설치 · 전체 변경 내역). 릴리스 CI(.github/workflows/build-mac.yml)가 쓰고, 로컬에서 미리 볼 수도 있다.
#
#   bash build/release-notes.sh <태그 vX.Y.Z> [저장소 owner/name]                 본문 전체 → 표준 출력
#   bash build/release-notes.sh --mac-section <태그 vX.Y.Z> [저장소 owner/name]   macOS 설치 단락만(이미 있는 릴리스 본문에 덧붙일 때)
#
# 변경 사항 = 이전 vX.Y.Z 태그 뒤 커밋 제목. 태그를 아직 안 만들었으면 HEAD 까지로 미리 본다.
# 저장소를 안 주면 GITHUB_REPOSITORY, 그것도 없으면 origin 주소에서 읽는다.
# macOS 단락은 <!-- macos-install --> 표시로 감싼다. CI 가 이 표시로 「이미 덧붙였는지」를 본다.
set -euo pipefail

MODE=full
if [ "${1:-}" = "--mac-section" ]; then MODE=mac; shift; fi
TAG="${1:?태그(vX.Y.Z)를 주세요}"
REPO="${2:-${GITHUB_REPOSITORY:-}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "$REPO" ]; then
  REPO="$(git -C "$ROOT" remote get-url origin | sed -e 's#.*github\.com[:/]##' -e 's#\.git$##')"
fi
BLOB="https://github.com/$REPO/blob/$TAG/README.md"

mac_section() {
  echo "<!-- macos-install -->"
  echo "**macOS** — \`SessionOrchestrator-mac.dmg\`를 받아 열고 Session Orchestrator를 응용 프로그램 폴더로 끌어다 놓은 뒤 실행하세요(애플 실리콘 · 인텔 공용)."
  echo "Apple 개발자 서명이 없어 처음 열 때 경고가 뜹니다. 그러면 시스템 설정 → 개인정보 보호 및 보안에서 \"그래도 열기\"를 누르세요."
  echo "처음 열면 감시할 작업 폴더를 묻고, 그다음부터는 메뉴 막대의 막대 아이콘에서 상황판을 엽니다. Python은 필요 없습니다."
  echo "업데이트는 새 dmg의 앱으로 바꿔 끼운 뒤 한 번 열면 됩니다. 아직 사람이 쓰는 Mac에서 써 본 적이 없는 판입니다 — [macOS 설치]($BLOB#macos-설치) · [시험 절차]($BLOB#macos-시험-절차mac-쓰는-팀원용)"
  echo "<!-- /macos-install -->"
}

if [ "$MODE" = mac ]; then
  echo "## 설치 (macOS)"
  echo
  mac_section
  exit 0
fi

END="$TAG"
git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || END=HEAD   # 태그 전 미리 보기
PREV="$(git -C "$ROOT" describe --tags --abbrev=0 --match 'v[0-9]*' "$END^" 2>/dev/null || true)"
if [ -n "$PREV" ]; then
  RANGE="$PREV..$END"
  COMPARE="https://github.com/$REPO/compare/$PREV...$TAG"
else
  RANGE="$END"
  COMPARE="https://github.com/$REPO/commits/$TAG"
fi

echo "## 변경 사항"
echo
git -C "$ROOT" log --no-merges --pretty='- %s' "$RANGE"
echo
echo "## 설치"
echo
echo "**Windows** — 이 릴리스의 Windows 용 파일을 받아 [README 설치 절]($BLOB#설치팀원용-windows)을 따르세요."
echo
mac_section
echo
echo "**전체 변경 내역**: $COMPARE"
